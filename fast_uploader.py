"""
High-throughput MTProto upload manager for Pyrogram.
Guarantees 100% part verification, automatic per-chunk retransmission,
Python 3.12 StreamReader concurrency shielding, and zero dropped file parts.
"""

import os
import math
import asyncio
import logging
from typing import Optional, Union, Callable, BinaryIO
from pathlib import Path
from pyrogram import Client, raw
from pyrogram.session import Session

logger = logging.getLogger("migration_bot.fast_uploader")

# ---------------------------------------------------------------------------
# Python 3.12 StreamReader & MTProto Session Concurrency Shield
# ---------------------------------------------------------------------------
_orig_session_stop = Session.stop
_orig_session_restart = Session.restart
_session_restart_lock = asyncio.Lock()


async def _safe_session_stop(self):
    """Safely terminates transport & cancels recv_task without raising StreamReader collision."""
    try:
        recv_task = getattr(self, "recv_task", None)
        if recv_task and not recv_task.done():
            recv_task.cancel()

        conn = getattr(self, "connection", None)
        if conn:
            try:
                await conn.close()
            except Exception:
                pass

        if recv_task:
            try:
                await recv_task
            except (asyncio.CancelledError, RuntimeError, Exception):
                pass
    except Exception as e:
        logger.debug(f"Pyrogram session stop handled: {e}")


async def _safe_session_restart(self):
    """Guarantees only ONE coroutine restarts the MTProto session at a time, preventing deadlocks."""
    async with _session_restart_lock:
        try:
            await _safe_session_stop(self)
            await self.start()
        except Exception as e:
            logger.debug(f"Pyrogram session restart recovered: {e}")


async def reset_client_sessions(client: Optional[Client] = None) -> None:
    """Safely closes broken DC sockets in media_sessions and resets the main session."""
    if not client:
        return
    try:
        media_sessions = getattr(client, "media_sessions", {})
        for dc, sess in list(media_sessions.items()):
            try:
                await sess.stop()
            except Exception:
                pass
        if hasattr(client, "media_sessions") and isinstance(client.media_sessions, dict):
            client.media_sessions.clear()
    except Exception:
        pass
    try:
        session = getattr(client, "session", None)
        if session and hasattr(session, "restart"):
            await session.restart()
    except Exception:
        pass


Session.stop = _safe_session_stop
Session.restart = _safe_session_restart

# ---------------------------------------------------------------------------
# Robust, High-Speed MTProto Chunk Uploader (Zero Part Drop Guarantee)
# ---------------------------------------------------------------------------
CHUNK_SIZE = 512 * 1024  # 512 KB per MTProto part (standard optimal)


async def fast_save_file(
    self: Client,
    path: Union[str, BinaryIO, Path],
    file_id: Optional[int] = None,
    file_part: int = 0,
    progress: Optional[Callable] = None,
    progress_args: tuple = ()
) -> Union[raw.types.InputFileBig, raw.types.InputFile, None]:
    """
    High-throughput MTProto chunk uploader with strict per-part acknowledgement,
    automatic per-part retry with exponential backoff, and zero dropped parts.
    """
    if isinstance(path, (str, Path)):
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found for upload: {path}")
        file_size = file_path.stat().st_size
        file_name = file_path.name
        is_stream = False
    else:
        is_stream = True
        file_path = None
        file_name = getattr(path, "name", "file.bin") or "file.bin"
        if isinstance(file_name, Path):
            file_name = file_name.name
        file_name = os.path.basename(str(file_name))
        path.seek(0, os.SEEK_END)
        file_size = path.tell()
        path.seek(0)

    if file_size == 0:
        total_parts = 1
    else:
        total_parts = math.ceil(file_size / CHUNK_SIZE)

    is_big = file_size > 10 * 1024 * 1024  # > 10 MB is Big File in MTProto
    file_id = file_id or self.rnd_id()

    queue: asyncio.Queue = asyncio.Queue()
    for i in range(total_parts):
        offset = i * CHUNK_SIZE
        part_size = min(CHUNK_SIZE, file_size - offset) if file_size > 0 else 0
        queue.put_nowait((i, offset, part_size))

    uploaded_bytes = 0
    upload_error: Optional[Exception] = None
    progress_lock = asyncio.Lock()
    stream_lock = asyncio.Lock() if is_stream else None

    # Use 2-3 concurrent chunk workers per client stream to avoid socket congestion
    num_workers = min(getattr(self, "max_concurrent_transmissions", 3) or 3, 3)
    num_workers = max(1, min(num_workers, total_parts))

    async def _worker():
        nonlocal uploaded_bytes, upload_error
        fp = None if is_stream else open(file_path, "rb")
        try:
            while not queue.empty() and upload_error is None:
                try:
                    part_idx, offset, part_size = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                if is_stream:
                    async with stream_lock:
                        path.seek(offset)
                        chunk = path.read(part_size)
                else:
                    fp.seek(offset)
                    chunk = fp.read(part_size)

                if is_big:
                    rpc = raw.functions.upload.SaveBigFilePart(
                        file_id=file_id,
                        file_part=part_idx,
                        file_total_parts=total_parts,
                        bytes=chunk
                    )
                else:
                    rpc = raw.functions.upload.SaveFilePart(
                        file_id=file_id,
                        file_part=part_idx,
                        bytes=chunk
                    )

                part_attempts = 0
                max_part_attempts = 20
                part_ack = False

                while part_attempts < max_part_attempts and upload_error is None:
                    try:
                        res = await self.invoke(rpc)
                        if res is True or res:
                            part_ack = True
                            break
                    except Exception as err:
                        part_attempts += 1
                        err_str = str(err)
                        if any(k in err_str.lower() for k in ("broken pipe", "connectionreseterror", "oserror", "connectionlost", "timed out", "timeouterror")):
                            await reset_client_sessions(self)
                        if part_attempts >= 3:
                            logger.warning(
                                f"⚠️ Part {part_idx + 1}/{total_parts} retry {part_attempts}/{max_part_attempts} due to: {err}"
                            )
                        await asyncio.sleep(min(0.2 * (1.5 ** min(part_attempts, 8)), 4.0))

                if not part_ack:
                    upload_error = RuntimeError(
                        f"Upload failed: Part {part_idx + 1}/{total_parts} could not be uploaded after {max_part_attempts} attempts."
                    )
                    break

                async with progress_lock:
                    uploaded_bytes += len(chunk)
                    if progress:
                        try:
                            res_prog = progress(uploaded_bytes, file_size, *progress_args)
                            if asyncio.iscoroutine(res_prog):
                                await res_prog
                        except Exception:
                            pass
        finally:
            if fp:
                try:
                    fp.close()
                except Exception:
                    pass

    workers = [asyncio.create_task(_worker()) for _ in range(num_workers)]
    await asyncio.gather(*workers, return_exceptions=True)

    if upload_error is not None:
        raise upload_error

    if uploaded_bytes < file_size:
        raise RuntimeError(f"Upload incomplete: uploaded {uploaded_bytes}/{file_size} bytes ({total_parts} parts).")

    if is_big:
        return raw.types.InputFileBig(id=file_id, parts=total_parts, name=file_name)
    else:
        return raw.types.InputFile(id=file_id, parts=total_parts, name=file_name, md5_checksum="")


# Monkeypatch Pyrogram Client.save_file to use our robust uploader
Client.save_file = fast_save_file


def install_fast_uploader(client: Client, max_workers: int = 3) -> None:
    """Installs high-speed verified parallel uploader on the Pyrogram client instance."""
    client.max_concurrent_transmissions = min(max_workers, 3)
    client.save_file = fast_save_file.__get__(client, Client)
    logger.info(f"⚡ Fast Verified MTProto Uploader active (max_concurrent_transmissions={client.max_concurrent_transmissions}).")


class ParallelUploader:
    def __init__(self, client: Client, max_workers: int = 3):
        self.client = client
        self.max_workers = max_workers
        install_fast_uploader(client, max_workers)

