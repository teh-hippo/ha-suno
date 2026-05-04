"""Contracts for the Downloaded Library: data shapes and Protocol surfaces.

These types are the vocabulary the reconciliation engine speaks. Reading them
in one module documents what the engine demands of its world.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from ..models import SunoClip, TrackMetadata


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

    def as_legacy_tuple(self) -> tuple[list[DownloadItem], set[str], dict[str, str], dict[str, list[str]]]:
        """Return the tuple shape used by the legacy download manager tests."""
        return self.items, self.preserved_ids, self.source_to_name, self.playlist_order

    @classmethod
    def from_legacy_tuple(
        cls,
        value: tuple[list[DownloadItem], set[str], dict[str, str], dict[str, list[str]]],
    ) -> DesiredDownloadPlan:
        """Build a plan from the legacy tuple shape."""
        items, preserved_ids, source_to_name, playlist_order = value
        return cls(items, preserved_ids, source_to_name, playlist_order)


@dataclass(frozen=True, slots=True)
class RenderedAudio:
    """Rendered audio bytes and file format."""

    data: bytes
    fmt: str


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
    "RenderedAudio",
    "RetagResult",
]
