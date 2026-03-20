"""The Suno integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SunoClient
from .const import CONF_COOKIE, DOMAIN
from .coordinator import SunoCoordinator
from .exceptions import SunoAuthError
from .proxy import SunoMediaProxyView

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

_VIEW_REGISTERED = f"{DOMAIN}_view_registered"

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

    if not hass.data.get(_VIEW_REGISTERED):
        hass.http.register_view(SunoMediaProxyView(hass))
        hass.data[_VIEW_REGISTERED] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> bool:
    """Unload a Suno config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
