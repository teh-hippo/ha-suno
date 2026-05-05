"""Home Assistant adapter for the Suno Downloaded Library."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DOWNLOAD_PATH,
    CONF_DOWNLOAD_VIDEOS,
)
from .downloaded_library import (
    DownloadedLibrary,
    DownloadedLibraryStatus,
    HomeAssistantDownloadedLibraryAudio,
    HomeAssistantDownloadedLibraryStorage,
    SunoCacheDownloadedLibraryAdapter,
)

if TYPE_CHECKING:
    from pathlib import Path

    from .api import SunoClient
    from .cache import SunoCache
    from .coordinator import SunoCoordinator

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
_SERVICE_DOWNLOAD = "download_library"


class SunoDownloadManager:
    """Home Assistant adapter for Downloaded Library reconciliation."""

    def __init__(self, hass: HomeAssistant, store_key: str) -> None:
        self.hass = hass
        self._storage = HomeAssistantDownloadedLibraryStorage(hass, store_key)
        self._downloaded_library = DownloadedLibrary(
            hass,
            self._storage,
            status_callback=self._handle_status_update,
        )
        self._coordinator: SunoCoordinator | None = None
        self._client: SunoClient | None = None
        self._raw_cache: SunoCache | Any | None = None
        self._updating_sensors = False

    async def async_init(self) -> None:
        """Load persisted Downloaded Library state."""
        await self._downloaded_library.async_load()

    @classmethod
    async def async_setup(
        cls,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: SunoCoordinator,
        client: SunoClient,
        cache: SunoCache | None = None,
    ) -> SunoDownloadManager:
        """Construct, wire adapters, and initialise persisted state.

        Home Assistant lifecycle wiring (coordinator listener, service
        registration, ``async_at_started`` initial-sync callback) lives with
        the runtime that owns the entry; see
        ``HomeAssistantRuntime._wire_downloaded_library_lifecycle``.
        """
        mgr = cls(hass, f"suno_sync_{entry.entry_id}")
        mgr._coordinator = coordinator
        mgr._client = client
        mgr._downloaded_library.audio = HomeAssistantDownloadedLibraryAudio(hass, client)
        raw_cache = cache if cache is not None else coordinator.cache
        mgr._raw_cache = raw_cache
        mgr._downloaded_library.cache = SunoCacheDownloadedLibraryAdapter(raw_cache) if raw_cache is not None else None
        mgr._downloaded_library.download_path = entry.options.get(CONF_DOWNLOAD_PATH, "")
        mgr._downloaded_library.download_videos = entry.options.get(CONF_DOWNLOAD_VIDEOS, True)
        await mgr.async_init()
        if download_path := entry.options.get(CONF_DOWNLOAD_PATH, ""):
            await mgr.cleanup_tmp_files(download_path)
        return mgr

    @property
    def is_running(self) -> bool:
        return self._downloaded_library.running

    @property
    def status(self) -> DownloadedLibraryStatus:
        return self._downloaded_library.status

    def get_downloaded_path(self, clip_id: str, meta_hash: str = "") -> Path | None:
        """Return absolute path to a downloaded file if it exists and is fresh."""
        return self._downloaded_library.get_downloaded_path(clip_id, meta_hash)

    def _handle_status_update(self, _status: DownloadedLibraryStatus) -> None:
        self._notify_coordinator()

    def _notify_coordinator(self) -> None:
        """Push sensor updates via the coordinator without re-triggering sync."""
        if self._coordinator and self._coordinator.data:
            self._updating_sensors = True
            try:
                self._coordinator.async_set_updated_data(self._coordinator.data)
            finally:
                self._updating_sensors = False

    async def cleanup_tmp_files(self, download_path: str) -> None:
        """Remove stale .tmp files from the download directory."""
        await self._downloaded_library.cleanup_tmp_files(download_path)

    async def async_cleanup_disabled_downloads(
        self,
        options: dict[str, Any],
        previous_options: dict[str, Any] | None = None,
    ) -> None:
        """Remove Mirror-managed downloads when local downloads are disabled."""
        await self._downloaded_library.async_cleanup_disabled_downloads(options, previous_options)


__all__ = [
    "STORE_VERSION",
    "SunoDownloadManager",
]
