"""Track-metadata utilities for the Downloaded Library.

Resolves album titles from clip lineage and attaches image bytes to a
``TrackMetadata`` dataclass without otherwise mutating it.
"""

from __future__ import annotations

from ..models import SunoClip, TrackMetadata


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


def _with_image(meta: TrackMetadata, image_data: bytes | None) -> TrackMetadata:
    """Return metadata with image data attached."""
    return TrackMetadata(
        title=meta.title,
        artist=meta.artist,
        album=meta.album,
        album_artist=meta.album_artist,
        date=meta.date,
        lyrics=meta.lyrics,
        comment=meta.comment,
        image_data=image_data,
        suno_style=meta.suno_style,
        suno_style_summary=meta.suno_style_summary,
        suno_model=meta.suno_model,
        suno_handle=meta.suno_handle,
        suno_parent=meta.suno_parent,
        suno_lineage=meta.suno_lineage,
    )


__all__ = ["_album_for_clip", "_with_image"]
