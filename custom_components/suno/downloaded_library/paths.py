"""Path-naming helpers for the Downloaded Library.

Builds filesystem-safe relative paths for clips and their video sidecars.
"""

from __future__ import annotations

from pathvalidate import sanitize_filename

from ..const import QUALITY_HIGH
from ..models import SunoClip

_MAX_FILENAME_LEN = 200


def _safe_name(name: str) -> str:
    """Sanitise a string for use as a file or directory name."""
    safe = sanitize_filename(name, replacement_text="_")
    return safe[:_MAX_FILENAME_LEN] if safe else "untitled"


def _clip_path(clip: SunoClip, quality: str) -> str:
    """Build the relative audio file path for a clip."""
    artist = _safe_name(clip.display_name or "Suno")
    title = _safe_name(clip.title or "untitled")
    clip_short = clip.id[:8]
    ext = "flac" if quality == QUALITY_HIGH else "mp3"
    return f"{artist}/{title}/{artist}-{title} [{clip_short}].{ext}"


def _video_clip_path(clip: SunoClip) -> str:
    """Build the relative music-video sidecar path for a clip."""
    artist = _safe_name(clip.display_name or "Suno")
    title = _safe_name(clip.title or "untitled")
    clip_short = clip.id[:8]
    return f"{artist}/{title}/{artist}-{title} [{clip_short}].mp4"


__all__ = ["_clip_path", "_safe_name", "_video_clip_path"]
