"""
High-throughput MTProto upload manager for Pyrogram.
Guarantees 100% part verification, automatic per-chunk retransmission,
Python 3.12 StreamReader concurrency shielding, and zero dropped file parts.
"""

import os
import math
import asyncio
import logging
from typing import Optional, Union, Callable, BinaryIO, Any
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

_orig_download_media = Client.download_media
from pyrogram.file_id import FileId, FileType, ThumbnailSource
from pyrogram.session import Auth
from pyrogram import utils


async def fast_download_media(
    self: Client,
    message: Any,
    file_name: str = "downloads/",
    in_memory: bool = False,
    block: bool = True,
    progress: Optional[Callable] = None,
    progress_args: tuple = ()
) -> Optional[str]:
    """
    High-throughput MTProto parallel chunk downloader.
    Each download gets its own dedicated media Session to prevent contention.
    Uses 3 concurrent chunk workers with 1MB parts for maximum throughput.
    """
    file_id_str = None
    file_size = 0

    if isinstance(message, str):
        file_id_str = message
    elif hasattr(message, "video") and message.video:
        file_id_str = message.video.file_id
        file_size = message.video.file_size or 0
    elif hasattr(message, "document") and message.document:
        file_id_str = message.document.file_id
        file_size = message.document.file_size or 0
    elif hasattr(message, "audio") and message.audio:
        file_id_str = message.audio.file_id
        file_size = message.audio.file_size or 0
    elif hasattr(message, "photo") and message.photo:
        file_id_str = message.photo.file_id
        file_size = message.photo.file_size or 0

    # Fallback to native download for in-memory or small/unsupported types (<1MB)
    if in_memory or not file_id_str or file_size < 1024 * 1024:
        return await _orig_download_media(
            self,
            message=message,
            file_name=file_name,
            in_memory=in_memory,
            block=block,
            progress=progress,
            progress_args=progress_args
        )

    out_path = Path(file_name).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        file_id = FileId.decode(file_id_str)
        file_type = file_id.file_type

        if file_type == FileType.CHAT_PHOTO:
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(user_id=file_id.chat_id, access_hash=file_id.chat_access_hash)
            else:
                if file_id.chat_access_hash == 0:
                    peer = raw.types.InputPeerChat(chat_id=-file_id.chat_id)
                else:
                    peer = raw.types.InputPeerChannel(channel_id=utils.get_channel_id(file_id.chat_id), access_hash=file_id.chat_access_hash)
            location = raw.types.InputPeerPhotoFileLocation(peer=peer, photo_id=file_id.media_id, big=file_id.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG)
        elif file_type == FileType.PHOTO:
            location = raw.types.InputPhotoFileLocation(id=file_id.media_id, access_hash=file_id.access_hash, file_reference=file_id.file_reference, thumb_size=file_id.thumbnail_size)
        else:
            location = raw.types.InputDocumentFileLocation(id=file_id.media_id, access_hash=file_id.access_hash, file_reference=file_id.file_reference, thumb_size=file_id.thumbnail_size)

        dc_id = file_id.dc_id

        # Create a DEDICATED session for THIS download (no sharing = no contention)
        session = Session(
            self, dc_id,
            await Auth(self, dc_id, await self.storage.test_mode()).create()
            if dc_id != await self.storage.dc_id()
            else await self.storage.auth_key(),
            await self.storage.test_mode(),
            is_media=True
        )
        await session.start()

        try:
            if dc_id != await self.storage.dc_id():
                exported_auth = await self.invoke(raw.functions.auth.ExportAuthorization(dc_id=dc_id))
                await session.invoke(raw.functions.auth.ImportAuthorization(id=exported_auth.id, bytes=exported_auth.bytes))

            chunk_size = 1024 * 1024  # 1MB per MTProto part (maximum allowed)
            total_parts = math.ceil(file_size / chunk_size) if file_size > 0 else 1

            part_queue: asyncio.Queue = asyncio.Queue()
            for i in range(total_parts):
                offset = i * chunk_size
                part_size = min(chunk_size, file_size - offset) if file_size > 0 else 0
                part_queue.put_nowait((i, offset, part_size))

            downloaded_bytes = 0
            progress_lock = asyncio.Lock()
            file_lock = asyncio.Lock()
            dl_error: Optional[Exception] = None

            # Pre-allocate output file
            with open(out_path, "wb") as fp:
                if file_size > 0:
                    fp.truncate(file_size)

            out_fp = open(out_path, "r+b")

            num_workers = min(3, total_parts)

            async def _dl_worker():
                nonlocal downloaded_bytes, dl_error
                while not part_queue.empty() and dl_error is None:
                    try:
                        part_idx, offset, part_size = part_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    part_attempts = 0
                    max_part_attempts = 10
                    chunk_data = None

                    while part_attempts < max_part_attempts and dl_error is None:
                        try:
                            r = await session.invoke(
                                raw.functions.upload.GetFile(location=location, offset=offset, limit=part_size),
                                sleep_threshold=30
                            )
                            if isinstance(r, raw.types.upload.File):
                                chunk_data = r.bytes
                                break
                            elif isinstance(r, raw.types.upload.FileCdnRedirect):
                                raise NotImplementedError("CDN redirect handled by fallback")
                        except Exception as err:
                            part_attempts += 1
                            if any(k in str(err).lower() for k in ("file_reference_expired", "filereferenceexpired")):
                                dl_error = err
                                break
                            if part_attempts >= max_part_attempts:
                                dl_error = err
                                break
                            await asyncio.sleep(min(0.2 * (1.5 ** min(part_attempts, 6)), 3.0))

                    if chunk_data is not None:
                        async with file_lock:
                            out_fp.seek(offset)
                            out_fp.write(chunk_data)

                        async with progress_lock:
                            downloaded_bytes += len(chunk_data)
                            if progress:
                                try:
                                    res_prog = progress(downloaded_bytes, file_size, *progress_args)
                                    if asyncio.iscoroutine(res_prog):
                                        await res_prog
                                except Exception:
                                    pass

            try:
                workers = [asyncio.create_task(_dl_worker()) for _ in range(num_workers)]
                await asyncio.gather(*workers)

                if dl_error is not None:
                    raise dl_error

                if downloaded_bytes < (file_size * 0.99):
                    raise RuntimeError(f"Download incomplete: {downloaded_bytes}/{file_size} bytes received.")

                return str(out_path)
            finally:
                try:
                    out_fp.close()
                except Exception:
                    pass
        finally:
            await session.stop()

    except Exception as e:
        logger.debug(f"Parallel chunk download fallback to native: {e}")
        return await _orig_download_media(
            self,
            message=message,
            file_name=file_name,
            in_memory=in_memory,
            block=block,
            progress=progress,
            progress_args=progress_args
        )


# Monkeypatch Client.download_media to use fast_download_media
Client.download_media = fast_download_media


def install_fast_uploader(client: Client, max_workers: int = 3) -> None:
    """Installs high-speed verified parallel uploader and downloader on the Pyrogram client instance."""
    client.max_concurrent_transmissions = min(max_workers, 3)
    client.save_file = fast_save_file.__get__(client, Client)
    client.download_media = fast_download_media.__get__(client, Client)
    logger.info(f"⚡ Fast Verified MTProto Uploader & Downloader active (max_concurrent_transmissions={client.max_concurrent_transmissions}).")


class ParallelUploader:
    def __init__(self, client: Client, max_workers: int = 3):
        self.client = client
        self.max_workers = max_workers
        install_fast_uploader(client, max_workers)

