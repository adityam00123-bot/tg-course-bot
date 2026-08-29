"""
High-Speed Media & Stream Downloader.
Supports async direct HTTP chunking and lossless FFmpeg HLS/m3u8 streaming.
"""

import os
import time
import asyncio
import logging
import aiohttp
import aiofiles
from pathlib import Path
from typing import Optional, Callable, Dict

logger = logging.getLogger("stream_downloader")


class CourseDownloader:
    """Downloader for HLS streams, direct MP4 video files, and PDF materials."""

    @staticmethod
    async def download_direct_file(
        url: str,
        output_path: Path,
        headers: Optional[Dict[str, str]] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None
    ) -> Optional[Path]:
        """Download direct file (PDF / MP4) with async chunk streaming and progress."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = output_path.with_suffix(f"{output_path.suffix}.temp")

        if temp_file.exists():
            try: temp_file.unlink()
            except Exception: pass

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=3600)) as resp:
                    if resp.status != 200:
                        logger.error(f"Download failed: HTTP {resp.status} for {url}")
                        return None

                    total_size = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0

                    async with aiofiles.open(temp_file, mode="wb") as f:
                        async for chunk in resp.content.iter_chunked(1024 * 1024):  # 1MB chunks
                            await f.write(chunk)
                            downloaded += len(chunk)
                            if progress_cb and total_size:
                                progress_cb(downloaded, total_size)

            if temp_file.exists():
                temp_file.rename(output_path)
                return output_path
        except Exception as e:
            logger.error(f"Direct download error for {url}: {e}")
            if temp_file.exists():
                try: temp_file.unlink()
                except Exception: pass
        return None

    @staticmethod
    async def download_m3u8_stream(
        m3u8_url: str,
        output_path: Path,
        custom_headers: Optional[Dict[str, str]] = None,
        progress_cb: Optional[Callable[[str], None]] = None
    ) -> Optional[Path]:
        """
        Download HLS / m3u8 playlist stream directly using FFmpeg copy mode.
        Lossless, zero quality drop, fastest possible conversion to streamable MP4.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            try: output_path.unlink()
            except Exception: pass

        # Build FFmpeg command
        cmd = ["ffmpeg", "-y"]

        # Add custom headers if required
        if custom_headers:
            header_str = "".join([f"{k}: {v}\r\n" for k, v in custom_headers.items()])
            cmd.extend(["-headers", header_str])

        cmd.extend([
            "-i", m3u8_url,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            "-movflags", "+faststart",
            str(output_path)
        ])

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024:
                return output_path
            else:
                logger.error(f"FFmpeg m3u8 download error: {stderr.decode(errors='replace')[:400]}")
        except Exception as e:
            logger.error(f"Execution error while downloading m3u8 stream: {e}")

        return None
