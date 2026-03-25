"""Button platform for Suno integration."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SunoConfigEntry
from .const import CONF_DOWNLOAD_PATH
from .coordinator import SunoCoordinator

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


class _SunoButton(CoordinatorEntity[SunoCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry, *, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info


class SunoClearCacheButton(_SunoButton):
    _attr_translation_key = "clear_cache"

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry) -> None:
        super().__init__(coordinator, entry, key="clear_cache")

    async def async_press(self) -> None:
        if self.coordinator.cache:
            await self.coordinator.cache.async_clear()
            _LOGGER.info("Suno audio cache cleared")


class SunoDownloadLibraryButton(_SunoButton):
    _attr_translation_key = "sync_library"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry) -> None:
        super().__init__(coordinator, entry, key="sync_library")

    async def async_press(self) -> None:
        if self.coordinator.download_manager:
            await self.coordinator.download_manager.async_download(
                dict(self._entry.options), self.coordinator.client, force=True
            )
            _LOGGER.info("Suno library download triggered")


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: SunoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SunoCoordinator = entry.runtime_data
    entities: list[ButtonEntity] = [SunoClearCacheButton(coordinator, entry)]
    if entry.options.get(CONF_DOWNLOAD_PATH):
        entities.append(SunoDownloadLibraryButton(coordinator, entry))
    async_add_entities(entities)
