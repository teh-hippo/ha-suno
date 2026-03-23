"""Diagnostics for the Suno integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SunoConfigEntry

REDACT_KEYS = {"cookie", "download_path"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SunoConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    data = coordinator.data
    rate_limiter = hass.data.get("suno", {}).get("rate_limiter")
    return {
        "config_entry": {
            "unique_id": entry.unique_id,
            "options": dict(entry.options),
            "data": async_redact_data(dict(entry.data), REDACT_KEYS),
        },
        "user": {
            "id": coordinator.user.id,
            "display_name": coordinator.user.display_name,
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
        "rate_limiter": {
            "is_throttled": rate_limiter.is_throttled if rate_limiter else None,
            "total_429_count": rate_limiter.total_429_count if rate_limiter else None,
            "seconds_remaining": rate_limiter.seconds_remaining if rate_limiter else None,
        },
    }
