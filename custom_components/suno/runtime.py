"""Home Assistant Runtime for the Suno integration."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.start import async_at_started

from .api import SunoClient
from .audio import download_and_transcode_to_flac
from .auth import ClerkAuth
from .cache import SunoCache
from .const import (
    CONF_CACHE_MAX_SIZE,
    CONF_COOKIE,
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_MY_SONGS,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_QUALITY_LIKED,
    CONF_QUALITY_PLAYLISTS,
    CONF_SHOW_LIKED,
    CONF_SHOW_MY_SONGS,
    CONF_SHOW_PLAYLISTS,
    DATA_VIEW_REGISTERED,
    DEFAULT_CACHE_MAX_SIZE,
    DEFAULT_DOWNLOAD_MODE,
    DEFAULT_DOWNLOAD_MODE_MY_SONGS,
    DEFAULT_SHOW_LIKED,
    DEFAULT_SHOW_MY_SONGS,
    DEFAULT_SHOW_PLAYLISTS,
    DOMAIN,
    DOWNLOAD_MODE_CACHE,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from .coordinator import SunoCoordinator, SunoData
from .download import _SERVICE_DOWNLOAD, SunoDownloadManager
from .downloaded_library import DownloadedLibraryStatus, HomeAssistantDownloadedLibraryAudio
from .exceptions import SunoAuthError, SunoConnectionError
from .models import SunoClip, SunoUser, TrackMetadata
from .rate_limit import SunoRateLimiter

_LOGGER = logging.getLogger(__name__)


_DOWNLOAD_SECTIONS = (
    (CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED, CONF_DOWNLOAD_MODE_LIKED, DEFAULT_DOWNLOAD_MODE),
    (CONF_SHOW_PLAYLISTS, DEFAULT_SHOW_PLAYLISTS, CONF_DOWNLOAD_MODE_PLAYLISTS, DEFAULT_DOWNLOAD_MODE),
    (CONF_SHOW_MY_SONGS, DEFAULT_SHOW_MY_SONGS, CONF_DOWNLOAD_MODE_MY_SONGS, DEFAULT_DOWNLOAD_MODE_MY_SONGS),
)
_PREVIOUS_OPTIONS = "previous_options"


def any_section_downloads(options: Mapping[str, Any]) -> bool:
    """Return True if any enabled section uses Mirror or Archive mode."""
    for show_key, show_default, mode_key, mode_default in _DOWNLOAD_SECTIONS:
        if options.get(show_key, show_default) and options.get(mode_key, mode_default) != DOWNLOAD_MODE_CACHE:
            return True
    return False


def downloaded_library_enabled(options: Mapping[str, Any]) -> bool:
    """Return True when the Downloaded Library should be managed."""
    return bool(options.get(CONF_DOWNLOAD_PATH)) and any_section_downloads(options)


def _is_empty_suno_library(data: SunoData) -> bool:
    return not data.clips and not data.liked_clips and not data.playlists and not data.playlist_clips


class HomeAssistantRuntime:
    """Per-entry loaded Home Assistant state for the Suno integration."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry[Any],
        coordinator: SunoCoordinator,
        client: SunoClient,
        cache: SunoCache,
        rate_limiter: SunoRateLimiter,
        *,
        download_manager: SunoDownloadManager | None = None,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._client = client
        self._cache = cache
        self._download_manager = download_manager
        self._rate_limiter = rate_limiter
        self._loaded_options = dict(entry.options)

    @classmethod
    async def async_setup(cls, hass: HomeAssistant, entry: ConfigEntry[Any]) -> HomeAssistantRuntime:
        """Create and initialise the Home Assistant Runtime for one entry."""
        rate_limiter = _shared_rate_limiter(hass)
        session = async_get_clientsession(hass)
        auth = ClerkAuth(session, entry.data[CONF_COOKIE])
        client = SunoClient(auth, rate_limiter=rate_limiter)

        coordinator = SunoCoordinator(hass, client, entry)
        stored_data = await _load_stored_library(coordinator)

        auth_ok = False
        try:
            await auth.authenticate()
            auth_ok = True
        except SunoConnectionError:
            message = "Cannot reach Suno, using stored library" if stored_data else "Cannot reach Suno, starting empty"
            _LOGGER.warning(message)
        except SunoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Exception as err:
            if not stored_data:
                raise ConfigEntryNotReady(f"Could not connect: {err}") from err
            _LOGGER.warning("Cannot reach Suno, using stored library")

        try:
            await coordinator.async_config_entry_first_refresh()
        except ConfigEntryNotReady:
            if not stored_data and auth_ok:
                raise
            message = (
                "First refresh failed, using stored library" if stored_data else "First refresh failed, starting empty"
            )
            _LOGGER.warning(message)
            coordinator.async_set_updated_data(stored_data or SunoData())

        cache = SunoCache(hass, entry.options.get(CONF_CACHE_MAX_SIZE, DEFAULT_CACHE_MAX_SIZE))
        await cache.async_init()

        runtime = cls(hass, entry, coordinator, client, cache, rate_limiter)
        entry.runtime_data = runtime
        runtime._register_proxy_view()
        await runtime._async_setup_downloaded_library()
        return runtime

    @property
    def rate_limiter(self) -> SunoRateLimiter:
        """Return the shared Suno rate limiter."""
        return self._rate_limiter

    @property
    def suno_library(self) -> SunoData:
        """Return the current Suno Library."""
        return self.coordinator.data

    @property
    def data(self) -> SunoData:
        """Compatibility access to the coordinator's current Suno Library."""
        return self.coordinator.data

    @data.setter
    def data(self, value: SunoData) -> None:
        self.coordinator.data = value

    @property
    def user(self) -> SunoUser:
        """Return the current Suno Identity."""
        return self.coordinator.user

    @property
    def data_version(self) -> int:
        """Return the current Suno Library data version."""
        return self.coordinator.data_version

    @property
    def _data_version(self) -> int:
        return self.coordinator._data_version

    @_data_version.setter
    def _data_version(self, version: int) -> None:
        self.coordinator._data_version = version

    @property
    def download_status(self) -> DownloadedLibraryStatus:
        """Return the current Downloaded Library status."""
        if self._download_manager is None:
            return DownloadedLibraryStatus()
        return self._download_manager.status

    @property
    def download_path(self) -> str:
        """Return the configured Downloaded Library path."""
        return str(self.entry.options.get(CONF_DOWNLOAD_PATH, ""))

    @property
    def downloads_enabled(self) -> bool:
        """Return True when the Downloaded Library is active for this entry."""
        return self._download_manager is not None

    @property
    def cache_file_count(self) -> int:
        """Return the number of audio cache files tracked by the runtime."""
        return self._cache.file_count

    async def async_cache_size_mb(self) -> float:
        """Return the audio cache size in MB."""
        return await self._cache.async_size_mb()

    async def async_clear_cache(self) -> None:
        """Clear the audio cache."""
        await self._cache.async_clear()

    async def async_force_download(self) -> None:
        """Force a Downloaded Library reconciliation if it is enabled."""
        if self._download_manager is None:
            return
        await self._run_reconcile(force=True)

    async def _run_reconcile(
        self,
        *,
        force: bool = False,
        coordinator_data: SunoData | None = None,
        initial: bool = False,
    ) -> None:
        """Drive a Downloaded Library reconciliation cycle."""
        if self._download_manager is None:
            return
        engine = self._download_manager._downloaded_library  # noqa: SLF001
        options = dict(self.entry.options)
        if not (options.get(CONF_DOWNLOAD_PATH) or engine.download_path):
            _LOGGER.warning("No download_path configured")
            return
        if engine.running:
            _LOGGER.debug("Downloaded Library reconciliation already running, skipping")
            return
        self._ensure_audio_adapter()
        suno_library = await self._library_for_run(coordinator_data, force=force)
        allow_destructive = self._allow_destructive_reconciliation(suno_library)
        desired_plan = engine.build_desired(options, suno_library)
        await engine.async_reconcile(
            options,
            suno_library,
            force=force,
            initial=initial,
            allow_destructive=allow_destructive,
            desired_plan=desired_plan,
        )

    def _ensure_audio_adapter(self) -> None:
        """Bind the engine's audio adapter to the current client if it changed."""
        if self._download_manager is None:
            return
        engine = self._download_manager._downloaded_library  # noqa: SLF001
        if engine.audio is None:
            engine.audio = HomeAssistantDownloadedLibraryAudio(self.hass, self._client)

    async def _library_for_run(
        self,
        coordinator_data: SunoData | None,
        *,
        force: bool,
    ) -> SunoData:
        coordinator = self.coordinator
        if force:
            try:
                data = await coordinator._async_fetch_remote_data()  # noqa: SLF001
            except Exception:
                _LOGGER.warning("Library Refresh before forced download failed", exc_info=True)
            else:
                coordinator.async_set_updated_data(data)
                return data
        if coordinator_data is not None:
            return coordinator_data
        return coordinator.data

    def _allow_destructive_reconciliation(self, data: SunoData) -> bool:
        if not _is_empty_suno_library(data):
            return True
        coordinator = self.coordinator
        refresh_task = getattr(coordinator, "_refresh_task", None)
        return not (coordinator.data_version <= 1 and refresh_task is not None and not refresh_task.done())

    def get_downloaded_path(self, clip_id: str, meta_hash: str = "") -> Path | None:
        """Return a fresh downloaded file path for a clip if one exists."""
        if self._download_manager is None:
            return None
        return self._download_manager.get_downloaded_path(clip_id, meta_hash)

    async def async_get_cached_audio(self, clip_id: str, fmt: str, meta_hash: str = "") -> Path | None:
        """Return a cached audio file path if it exists and is fresh."""
        return await self._cache.async_get(clip_id, fmt, meta_hash=meta_hash)

    async def async_put_cached_audio(self, clip_id: str, fmt: str, data: bytes, meta_hash: str = "") -> Path | None:
        """Write rendered audio to the audio cache."""
        return await self._cache.async_put(clip_id, fmt, data, meta_hash)

    async def async_render_hq_audio(
        self,
        clip_id: str,
        metadata: TrackMetadata,
        *,
        duration: float = 0.0,
        image_url: str | None = None,
        session: Any | None = None,
        ffmpeg_binary: str | None = None,
    ) -> bytes | None:
        """Render a clip as FLAC using the runtime-owned Suno transport."""
        return await download_and_transcode_to_flac(
            self._client,
            session or async_get_clientsession(self.hass),
            ffmpeg_binary or get_ffmpeg_manager(self.hass).binary,
            clip_id,
            metadata,
            duration=duration,
            image_url=image_url,
        )

    def find_clip(self, clip_id: str) -> SunoClip | None:
        """Find a clip in the current Suno Library."""
        for clip in self.iter_clips():
            if clip.id == clip_id:
                return clip
        return None

    def iter_clips(self) -> Iterable[SunoClip]:
        """Iterate clips visible to playback and browsing."""
        seen: set[str] = set()
        for clip in self.suno_library.clips:
            seen.add(clip.id)
            yield clip
        for clip in self.suno_library.liked_clips:
            if clip.id not in seen:
                yield clip

    def quality_for_clip(self, clip: SunoClip) -> str:
        """Determine playback quality from current source membership."""
        opts = self.entry.options
        data = self.suno_library
        if opts.get(CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED):
            if opts.get(CONF_QUALITY_LIKED, QUALITY_HIGH) == QUALITY_HIGH:
                if clip.is_liked or any(liked.id == clip.id for liked in data.liked_clips):
                    return QUALITY_HIGH
        if opts.get(CONF_SHOW_PLAYLISTS, DEFAULT_SHOW_PLAYLISTS):
            if opts.get(CONF_QUALITY_PLAYLISTS, QUALITY_HIGH) == QUALITY_HIGH:
                for playlist_clips in data.playlist_clips.values():
                    if any(playlist_clip.id == clip.id for playlist_clip in playlist_clips):
                        return QUALITY_HIGH
        return QUALITY_STANDARD

    def diagnostics(self) -> dict[str, Any]:
        """Return a diagnostics read model for this runtime."""
        data = self.suno_library
        return {
            "user": {
                "id": self.user.id,
                "display_name": self.user.display_name,
            },
            "library": {
                "total_clips": len(data.clips),
                "liked_clips": len(data.liked_clips),
                "playlists": len(data.playlists),
            },
            "credits": {
                "credits_left": data.credits.credits_left if data.credits else None,
                "monthly_limit": data.credits.monthly_limit if data.credits else None,
                "monthly_usage": data.credits.monthly_usage if data.credits else None,
            },
            "rate_limiter": {
                "is_throttled": self.rate_limiter.is_throttled,
                "total_429_count": self.rate_limiter.total_429_count,
                "seconds_remaining": self.rate_limiter.seconds_remaining,
            },
        }

    async def async_unload(self) -> None:
        """Flush runtime-owned resources and remember options for reload cleanup."""
        _remember_previous_options(self.hass, self.entry.entry_id, self._loaded_options)
        try:
            await self._cache.async_flush()
        except Exception:
            _LOGGER.debug("Cache flush on unload failed")

    async def _async_setup_downloaded_library(self) -> None:
        previous_options = _pop_previous_options(self.hass, self.entry.entry_id)
        if downloaded_library_enabled(self.entry.options):
            self._download_manager = await SunoDownloadManager.async_setup(
                self.hass,
                self.entry,
                self.coordinator,
                self._client,
                self._cache,
            )
            self._wire_downloaded_library_lifecycle(self._download_manager)
        elif (
            previous_options is not None
            and downloaded_library_enabled(previous_options)
            and not any_section_downloads(self.entry.options)
        ):
            await self._async_cleanup_disabled_downloads(self.entry.options, previous_options)
        elif self.entry.options.get(CONF_DOWNLOAD_PATH):
            await self._async_cleanup_disabled_downloads(self.entry.options, None)

    def _wire_downloaded_library_lifecycle(self, manager: SunoDownloadManager) -> None:
        """Wire Home Assistant lifecycle hooks for an active download manager.

        Lifted from ``SunoDownloadManager.async_setup`` during Phase 1.6 of the
        download.py wrapper collapse: registration with the coordinator,
        Home Assistant service registration, and the ``async_at_started``
        initial-sync callback all live with the runtime that owns the entry,
        not with the manager that does the work.
        """
        entry = self.entry
        hass = self.hass
        coordinator = self.coordinator

        def _on_coordinator_update() -> None:
            if not manager.is_running and not manager._updating_sensors:  # noqa: SLF001
                hass.async_create_task(
                    self._run_reconcile(coordinator_data=coordinator.data),
                    f"suno_download_refresh_{entry.entry_id}",
                )

        entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))

        async def _handle_download_service(call: ServiceCall) -> None:
            await self._run_reconcile(force=call.data.get("force", False))

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
            await self._run_reconcile(initial=True)

        async_at_started(hass, _on_ha_started)

    async def _async_cleanup_disabled_downloads(
        self,
        options: Mapping[str, Any],
        previous_options: Mapping[str, Any] | None,
    ) -> None:
        manager = self._download_manager or SunoDownloadManager(self.hass, f"suno_sync_{self.entry.entry_id}")
        if manager is not self._download_manager:
            await manager.async_init()
        await manager.async_cleanup_disabled_downloads(
            dict(options),
            dict(previous_options) if previous_options else None,
        )

    def _register_proxy_view(self) -> None:
        if self.hass.data.get(DATA_VIEW_REGISTERED):
            return
        from .proxy import SunoMediaProxyView  # noqa: PLC0415

        self.hass.http.register_view(SunoMediaProxyView(self.hass))
        self.hass.data[DATA_VIEW_REGISTERED] = True

    @property
    def cache(self) -> SunoCache:
        """Compatibility access for older tests and private callers."""
        return self._cache

    @cache.setter
    def cache(self, value: SunoCache) -> None:
        self._cache = value

    @property
    def download_manager(self) -> SunoDownloadManager | None:
        """Compatibility access for older tests and private callers."""
        return self._download_manager

    @download_manager.setter
    def download_manager(self, value: SunoDownloadManager | None) -> None:
        self._download_manager = value

    @property
    def client(self) -> SunoClient:
        """Compatibility access for older tests and private callers."""
        return self._client

    @client.setter
    def client(self, value: SunoClient) -> None:
        self._client = value

    def __getattr__(self, name: str) -> Any:
        """Delegate legacy coordinator-shaped access to the coordinator."""
        return getattr(self.coordinator, name)


def runtime_from_entry(entry: ConfigEntry[Any]) -> HomeAssistantRuntime | None:
    """Return the Home Assistant Runtime for a loaded entry."""
    runtime = getattr(entry, "runtime_data", None)
    return runtime if isinstance(runtime, HomeAssistantRuntime) else None


def iter_entry_runtimes(hass: HomeAssistant) -> Iterable[tuple[ConfigEntry[Any], HomeAssistantRuntime]]:
    """Iterate loaded Suno entries and their runtimes."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if (runtime := runtime_from_entry(entry)) is not None:
            yield entry, runtime


async def async_remove_runtime_entry(hass: HomeAssistant, entry: ConfigEntry[Any]) -> None:
    """Clean up per-entry and shared runtime state on removal."""
    remaining = [
        existing for existing in hass.config_entries.async_entries(DOMAIN) if existing.entry_id != entry.entry_id
    ]

    storage_dir = Path(hass.config.path(".storage"))
    if storage_dir.is_dir():
        for store_file in await hass.async_add_executor_job(lambda: list(storage_dir.glob(f"suno_*{entry.entry_id}*"))):
            try:
                await hass.async_add_executor_job(store_file.unlink)
                _LOGGER.debug("Removed store file: %s", store_file.name)
            except OSError:
                _LOGGER.warning("Could not remove store file: %s", store_file)

    if not remaining:
        cache_dir = Path(hass.config.cache_path("suno"))
        if cache_dir.is_dir():
            await hass.async_add_executor_job(shutil.rmtree, cache_dir, True)
            _LOGGER.debug("Removed cache directory: %s", cache_dir)

        if storage_dir.is_dir():
            for store_file in await hass.async_add_executor_job(lambda: list(storage_dir.glob("suno_cache*"))):
                try:
                    await hass.async_add_executor_job(store_file.unlink)
                except OSError:
                    pass

        hass.data.pop(DOMAIN, None)


async def _load_stored_library(coordinator: SunoCoordinator) -> SunoData | None:
    try:
        return await coordinator.async_load_stored_data()
    except Exception:
        _LOGGER.warning("Could not load stored Suno library", exc_info=True)
        return None


def _shared_rate_limiter(hass: HomeAssistant) -> SunoRateLimiter:
    domain_data = hass.data.setdefault(DOMAIN, {})
    rate_limiter = domain_data.get("rate_limiter")
    if not isinstance(rate_limiter, SunoRateLimiter):
        rate_limiter = SunoRateLimiter()
        domain_data["rate_limiter"] = rate_limiter
    return rate_limiter


def _remember_previous_options(hass: HomeAssistant, entry_id: str, options: Mapping[str, Any]) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    previous_by_entry = domain_data.setdefault(_PREVIOUS_OPTIONS, {})
    if isinstance(previous_by_entry, dict):
        previous_by_entry[entry_id] = dict(options)


def _pop_previous_options(hass: HomeAssistant, entry_id: str) -> dict[str, Any] | None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    previous_by_entry = domain_data.get(_PREVIOUS_OPTIONS)
    if not isinstance(previous_by_entry, dict):
        return None
    previous = previous_by_entry.pop(entry_id, None)
    return dict(previous) if isinstance(previous, Mapping) else None


__all__ = [
    "HomeAssistantRuntime",
    "any_section_downloads",
    "async_remove_runtime_entry",
    "downloaded_library_enabled",
    "iter_entry_runtimes",
    "runtime_from_entry",
]
