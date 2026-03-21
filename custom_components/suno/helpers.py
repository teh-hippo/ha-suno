"""Pure utility functions for the Suno integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .const import CDN_BASE_URL, SYNC_FFMPEG_TIMEOUT
from .models import SunoClip

_LOGGER = logging.getLogger(__name__)


def _fix_cdn_url(url: str | None) -> str:
    """Rewrite cdn2.suno.ai URLs to cdn1.suno.ai (cdn2 returns 403)."""
    if not url:
        return ""
    return url.replace("cdn2.suno.ai", "cdn1.suno.ai")


def _sanitise_clip(raw: dict[str, Any]) -> SunoClip:
    """Build a SunoClip from raw API data, keeping only allowlisted fields."""
    metadata = raw.get("metadata") or {}
    image_url = _fix_cdn_url(raw.get("image_url"))
    image_large_url = _fix_cdn_url(raw.get("image_large_url"))

    audio_url = raw.get("audio_url", "")
    clip_id = raw.get("id", "")
    if audio_url and "audiopipe" in audio_url and clip_id:
        audio_url = f"{CDN_BASE_URL}/{clip_id}.mp3"

    return SunoClip(
        id=clip_id,
        title=raw.get("title", "Untitled"),
        audio_url=audio_url,
        image_url=image_url,
        image_large_url=image_large_url,
        is_liked=raw.get("is_liked", False),
        status=raw.get("status", "unknown"),
        created_at=raw.get("created_at", ""),
        tags=metadata.get("tags", ""),
        duration=metadata.get("duration") or 0.0,
        clip_type=metadata.get("type", ""),
        has_vocal=metadata.get("has_vocal", False),
    )


async def wav_to_flac(ffmpeg_binary: str, wav_data: bytes, title: str, artist: str) -> bytes | None:
    """Transcode WAV bytes to FLAC with embedded metadata via ffmpeg."""
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg_binary,
            "-i",
            "pipe:0",
            "-metadata",
            f"title={title}",
            "-metadata",
            f"artist={artist}",
            "-f",
            "flac",
            "-compression_level",
            "5",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=wav_data),
            timeout=SYNC_FFMPEG_TIMEOUT,
        )
        if proc.returncode != 0:
            _LOGGER.warning("ffmpeg transcode failed: %s", stderr.decode()[:200])
            return None
        return stdout
    except TimeoutError:
        _LOGGER.error("ffmpeg transcode timed out after %ds", SYNC_FFMPEG_TIMEOUT)
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        return None
    except FileNotFoundError:
        _LOGGER.error("ffmpeg not found. Install ffmpeg for high quality audio.")
        return None
    except Exception:
        _LOGGER.exception("FLAC transcode error")
        return None
