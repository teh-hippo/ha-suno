"""On-disk audio cache with LRU eviction for the Suno integration."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from time import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
STORE_KEY = "suno_cache_index"
_MP3_ID3_MAGIC = b"ID3"
_MP3_SYNC_BYTE = 0xFF
_FLAC_MAGIC = b"fLaC"


class SunoCache:
    """On-disk audio cache with LRU eviction."""

    def __init__(self, hass: HomeAssistant, max_size_mb: int) -> None:
        self._hass = hass
        self._max_bytes = max_size_mb * 1024 * 1024
        self._cache_dir = Path(hass.config.cache_path("suno"))
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, STORE_KEY)
        self._index: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._save_pending = False
        self._save_handle: asyncio.TimerHandle | None = None

    @property
    def cache_dir(self) -> Path:
        """Return the cache directory path."""
        return self._cache_dir

    @property
    def file_count(self) -> int:
        """Number of files in the cache."""
        return len(self._index)

    async def async_size_mb(self) -> float:
        """Total cache size in MB."""
        return await self._hass.async_add_executor_job(self._calc_size_mb)

    def _calc_size_mb(self) -> float:
        """Calculate total cache size in MB."""
        try:
            total = sum(
                f.stat().st_size for f in self._cache_dir.iterdir() if f.is_file() and not f.name.startswith(".")
            )
        except OSError:
            return 0.0
        return round(total / 1048576, 1)

    def _schedule_save(self) -> None:
        """Schedule a debounced index save."""
        self._save_pending = True
        if self._save_handle is not None:
            self._save_handle.cancel()
        self._save_handle = self._hass.loop.call_later(5, self._do_save)

    def _do_save(self) -> None:
        """Trigger the async save from the event loop."""
        self._save_handle = None
        if self._save_pending:
            self._save_pending = False
            self._hass.async_create_task(self._store.async_save(self._index))

    async def async_flush(self) -> None:
        """Save immediately if a debounced save is pending."""
        if self._save_handle is not None:
            self._save_handle.cancel()
            self._save_handle = None
        if self._save_pending:
            self._save_pending = False
            await self._store.async_save(self._index)

    async def async_clear(self) -> None:
        """Clear all cached files and reset the index."""
        await self._hass.async_add_executor_job(self._wipe_cache_files)
        self._index = {}
        await self._store.async_save(self._index)

    async def async_init(self) -> None:
        """Create the cache directory, clean temp files, and load the index."""
        await self._hass.async_add_executor_job(self._init_dir)
        try:
            saved = await self._store.async_load()
        except Exception:
            _LOGGER.warning("Cache index incompatible or corrupt, resetting")
            storage_path = Path(self._hass.config.path(".storage", STORE_KEY))
            await self._hass.async_add_executor_job(storage_path.unlink, True)
            self._store = Store(self._hass, STORE_VERSION, STORE_KEY)
            await self._hass.async_add_executor_job(self._wipe_cache_files)
            saved = None
        if saved is not None:
            self._index = saved

    def _wipe_cache_files(self) -> None:
        """Remove all cached audio files."""
        if not self._cache_dir.exists():
            return
        for f in self._cache_dir.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                except OSError:
                    pass

    def _init_dir(self) -> None:
        """Create cache dir and remove stale .tmp files."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        for tmp in self._cache_dir.glob("*.tmp"):
            try:
                tmp.unlink()
            except OSError:
                _LOGGER.warning("Could not remove stale temp file: %s", tmp)

    async def async_get(self, clip_id: str, fmt: str, meta_hash: str | None = None) -> Path | None:
        """Return the cached file path if valid, else None."""
        async with self._lock:
            filename = f"{clip_id}.{fmt}"
            if (entry := self._index.get(filename)) is None:
                return None
            if meta_hash and isinstance(entry, dict) and entry.get("meta_hash") != meta_hash:
                _LOGGER.debug("Cache stale for %s (meta changed), invalidating", clip_id)
                await self._async_invalidate_locked(clip_id, fmt)
                return None
            path = self._cache_dir / filename
            if not await self._hass.async_add_executor_job(self._validate_file, path, fmt):
                self._index.pop(filename, None)
                await self._store.async_save(self._index)
                return None
            if isinstance(entry, dict):
                entry["access"] = time()
            else:
                self._index[filename] = {"access": time(), "meta_hash": ""}
            self._schedule_save()
            return path

    async def async_put(self, clip_id: str, fmt: str, data: bytes, meta_hash: str = "") -> Path | None:
        """Write data to cache atomically. Returns the final path or None."""
        async with self._lock:
            filename = f"{clip_id}.{fmt}"
            final_path = self._cache_dir / filename
            tmp_path = self._cache_dir / f"{filename}.tmp"
            await self._async_evict_locked(len(data))
            try:
                await self._hass.async_add_executor_job(self._atomic_write, tmp_path, final_path, data)
            except OSError:
                _LOGGER.warning("Failed to write cache file %s, skipping", filename)
                return None
            self._index[filename] = {"access": time(), "meta_hash": meta_hash}
            self._schedule_save()
            return final_path

    async def _async_invalidate_locked(self, clip_id: str, fmt: str) -> None:
        """Remove a cached file and its index entry (caller holds _lock)."""
        filename = f"{clip_id}.{fmt}"
        self._index.pop(filename, None)
        try:
            await self._hass.async_add_executor_job((self._cache_dir / filename).unlink, True)
        except OSError:
            pass
        self._schedule_save()

    async def async_evict(self, needed_bytes: int) -> None:
        """Remove oldest entries until there is room for needed_bytes."""
        async with self._lock:
            await self._async_evict_locked(needed_bytes)

    async def _async_evict_locked(self, needed_bytes: int) -> None:
        """Remove oldest entries (caller holds _lock)."""
        current = await self._hass.async_add_executor_job(self._total_size)
        if (target := self._max_bytes - needed_bytes) >= current:
            return
        by_age = sorted(
            self._index.items(),
            key=lambda kv: kv[1].get("access", 0) if isinstance(kv[1], dict) else float(kv[1]),
        )
        for filename, _ in by_age:
            if current <= target:
                break
            path = self._cache_dir / filename
            try:
                size = await self._hass.async_add_executor_job(self._file_size, path)
                await self._hass.async_add_executor_job(path.unlink, True)
            except OSError:
                # Could not unlink — leave the on-disk byte count untouched
                # so the caller doesn't think it freed space it didn't.
                pass
            else:
                if size > 0:
                    current -= size
            self._index.pop(filename, None)
        self._schedule_save()

    def _total_size(self) -> int:
        """Sum the size of all files in the cache directory."""
        total = 0
        for filename in list(self._index):
            try:
                total += (self._cache_dir / filename).stat().st_size
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
        """Write to a temp file then replace for atomicity."""
        try:
            tmp_path.write_bytes(data)
            os.replace(tmp_path, final_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _validate_file(path: Path, fmt: str) -> bool:
        """Check file exists, is non-empty, and has correct magic bytes."""
        try:
            if not path.is_file() or path.stat().st_size == 0:
                return False
            with open(path, "rb") as f:
                header = f.read(4)
            if fmt == "mp3":
                return header[:3] == _MP3_ID3_MAGIC or (len(header) >= 1 and header[0] == _MP3_SYNC_BYTE)
            return header[:4] == _FLAC_MAGIC if fmt == "flac" else True
        except OSError:
            return False
