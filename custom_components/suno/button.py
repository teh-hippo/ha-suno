"""Button platform for the Suno integration."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SunoConfigEntry
from .const import CONF_SYNC_ENABLED, DEFAULT_SYNC_ENABLED
from .coordinator import SunoCoordinator

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


# ── Base class ──────────────────────────────────────────────────────


class _SunoButton(CoordinatorEntity[SunoCoordinator], ButtonEntity):
    """Base for all Suno buttons."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry, *, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info from coordinator (picks up name changes)."""
        return self.coordinator.device_info


# ── Clear cache ─────────────────────────────────────────────────────


class SunoClearCacheButton(_SunoButton):
    """Clear the local audio cache."""

    _attr_translation_key = "clear_cache"

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry) -> None:
        super().__init__(coordinator, entry, key="clear_cache")

    async def async_press(self) -> None:
        """Wipe all cached audio files and reset the index."""
        if self.coordinator.cache is None:
            return
        await self.coordinator.hass.async_add_executor_job(self.coordinator.cache._wipe_cache_files)
        self.coordinator.cache._index = {}
        await self.coordinator.cache._store.async_save(self.coordinator.cache._index)
        _LOGGER.info("Suno audio cache cleared")


# ── Clear sync library ──────────────────────────────────────────────


class SunoClearSyncButton(_SunoButton):
    """Trigger a forced full re-sync of the media library."""

    _attr_translation_key = "clear_sync_library"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry) -> None:
        super().__init__(coordinator, entry, key="clear_sync_library")

    async def async_press(self) -> None:
        """Force a full re-sync (re-evaluates all clips, cleans up orphans)."""
        if self.coordinator.sync is None:
            return
        await self.coordinator.sync.async_sync(dict(self._entry.options), self.coordinator.client, force=True)
        _LOGGER.info("Suno force re-sync triggered")


# ── Setup ───────────────────────────────────────────────────────────


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SunoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Suno buttons."""
    coordinator: SunoCoordinator = entry.runtime_data
    entities: list[ButtonEntity] = [SunoClearCacheButton(coordinator, entry)]
    if entry.options.get(CONF_SYNC_ENABLED, DEFAULT_SYNC_ENABLED):
        entities.append(SunoClearSyncButton(coordinator, entry))
    async_add_entities(entities)
