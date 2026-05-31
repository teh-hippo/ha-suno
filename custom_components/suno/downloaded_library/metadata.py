"""Track-metadata utilities for the Downloaded Library.

Resolves album titles from clip lineage.
"""

from __future__ import annotations

from ..models import SunoClip


def _album_for_clip(clip: SunoClip, clip_index: dict[str, SunoClip]) -> str | None:
    """Resolve the album title for a clip."""
    if clip.album_title:
        return clip.album_title
    if not clip.is_remix:
        return None
    root_id = clip.root_ancestor_id
    if not root_id or root_id == clip.id:
        return None
    if (root_clip := clip_index.get(root_id)) is None:
        return f"Remixes of {root_id[:8]}"
    return root_clip.title


def _manifest_album_for_clip(clip: SunoClip, clip_index: dict[str, SunoClip]) -> str | None:
    """Return the resolved album value worth storing in the manifest."""
    resolved_album = _album_for_clip(clip, clip_index)
    if resolved_album is None or resolved_album == clip.album_title:
        return None
    return resolved_album


__all__ = ["_album_for_clip", "_manifest_album_for_clip"]
