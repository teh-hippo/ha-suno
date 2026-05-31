"""Contracts for the Downloaded Library: data shapes and Protocol surfaces.

These types are the vocabulary the reconciliation engine speaks. Reading them
in one module documents what the engine demands of its world.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Protocol

from ..models import SunoClip, TrackMetadata, clip_meta_hash, image_url_hash, selected_image_url


class RetagResult(Enum):
    """Outcome of attempting to re-tag an existing audio file on disk."""

    OK = "ok"
    MISSING = "missing"
    FAILED = "failed"


@dataclass
class DownloadItem:
    """A clip selected for the Downloaded Library."""

    clip: SunoClip
    sources: list[str]
    quality: str


@dataclass(frozen=True, slots=True)
class DownloadedLibraryStatus:
    """Published Downloaded Library status for Home Assistant consumers."""

    running: bool = False
    pending: int = 0
    errors: int = 0
    last_result: str = ""
    last_download: str | None = None
    file_count: int = 0
    size_mb: float = 0.0
    source_breakdown: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class DesiredDownloadPlan:
    """Desired Downloaded Library records and safety metadata."""

    items: list[DownloadItem]
    preserved_ids: set[str]
    source_to_name: dict[str, str]
    playlist_order: dict[str, list[str]]


@dataclass(frozen=True, slots=True)
class RenderedAudio:
    """Rendered audio bytes and file format."""

    data: bytes
    fmt: str


# Sentinel for "no album marker should be stored" — distinguishes from
# "store an empty-string album", which is never meaningful here.
class _AlbumUnchanged:
    """Marker that means 'leave the album field as it is'."""


_ALBUM_UNCHANGED: Any = _AlbumUnchanged()


@dataclass(slots=True)
class ManifestEntry:
    """Typed in-memory representation of one ``.suno_download.json`` record.

    Fields group by lifecycle to make the v6.3.4 clip-mirror vs file-mirror
    split a type-level contract instead of a docstring convention.

    Group 1 — identity / routing: refreshed whenever the desired plan is
    rebuilt, not tied to clip metadata or file bytes.

    Group 2 — clip mirror: refreshable from a ``SunoClip`` alone, no
    dependency on the on-disk file. ``apply_clip_metadata`` is the only
    code path that writes these.

    Group 3 — file mirror: requires a verified file write to be
    truthful. Writing them on an unchanged file would falsely claim the
    file matches current Suno bytes when it might not.
    ``apply_file_state`` is the only code path that writes these.

    Group 4 — video sidecar: refreshed when the music-video URL changes
    or the WebP conversion settings change.

    Serialisation: ``to_dict`` and ``from_dict`` round-trip to the same
    JSON dict shape that lives on disk today, so no manifest migration
    is required when this type is introduced. Unknown extra keys are
    preserved in ``extras`` so future fields written by a newer build
    survive a load-save cycle in an older build.
    """

    # Identity / routing
    path: str = ""
    sources: list[str] = field(default_factory=list)
    source_modes: dict[str, str] = field(default_factory=dict)
    quality: str = ""

    # Clip mirror (managed via apply_clip_metadata)
    title: str = ""
    created: str | None = None
    meta_hash: str = ""
    album: str | None = None  # None = "not stored" (matches absence from dict)

    # File mirror (managed via apply_file_state)
    size: int = 0
    embedded_art_hash: str = ""

    # Video sidecar
    video_url_hash: str = ""
    video_art_settings: dict[str, Any] | None = None
    video_art_failed: dict[str, Any] | None = None

    # Any keys we did not recognise — preserved across round-trip so a
    # newer build's fields are not destroyed by an older build's load-save.
    extras: dict[str, Any] = field(default_factory=dict)

    _KNOWN_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "path",
            "sources",
            "source_modes",
            "quality",
            "title",
            "created",
            "meta_hash",
            "album",
            "size",
            "embedded_art_hash",
            "video_url_hash",
            "video_art_settings",
            "video_art_failed",
        }
    )

    # ── Clip-mirror writes ──────────────────────────────────────────

    def apply_clip_metadata(self, clip: SunoClip, *, album: str | None = None) -> None:
        """Refresh fields that mirror the ``SunoClip`` and nothing else.

        Safe to call any time a fresh clip is available — including
        the per-sync reconcile path that heals records whose
        denormalised metadata drifted in a prior code generation. Never
        touches file-mirror fields.
        """
        self.title = clip.title
        self.created = clip.created_at[:10] if clip.created_at else None
        self.meta_hash = clip_meta_hash(clip)
        self.album = album

    # ── File-mirror writes ──────────────────────────────────────────

    def apply_file_state(self, clip: SunoClip, file_size: int) -> None:
        """Refresh fields that mirror the on-disk file bytes.

        Call ONLY after a verified file write (download or successful
        retag). ``embedded_art_hash`` claims the file contains the
        current Suno art; writing it without a corresponding file change
        makes the manifest lie about reality.
        """
        self.size = file_size
        self.embedded_art_hash = image_url_hash(selected_image_url(clip))

    # ── Lifecycle helpers ───────────────────────────────────────────

    def clear_for_redownload(self) -> None:
        """Reset every field whose meaning is "describes the file on disk".

        Closes the v6.3.1–v6.3.4 leak where three call sites manually
        cleared ``path`` + ``meta_hash`` (+ sometimes ``album``) but
        forgot ``embedded_art_hash``, leaving a stale art-hash sentinel
        that suppressed the next retag.
        """
        self.path = ""
        self.meta_hash = ""
        self.album = None
        self.embedded_art_hash = ""

    # ── Planning ────────────────────────────────────────────────────

    def needs_retag(self, clip: SunoClip, resolved_album: str | None) -> str | None:
        """Return the first reason this entry needs retagging, or None.

        Returns ``"meta"`` when the clip's text fields changed,
        ``"art"`` when the file's embedded art is older than what
        Suno has now, ``"album"`` when the inherited album marker
        changed, or ``None`` when nothing is stale.

        Returning a reason string instead of a bool gives planning a
        cheap log line so reconcile runs can explain themselves.
        """
        new_hash = clip_meta_hash(clip)
        if self.meta_hash and new_hash != self.meta_hash:
            return "meta"
        expected_art_hash = image_url_hash(selected_image_url(clip))
        if expected_art_hash and self.embedded_art_hash != expected_art_hash:
            return "art"
        if resolved_album is None:
            if self.album is not None:
                return "album"
        elif resolved_album != self.album:
            return "album"
        return None

    # ── Serialisation boundary ──────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the on-disk JSON dict shape.

        Omits empty optional fields so the on-disk representation
        matches what v6.3.4 wrote (e.g. ``album`` only appears when
        set, ``embedded_art_hash`` only when non-empty).
        """
        out: dict[str, Any] = {
            "path": self.path,
            "sources": list(self.sources),
            "source_modes": dict(self.source_modes),
            "quality": self.quality,
            "title": self.title,
            "created": self.created,
            "size": self.size,
            "meta_hash": self.meta_hash,
        }
        if self.album is not None:
            out["album"] = self.album
        if self.embedded_art_hash:
            out["embedded_art_hash"] = self.embedded_art_hash
        if self.video_url_hash:
            out["video_url_hash"] = self.video_url_hash
        if self.video_art_settings is not None:
            out["video_art_settings"] = self.video_art_settings
        if self.video_art_failed is not None:
            out["video_art_failed"] = self.video_art_failed
        out.update(self.extras)
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ManifestEntry:
        """Construct from an on-disk JSON dict entry, preserving unknown keys."""
        extras = {k: v for k, v in raw.items() if k not in cls._KNOWN_FIELDS}
        return cls(
            path=str(raw.get("path", "")),
            sources=list(raw.get("sources", []) or []),
            source_modes=dict(raw.get("source_modes", {}) or {}),
            quality=str(raw.get("quality", "")),
            title=str(raw.get("title", "")),
            created=raw.get("created"),
            meta_hash=str(raw.get("meta_hash", "")),
            album=raw.get("album"),
            size=int(raw.get("size", 0) or 0),
            embedded_art_hash=str(raw.get("embedded_art_hash", "")),
            video_url_hash=str(raw.get("video_url_hash", "")),
            video_art_settings=raw.get("video_art_settings"),
            video_art_failed=raw.get("video_art_failed"),
            extras=extras,
        )

    def __post_init__(self) -> None:
        # Guard against accidental ``album=""`` writes that would
        # serialise to a stored empty-string instead of "absent".
        if self.album == "":
            self.album = None


class DownloadedLibraryStorage(Protocol):
    """Persistence adapter for Downloaded Library state."""

    async def async_load(self) -> dict[str, Any] | None: ...

    async def async_save(self, state: dict[str, Any]) -> None: ...


class DownloadedLibraryCache(Protocol):
    """Audio cache adapter used by the Downloaded Library."""

    async def async_get(self, clip_id: str, fmt: str, meta_hash: str) -> Path | None: ...

    async def async_put(self, clip_id: str, fmt: str, data: bytes, meta_hash: str) -> None: ...


class DownloadedLibraryAudio(Protocol):
    """Audio rendering adapter for Downloaded Library files."""

    async def fetch_image(self, image_url: str) -> bytes | None: ...

    async def render(
        self,
        clip: SunoClip,
        quality: str,
        meta: TrackMetadata,
        image_url: str | None,
    ) -> RenderedAudio | None: ...

    async def retag(self, target: Path, meta: TrackMetadata) -> bool: ...

    async def download_video(self, video_url: str, target: Path) -> None: ...


__all__ = [
    "DesiredDownloadPlan",
    "DownloadItem",
    "DownloadedLibraryAudio",
    "DownloadedLibraryCache",
    "DownloadedLibraryStatus",
    "DownloadedLibraryStorage",
    "ManifestEntry",
    "RenderedAudio",
    "RetagResult",
]
