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
    CONF_CACHE_ENABLED,
    CONF_CACHE_MAX_SIZE,
    CONF_COOKIE,
    CONF_SYNC_ENABLED,
    CONF_SYNC_LATEST_COUNT,
    CONF_SYNC_LATEST_DAYS,
    CONF_SYNC_MODE_LATEST,
    CONF_SYNC_MODE_LIKED,
    CONF_SYNC_MODE_PLAYLISTS,
    CONF_SYNC_QUALITY_LATEST,
    CONF_SYNC_QUALITY_LIKED,
    CONF_SYNC_QUALITY_PLAYLISTS,
    DATA_VIEW_REGISTERED,
    DEFAULT_CACHE_ENABLED,
    DEFAULT_CACHE_MAX_SIZE,
    DEFAULT_SYNC_ENABLED,
    DEFAULT_SYNC_MODE,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from .coordinator import SunoCoordinator
from .exceptions import SunoAuthError, SunoConnectionError
from .proxy import SunoMediaProxyView

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]

type SunoConfigEntry = ConfigEntry[SunoCoordinator]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry from VERSION 1 to VERSION 2."""
    if entry.version < 2:
        new_options = dict(entry.options)

        # Add per-source quality defaults
        new_options.setdefault(CONF_SYNC_QUALITY_LIKED, QUALITY_HIGH)
        new_options.setdefault(CONF_SYNC_QUALITY_PLAYLISTS, QUALITY_HIGH)
        new_options.setdefault(CONF_SYNC_QUALITY_LATEST, QUALITY_STANDARD)

        # Add per-source mode defaults
        new_options.setdefault(CONF_SYNC_MODE_LIKED, DEFAULT_SYNC_MODE)
        new_options.setdefault(CONF_SYNC_MODE_PLAYLISTS, DEFAULT_SYNC_MODE)
        new_options.setdefault(CONF_SYNC_MODE_LATEST, DEFAULT_SYNC_MODE)

        # Rename recent → latest (carry over values)
        if "sync_recent_count" in new_options:
            val = new_options.pop("sync_recent_count")
            new_options[CONF_SYNC_LATEST_COUNT] = int(val) if val else None
        if "sync_recent_days" in new_options:
            val = new_options.pop("sync_recent_days")
            new_options[CONF_SYNC_LATEST_DAYS] = int(val) if val else None

        # Float → int coercion for number fields
        for key in (
            CONF_SYNC_LATEST_COUNT,
            CONF_SYNC_LATEST_DAYS,
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
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    auth = ClerkAuth(session, entry.data[CONF_COOKIE])
    client = SunoClient(auth)

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

    if entry.options.get(CONF_CACHE_ENABLED, DEFAULT_CACHE_ENABLED):
        from .cache import SunoCache  # noqa: PLC0415

        cache = SunoCache(hass, entry.options.get(CONF_CACHE_MAX_SIZE, DEFAULT_CACHE_MAX_SIZE))
        await cache.async_init()
        coordinator.cache = cache

    if not hass.data.get(DATA_VIEW_REGISTERED):
        hass.http.register_view(SunoMediaProxyView(hass))
        hass.data[DATA_VIEW_REGISTERED] = True

    if entry.options.get(CONF_SYNC_ENABLED, DEFAULT_SYNC_ENABLED):
        from .sync import SunoSync  # noqa: PLC0415

        coordinator.sync = await SunoSync.async_setup(hass, entry, coordinator, client)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> None:
    """Clean up cache and stored data on removal."""
    cache_dir = Path(hass.config.cache_path("suno"))
    if cache_dir.is_dir():
        await hass.async_add_executor_job(shutil.rmtree, cache_dir, True)
        _LOGGER.debug("Removed cache directory: %s", cache_dir)

    old_cache_dir = Path(hass.config.path("suno_cache"))
    if old_cache_dir.is_dir():
        await hass.async_add_executor_job(shutil.rmtree, old_cache_dir, True)
        _LOGGER.debug("Removed legacy cache directory: %s", old_cache_dir)

    storage_dir = Path(hass.config.path(".storage"))
    if storage_dir.is_dir():
        for store_file in await hass.async_add_executor_job(lambda: list(storage_dir.glob("suno_*"))):
            try:
                await hass.async_add_executor_job(store_file.unlink)
                _LOGGER.debug("Removed store file: %s", store_file.name)
            except OSError:
                _LOGGER.warning("Could not remove store file: %s", store_file)
