"""Local audio file cache for the Suno integration.

Caches downloaded audio files (MP3/FLAC) on disk with LRU eviction.
Uses homeassistant.helpers.storage.Store for a persistent index so we
don't rely on filesystem atime (unreliable on many platforms).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from time import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
STORE_KEY = "suno_cache_index"

# Magic bytes used to validate cached files
_MP3_ID3_MAGIC = b"ID3"
_MP3_SYNC_BYTE = 0xFF
_FLAC_MAGIC = b"fLaC"


class SunoCache:
    """On-disk audio cache with LRU eviction."""

    def __init__(self, hass: HomeAssistant, max_size_mb: int) -> None:
        self._hass = hass
        self._max_bytes = max_size_mb * 1024 * 1024
        self._cache_dir = Path(hass.config.path("suno_cache"))
        self._store: Store[dict[str, float]] = Store(hass, STORE_VERSION, STORE_KEY)
        # filename -> last access epoch
        self._index: dict[str, float] = {}

    @property
    def cache_dir(self) -> Path:
        """Return the cache directory path."""
        return self._cache_dir

    @property
    def file_count(self) -> int:
        """Number of files in the cache."""
        return len(self._index)

    @property
    def size_mb(self) -> float:
        """Total cache size in MB (from disk)."""
        try:
            total = sum(
                f.stat().st_size for f in self._cache_dir.iterdir() if f.is_file() and not f.name.startswith(".")
            )
        except OSError:
            return 0.0
        return round(total / 1048576, 1)

    async def async_init(self) -> None:
        """Create the cache directory, clean temp files, and load the index."""
        await self._hass.async_add_executor_job(self._init_dir)
        saved = await self._store.async_load()
        if saved is not None:
            self._index = saved

    def _init_dir(self) -> None:
        """Create cache dir and remove stale .tmp files (runs in executor)."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        for tmp in self._cache_dir.glob("*.tmp"):
            try:
                tmp.unlink()
            except OSError:
                _LOGGER.warning("Could not remove stale temp file: %s", tmp)

    async def async_get(self, clip_id: str, fmt: str) -> Path | None:
        """Return the cached file path if valid, otherwise None."""
        filename = f"{clip_id}.{fmt}"
        if filename not in self._index:
            return None

        path = self._cache_dir / filename
        valid = await self._hass.async_add_executor_job(self._validate_file, path, fmt)
        if not valid:
            self._index.pop(filename, None)
            await self._store.async_save(self._index)
            return None

        self._index[filename] = time()
        await self._store.async_save(self._index)
        return path

    async def async_put(self, clip_id: str, fmt: str, data: bytes) -> Path | None:
        """Write data to cache atomically.  Returns the final path or None."""
        filename = f"{clip_id}.{fmt}"
        final_path = self._cache_dir / filename
        tmp_path = self._cache_dir / f"{filename}.tmp"

        # Evict if needed
        await self.async_evict(len(data))

        try:
            await self._hass.async_add_executor_job(self._atomic_write, tmp_path, final_path, data)
        except OSError:
            _LOGGER.warning("Failed to write cache file %s, skipping", filename)
            return None

        self._index[filename] = time()
        await self._store.async_save(self._index)
        return final_path

    async def async_evict(self, needed_bytes: int) -> None:
        """Remove oldest entries until there is room for needed_bytes."""
        current = await self._hass.async_add_executor_job(self._total_size)
        target = self._max_bytes - needed_bytes
        if current <= target:
            return

        # Sort by access time ascending (oldest first)
        by_age = sorted(self._index.items(), key=lambda kv: kv[1])
        for filename, _ in by_age:
            if current <= target:
                break
            path = self._cache_dir / filename
            try:
                size = await self._hass.async_add_executor_job(self._file_size, path)
                await self._hass.async_add_executor_job(path.unlink, True)
                current -= size
            except OSError:
                pass
            self._index.pop(filename, None)

        await self._store.async_save(self._index)

    def _total_size(self) -> int:
        """Sum the size of all files in the cache directory."""
        total = 0
        for filename in list(self._index):
            path = self._cache_dir / filename
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total

    @staticmethod
    def _file_size(path: Path) -> int:
        """Return file size or 0 if missing."""
        try:
            return path.stat().st_size
        except OSError:
            return 0

    @staticmethod
    def _atomic_write(tmp_path: Path, final_path: Path, data: bytes) -> None:
        """Write to a temp file then rename for atomicity."""
        tmp_path.write_bytes(data)
        os.rename(tmp_path, final_path)

    @staticmethod
    def _validate_file(path: Path, fmt: str) -> bool:
        """Check file exists, is non-empty, and has correct magic bytes."""
        try:
            if not path.is_file():
                return False
            size = path.stat().st_size
            if size == 0:
                return False
            with path.open("rb") as fh:
                header = fh.read(4)
            if fmt == "mp3":
                return header[:3] == _MP3_ID3_MAGIC or (len(header) >= 1 and header[0] == _MP3_SYNC_BYTE)
            if fmt == "flac":
                return header[:4] == _FLAC_MAGIC
            return True
        except OSError:
            return False
