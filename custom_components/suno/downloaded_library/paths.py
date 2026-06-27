"""Path-naming helpers for the Downloaded Library.

Builds filesystem-safe relative paths for clips and their video sidecars.
"""

from __future__ import annotations

import hashlib

from pathvalidate import sanitize_filename

from ..const import QUALITY_HIGH
from ..models import SunoClip

_MAX_FILENAME_LEN = 200
# Length of the trailing disambiguation hash appended when sanitisation
# truncates a long name. Six hex characters give 24 bits, which is enough
# to make collisions vanishingly unlikely across a single library.
_TRUNCATION_HASH_LEN = 6
# Marker used to detect names this helper already disambiguated and to
# locate the boundary between truncated body and trailing hash.
_TRUNCATION_HASH_MARKER = "~"


def _safe_name(name: str) -> str:
    """Sanitise a string for use as a file or directory name.

    When sanitisation forces truncation past ``_MAX_FILENAME_LEN`` characters,
    a short stable hash of the *original* name is appended so that two distinct
    long names cannot collapse onto the same folder. Short names round-trip
    byte-identically with the legacy implementation.
    """
    safe = sanitize_filename(name, replacement_text="_")
    if not safe:
        return "untitled"
    if len(safe) <= _MAX_FILENAME_LEN:
        return safe
    suffix = (
        _TRUNCATION_HASH_MARKER
        + hashlib.sha1(name.encode("utf-8"), usedforsecurity=False).hexdigest()[:_TRUNCATION_HASH_LEN]
    )
    keep = _MAX_FILENAME_LEN - len(suffix)
    return safe[:keep] + suffix


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
