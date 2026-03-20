"""Pure utility functions for the Suno integration."""

from __future__ import annotations

from typing import Any

from .const import CDN_BASE_URL
from .models import SunoClip


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

    # Prefer cdn1 direct MP3 URL over audiopipe
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
