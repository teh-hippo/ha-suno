"""Data models for the Suno integration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .const import CDN_BASE_URL


def _fix_cdn_url(url: str | None) -> str:
    """Rewrite cdn2.suno.ai URLs to cdn1.suno.ai (cdn2 returns 403)."""
    if not url:
        return ""
    return url.replace("cdn2.suno.ai", "cdn1.suno.ai")


@dataclass(slots=True)
class SunoUser:
    """A Suno user profile."""

    id: str
    display_name: str


@dataclass(slots=True)
class SunoClip:
    """A song/clip from the Suno library."""

    id: str
    title: str
    audio_url: str
    image_url: str
    image_large_url: str
    is_liked: bool
    status: str
    created_at: str
    tags: str
    duration: float
    clip_type: str
    has_vocal: bool

    @classmethod
    def from_api_response(cls, raw: dict[str, Any]) -> SunoClip:
        """Build a SunoClip from raw API data, keeping only allowlisted fields."""
        metadata = raw.get("metadata") or {}
        image_url = _fix_cdn_url(raw.get("image_url"))
        image_large_url = _fix_cdn_url(raw.get("image_large_url"))

        audio_url = raw.get("audio_url", "")
        clip_id = raw.get("id", "")
        if audio_url and "audiopipe" in audio_url and clip_id:
            audio_url = f"{CDN_BASE_URL}/{clip_id}.mp3"

        return cls(
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


@dataclass(slots=True)
class SunoCredits:
    """Credit balance information."""

    credits_left: int
    monthly_limit: int
    monthly_usage: int
    period: str | None

    @classmethod
    def from_api_response(cls, raw: dict[str, Any]) -> SunoCredits:
        """Build SunoCredits from raw API billing data."""
        return cls(
            credits_left=raw.get("total_credits_left", 0),
            monthly_limit=raw.get("monthly_limit", 0),
            monthly_usage=raw.get("monthly_usage", 0),
            period=raw.get("period"),
        )


def clip_meta_hash(clip: SunoClip) -> str:
    """Compute a short hash of clip metadata for change detection."""
    key = f"{clip.title}|{clip.tags}|{clip.image_url}"
    return hashlib.md5(key.encode()).hexdigest()[:12]  # noqa: S324


@dataclass(slots=True)
class SunoPlaylist:
    """A playlist from the user's library."""

    id: str
    name: str
    image_url: str
    num_clips: int

    @classmethod
    def from_api_response(cls, raw: dict[str, Any]) -> SunoPlaylist:
        """Build a SunoPlaylist from raw API data."""
        return cls(
            id=raw.get("id", ""),
            name=raw.get("name", "Untitled"),
            image_url=_fix_cdn_url(raw.get("image_url")),
            num_clips=raw.get("num_total_results", 0),
        )
