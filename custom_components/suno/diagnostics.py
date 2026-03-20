"""Diagnostics for the Suno integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_COOKIE
from .coordinator import SunoCoordinator


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: SunoCoordinator = entry.runtime_data

    data = coordinator.data
    return {
        "config_entry": {
            "unique_id": entry.unique_id,
            "options": dict(entry.options),
            # Cookie is redacted
            "data": {k: "***REDACTED***" if k == CONF_COOKIE else v for k, v in entry.data.items()},
        },
        "library": {
            "total_clips": len(data.clips),
            "liked_clips": sum(1 for c in data.clips if c.is_liked),
            "playlists": len(data.playlists),
        },
        "credits": {
            "credits_left": data.credits.credits_left if data.credits else None,
            "monthly_limit": data.credits.monthly_limit if data.credits else None,
            "monthly_usage": data.credits.monthly_usage if data.credits else None,
        },
    }
