"""Sensor platform for the Suno integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SunoConfigEntry
from .const import (
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_MY_SONGS,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    DEFAULT_DOWNLOAD_MODE,
    DEFAULT_DOWNLOAD_MODE_MY_SONGS,
    DOWNLOAD_MODE_CACHE,
)
from .coordinator import SunoCoordinator
from .models import SunoData
from .runtime import HomeAssistantRuntime

PARALLEL_UPDATES = 0


def _has_download_sections(options: Mapping[str, Any]) -> bool:
    """Return True if any section uses Mirror or Archive download mode."""
    modes = [
        options.get(CONF_DOWNLOAD_MODE_LIKED, DEFAULT_DOWNLOAD_MODE),
        options.get(CONF_DOWNLOAD_MODE_PLAYLISTS, DEFAULT_DOWNLOAD_MODE),
        options.get(CONF_DOWNLOAD_MODE_MY_SONGS, DEFAULT_DOWNLOAD_MODE_MY_SONGS),
    ]
    return any(m != DOWNLOAD_MODE_CACHE for m in modes)


# ── Base class ──────────────────────────────────────────────────────


class _SunoSensor(CoordinatorEntity[SunoCoordinator], SensorEntity):
    """Base for all Suno sensors."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime: HomeAssistantRuntime, entry: SunoConfigEntry, key: str) -> None:
        super().__init__(runtime.coordinator)
        self._runtime = runtime
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
        runtime: HomeAssistantRuntime,
        entry: SunoConfigEntry,
        key: str,
        icon: str,
        value_fn: Callable[[HomeAssistantRuntime], Any],
        unit: str | None = None,
        state_class: SensorStateClass | None = SensorStateClass.MEASUREMENT,
        device_class: SensorDeviceClass | None = None,
        attr_fn: Callable[[HomeAssistantRuntime], dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(runtime, entry, key)
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
        return self._value_fn(self._runtime)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._attr_fn:
            return self._attr_fn(self._runtime)
        return None


# ── Sensor definitions ──────────────────────────────────────────────

_LIBRARY_SENSORS: list[tuple[str, str, Callable[[HomeAssistantRuntime], Any], str | None]] = [
    ("liked_songs", "mdi:heart-outline", lambda runtime: len(runtime.suno_library.liked_clips), None),
]


def _library_files_breakdown(runtime: HomeAssistantRuntime) -> dict[str, int]:
    """Source breakdown for library_files attributes."""
    return runtime.download_status.source_breakdown


def _parse_last_sync(runtime: HomeAssistantRuntime) -> datetime | None:
    """Parse last_download ISO string to datetime for TIMESTAMP sensor."""
    if runtime.download_status.last_download:
        try:
            return datetime.fromisoformat(runtime.download_status.last_download)
        except ValueError:
            return None
    return None


_SYNC_SENSORS: list[tuple[Any, ...]] = [
    (
        "library_files",
        "mdi:file-music",
        lambda runtime: runtime.download_status.file_count,
        None,
        SensorStateClass.MEASUREMENT,
        None,
        _library_files_breakdown,
    ),
    (
        "sync_remaining",
        "mdi:sync",
        lambda runtime: runtime.download_status.pending,
        None,
    ),
    (
        "library_size",
        "mdi:harddisk",
        lambda runtime: runtime.download_status.size_mb,
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
        lambda runtime: runtime.download_status.last_result,
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

    def __init__(self, runtime: HomeAssistantRuntime, entry: SunoConfigEntry) -> None:
        super().__init__(runtime, entry, "credits")

    @property
    def native_value(self) -> int | None:
        data: SunoData = self._runtime.suno_library
        return data.credits.credits_left if data.credits else None

    @property
    def extra_state_attributes(self) -> dict[str, int | str | None]:
        data: SunoData = self._runtime.suno_library
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

    def __init__(self, runtime: HomeAssistantRuntime, entry: SunoConfigEntry) -> None:
        super().__init__(runtime, entry, "cache_size")
        self._cached_size: float = 0.0

    @property
    def native_value(self) -> float:
        return self._cached_size

    async def async_update(self) -> None:
        self._cached_size = await self._runtime.async_cache_size_mb()

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        return {"cached_files": self._runtime.cache_file_count}


class SunoDownloadStatusSensor(_SunoSensor):
    """Sync status: idle, syncing, or error."""

    _attr_translation_key = "sync_status"
    _attr_icon = "mdi:sync"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["idle", "syncing", "error"]

    def __init__(self, runtime: HomeAssistantRuntime, entry: SunoConfigEntry) -> None:
        super().__init__(runtime, entry, "sync_status")

    @property
    def native_value(self) -> str:
        status = self._runtime.download_status
        if status.running:
            return "syncing"
        return "error" if status.errors > 0 else "idle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "download_path": self._entry.options.get(CONF_DOWNLOAD_PATH, ""),
        }
        status = self._runtime.download_status
        if self._runtime.downloads_enabled:
            attrs["errors"] = status.errors
        return attrs


# ── Setup ───────────────────────────────────────────────────────────


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: SunoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Suno sensors."""
    runtime: HomeAssistantRuntime = entry.runtime_data

    entities: list[SensorEntity] = [SunoCreditsSensor(runtime, entry)]
    entities.extend(_SimpleSensor(runtime, entry, *cfg) for cfg in _LIBRARY_SENSORS)
    entities.append(SunoCacheSizeSensor(runtime, entry))

    if _has_download_sections(entry.options) and entry.options.get(CONF_DOWNLOAD_PATH):
        entities.append(SunoDownloadStatusSensor(runtime, entry))
        entities.extend(_SimpleSensor(runtime, entry, *cfg) for cfg in _SYNC_SENSORS)

    async_add_entities(entities)
