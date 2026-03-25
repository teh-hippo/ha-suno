"""Sensor platform for the Suno integration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SunoConfigEntry
from .const import CONF_DOWNLOAD_ENABLED, CONF_DOWNLOAD_PATH, DEFAULT_DOWNLOAD_ENABLED
from .coordinator import SunoCoordinator, SunoData

PARALLEL_UPDATES = 0


# ── Base class ──────────────────────────────────────────────────────


class _SunoSensor(CoordinatorEntity[SunoCoordinator], SensorEntity):
    """Base for all Suno sensors."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info from coordinator (picks up name changes)."""
        return self.coordinator.device_info


class _SimpleSensor(_SunoSensor):
    """Data-driven sensor with a value getter lambda."""

    def __init__(
        self,
        coordinator: SunoCoordinator,
        entry: SunoConfigEntry,
        key: str,
        icon: str,
        value_fn: Callable[[SunoCoordinator], Any],
        unit: str | None = None,
        state_class: SensorStateClass | None = SensorStateClass.MEASUREMENT,
        device_class: SensorDeviceClass | None = None,
        attr_fn: Callable[[SunoCoordinator], dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(coordinator, entry, key)
        self._attr_translation_key = key
        self._attr_icon = icon
        if state_class is not None:
            self._attr_state_class = state_class
        if device_class is not None:
            self._attr_device_class = device_class
        if unit:
            self._attr_native_unit_of_measurement = unit
        self._value_fn = value_fn
        self._attr_fn = attr_fn

    @property
    def native_value(self) -> Any:
        return self._value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._attr_fn:
            return self._attr_fn(self.coordinator)
        return None


# ── Sensor definitions ──────────────────────────────────────────────

_LIBRARY_SENSORS: list[tuple[str, str, Callable[[SunoCoordinator], Any], str | None]] = [
    ("liked_songs", "mdi:heart-outline", lambda c: len(c.data.liked_clips), None),
]


def _library_files_breakdown(c: SunoCoordinator) -> dict[str, int]:
    """Source breakdown for library_files attributes."""
    return c.download_manager.source_breakdown if c.download_manager else {}


def _parse_last_sync(c: SunoCoordinator) -> datetime | None:
    """Parse last_download ISO string to datetime for TIMESTAMP sensor."""
    if c.download_manager and c.download_manager.last_download:
        try:
            return datetime.fromisoformat(c.download_manager.last_download)
        except ValueError:
            return None
    return None


_SYNC_SENSORS: list[tuple[Any, ...]] = [
    (
        "library_files",
        "mdi:file-music",
        lambda c: c.download_manager.total_files if c.download_manager else 0,
        None,
        SensorStateClass.MEASUREMENT,
        None,
        _library_files_breakdown,
    ),
    (
        "sync_remaining",
        "mdi:sync",
        lambda c: c.download_manager.pending if c.download_manager else 0,
        None,
    ),
    (
        "library_size",
        "mdi:harddisk",
        lambda c: c.download_manager.library_size_mb if c.download_manager else 0.0,
        "MB",
    ),
    (
        "last_sync",
        "mdi:clock-check-outline",
        _parse_last_sync,
        None,
        None,
        SensorDeviceClass.TIMESTAMP,
    ),
    (
        "last_sync_result",
        "mdi:text-box-check-outline",
        lambda c: c.download_manager.last_result if c.download_manager else "",
        None,
        None,
    ),
]


# ── Special sensors (custom logic) ─────────────────────────────────


class SunoCreditsSensor(_SunoSensor):
    """Remaining Suno credits."""

    _attr_translation_key = "credits"
    _attr_native_unit_of_measurement = "credits"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry) -> None:
        super().__init__(coordinator, entry, "credits")

    @property
    def native_value(self) -> int | None:
        data: SunoData = self.coordinator.data
        return data.credits.credits_left if data.credits else None

    @property
    def extra_state_attributes(self) -> dict[str, int | str | None]:
        data: SunoData = self.coordinator.data
        if not data.credits:
            return {}
        return {
            "monthly_limit": data.credits.monthly_limit,
            "monthly_usage": data.credits.monthly_usage,
            "period": data.credits.period,
        }


class SunoCacheSizeSensor(_SunoSensor):
    """Playback cache size on disk."""

    _attr_translation_key = "cache_size"
    _attr_native_unit_of_measurement = "MB"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:database"

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry) -> None:
        super().__init__(coordinator, entry, "cache_size")
        self._cached_size: float = 0.0

    @property
    def native_value(self) -> float:
        return self._cached_size

    async def async_update(self) -> None:
        cache = self.coordinator.cache
        self._cached_size = await cache.async_size_mb() if cache else 0.0

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        cache = self.coordinator.cache
        return {"cached_files": cache.file_count} if cache is not None else {}


class SunoDownloadStatusSensor(_SunoSensor):
    """Sync status: idle, syncing, or error."""

    _attr_translation_key = "sync_status"
    _attr_icon = "mdi:sync"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["idle", "syncing", "error"]

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry) -> None:
        super().__init__(coordinator, entry, "sync_status")

    @property
    def native_value(self) -> str:
        dm = self.coordinator.download_manager
        if dm is None:
            return "idle"
        if dm.is_running:
            return "syncing"
        return "error" if dm.errors > 0 else "idle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "download_path": self._entry.options.get(CONF_DOWNLOAD_PATH, ""),
        }
        dm = self.coordinator.download_manager
        if dm is not None:
            attrs["errors"] = dm.errors
        return attrs


# ── Setup ───────────────────────────────────────────────────────────


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: SunoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Suno sensors."""
    coordinator: SunoCoordinator = entry.runtime_data

    entities: list[SensorEntity] = [SunoCreditsSensor(coordinator, entry)]
    entities.extend(_SimpleSensor(coordinator, entry, *cfg) for cfg in _LIBRARY_SENSORS)
    entities.append(SunoCacheSizeSensor(coordinator, entry))

    if entry.options.get(CONF_DOWNLOAD_ENABLED, DEFAULT_DOWNLOAD_ENABLED) and entry.options.get(CONF_DOWNLOAD_PATH):
        entities.append(SunoDownloadStatusSensor(coordinator, entry))
        entities.extend(_SimpleSensor(coordinator, entry, *cfg) for cfg in _SYNC_SENSORS)

    async_add_entities(entities)
