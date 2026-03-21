"""Sensor platform for the Suno integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
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

    entities: list[SensorEntity] = [
        SunoCreditsSensor(coordinator, entry),
        SunoTotalSongsSensor(coordinator, entry),
        SunoLikedSongsSensor(coordinator, entry),
    ]

    # Cache sensors (always, cache may be enabled later)
    entities.append(SunoCacheSizeSensor(coordinator, entry))

    from .const import CONF_SYNC_ENABLED, DEFAULT_SYNC_ENABLED  # noqa: PLC0415

    if entry.options.get(CONF_SYNC_ENABLED, DEFAULT_SYNC_ENABLED):
        entities.extend(
            [
                SunoSyncStatusSensor(coordinator, entry),
                SunoSyncFilesSensor(coordinator, entry),
                SunoSyncPendingSensor(coordinator, entry),
                SunoSyncSizeSensor(coordinator, entry),
            ]
        )

    async_add_entities(entities)


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


# ── Credits ─────────────────────────────────────────────────────────


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


# ── Library stats ───────────────────────────────────────────────────


class SunoTotalSongsSensor(_SunoSensor):
    """Total songs in library."""

    _attr_translation_key = "total_songs"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:music-note-plus"

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry) -> None:
        super().__init__(coordinator, entry, "total_songs")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.clips)


class SunoLikedSongsSensor(_SunoSensor):
    """Liked songs count."""

    _attr_translation_key = "liked_songs"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:heart-outline"

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry) -> None:
        super().__init__(coordinator, entry, "liked_songs")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.liked_clips)


# ── Cache ───────────────────────────────────────────────────────────


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
        if cache is not None:
            self._cached_size = await cache.async_size_mb()
        else:
            self._cached_size = 0.0

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        cache = self.coordinator.cache
        return {"cached_files": cache.file_count} if cache is not None else {}


# ── Sync sensors ────────────────────────────────────────────────────


class _SunoSyncSensor(_SunoSensor):
    """Base for sync sensors."""

    def _get_sync(self) -> Any:
        return self.coordinator.sync


class SunoSyncStatusSensor(_SunoSyncSensor):
    """Sync status: idle, syncing, or error."""

    _attr_translation_key = "sync_status"
    _attr_icon = "mdi:sync"

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry) -> None:
        super().__init__(coordinator, entry, "sync_status")

    @property
    def native_value(self) -> str:
        sync = self._get_sync()
        if sync is None:
            return "idle"
        if sync.is_running:
            return "syncing"
        if sync.errors > 0:
            return "error"
        return "idle"

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        from .const import CONF_SYNC_PATH  # noqa: PLC0415

        sync = self._get_sync()
        return {
            "last_run": sync.last_sync if sync else None,
            "sync_path": self._entry.options.get(CONF_SYNC_PATH, ""),
        }


class SunoSyncFilesSensor(_SunoSyncSensor):
    """Total synced files."""

    _attr_translation_key = "sync_files"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:file-music"

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry) -> None:
        super().__init__(coordinator, entry, "sync_files")

    @property
    def native_value(self) -> int:
        sync = self._get_sync()
        return sync.total_files if sync else 0


class SunoSyncPendingSensor(_SunoSyncSensor):
    """Pending downloads."""

    _attr_translation_key = "sync_pending"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:download"

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry) -> None:
        super().__init__(coordinator, entry, "sync_pending")

    @property
    def native_value(self) -> int:
        sync = self._get_sync()
        return sync.pending if sync else 0


class SunoSyncSizeSensor(_SunoSyncSensor):
    """Synced library size on disk."""

    _attr_translation_key = "sync_size"
    _attr_native_unit_of_measurement = "MB"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:harddisk"

    def __init__(self, coordinator: SunoCoordinator, entry: SunoConfigEntry) -> None:
        super().__init__(coordinator, entry, "sync_size")

    @property
    def native_value(self) -> float:
        sync = self._get_sync()
        return sync.library_size_mb if sync else 0.0
