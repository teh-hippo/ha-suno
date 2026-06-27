"""The Suno integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .auth import ClerkAuth
from .const import CONF_COOKIE, DATA_VIEW_REGISTERED
from .proxy import SunoMediaProxyView
from .runtime import HomeAssistantRuntime, async_remove_runtime_entry, runtime_from_entry

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]

type SunoConfigEntry = ConfigEntry[HomeAssistantRuntime]


async def async_migrate_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> bool:
    """Migrate old config entries.

    1.1 -> 1.2: backfill a missing ``unique_id`` (the Suno user_id) so that
    entity ids and the per-account device are stably namespaced. Older
    entries created before unique_id was enforced could otherwise collide
    once a second account is added.
    """
    if entry.minor_version >= 2:
        return True
    unique_id = entry.unique_id
    if unique_id is None:
        unique_id = await _recover_unique_id(hass, entry)
    hass.config_entries.async_update_entry(entry, unique_id=unique_id, minor_version=2)
    return True


async def _recover_unique_id(hass: HomeAssistant, entry: SunoConfigEntry) -> str | None:
    """Best-effort authenticate to recover the Suno user_id for backfill."""
    cookie = entry.data.get(CONF_COOKIE)
    if not cookie:
        return None
    try:
        auth = ClerkAuth(async_get_clientsession(hass), cookie)
        return await auth.authenticate()
    except Exception:
        _LOGGER.warning("Could not backfill Suno unique_id during migration", exc_info=True)
        return None


async def async_setup_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> bool:
    await HomeAssistantRuntime.async_setup(hass, entry)
    if not hass.data.get(DATA_VIEW_REGISTERED):
        hass.http.register_view(SunoMediaProxyView(hass))
        hass.data[DATA_VIEW_REGISTERED] = True
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> bool:
    if (runtime := runtime_from_entry(entry)) is not None:
        await runtime.async_unload()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> None:
    """Clean up cache and stored data on removal."""
    await async_remove_runtime_entry(hass, entry)
