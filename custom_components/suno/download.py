"""Home Assistant adapter for the Suno Downloaded Library."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    CONF_ALL_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_DOWNLOAD_VIDEOS,
    CONF_MY_SONGS_COUNT,
    CONF_MY_SONGS_DAYS,
    CONF_MY_SONGS_MINIMUM,
    CONF_PLAYLISTS,
    CONF_SHOW_LIKED,
    CONF_SHOW_MY_SONGS,
    CONF_SHOW_PLAYLISTS,
    DEFAULT_ALL_PLAYLISTS,
    DEFAULT_MY_SONGS_COUNT,
    DEFAULT_MY_SONGS_DAYS,
    DEFAULT_MY_SONGS_MINIMUM,
    DEFAULT_SHOW_LIKED,
    DEFAULT_SHOW_MY_SONGS,
    DEFAULT_SHOW_PLAYLISTS,
    DOMAIN,
    DOWNLOAD_MODE_CACHE,
)
from .downloaded_library import (
    DesiredDownloadPlan,
    DownloadedLibrary,
    DownloadedLibraryStatus,
    DownloadItem,
    HomeAssistantDownloadedLibraryAudio,
    HomeAssistantDownloadedLibraryStorage,
    RetagResult,
    SunoCacheDownloadedLibraryAdapter,
    _add_clip,
    _album_for_clip,
    _build_download_summary,
    _clip_path,
    _get_source_mode,
    _safe_name,
    _source_preserves_files,
    _update_cover_art,
    _video_clip_path,
    _write_file,
    _write_m3u8_playlists,
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
        """Create, initialise, and wire up the Home Assistant adapter."""
        mgr = cls(hass, f"suno_sync_{entry.entry_id}")
        mgr._coordinator = coordinator
        mgr._client = client
        mgr._downloaded_library.audio = HomeAssistantDownloadedLibraryAudio(hass, client)
        mgr._cache = cache if cache is not None else coordinator.cache
        mgr._download_path = entry.options.get(CONF_DOWNLOAD_PATH, "")
        mgr._download_videos = entry.options.get(CONF_DOWNLOAD_VIDEOS, True)
        await mgr.async_init()
        if download_path := entry.options.get(CONF_DOWNLOAD_PATH, ""):
            await mgr.cleanup_tmp_files(download_path)

        def _on_coordinator_update() -> None:
            if not mgr.is_running and not mgr._updating_sensors:
                hass.async_create_task(
                    mgr.async_download(dict(entry.options), client, coordinator_data=coordinator.data),
                    f"suno_download_refresh_{entry.entry_id}",
                )

        entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))

        async def _handle_download_service(call: ServiceCall) -> None:
            await mgr.async_download(dict(entry.options), client, force=call.data.get("force", False))

        if not hass.services.has_service(DOMAIN, _SERVICE_DOWNLOAD):
            hass.services.async_register(DOMAIN, _SERVICE_DOWNLOAD, _handle_download_service)

            def _maybe_remove_service() -> None:
                remaining = [
                    existing_entry
                    for existing_entry in hass.config_entries.async_entries(DOMAIN)
                    if existing_entry.entry_id != entry.entry_id
                ]
                if not remaining:
                    hass.services.async_remove(DOMAIN, _SERVICE_DOWNLOAD)

            entry.async_on_unload(_maybe_remove_service)

        async def _on_ha_started(_event: Any) -> None:
            _LOGGER.info("Home Assistant started - beginning initial sync")
            await mgr.async_download(dict(entry.options), client, initial=True)

        from homeassistant.helpers.start import async_at_started  # noqa: PLC0415

        async_at_started(hass, _on_ha_started)
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
        if not (options.get(CONF_DOWNLOAD_PATH) or self._download_path):
            _LOGGER.warning("No download_path configured")
            return
        if self.is_running:
            _LOGGER.debug("Downloaded Library reconciliation already running, skipping")
            return
        self._ensure_audio_adapter(client)
        suno_library = await self._library_for_run(options, client, coordinator_data, force=force)
        allow_destructive = self._allow_destructive_reconciliation(suno_library)
        desired_tuple = await self._build_desired(options, client, suno_library)
        desired_plan = DesiredDownloadPlan.from_legacy_tuple(desired_tuple)
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
        options: dict[str, Any],
        client: Any,
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
        return await self._legacy_fetch_suno_library(options, client)

    def _allow_destructive_reconciliation(self, data: SunoData) -> bool:
        if self._coordinator is None or not _is_empty_suno_library(data):
            return True
        refresh_task: asyncio.Task[None] | None = getattr(self._coordinator, "_refresh_task", None)
        return not (self._coordinator.data_version <= 1 and refresh_task is not None and not refresh_task.done())

    async def _legacy_fetch_suno_library(self, options: dict[str, Any], client: Any) -> SunoData:
        """Build a Suno Library from legacy private helper callers.

        Production runs use the coordinator's current Suno Library. This fallback
        preserves old private test entry points while the real download seam no
        longer fetches Suno transport directly.
        """
        stale_sections: set[str] = set()
        clips = []
        liked_clips = []
        playlists = []
        playlist_clips = {}

        if (
            options.get(CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED)
            and _get_source_mode("liked", options) != DOWNLOAD_MODE_CACHE
        ):
            try:
                liked_clips = await client.get_liked_songs()
            except Exception:
                _LOGGER.warning("Failed to fetch liked songs for legacy sync planning")
                stale_sections.add("liked_clips")

        sync_all = options.get(CONF_ALL_PLAYLISTS, DEFAULT_ALL_PLAYLISTS)
        selected_ids = options.get(CONF_PLAYLISTS, []) or []
        if (
            options.get(CONF_SHOW_PLAYLISTS, DEFAULT_SHOW_PLAYLISTS)
            and _get_source_mode("playlist:", options) != DOWNLOAD_MODE_CACHE
            and (sync_all or selected_ids)
        ):
            try:
                playlists = await client.get_playlists()
            except Exception:
                _LOGGER.warning("Failed to fetch playlists for legacy sync planning")
                stale_sections.update(("playlists", "playlist_clips"))
            else:
                for playlist in playlists:
                    if not sync_all and playlist.id not in selected_ids:
                        continue
                    try:
                        playlist_clips[playlist.id] = await client.get_playlist_clips(playlist.id)
                    except Exception:
                        _LOGGER.warning("Failed to fetch clips for playlist %s", playlist.name)
                        stale_sections.add(f"playlist_clips:{playlist.id}")

        if (
            options.get(CONF_SHOW_MY_SONGS, DEFAULT_SHOW_MY_SONGS)
            and _get_source_mode("my_songs", options) != DOWNLOAD_MODE_CACHE
        ):
            my_songs_count = options.get(CONF_MY_SONGS_COUNT, DEFAULT_MY_SONGS_COUNT)
            my_songs_days = options.get(CONF_MY_SONGS_DAYS, DEFAULT_MY_SONGS_DAYS)
            minimum = int(options.get(CONF_MY_SONGS_MINIMUM, DEFAULT_MY_SONGS_MINIMUM))
            if my_songs_count or my_songs_days or minimum:
                try:
                    clips = await client.get_all_songs()
                except Exception:
                    _LOGGER.warning("Failed to fetch my songs for legacy sync planning")
                    stale_sections.add("clips")

        return SunoData(
            clips=clips,
            liked_clips=liked_clips,
            playlists=playlists,
            playlist_clips=playlist_clips,
            stale_sections=tuple(sorted(stale_sections)),
        )

    async def _build_desired(
        self,
        options: dict[str, Any],
        client: Any,
        coordinator_data: SunoData | None = None,
    ) -> tuple[list[DownloadItem], set[str], dict[str, str], dict[str, list[str]]]:
        data = (
            coordinator_data if coordinator_data is not None else await self._legacy_fetch_suno_library(options, client)
        )
        return self._downloaded_library.build_desired(options, data).as_legacy_tuple()

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

    async def _reconcile_disk(self, base: Path, clips_state: dict[str, Any]) -> int:
        return await self._downloaded_library._reconcile_disk(base, clips_state)

    async def _reconcile_manifest(self, base: Path, clips_state: dict[str, dict[str, Any]]) -> int:
        return await self._downloaded_library._reconcile_manifest(base, clips_state)

    async def _retag_clip(self, item: DownloadItem, target: Path) -> RetagResult:
        return await self._downloaded_library._retag_clip(item, target)

    async def _download_clip(self, _client: Any, item: DownloadItem, base: Path, rel_path: str) -> int | None:
        return await self._downloaded_library._download_clip(item, base, rel_path)

    async def _save_state(self, base: Path) -> None:
        await self._downloaded_library._save_state(base)

    @property
    def _store(self) -> Any:
        return self._storage.store

    @property
    def _state(self) -> dict[str, Any]:
        return self._downloaded_library.state

    @_state.setter
    def _state(self, value: dict[str, Any]) -> None:
        self._downloaded_library.state = value

    @property
    def _cache(self) -> Any:
        return self._raw_cache

    @_cache.setter
    def _cache(self, value: Any) -> None:
        self._raw_cache = value
        self._downloaded_library.cache = SunoCacheDownloadedLibraryAdapter(value) if value is not None else None

    @property
    def _download_path(self) -> str:
        return self._downloaded_library.download_path

    @_download_path.setter
    def _download_path(self, value: str) -> None:
        self._downloaded_library.download_path = value

    @property
    def _download_videos(self) -> bool:
        return self._downloaded_library.download_videos

    @_download_videos.setter
    def _download_videos(self, value: bool) -> None:
        self._downloaded_library.download_videos = value

    @property
    def _running(self) -> bool:
        return self._downloaded_library.running

    @_running.setter
    def _running(self, value: bool) -> None:
        self._downloaded_library.running = value

    @property
    def _errors(self) -> int:
        return self._downloaded_library.errors

    @_errors.setter
    def _errors(self, value: int) -> None:
        self._downloaded_library.errors = value

    @property
    def _pending(self) -> int:
        return self._downloaded_library.pending

    @_pending.setter
    def _pending(self, value: int) -> None:
        self._downloaded_library.pending = value

    @property
    def _last_result(self) -> str:
        return self._downloaded_library.last_result

    @_last_result.setter
    def _last_result(self, value: str) -> None:
        self._downloaded_library.last_result = value

    @property
    def _clip_index(self) -> dict[str, Any]:
        return self._downloaded_library.clip_index

    @_clip_index.setter
    def _clip_index(self, value: dict[str, Any]) -> None:
        self._downloaded_library.clip_index = value


__all__ = [
    "DownloadItem",
    "RetagResult",
    "STORE_VERSION",
    "SunoDownloadManager",
    "_add_clip",
    "_album_for_clip",
    "_build_download_summary",
    "_clip_path",
    "_get_source_mode",
    "_safe_name",
    "_source_preserves_files",
    "_video_clip_path",
    "_update_cover_art",
    "_write_file",
    "_write_m3u8_playlists",
]
