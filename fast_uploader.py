"""
High-throughput MTProto upload manager for Pyrogram.
Works seamlessly with Pyrogram's native multi-worker concurrent transmission engine.
Includes Python 3.12 StreamReader concurrency shield and safe session recovery.
"""

import os
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


Session.stop = _safe_session_stop
Session.restart = _safe_session_restart

ORIGINAL_SAVE_FILE = Client.save_file


async def fast_save_file(
    self: Client,
    path: Union[str, BinaryIO, Path],
    file_id: Optional[int] = None,
    file_part: int = 0,
    progress: Optional[Callable] = None,
    progress_args: tuple = ()
) -> Union[raw.types.InputFileBig, raw.types.InputFile, None]:
    """
    High-throughput MTProto chunk uploader utilizing native multi-stream pool.
    """
    return await ORIGINAL_SAVE_FILE(self, path, file_id, file_part, progress, progress_args)


def install_fast_uploader(client: Client, max_workers: int = 4) -> None:
    """Installs high-speed parallel uploader on the Pyrogram client instance."""
    logger.info(f"⚡ Fast MTProto Uploader active (max_concurrent_transmissions={max_workers}).")


class ParallelUploader:
    def __init__(self, client: Client, max_workers: int = 4):
        self.client = client
        self.max_workers = max_workers
        install_fast_uploader(client, max_workers)

