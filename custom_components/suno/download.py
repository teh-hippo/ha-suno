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
from .library_refresh import SunoData

if TYPE_CHECKING:
    import asyncio
    from pathlib import Path

    from .api import SunoClient
    from .cache import SunoCache
    from .coordinator import SunoCoordinator

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
_SERVICE_DOWNLOAD = "download_library"


def _is_empty_suno_library(data: SunoData) -> bool:
    return not data.clips and not data.liked_clips and not data.playlists and not data.playlist_clips


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
    def last_download(self) -> str | None:
        return self._downloaded_library.last_download

    @property
    def last_result(self) -> str:
        return self._downloaded_library.last_result

    @property
    def total_files(self) -> int:
        return self._downloaded_library.total_files

    @property
    def pending(self) -> int:
        return self._downloaded_library.pending

    @property
    def errors(self) -> int:
        return self._downloaded_library.errors

    @property
    def is_running(self) -> bool:
        return self._downloaded_library.running

    @property
    def library_size_mb(self) -> float:
        return self._downloaded_library.library_size_mb

    @property
    def source_breakdown(self) -> dict[str, int]:
        return self._downloaded_library.source_breakdown

    @property
    def status(self) -> DownloadedLibraryStatus:
        return self._downloaded_library.status

    def get_downloaded_path(self, clip_id: str, meta_hash: str = "") -> Path | None:
        """Return absolute path to a downloaded file if it exists and is fresh."""
        return self._downloaded_library.get_downloaded_path(clip_id, meta_hash)

    async def async_download(
        self,
        options: dict[str, Any],
        client: Any,
        force: bool = False,
        coordinator_data: SunoData | None = None,
        initial: bool = False,
    ) -> None:
        """Run a Downloaded Library reconciliation cycle."""
        if not (options.get(CONF_DOWNLOAD_PATH) or self._downloaded_library.download_path):
            _LOGGER.warning("No download_path configured")
            return
        if self.is_running:
            _LOGGER.debug("Downloaded Library reconciliation already running, skipping")
            return
        self._ensure_audio_adapter(client)
        suno_library = await self._library_for_run(coordinator_data, force=force)
        allow_destructive = self._allow_destructive_reconciliation(suno_library)
        desired_plan = self._downloaded_library.build_desired(options, suno_library)
        await self._downloaded_library.async_reconcile(
            options,
            suno_library,
            force=force,
            initial=initial,
            allow_destructive=allow_destructive,
            desired_plan=desired_plan,
        )

    def _ensure_audio_adapter(self, client: Any) -> None:
        if self._client is client and self._downloaded_library.audio is not None:
            return
        self._client = client
        self._downloaded_library.audio = HomeAssistantDownloadedLibraryAudio(self.hass, client)

    async def _library_for_run(
        self,
        coordinator_data: SunoData | None,
        *,
        force: bool,
    ) -> SunoData:
        if force and self._coordinator is not None:
            try:
                data = await self._coordinator._async_fetch_remote_data()
            except Exception:
                _LOGGER.warning("Library Refresh before forced download failed", exc_info=True)
            else:
                self._coordinator.async_set_updated_data(data)
                return data
        if coordinator_data is not None:
            return coordinator_data
        if self._coordinator is not None and self._coordinator.data is not None:
            return self._coordinator.data
        return SunoData()

    def _allow_destructive_reconciliation(self, data: SunoData) -> bool:
        if self._coordinator is None or not _is_empty_suno_library(data):
            return True
        refresh_task: asyncio.Task[None] | None = getattr(self._coordinator, "_refresh_task", None)
        return not (self._coordinator.data_version <= 1 and refresh_task is not None and not refresh_task.done())

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
