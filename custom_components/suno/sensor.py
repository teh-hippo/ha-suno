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
    ("total_songs", "mdi:music-note-plus", lambda c: len(c.data.clips), None),
    ("liked_songs", "mdi:heart-outline", lambda c: len(c.data.liked_clips), None),
]


def _sync_files_breakdown(c: SunoCoordinator) -> dict[str, int]:
    """Source breakdown for sync_files attributes."""
    return c.sync.source_breakdown if c.sync else {}


def _parse_last_sync(c: SunoCoordinator) -> datetime | None:
    """Parse last_sync ISO string to datetime for TIMESTAMP sensor."""
    if c.sync and c.sync.last_sync:
        try:
            return datetime.fromisoformat(c.sync.last_sync)
        except ValueError:
            return None
    return None


_SYNC_SENSORS: list[tuple[Any, ...]] = [
    (
        "sync_files",
        "mdi:file-music",
        lambda c: c.sync.total_files if c.sync else 0,
        None,
        SensorStateClass.MEASUREMENT,
        None,
        _sync_files_breakdown,
    ),
    ("sync_remaining", "mdi:download", lambda c: c.sync.pending if c.sync else 0, None),
    ("sync_size", "mdi:harddisk", lambda c: c.sync.library_size_mb if c.sync else 0.0, "MB"),
    ("sync_last_run", "mdi:clock-check-outline", _parse_last_sync, None, None, SensorDeviceClass.TIMESTAMP),
    ("sync_result", "mdi:text-box-check-outline", lambda c: c.sync.last_result if c.sync else "", None, None),
]


_CACHE_SENSORS: list[tuple[str, str, Callable[[SunoCoordinator], Any], str | None]] = [
    ("cached_files", "mdi:file-multiple", lambda c: c.cache.file_count if c.cache else 0, None),
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


class SunoSyncStatusSensor(_SunoSensor):
    """Sync status: idle, syncing, or error."""

    _attr_translation_key = "sync_status"
    _attr_icon = "mdi:sync"

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry) -> None:
        super().__init__(coordinator, entry, "sync_status")

    @property
    def native_value(self) -> str:
        sync = self.coordinator.sync
        if sync is None:
            return "idle"
        if sync.is_running:
            return "syncing"
        return "error" if sync.errors > 0 else "idle"

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        from .const import CONF_SYNC_PATH  # noqa: PLC0415

        return {
            "sync_path": self._entry.options.get(CONF_SYNC_PATH, ""),
        }


# ── Setup ───────────────────────────────────────────────────────────


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SunoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Suno sensors."""
    coordinator: SunoCoordinator = entry.runtime_data

    entities: list[SensorEntity] = [SunoCreditsSensor(coordinator, entry)]
    entities.extend(_SimpleSensor(coordinator, entry, *cfg) for cfg in _LIBRARY_SENSORS)
    entities.append(SunoCacheSizeSensor(coordinator, entry))

    from .const import (  # noqa: PLC0415
        CONF_CACHE_ENABLED,
        CONF_SYNC_ENABLED,
        DEFAULT_CACHE_ENABLED,
        DEFAULT_SYNC_ENABLED,
    )

    if entry.options.get(CONF_CACHE_ENABLED, DEFAULT_CACHE_ENABLED):
        entities.extend(_SimpleSensor(coordinator, entry, *cfg) for cfg in _CACHE_SENSORS)

    if entry.options.get(CONF_SYNC_ENABLED, DEFAULT_SYNC_ENABLED):
        entities.append(SunoSyncStatusSensor(coordinator, entry))
        entities.extend(_SimpleSensor(coordinator, entry, *cfg) for cfg in _SYNC_SENSORS)

    async_add_entities(entities)
