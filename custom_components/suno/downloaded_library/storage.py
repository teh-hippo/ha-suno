"""Persistence adapters for the Downloaded Library.

Production adapter backed by Home Assistant's Store, plus an in-memory
adapter used as a reference implementation by the engine tests.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

STORE_VERSION = 1


class HomeAssistantDownloadedLibraryStorage:
    """Downloaded Library storage backed by Home Assistant Store."""

    def __init__(self, hass: HomeAssistant, store_key: str) -> None:
        self.store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, store_key)

    async def async_load(self) -> dict[str, Any] | None:
        data = await self.store.async_load()
        return data if isinstance(data, dict) else None

    async def async_save(self, state: dict[str, Any]) -> None:
        await self.store.async_save(state)


class InMemoryDownloadedLibraryStorage:
    """In-memory Downloaded Library storage for tests."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state = state

    async def async_load(self) -> dict[str, Any] | None:
        return self.state

    async def async_save(self, state: dict[str, Any]) -> None:
        self.state = state


__all__ = [
    "HomeAssistantDownloadedLibraryStorage",
    "InMemoryDownloadedLibraryStorage",
    "STORE_VERSION",
]
