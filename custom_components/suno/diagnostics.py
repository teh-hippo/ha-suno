"""Diagnostics for the Suno integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SunoConfigEntry
from .runtime import runtime_from_entry

REDACT_KEYS = {"cookie", "download_path"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SunoConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = runtime_from_entry(entry)
    if runtime is None:
        return {
            "config_entry": {
                "unique_id": entry.unique_id,
                "options": dict(entry.options),
                "data": async_redact_data(dict(entry.data), REDACT_KEYS),
            },
            "error": "Integration not fully loaded",
        }

    return {
        "config_entry": {
            "unique_id": entry.unique_id,
            "options": dict(entry.options),
            "data": async_redact_data(dict(entry.data), REDACT_KEYS),
        },
        **runtime.diagnostics(),
    }
