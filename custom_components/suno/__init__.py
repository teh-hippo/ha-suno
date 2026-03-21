"""The Suno integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SunoClient
from .const import (
    CONF_CACHE_ENABLED,
    CONF_CACHE_MAX_SIZE,
    CONF_COOKIE,
    CONF_SYNC_ENABLED,
    CONF_SYNC_PATH,
    DEFAULT_CACHE_ENABLED,
    DEFAULT_CACHE_MAX_SIZE,
    DEFAULT_SYNC_ENABLED,
    DOMAIN,
)
from .coordinator import SunoCoordinator
from .exceptions import SunoAuthError
from .proxy import _SUNO_CACHE_KEY, SunoMediaProxyView

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

_VIEW_REGISTERED = f"{DOMAIN}_view_registered"
_SYNC_KEY = f"{DOMAIN}_sync"
_SERVICE_SYNC = "sync_media"

type SunoConfigEntry = ConfigEntry[SunoCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> bool:
    """Set up Suno from a config entry."""
    session = async_get_clientsession(hass)
    client = SunoClient(session, entry.data[CONF_COOKIE])

    try:
        await client.authenticate()
    except SunoAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except Exception as err:
        raise ConfigEntryNotReady(f"Could not connect to Suno: {err}") from err

    coordinator = SunoCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    # Initialise local audio cache if enabled
    if entry.options.get(CONF_CACHE_ENABLED, DEFAULT_CACHE_ENABLED):
        from .cache import SunoCache  # noqa: PLC0415

        cache = SunoCache(hass, entry.options.get(CONF_CACHE_MAX_SIZE, DEFAULT_CACHE_MAX_SIZE))
        await cache.async_init()
        hass.data[_SUNO_CACHE_KEY] = cache

    if not hass.data.get(_VIEW_REGISTERED):
        hass.http.register_view(SunoMediaProxyView(hass))
        hass.data[_VIEW_REGISTERED] = True

    # Initialise background sync if enabled
    if entry.options.get(CONF_SYNC_ENABLED, DEFAULT_SYNC_ENABLED):
        await _setup_sync(hass, entry, coordinator, client)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _setup_sync(
    hass: HomeAssistant,
    entry: SunoConfigEntry,
    coordinator: SunoCoordinator,
    client: SunoClient,
) -> None:
    """Initialise sync, register service, schedule background task."""
    from .sync import SunoSync  # noqa: PLC0415

    sync = SunoSync(hass, f"suno_sync_{entry.entry_id}")
    await sync.async_init()
    hass.data[_SYNC_KEY] = sync

    sync_path = entry.options.get(CONF_SYNC_PATH, "")
    if sync_path:
        await sync.cleanup_tmp_files(sync_path)

    # Trigger sync on coordinator refresh
    def _on_coordinator_update() -> None:
        if sync.is_running:
            return
        hass.async_create_task(
            sync.async_sync(dict(entry.options), client),
            f"suno_sync_refresh_{entry.entry_id}",
        )

    entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))

    # Register sync service
    async def _handle_sync_service(call: ServiceCall) -> None:
        force = call.data.get("force", False)
        await sync.async_sync(dict(entry.options), client, force=force)

    if not hass.services.has_service(DOMAIN, _SERVICE_SYNC):
        hass.services.async_register(DOMAIN, _SERVICE_SYNC, _handle_sync_service)
        entry.async_on_unload(lambda: hass.services.async_remove(DOMAIN, _SERVICE_SYNC))

    # Delayed initial sync (60s after startup)
    async def _initial_sync() -> None:
        await asyncio.sleep(60)
        await sync.async_sync(dict(entry.options), client)

    entry.async_create_background_task(hass, _initial_sync(), f"suno_sync_init_{entry.entry_id}")


async def async_unload_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> bool:
    """Unload a Suno config entry."""
    hass.data.pop(_SYNC_KEY, None)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
