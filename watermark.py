"""
Video watermarking and thumbnail extraction module using FFmpeg.
Supports moving/floating anti-theft text watermarks and high-resolution video thumbnails.
"""

import os
import shutil
import asyncio
import logging
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger("migration_bot.watermark")


def get_ffmpeg_cmd() -> Optional[str]:
    """Find a usable ffmpeg executable path across system PATH, imageio-ffmpeg, or local dir."""
    # 1. System PATH
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg

    # 2. imageio-ffmpeg bundled binary
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass

    # 3. Local project binary
    local_win = Path(__file__).resolve().parent / "ffmpeg.exe"
    if local_win.exists():
        return str(local_win)

    local_unix = Path(__file__).resolve().parent / "ffmpeg"
    if local_unix.exists():
        return str(local_unix)

    return None


def is_ffmpeg_available() -> bool:
    """Check if ffmpeg executable is installed and reachable."""
    return get_ffmpeg_cmd() is not None


async def extract_video_thumbnail(video_path: Union[str, Path], output_thumb_path: Union[str, Path]) -> Optional[str]:
    """
    Extract a high-quality JPEG thumbnail frame from the 3-second mark of a video.
    Returns path to thumbnail if successful, else None.
    """
    ffmpeg_bin = get_ffmpeg_cmd()
    if not ffmpeg_bin:
        logger.debug("FFmpeg not found; skipping thumbnail extraction.")
        return None

    v_path = Path(video_path).resolve()
    t_path = Path(output_thumb_path).resolve()

    cmd = [
        ffmpeg_bin, "-y",
        "-ss", "00:00:03",
        "-i", str(v_path),
        "-vframes", "1",
        "-q:v", "2",
        str(t_path)
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        if t_path.exists() and t_path.stat().st_size > 0:
            logger.debug(f"Extracted thumbnail for {v_path.name}")
            return str(t_path)
    except Exception as e:
        logger.warning(f"Failed to extract thumbnail: {e}")

    return None


def get_system_font_path() -> Optional[str]:
    """Find a readable TTF font path across Linux and Windows systems."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\segoeui.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c.replace("\\", "/")
    return None


async def apply_video_watermark(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    watermark_text: str = "@CourseVerseHere",
    mode: str = "moving",
    opacity: float = 0.45
) -> str:
    """
    Apply a semi-transparent moving or static watermark to a video using FFmpeg.
    If FFmpeg is not available, returns the original input path safely.
    """
    ffmpeg_bin = get_ffmpeg_cmd()
    if not ffmpeg_bin:
        logger.warning("FFmpeg not installed. Uploading video without watermark.")
        return str(input_path)

    in_p = Path(input_path).resolve()
    out_p = Path(output_path).resolve()
    if out_p.suffix.lower() not in [".mp4", ".mkv", ".mov", ".webm", ".avi"]:
        out_p = out_p.with_suffix(".mp4")

    safe_text = watermark_text.replace(":", "\\:").replace("'", "\\'")
    if mode == "moving":
        x_expr = "(W-tw)*(0.5+0.42*sin(t*0.4))"
        y_expr = "(H-th)*(0.5+0.42*cos(t*0.25))"
    else:
        x_expr = "W-tw-25"
        y_expr = "25"

    filter_str = (
        f"drawtext=text='{safe_text}':"
        f"fontsize=24:"
        f"fontcolor=white@{opacity}:"
        f"box=1:boxcolor=black@0.30:boxborderw=6:"
        f"x={x_expr}:y={y_expr}"
    )

    cmd = [
        ffmpeg_bin, "-y",
        "-i", str(in_p),
        "-vf", filter_str,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "26",
        "-pix_fmt", "yuv420p",
        "-threads", "0",
        "-c:a", "copy",
        "-f", "mp4",
        str(out_p)
    ]

    logger.info(f"🎨 Applying {mode} anti-theft watermark to {in_p.name}...")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr_data = await proc.communicate()

        if out_p.exists() and out_p.stat().st_size > 0:
            logger.info(f"✅ Watermark applied successfully: {out_p.name}")
            return str(out_p)
        else:
            err_msg = stderr_data.decode("utf-8", errors="ignore")[-300:] if stderr_data else "Unknown error"
            logger.warning(f"Watermark output missing. FFmpeg error: {err_msg.strip()}")
            return str(input_path)
    except Exception as e:
        logger.error(f"Error applying watermark: {e}", exc_info=True)
        return str(input_path)


async def remux_to_streamable_mp4(input_path: Union[str, Path], output_path: Union[str, Path]) -> str:
    """
    Ultra-fast stream copy to H.264 MP4 with +faststart flags for instant in-app streaming in Telegram.
    Executes in ~0.1s without re-encoding. If FFmpeg is unavailable, safely returns original path.
    """
    ffmpeg_bin = get_ffmpeg_cmd()
    if not ffmpeg_bin:
        return str(input_path)

    in_p = Path(input_path).resolve()
    out_p = Path(output_path).resolve().with_suffix(".mp4")

    cmd = [
        ffmpeg_bin, "-y",
        "-i", str(in_p),
        "-c", "copy",
        "-movflags", "+faststart",
        str(out_p)
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        if out_p.exists() and out_p.stat().st_size > 0:
            return str(out_p)
    except Exception as e:
        logger.debug(f"Remux failed, falling back to original: {e}")

    return str(input_path)


async def remove_or_mask_watermark(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    position: str = "bottom_right",
    style: str = "delogo",
    brand_text: str = "@CourseVerseHere"
) -> str:
    ffmpeg_bin = get_ffmpeg_cmd()
    if not ffmpeg_bin:
        return str(input_path)

    in_p = Path(input_path).resolve()
    out_p = Path(output_path).resolve().with_suffix(".mp4")

    pos_coords = {
        "bottom_right": "x=main_w-260:y=main_h-90:w=250:h=80",
        "top_right": "x=main_w-260:y=10:w=250:h=80",
        "bottom_left": "x=10:y=main_h-90:w=250:h=80",
        "top_left": "x=10:y=10:w=250:h=80"
    }
    box_coords = pos_coords.get(position, pos_coords["bottom_right"])

    if style == "delogo":
        vf_filter = f"delogo={box_coords}:show=0"
    else:
        safe_brand = brand_text.replace(":", "\\:").replace("'", "\\'")
        bx, by = ("W-tw-30", "H-th-30") if position == "bottom_right" else ("30", "30")
        vf_filter = f"drawtext=text='{safe_brand}':fontsize=22:fontcolor=white:box=1:boxcolor=black@0.90:boxborderw=10:x={bx}:y={by}"

    cmd = [
        ffmpeg_bin, "-y",
        "-i", str(in_p),
        "-vf", vf_filter,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "26",
        "-pix_fmt", "yuv420p",
        "-threads", "0",
        "-c:a", "copy",
        str(out_p)
    ]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
        if out_p.exists() and out_p.stat().st_size > 0:
            return str(out_p)
    except Exception:
        pass
    return str(input_path)
