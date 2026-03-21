"""Sensor platform for the Suno integration."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
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
    entities: list[SensorEntity] = [SunoCreditsSensor(coordinator, entry)]

    from .const import CONF_SYNC_ENABLED, DEFAULT_SYNC_ENABLED  # noqa: PLC0415

    if entry.options.get(CONF_SYNC_ENABLED, DEFAULT_SYNC_ENABLED):
        entities.append(SunoSyncSensor(coordinator, entry, hass))

    async_add_entities(entities)


class SunoCreditsSensor(CoordinatorEntity[SunoCoordinator], SensorEntity):
    """Sensor showing remaining Suno credits."""

    _attr_has_entity_name = True
    _attr_translation_key = "credits"
    _attr_native_unit_of_measurement = "credits"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

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


class SunoSyncSensor(CoordinatorEntity[SunoCoordinator], SensorEntity):
    """Sensor showing sync status."""

    _attr_has_entity_name = True
    _attr_translation_key = "sync_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry, hass: HomeAssistant) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_sync_status"
        self._attr_device_info = coordinator.device_info
        self._hass = hass

    @property
    def native_value(self) -> str:
        """Return the sync state: idle, syncing, or error."""
        from . import _SYNC_KEY  # noqa: PLC0415

        sync = self._hass.data.get(_SYNC_KEY)
        if sync is None:
            return "idle"
        if sync.is_running:
            return "syncing"
        if sync.errors > 0:
            return "error"
        return "idle"

    @property
    def extra_state_attributes(self) -> dict[str, int | str | None]:
        """Return sync details as attributes."""
        from . import _SYNC_KEY  # noqa: PLC0415

        sync = self._hass.data.get(_SYNC_KEY)
        if sync is None:
            return {}
        return {
            "last_run": sync.last_sync,
            "total_files": sync.total_files,
            "pending": sync.pending,
            "errors": sync.errors,
        }
