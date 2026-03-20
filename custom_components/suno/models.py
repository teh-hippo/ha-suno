"""Data models for the Suno integration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
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


@dataclass
class SunoCredits:
    """Credit balance information."""

    credits_left: int
    monthly_limit: int
    monthly_usage: int
    period: str | None


@dataclass
class SunoPlaylist:
    """A playlist from the user's library."""

    id: str
    name: str
    image_url: str
    num_clips: int
