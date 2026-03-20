"""The Suno integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SunoClient
from .const import CONF_COOKIE, DOMAIN  # noqa: F401 - DOMAIN used by HA discovery
from .coordinator import SunoCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

type SunoConfigEntry = ConfigEntry[SunoCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> bool:
    """Set up Suno from a config entry."""
    session = async_get_clientsession(hass)
    client = SunoClient(session, entry.data[CONF_COOKIE])

    # Validate auth on startup
    await client.authenticate()

    coordinator = SunoCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> bool:
    """Unload a Suno config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
