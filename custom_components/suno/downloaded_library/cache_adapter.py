"""Cache adapters for the Downloaded Library.

A no-op cache used when caching is disabled, plus an adapter that wraps the
playback ``SunoCache`` so the Downloaded Library can read / write through it
with its own keying convention.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class NullDownloadedLibraryCache:
    """No-op audio cache adapter."""

    async def async_get(self, _clip_id: str, _fmt: str, _meta_hash: str) -> Path | None:
        return None

    async def async_put(self, _clip_id: str, _fmt: str, _data: bytes, meta_hash: str) -> None:
        return


class SunoCacheDownloadedLibraryAdapter:
    """Adapter from the playback audio cache to Downloaded Library cache operations."""

    def __init__(self, cache: Any) -> None:
        self._cache = cache

    async def async_get(self, clip_id: str, fmt: str, meta_hash: str) -> Path | None:
        if not hasattr(self._cache, "async_get"):
            return None
        result = await self._cache.async_get(clip_id, fmt, meta_hash=meta_hash)
        return result if isinstance(result, Path) and result.is_file() else None

    async def async_put(self, clip_id: str, fmt: str, data: bytes, meta_hash: str) -> None:
        if not hasattr(self._cache, "async_put"):
            return
        await self._cache.async_put(clip_id, fmt, data, meta_hash=meta_hash)


__all__ = [
    "NullDownloadedLibraryCache",
    "SunoCacheDownloadedLibraryAdapter",
]
