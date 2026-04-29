"""The Suno integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .runtime import HomeAssistantRuntime, async_remove_runtime_entry, runtime_from_entry

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]

type SunoConfigEntry = ConfigEntry[HomeAssistantRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> bool:
    await HomeAssistantRuntime.async_setup(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> bool:
    if (runtime := runtime_from_entry(entry)) is not None:
        await runtime.async_unload()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SunoConfigEntry) -> None:
    """Clean up cache and stored data on removal."""
    await async_remove_runtime_entry(hass, entry)
