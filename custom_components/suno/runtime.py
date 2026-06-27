"""Home Assistant Runtime for the Suno integration."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path, PurePath
from typing import Any

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.start import async_at_started

from .api import SunoClient
from .audio_stream import download_and_transcode_to_flac
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
    VIDEO_ART_BOTH,
    VIDEO_ART_CONVERT,
    VIDEO_ART_DOWNLOAD,
)
from .coordinator import SunoCoordinator
from .downloaded_library import (
    DownloadedLibrary,
    DownloadedLibraryStatus,
    HomeAssistantDownloadedLibraryAudio,
    HomeAssistantDownloadedLibraryStorage,
    SunoCacheDownloadedLibraryAdapter,
)
from .downloaded_library.video_art import VideoArtSettings, probe_libwebp_anim, resolve_video_art_mode
from .exceptions import SunoAuthError, SunoConnectionError
from .models import SunoClip, SunoData, SunoUser, TrackMetadata
from .rate_limit import SunoRateLimiter

_LOGGER = logging.getLogger(__name__)

_SERVICE_DOWNLOAD = "download_library"
_SERVICE_ATTR_ENTRY_ID = "config_entry_id"
_SERVICE_ATTR_FORCE = "force"

# Shared global state stored under ``hass.data[DOMAIN]``.
_CONCURRENCY_GATE = "concurrency_gate"
_GLOBAL_MAX_CONCURRENT = 3

_DOWNLOAD_PATH_CONFLICT_ISSUE = "download_path_conflict"
_WRONG_ACCOUNT_ISSUE = "wrong_account"

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


def _path_identity(path: Path) -> tuple[int, int] | None:
    """Return a ``(st_dev, st_ino)`` identity for an existing path, else None."""
    try:
        stat_result = path.stat()
    except OSError:
        return None
    return (stat_result.st_dev, stat_result.st_ino)


def paths_overlap(first: str, second: str) -> bool:
    """Return True when two download paths are equal or nested either way.

    Overlapping download paths let one account's mirror reconciliation
    delete another account's files, so both the config flow and the runtime
    setup invariant reject them. Paths are resolved (following symlinks and
    normalising ``..`` and trailing slashes); when both exist we compare by
    device/inode so symlinked or case-insensitive duplicates are caught,
    otherwise we fall back to a case-normalised comparison.
    """
    if not first or not second:
        return False
    first_resolved = Path(first).expanduser().resolve()
    second_resolved = Path(second).expanduser().resolve()
    first_id = _path_identity(first_resolved)
    second_id = _path_identity(second_resolved)
    if first_id is not None and first_id == second_id:
        return True
    first_norm = PurePath(os.path.normcase(str(first_resolved)))
    second_norm = PurePath(os.path.normcase(str(second_resolved)))
    return first_norm == second_norm or first_norm.is_relative_to(second_norm) or second_norm.is_relative_to(first_norm)


def _conflicting_entry(hass: HomeAssistant, entry: ConfigEntry[Any]) -> ConfigEntry[Any] | None:
    """Return an overlapping Suno entry this one must defer to.

    An entry refuses to load if another overlapping (equal/parent/child) entry
    is already loaded, or — when neither is loaded yet, e.g. a concurrent
    Home Assistant restart — if the other sorts earlier by ``entry_id``. This
    keeps exactly one of an overlapping set loadable (instead of taking them
    all down) and never lets two engines reconcile the same tree.
    """
    my_path = entry.options.get(CONF_DOWNLOAD_PATH)
    if not my_path:
        return None
    for other in hass.config_entries.async_entries(DOMAIN):
        if other.entry_id == entry.entry_id:
            continue
        other_path = other.options.get(CONF_DOWNLOAD_PATH)
        if not (other_path and paths_overlap(str(my_path), str(other_path))):
            continue
        if other.state is ConfigEntryState.LOADED or other.entry_id < entry.entry_id:
            return other
    return None


def _download_path_conflict_issue_id(entry_id: str) -> str:
    return f"{_DOWNLOAD_PATH_CONFLICT_ISSUE}_{entry_id}"


def _wrong_account_issue_id(entry_id: str) -> str:
    return f"{_WRONG_ACCOUNT_ISSUE}_{entry_id}"


def _assert_no_download_path_conflict(hass: HomeAssistant, entry: ConfigEntry[Any]) -> None:
    """Refuse to start when this entry's download path overlaps another account.

    Two accounts sharing or nesting a download directory mutually delete
    each other's files during mirror reconciliation. The config/options
    flows block this up front, but this invariant also guards entries that
    pre-date the flow guard or were imported. On conflict we surface a
    Repairs issue and raise ``ConfigEntryError``; otherwise we clear any
    stale issue.
    """
    issue_id = _download_path_conflict_issue_id(entry.entry_id)
    other = _conflicting_entry(hass, entry)
    if other is None:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    # A refused entry never reaches the options handshake, so reclaim any
    # options we remembered for a reload to avoid leaking them.
    _pop_previous_options(hass, entry.entry_id)
    my_path = str(entry.options.get(CONF_DOWNLOAD_PATH, ""))
    other_path = str(other.options.get(CONF_DOWNLOAD_PATH, ""))
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="download_path_conflict",
        translation_placeholders={
            "title": entry.title or entry.entry_id,
            "path": my_path,
            "other_title": other.title or other.entry_id,
            "other_path": other_path,
        },
    )
    raise ConfigEntryError(
        translation_domain=DOMAIN,
        translation_key="download_path_conflict",
        translation_placeholders={"other_title": other.title or other.entry_id, "other_path": other_path},
    )


def _enforce_account_identity(hass: HomeAssistant, entry: ConfigEntry[Any], user_id: str | None) -> None:
    """Backfill a missing unique_id and flag a mismatched Suno account.

    The config entry's ``unique_id`` is the Suno ``user_id``. When it is
    missing we backfill it from the authenticated id. When the cookie now
    authenticates as a *different* account we must not silently serve that
    account's library under this entry's identity, so we warn and raise a
    Repairs issue pointing the user at reauth. The authoritative hard block
    lives in the reauth flow (``config_flow``), which is the real
    cookie-change entry point; failing setup outright here is avoided so a
    transient identity hiccup cannot strand an otherwise healthy entry.
    """
    issue_id = _wrong_account_issue_id(entry.entry_id)
    if not user_id or user_id == entry.unique_id:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    if entry.unique_id is None:
        hass.config_entries.async_update_entry(entry, unique_id=user_id)
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    _LOGGER.warning(
        "Suno cookie for entry %s authenticates as account %s but the entry is %s; re-authentication required",
        entry.entry_id,
        user_id,
        entry.unique_id,
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="wrong_account",
        translation_placeholders={"title": entry.title or entry.entry_id},
    )


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
        downloaded_library: DownloadedLibrary | None = None,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._client = client
        self._cache = cache
        self._downloaded_library = downloaded_library
        self._rate_limiter = rate_limiter
        self._loaded_options = dict(entry.options)
        self._updating_sensors = False

    @classmethod
    async def async_setup(cls, hass: HomeAssistant, entry: ConfigEntry[Any]) -> HomeAssistantRuntime:
        """Create and initialise the Home Assistant Runtime for one entry."""
        _assert_no_download_path_conflict(hass, entry)
        rate_limiter = _entry_rate_limiter(hass)
        session = async_get_clientsession(hass)
        auth = ClerkAuth(session, entry.data[CONF_COOKIE])
        client = SunoClient(auth, rate_limiter=rate_limiter)

        coordinator = SunoCoordinator(hass, client, entry)
        stored_data = await _load_stored_library(coordinator)

        auth_ok = False
        user_id: str | None = None
        try:
            user_id = await auth.authenticate()
            auth_ok = True
        except SunoConnectionError:
            message = "Cannot reach Suno, using stored library" if stored_data else "Cannot reach Suno, starting empty"
            _LOGGER.warning(message)
        except SunoAuthError as err:
            _pop_previous_options(hass, entry.entry_id)
            raise ConfigEntryAuthFailed(str(err)) from err
        except Exception as err:
            if not stored_data:
                raise ConfigEntryNotReady(f"Could not connect: {err}") from err
            _LOGGER.warning("Cannot reach Suno, using stored library")

        if auth_ok:
            _enforce_account_identity(hass, entry, user_id)

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

        cache = SunoCache(hass, entry.options.get(CONF_CACHE_MAX_SIZE, DEFAULT_CACHE_MAX_SIZE), entry.entry_id)
        await cache.async_init()

        runtime = cls(hass, entry, coordinator, client, cache, rate_limiter)
        entry.runtime_data = runtime
        await runtime._async_setup_downloaded_library()
        return runtime

    @property
    def rate_limiter(self) -> SunoRateLimiter:
        """Return this account's rate limiter (per-entry throttle state)."""
        return self._rate_limiter

    @property
    def suno_library(self) -> SunoData:
        """Return the current Suno Library."""
        return self.coordinator.data

    @property
    def data(self) -> SunoData:
        """Compatibility access to the coordinator's current Suno Library."""
        return self.coordinator.data

    @property
    def user(self) -> SunoUser:
        """Return the current Suno Identity."""
        return self.coordinator.user

    @property
    def data_version(self) -> int:
        """Return the current Suno Library data version."""
        return self.coordinator.data_version

    @property
    def download_status(self) -> DownloadedLibraryStatus:
        """Return the current Downloaded Library status."""
        if self._downloaded_library is None:
            return DownloadedLibraryStatus()
        return self._downloaded_library.status

    @property
    def download_path(self) -> str:
        """Return the configured Downloaded Library path."""
        return str(self.entry.options.get(CONF_DOWNLOAD_PATH, ""))

    @property
    def downloads_enabled(self) -> bool:
        """Return True when the Downloaded Library is active for this entry."""
        return self._downloaded_library is not None

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
        if self._downloaded_library is None:
            return
        await self._run_reconcile(force=True)

    async def async_run_download(self, *, force: bool = False) -> None:
        """Run a reconciliation for this account (download_library service)."""
        await self._run_reconcile(force=force)

    async def _run_reconcile(
        self,
        *,
        force: bool = False,
        coordinator_data: SunoData | None = None,
        initial: bool = False,
    ) -> None:
        """Drive a Downloaded Library reconciliation cycle."""
        engine = self._downloaded_library
        if engine is None:
            return
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
        engine = self._downloaded_library
        if engine is None:
            return
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
                data = await coordinator.async_fetch_remote()
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
        return not (coordinator.data_version <= 1 and coordinator.pending_initial_refresh)

    def get_downloaded_path(self, clip_id: str, meta_hash: str = "") -> Path | None:
        """Return a fresh downloaded file path for a clip if one exists."""
        if self._downloaded_library is None:
            return None
        return self._downloaded_library.get_downloaded_path(clip_id, meta_hash)

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
        previous_path = (previous_options or {}).get(CONF_DOWNLOAD_PATH)
        current_path = self.entry.options.get(CONF_DOWNLOAD_PATH)
        path_changed = bool(previous_path and current_path and previous_path != current_path)
        if downloaded_library_enabled(self.entry.options):
            self._downloaded_library = await self._build_downloaded_library_engine()
            self._wire_downloaded_library_lifecycle()
            if path_changed:
                await self._async_purge_old_path(str(previous_path))
            return
        if path_changed:
            await self._async_purge_old_path(str(previous_path))
        elif (
            previous_options is not None
            and downloaded_library_enabled(previous_options)
            and not any_section_downloads(self.entry.options)
        ):
            await self._async_cleanup_disabled_downloads(self.entry.options, previous_options)
        elif self.entry.options.get(CONF_DOWNLOAD_PATH):
            await self._async_cleanup_disabled_downloads(self.entry.options, None)

    async def _build_downloaded_library_engine(self) -> DownloadedLibrary:
        """Construct, wire, and load a Downloaded Library engine for this entry."""
        entry = self.entry
        storage = HomeAssistantDownloadedLibraryStorage(self.hass, f"suno_sync_{entry.entry_id}")
        engine = DownloadedLibrary(
            self.hass,
            storage,
            status_callback=self._handle_engine_status_update,
        )
        engine.audio = HomeAssistantDownloadedLibraryAudio(self.hass, self._client)
        engine.cache = SunoCacheDownloadedLibraryAdapter(self._cache)
        engine.download_path = entry.options.get(CONF_DOWNLOAD_PATH, "")
        video_mode = self._resolve_video_art_mode(entry)
        ffmpeg_binary = get_ffmpeg_manager(self.hass).binary
        # Probe libwebp_anim for WebP modes; degrade to download if unavailable
        if video_mode in (VIDEO_ART_CONVERT, VIDEO_ART_BOTH):
            if await probe_libwebp_anim(self.hass, ffmpeg_binary):
                engine.ffmpeg_binary = ffmpeg_binary
            else:
                _LOGGER.warning("ffmpeg lacks libwebp_anim encoder; degrading video art mode to 'download'")
                video_mode = VIDEO_ART_DOWNLOAD
        engine.video_art_mode = video_mode
        engine.video_art_settings = VideoArtSettings.from_options(entry.options)
        await engine.async_load()
        if download_path := entry.options.get(CONF_DOWNLOAD_PATH, ""):
            await engine.cleanup_tmp_files(download_path)
        return engine

    def _handle_engine_status_update(self, _status: DownloadedLibraryStatus) -> None:
        """Push sensor updates via the coordinator without re-triggering sync."""
        coordinator = self.coordinator
        if not coordinator.data:
            return
        self._updating_sensors = True
        try:
            coordinator.async_set_updated_data(coordinator.data)
        finally:
            self._updating_sensors = False

    @staticmethod
    def _resolve_video_art_mode(entry: ConfigEntry[Any]) -> str:
        """Resolve video_art_mode from options, migrating legacy download_videos bool."""
        return resolve_video_art_mode(entry.options)

    def _wire_downloaded_library_lifecycle(self) -> None:
        """Wire Home Assistant lifecycle hooks for the active Downloaded Library."""
        entry = self.entry
        hass = self.hass
        coordinator = self.coordinator

        def _on_coordinator_update() -> None:
            engine = self._downloaded_library
            if engine is None or engine.running or self._updating_sensors:
                return
            # Background task so it is cancelled if the entry unloads mid-run,
            # preventing two engines writing the same path across a reload.
            entry.async_create_background_task(
                hass,
                self._run_reconcile(coordinator_data=coordinator.data),
                f"suno_download_refresh_{entry.entry_id}",
            )

        entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))

        _async_register_download_service(hass)
        entry.async_on_unload(_download_service_remover(hass, entry))

        async def _on_ha_started(_hass: HomeAssistant) -> None:
            _LOGGER.info("Home Assistant started - beginning initial sync")
            await self._run_reconcile(initial=True)

        # Cancel the start listener on unload so a reload during HA startup
        # cannot fire an initial sync against a torn-down runtime.
        entry.async_on_unload(async_at_started(hass, _on_ha_started))

    async def _async_cleanup_disabled_downloads(
        self,
        options: Mapping[str, Any],
        previous_options: Mapping[str, Any] | None,
    ) -> None:
        engine = await self._async_downloaded_library_engine_for_cleanup()
        await engine.async_cleanup_disabled_downloads(
            dict(options),
            dict(previous_options) if previous_options else None,
        )

    async def _async_purge_old_path(self, old_path: str) -> None:
        engine = await self._async_downloaded_library_engine_for_cleanup()
        await engine.async_purge_old_path(old_path)

    async def _async_downloaded_library_engine_for_cleanup(self) -> DownloadedLibrary:
        engine = self._downloaded_library
        if engine is not None:
            return engine
        storage = HomeAssistantDownloadedLibraryStorage(self.hass, f"suno_sync_{self.entry.entry_id}")
        engine = DownloadedLibrary(self.hass, storage)
        await engine.async_load()
        return engine

    @property
    def cache(self) -> SunoCache:
        """Return the audio cache backing the proxy and downloaded library."""
        return self._cache

    @property
    def downloaded_library(self) -> DownloadedLibrary | None:
        """Return the active Downloaded Library engine, or None when disabled."""
        return self._downloaded_library

    @property
    def client(self) -> SunoClient:
        """Return the Suno API client used by this entry."""
        return self._client


def runtime_from_entry(entry: ConfigEntry[Any]) -> HomeAssistantRuntime | None:
    """Return the Home Assistant Runtime for a loaded entry."""
    runtime = getattr(entry, "runtime_data", None)
    return runtime if isinstance(runtime, HomeAssistantRuntime) else None


def iter_entry_runtimes(hass: HomeAssistant) -> Iterable[tuple[ConfigEntry[Any], HomeAssistantRuntime]]:
    """Iterate fully loaded Suno entries and their runtimes.

    Entries that are setting up or tearing down are skipped so the proxy,
    media source, and download service never serve a half-built or
    already-unloaded sibling runtime.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        if (runtime := runtime_from_entry(entry)) is not None:
            yield entry, runtime


def _async_register_download_service(hass: HomeAssistant) -> None:
    """Register the ``download_library`` service once for the whole domain.

    The handler resolves target runtimes at call time (rather than closing
    over the first entry's runtime), so it never points at a torn-down
    runtime and can drive every account, or one targeted account.
    """
    if hass.services.has_service(DOMAIN, _SERVICE_DOWNLOAD):
        return

    async def _handle_download_service(call: ServiceCall) -> None:
        force = bool(call.data.get(_SERVICE_ATTR_FORCE, False))
        target_id = call.data.get(_SERVICE_ATTR_ENTRY_ID)
        targets = [
            (target_entry, runtime)
            for target_entry, runtime in iter_entry_runtimes(hass)
            if runtime.downloads_enabled and (target_id is None or target_entry.entry_id == target_id)
        ]
        if target_id is not None and not targets:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_download_target",
                translation_placeholders={"config_entry_id": str(target_id)},
            )
        for _target_entry, runtime in targets:
            await runtime.async_run_download(force=force)

    hass.services.async_register(DOMAIN, _SERVICE_DOWNLOAD, _handle_download_service)


def _download_service_remover(hass: HomeAssistant, entry: ConfigEntry[Any]) -> Callable[[], None]:
    """Return an unload callback that removes the service when this is the last entry."""

    def _maybe_remove_service() -> None:
        others = [
            existing for existing in hass.config_entries.async_entries(DOMAIN) if existing.entry_id != entry.entry_id
        ]
        if not others and hass.services.has_service(DOMAIN, _SERVICE_DOWNLOAD):
            hass.services.async_remove(DOMAIN, _SERVICE_DOWNLOAD)

    return _maybe_remove_service


async def async_remove_runtime_entry(hass: HomeAssistant, entry: ConfigEntry[Any]) -> None:
    """Clean up per-entry and shared runtime state on removal."""
    entry_id = entry.entry_id

    # Per-entry audio cache directory (suno/<entry_id>): removed for this
    # entry regardless of whether other accounts remain.
    entry_cache_dir = Path(hass.config.cache_path(f"suno/{entry_id}"))
    if entry_cache_dir.is_dir():
        await hass.async_add_executor_job(shutil.rmtree, entry_cache_dir, True)
        _LOGGER.debug("Removed entry cache directory: %s", entry_cache_dir)

    storage_dir = Path(hass.config.path(".storage"))
    if storage_dir.is_dir():
        for store_file in await hass.async_add_executor_job(lambda: list(storage_dir.glob(f"suno_*{entry_id}*"))):
            try:
                await hass.async_add_executor_job(store_file.unlink)
                _LOGGER.debug("Removed store file: %s", store_file.name)
            except OSError:
                _LOGGER.warning("Could not remove store file: %s", store_file)

    # HA deletes the entry from the registry before calling this hook, so an
    # empty domain list means we were the last entry: tear down shared state
    # idempotently. Concurrent removals are safe because the final hook to
    # run always observes the empty registry.
    if not hass.config_entries.async_entries(DOMAIN):
        legacy_cache_dir = Path(hass.config.cache_path("suno"))
        if legacy_cache_dir.is_dir():
            await hass.async_add_executor_job(shutil.rmtree, legacy_cache_dir, True)
            _LOGGER.debug("Removed legacy cache directory: %s", legacy_cache_dir)

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


def _shared_concurrency_gate(hass: HomeAssistant) -> asyncio.Semaphore:
    """Return the domain-wide concurrency gate shared by every account.

    Per-account limiters reset their throttle state independently, but they
    all acquire this single semaphore so ``N`` accounts cannot saturate the
    Suno API at once (for example when every account's initial sync fires on
    a Home Assistant restart).
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    gate = domain_data.get(_CONCURRENCY_GATE)
    if not isinstance(gate, asyncio.Semaphore):
        gate = asyncio.Semaphore(_GLOBAL_MAX_CONCURRENT)
        domain_data[_CONCURRENCY_GATE] = gate
    return gate


def _entry_rate_limiter(hass: HomeAssistant) -> SunoRateLimiter:
    """Build a per-account rate limiter bound to the shared concurrency gate."""
    return SunoRateLimiter(concurrency_gate=_shared_concurrency_gate(hass))


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
    "paths_overlap",
    "runtime_from_entry",
]
