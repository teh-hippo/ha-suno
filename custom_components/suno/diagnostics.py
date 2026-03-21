"""Diagnostics for the Suno integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import SunoCoordinator

REDACT_KEYS = {"cookie"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: SunoCoordinator = entry.runtime_data

    data = coordinator.data
    return {
        "config_entry": {
            "unique_id": entry.unique_id,
            "options": dict(entry.options),
            "data": async_redact_data(dict(entry.data), REDACT_KEYS),
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
    }
