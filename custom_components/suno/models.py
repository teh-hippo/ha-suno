"""Data models for Suno integration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .const import CDN_BASE_URL


def _fix_cdn_url(url: str | None) -> str:
    """Normalise CDN URLs to the primary endpoint.

    The Suno API sometimes returns cdn2.suno.ai URLs that are unreliable or
    return errors. We rewrite them to cdn1.suno.ai which is the stable CDN.
    """
    return url.replace("cdn2.suno.ai", "cdn1.suno.ai") if url else ""


@dataclass(slots=True)
class SunoUser:
    id: str
    display_name: str


@dataclass(slots=True)
class SunoClip:
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
        metadata = raw.get("metadata") or {}
        clip_id = raw.get("id", "")
        audio_url = raw.get("audio_url", "")
        # Suno sometimes returns temporary "audiopipe" streaming URLs that
        # expire and break later playback. Replace with the deterministic
        # CDN URL which is permanent.
        if audio_url and "audiopipe" in audio_url and clip_id:
            audio_url = f"{CDN_BASE_URL}/{clip_id}.mp3"
        return cls(
            id=clip_id,
            title=raw.get("title", "Untitled"),
            audio_url=audio_url,
            image_url=_fix_cdn_url(raw.get("image_url")),
            image_large_url=_fix_cdn_url(raw.get("image_large_url")),
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
    credits_left: int
    monthly_limit: int
    monthly_usage: int
    period: str | None

    @classmethod
    def from_api_response(cls, raw: dict[str, Any]) -> SunoCredits:
        return cls(
            credits_left=raw.get("total_credits_left", 0),
            monthly_limit=raw.get("monthly_limit", 0),
            monthly_usage=raw.get("monthly_usage", 0),
            period=raw.get("period"),
        )


def clip_meta_hash(clip: SunoClip) -> str:
    """Short hash of clip metadata for change detection."""
    return hashlib.md5(f"{clip.title}|{clip.tags}|{clip.image_url}".encode()).hexdigest()[:12]  # noqa: S324


@dataclass(slots=True)
class SunoPlaylist:
    id: str
    name: str
    image_url: str
    num_clips: int

    @classmethod
    def from_api_response(cls, raw: dict[str, Any]) -> SunoPlaylist:
        return cls(
            id=raw.get("id", ""),
            name=raw.get("name", "Untitled"),
            image_url=_fix_cdn_url(raw.get("image_url")),
            num_clips=raw.get("num_total_results", 0),
        )
