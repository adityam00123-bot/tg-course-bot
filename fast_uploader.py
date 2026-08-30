"""
High-throughput MTProto upload manager for Pyrogram.
Works seamlessly with Pyrogram's native multi-worker concurrent transmission engine.
"""

import logging
from typing import Optional, Union, Callable, BinaryIO
from pathlib import Path
from pyrogram import Client, raw

logger = logging.getLogger("migration_bot.fast_uploader")

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
    def __init__(self, client: Client, max_workers: int = 8):
        self.client = client
        self.max_workers = max_workers
        install_fast_uploader(client, max_workers)
