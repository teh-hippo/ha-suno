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
    CONF_DOWNLOAD_ENABLED,
    CONF_DOWNLOAD_PATH,
    DATA_VIEW_REGISTERED,
    DEFAULT_CACHE_MAX_SIZE,
    DEFAULT_DOWNLOAD_ENABLED,
    DOMAIN,
)
from .coordinator import SunoCoordinator
from .exceptions import SunoAuthError, SunoConnectionError
from .proxy import SunoMediaProxyView
from .rate_limit import SunoRateLimiter

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]

type SunoConfigEntry = ConfigEntry[SunoCoordinator]


# Migration removed — all entries expected at VERSION 3 (no external users)


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

    # Clean up removed sensors from the entity registry
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    registry = er.async_get(hass)
    for old_key in ("total_songs",):
        old_uid = f"{entry.unique_id}_{old_key}"
        if eid := registry.async_get_entity_id("sensor", DOMAIN, old_uid):
            registry.async_remove(eid)

    # Always create cache
    from .cache import SunoCache  # noqa: PLC0415

    cache = SunoCache(hass, entry.options.get(CONF_CACHE_MAX_SIZE, DEFAULT_CACHE_MAX_SIZE))
    await cache.async_init()
    coordinator.cache = cache

    if not hass.data.get(DATA_VIEW_REGISTERED):
        hass.http.register_view(SunoMediaProxyView(hass))
        hass.data[DATA_VIEW_REGISTERED] = True

    # Create download manager when downloads are enabled and path is configured
    if entry.options.get(CONF_DOWNLOAD_ENABLED, DEFAULT_DOWNLOAD_ENABLED) and entry.options.get(CONF_DOWNLOAD_PATH):
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
