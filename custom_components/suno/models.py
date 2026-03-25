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
    lyrics: str = ""
    prompt: str = ""
    gpt_description_prompt: str = ""
    video_url: str = ""
    video_is_stale: bool | None = None
    video_cover_url: str = ""
    model_name: str = ""
    major_model_version: str = ""
    display_name: str = ""
    handle: str = ""
    edited_clip_id: str = ""
    is_remix: bool = False
    history: list[dict[str, Any]] | None = None

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
            lyrics=raw.get("lyrics", ""),
            prompt=metadata.get("prompt", ""),
            gpt_description_prompt=metadata.get("gpt_description_prompt", ""),
            video_url=_fix_cdn_url(raw.get("video_url")),
            video_is_stale=metadata.get("video_is_stale"),
            video_cover_url=_fix_cdn_url(raw.get("video_cover_url")),
            model_name=raw.get("model_name", ""),
            major_model_version=raw.get("major_model_version", ""),
            display_name=raw.get("display_name", ""),
            handle=raw.get("handle", ""),
            edited_clip_id=metadata.get("edited_clip_id", ""),
            is_remix=metadata.get("is_remix", False) or False,
            history=metadata.get("history"),
        )

    @property
    def suno_model(self) -> str:
        """Combined model identifier for metadata tags."""
        if self.model_name and self.major_model_version:
            return f"{self.model_name} ({self.major_model_version})"
        return self.model_name

    @property
    def suno_lineage(self) -> str:
        """Formatted edit lineage for metadata tags."""
        parts: list[str] = []
        if self.is_remix and self.edited_clip_id:
            parts.append(f"Remix of {self.edited_clip_id[:8]}")
        elif self.edited_clip_id:
            parts.append(f"Derived from {self.edited_clip_id[:8]}")
        if self.history:
            for entry in self.history:
                parent = (entry.get("id") or "")[:8]
                start = entry.get("infill_start_s")
                end = entry.get("infill_end_s")
                if start is not None and end is not None:
                    mm_s, ss_s = divmod(int(start), 60)
                    mm_e, ss_e = divmod(int(end), 60)
                    desc = f"Edit {mm_s:02d}:{ss_s:02d}-{mm_e:02d}:{ss_e:02d}"
                else:
                    desc = "Edit"
                lyr = (entry.get("infill_lyrics") or "").strip()
                if lyr:
                    preview = lyr[:60].replace("\n", " ")
                    if len(lyr) > 60:
                        preview += "..."
                    desc += f': "{preview}"'
                if parent:
                    desc += f" (from {parent})"
                parts.append(desc)
        return "\n".join(parts)


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
    return hashlib.md5(  # noqa: S324
        f"{clip.title}|{clip.tags}|{clip.image_url}|{clip.display_name}|{clip.video_url}".encode()
    ).hexdigest()[:12]


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
