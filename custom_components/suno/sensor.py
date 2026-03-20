"""Sensor platform for the Suno integration."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SunoConfigEntry
from .coordinator import SunoCoordinator, SunoData

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SunoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Suno sensors."""
    coordinator: SunoCoordinator = entry.runtime_data
    async_add_entities([SunoCreditsSensor(coordinator, entry)])


class SunoCreditsSensor(CoordinatorEntity[SunoCoordinator], SensorEntity):
    """Sensor showing remaining Suno credits."""

    _attr_has_entity_name = True
    _attr_translation_key = "credits"
    _attr_native_unit_of_measurement = "credits"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_credits"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> int | None:
        """Return the number of credits remaining."""
        data: SunoData = self.coordinator.data
        if data.credits:
            return data.credits.credits_left
        return None

    @property
    def extra_state_attributes(self) -> dict[str, int | str | None]:
        """Return credit details as attributes."""
        data: SunoData = self.coordinator.data
        if not data.credits:
            return {}
        return {
            "monthly_limit": data.credits.monthly_limit,
            "monthly_usage": data.credits.monthly_usage,
            "period": data.credits.period,
        }
