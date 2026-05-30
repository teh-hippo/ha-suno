"""Animated WebP cover art conversion for the Downloaded Library engine.

Converts MP4 animated cover art videos to animated WebP format using
ffmpeg's libwebp_anim encoder in lossless mode. The decoded source frames are
preserved at their source frame rate and resolution, with maximum compression
effort. Animated WebP is compatible with Navidrome and other music servers as
album cover art.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from homeassistant.core import HomeAssistant

from ..const import DOWNLOAD_FFMPEG_TIMEOUT

_LOGGER = logging.getLogger(__name__)


async def probe_libwebp_anim(hass: HomeAssistant, ffmpeg_binary: str) -> bool:
    """Check whether the system ffmpeg supports the libwebp_anim encoder.

    Returns True if the encoder is available, False otherwise.
    Runs once at startup; the result should be cached for the session.
    """

    def _probe() -> bool:
        import subprocess  # noqa: S404

        try:
            result = subprocess.run(  # noqa: S603
                [ffmpeg_binary, "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return "libwebp_anim" in result.stdout
        except OSError, subprocess.TimeoutExpired:
            return False

    return await hass.async_add_executor_job(_probe)


async def convert_mp4_to_webp(
    hass: HomeAssistant,
    ffmpeg_binary: str,
    mp4_path: Path,
    webp_path: Path,
) -> bool:
    """Convert an MP4 cover video to animated WebP using libwebp_anim.

    Returns True on success, False on any failure. Uses atomic write
    (tmp + os.replace) to prevent partial files on disk.
    """
    if not await hass.async_add_executor_job(mp4_path.exists):
        _LOGGER.debug("MP4 source missing for WebP conversion: %s", mp4_path)
        return False

    tmp_path = webp_path.with_suffix(".webp.tmp")
    await hass.async_add_executor_job(webp_path.parent.mkdir, 0o755, True, True)

    args = [
        ffmpeg_binary,
        "-y",
        "-hide_banner",
        "-i",
        str(mp4_path),
        "-c:v",
        "libwebp_anim",
        "-lossless",
        "1",
        "-compression_level",
        "6",
        "-loop",
        "0",
        "-an",
        "-f",
        "webp",
        str(tmp_path),
    ]

    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=DOWNLOAD_FFMPEG_TIMEOUT)
        if proc.returncode != 0:
            err_text = stderr.decode() if stderr else "unknown error"
            # Strip ffmpeg banner to surface the actual error
            err_lines = [
                ln
                for ln in err_text.splitlines()
                if not ln.startswith(("ffmpeg version", "  built with", "  configuration:", "  lib"))
            ]
            _LOGGER.warning(
                "WebP conversion failed for %s (rc=%d): %s",
                mp4_path.name,
                proc.returncode,
                "\n".join(err_lines)[:500] if err_lines else err_text[:200],
            )
            await hass.async_add_executor_job(tmp_path.unlink, True)
            return False

        await hass.async_add_executor_job(os.replace, str(tmp_path), str(webp_path))
        _LOGGER.info("Converted to animated WebP: %s", webp_path.name)
        return True

    except TimeoutError:
        _LOGGER.error("ffmpeg timed out converting %s to WebP", mp4_path.name)
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
        await hass.async_add_executor_job(tmp_path.unlink, True)
        return False
    except FileNotFoundError:
        _LOGGER.error("ffmpeg not found for WebP conversion")
        await hass.async_add_executor_job(tmp_path.unlink, True)
        return False
    except Exception:
        _LOGGER.warning("WebP conversion failed unexpectedly for %s", mp4_path.name, exc_info=True)
        await hass.async_add_executor_job(tmp_path.unlink, True)
        return False
