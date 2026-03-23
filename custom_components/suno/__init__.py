"""The Suno integration."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SunoClient
from .auth import ClerkAuth
from .const import (
    CONF_CACHE_MAX_SIZE,
    CONF_COOKIE,
    CONF_DOWNLOAD_MODE_LATEST,
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_QUALITY_LATEST,
    CONF_QUALITY_LIKED,
    CONF_QUALITY_PLAYLISTS,
    DATA_VIEW_REGISTERED,
    DEFAULT_CACHE_MAX_SIZE,
    DEFAULT_DOWNLOAD_MODE,
    DOMAIN,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from .coordinator import SunoCoordinator
from .exceptions import SunoAuthError, SunoConnectionError
from .proxy import SunoMediaProxyView
from .rate_limit import SunoRateLimiter

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]

type SunoConfigEntry = ConfigEntry[SunoCoordinator]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry to latest version."""
    if entry.version < 2:
        new_options = dict(entry.options)

        # Add per-source quality defaults
        new_options.setdefault(CONF_QUALITY_LIKED, QUALITY_HIGH)
        new_options.setdefault(CONF_QUALITY_PLAYLISTS, QUALITY_HIGH)
        new_options.setdefault(CONF_QUALITY_LATEST, QUALITY_STANDARD)

        # Add per-source mode defaults
        new_options.setdefault(CONF_DOWNLOAD_MODE_LIKED, DEFAULT_DOWNLOAD_MODE)
        new_options.setdefault(CONF_DOWNLOAD_MODE_PLAYLISTS, DEFAULT_DOWNLOAD_MODE)
        new_options.setdefault(CONF_DOWNLOAD_MODE_LATEST, DEFAULT_DOWNLOAD_MODE)

        # Rename recent → latest (carry over values)
        if "sync_recent_count" in new_options:
            val = new_options.pop("sync_recent_count")
            new_options["sync_latest_count"] = int(val) if val else None
        if "sync_recent_days" in new_options:
            val = new_options.pop("sync_recent_days")
            new_options["sync_latest_days"] = int(val) if val else None

        # Float → int coercion for number fields
        for key in (
            "sync_latest_count",
            "sync_latest_days",
            "recent_count",
            "cache_ttl_minutes",
            "cache_max_size_mb",
        ):
            if key in new_options and new_options[key] is not None:
                try:
                    new_options[key] = int(new_options[key])
                except ValueError, TypeError:
                    pass

        hass.config_entries.async_update_entry(entry, options=new_options, version=2)
        _LOGGER.info("Migrated Suno config entry to version 2")

    if entry.version < 3:
        opts = dict(entry.options)

        # Key renames: sync → download/quality/latest
        renames = {
            "sync_path": "download_path",
            "sync_mode_liked": "download_mode_liked",
            "sync_mode_playlists": "download_mode_playlists",
            "sync_mode_latest": "download_mode_latest",
            "sync_quality_liked": "quality_liked",
            "sync_quality_playlists": "quality_playlists",
            "sync_quality_latest": "quality_latest",
            "sync_latest_count": "latest_count",
            "sync_latest_days": "latest_days",
            "sync_all_playlists": "all_playlists",
            "sync_playlists": "playlists",
            "sync_playlists_m3u": "create_playlists",
            "show_recent": "show_latest",
        }
        for old, new in renames.items():
            if old in opts:
                opts[new] = opts.pop(old)

        # Mode value renames: sync→mirror, copy→collect
        for mode_key in ("download_mode_liked", "download_mode_playlists", "download_mode_latest"):
            if mode_key in opts:
                if opts[mode_key] == "sync":
                    opts[mode_key] = "mirror"
                elif opts[mode_key] == "copy":
                    opts[mode_key] = "collect"

        # sync_enabled=False guard: don't accidentally activate downloading
        if not opts.pop("sync_enabled", False):
            opts.pop("download_path", None)

        # Remove deprecated keys
        for key in (
            "audio_quality",
            "recent_count",
            "cache_ttl_minutes",
            "cache_enabled",
            "sync_enabled",
            "sync_liked",
            "sync_trash_days",
        ):
            opts.pop(key, None)

        # Ensure defaults for new keys
        opts.setdefault("cache_max_size_mb", 500)
        opts.setdefault("latest_count", 20)
        opts.setdefault("latest_days", 7)

        # Entity registry migration (sync → download sensor names)
        try:
            from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

            registry = er.async_get(hass)
            remap = {
                "sync_status": "download_status",
                "sync_files": "downloaded_files",
                "sync_remaining": "download_remaining",
                "sync_size": "download_size",
                "sync_last_run": "last_download_run",
                "sync_result": "last_download_result",
            }
            for old_suffix, new_suffix in remap.items():
                old_uid = f"{entry.unique_id}_{old_suffix}"
                if eid := registry.async_get_entity_id("sensor", DOMAIN, old_uid):
                    registry.async_update_entity(eid, new_unique_id=f"{entry.unique_id}_{new_suffix}")
        except Exception:
            _LOGGER.debug("Entity registry migration skipped")

        hass.config_entries.async_update_entry(entry, options=opts, version=3)
        _LOGGER.info("Migrated Suno config entry to version 3")

    return True


async def async_setup_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> bool:
    # Shared rate limiter across all config entries
    hass.data.setdefault(DOMAIN, {})
    if "rate_limiter" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["rate_limiter"] = SunoRateLimiter()
    rate_limiter = hass.data[DOMAIN]["rate_limiter"]

    session = async_get_clientsession(hass)
    auth = ClerkAuth(session, entry.data[CONF_COOKIE])
    client = SunoClient(auth, rate_limiter=rate_limiter)

    coordinator = SunoCoordinator(hass, client, entry)
    stored_data = await coordinator.async_load_stored_data()

    auth_ok = False
    try:
        await auth.authenticate()
        auth_ok = True
    except SunoConnectionError as err:
        if not stored_data:
            raise ConfigEntryNotReady("Cannot reach Suno (no stored data)") from err
        _LOGGER.warning("Cannot reach Suno, using stored library")
    except SunoAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except Exception as err:
        if not stored_data:
            raise ConfigEntryNotReady(f"Could not connect: {err}") from err
        _LOGGER.warning("Cannot reach Suno, using stored library")

    if auth_ok:
        try:
            await coordinator.async_config_entry_first_refresh()
        except ConfigEntryNotReady:
            if not stored_data:
                raise
            _LOGGER.warning("First refresh failed, using stored library")
            coordinator.last_update_success = False

    entry.runtime_data = coordinator

    # Always create cache
    from .cache import SunoCache  # noqa: PLC0415

    cache = SunoCache(hass, entry.options.get(CONF_CACHE_MAX_SIZE, DEFAULT_CACHE_MAX_SIZE))
    await cache.async_init()
    coordinator.cache = cache

    if not hass.data.get(DATA_VIEW_REGISTERED):
        hass.http.register_view(SunoMediaProxyView(hass))
        hass.data[DATA_VIEW_REGISTERED] = True

    # Create download manager when download_path is configured
    if entry.options.get(CONF_DOWNLOAD_PATH):
        from .download import SunoDownloadManager  # noqa: PLC0415

        coordinator.download_manager = await SunoDownloadManager.async_setup(hass, entry, coordinator, client)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> None:
    """Clean up cache and stored data on removal."""
    remaining = [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]

    # Always clean per-entry storage files
    storage_dir = Path(hass.config.path(".storage"))
    if storage_dir.is_dir():
        for store_file in await hass.async_add_executor_job(lambda: list(storage_dir.glob(f"suno_*{entry.entry_id}*"))):
            try:
                await hass.async_add_executor_job(store_file.unlink)
                _LOGGER.debug("Removed store file: %s", store_file.name)
            except OSError:
                _LOGGER.warning("Could not remove store file: %s", store_file)

    # Only remove shared resources if this is the last entry
    if not remaining:
        cache_dir = Path(hass.config.cache_path("suno"))
        if cache_dir.is_dir():
            await hass.async_add_executor_job(shutil.rmtree, cache_dir, True)
            _LOGGER.debug("Removed cache directory: %s", cache_dir)

        old_cache_dir = Path(hass.config.path("suno_cache"))
        if old_cache_dir.is_dir():
            await hass.async_add_executor_job(shutil.rmtree, old_cache_dir, True)
            _LOGGER.debug("Removed legacy cache directory: %s", old_cache_dir)

        # Clean shared storage (cache index)
        if storage_dir.is_dir():
            for store_file in await hass.async_add_executor_job(lambda: list(storage_dir.glob("suno_cache*"))):
                try:
                    await hass.async_add_executor_job(store_file.unlink)
                except OSError:
                    pass

        # Clean up domain data
        hass.data.pop(DOMAIN, None)
