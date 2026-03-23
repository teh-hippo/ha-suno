"""Tests for the Suno sensors."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant

from custom_components.suno.coordinator import SunoCoordinator, SunoData
from custom_components.suno.models import SunoCredits
from custom_components.suno.sensor import (
    _SYNC_SENSORS,
    SunoSyncStatusSensor,
    _parse_last_sync,
    _SimpleSensor,
    _sync_files_breakdown,
)

from .conftest import make_entry, patch_suno_setup, setup_entry


def test_credits_data() -> None:
    """Test SunoCredits dataclass."""
    credits = SunoCredits(
        credits_left=1500,
        monthly_limit=2500,
        monthly_usage=1000,
        period="2026-03",
    )
    assert credits.credits_left == 1500
    assert credits.monthly_limit == 2500


def test_suno_data_defaults() -> None:
    """Test SunoData defaults."""
    data = SunoData()
    assert data.clips == []
    assert data.playlists == []
    assert data.credits is None


async def test_sensor_setup_creates_all_sensors(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Platform setup creates credits, total_songs, liked_songs, and cache_size sensors."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    states = hass.states.async_all("sensor")
    # credits, total_songs, liked_songs, cache_size (sync disabled by default)
    assert len(states) == 4

    state_ids = {s.entity_id for s in states}
    assert "sensor.suno_credits" in state_ids
    assert "sensor.suno_total_songs" in state_ids
    assert "sensor.suno_liked_songs" in state_ids
    assert "sensor.suno_cache_size" in state_ids


async def test_credits_sensor_state(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Credits sensor reports credits_left with attributes."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    state = hass.states.get("sensor.suno_credits")
    assert state is not None
    assert state.state == "1500"
    assert state.attributes["monthly_limit"] == 2500
    assert state.attributes["monthly_usage"] == 1000


async def test_total_songs_sensor(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Total songs sensor reports clip count."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    state = hass.states.get("sensor.suno_total_songs")
    assert state is not None
    assert state.state == "2"  # sample_clips returns 2


async def test_liked_songs_sensor(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Liked songs sensor reports liked clip count."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    state = hass.states.get("sensor.suno_liked_songs")
    assert state is not None
    assert state.state == "1"  # sample_liked_clips returns 1


async def test_sensor_no_credits(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Credits sensor returns unknown when credits unavailable."""
    mock_suno_client.get_credits.side_effect = Exception("Credits error")
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    state = hass.states.get("sensor.suno_credits")
    assert state is not None
    assert state.state == "unknown"


async def test_sensor_unique_ids(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """All sensors have unique IDs based on entry unique_id."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    from homeassistant.helpers import entity_registry as er_mod

    registry = er_mod.async_get(hass)
    entities = er_mod.async_entries_for_config_entry(registry, entry.entry_id)
    unique_ids = {e.unique_id for e in entities}
    assert "test-user-id-123_credits" in unique_ids
    assert "test-user-id-123_total_songs" in unique_ids
    assert "test-user-id-123_liked_songs" in unique_ids
    assert "test-user-id-123_cache_size" in unique_ids


# ── Sync sensor unit tests ─────────────────────────────────────────


def _make_sync_sensor(sensor_cls, sync_mock=None, **kwargs):
    """Create a sync sensor with a mocked coordinator."""
    coordinator = MagicMock(spec=SunoCoordinator)
    coordinator.sync = sync_mock
    coordinator.data = SunoData()
    entry = make_entry()
    if sensor_cls is _SimpleSensor:
        sensor = _SimpleSensor.__new__(_SimpleSensor)
        sensor.coordinator = coordinator
        sensor._entry = entry
        sensor._value_fn = kwargs["value_fn"]
    else:
        sensor = sensor_cls.__new__(sensor_cls)
        sensor.coordinator = coordinator
        sensor._entry = entry
    return sensor


def test_sync_status_idle_when_no_sync() -> None:
    """Returns 'idle' when sync object is None."""
    sensor = _make_sync_sensor(SunoSyncStatusSensor, sync_mock=None)
    assert sensor.native_value == "idle"


def test_sync_status_syncing_when_running() -> None:
    """Returns 'syncing' when sync is running."""
    sync = MagicMock()
    sync.is_running = True
    sync.errors = 0
    sensor = _make_sync_sensor(SunoSyncStatusSensor, sync_mock=sync)
    assert sensor.native_value == "syncing"


def test_sync_status_error_when_errors() -> None:
    """Returns 'error' when sync has errors."""
    sync = MagicMock()
    sync.is_running = False
    sync.errors = 3
    sensor = _make_sync_sensor(SunoSyncStatusSensor, sync_mock=sync)
    assert sensor.native_value == "error"


def test_sync_status_idle_when_no_errors() -> None:
    """Returns 'idle' when sync finished with no errors."""
    sync = MagicMock()
    sync.is_running = False
    sync.errors = 0
    sensor = _make_sync_sensor(SunoSyncStatusSensor, sync_mock=sync)
    assert sensor.native_value == "idle"


def test_sync_files_returns_total() -> None:
    """Returns total_files count from sync."""
    sync = MagicMock()
    sync.total_files = 42
    value_fn = _SYNC_SENSORS[0][2]  # sync_files
    sensor = _make_sync_sensor(_SimpleSensor, sync_mock=sync, value_fn=value_fn)
    assert sensor.native_value == 42


def test_sync_files_zero_when_no_sync() -> None:
    """Returns 0 when sync is None."""
    value_fn = _SYNC_SENSORS[0][2]
    sensor = _make_sync_sensor(_SimpleSensor, sync_mock=None, value_fn=value_fn)
    assert sensor.native_value == 0


def test_sync_remaining_returns_count() -> None:
    """Returns pending count from sync."""
    sync = MagicMock()
    sync.pending = 5
    value_fn = _SYNC_SENSORS[1][2]  # sync_remaining
    sensor = _make_sync_sensor(_SimpleSensor, sync_mock=sync, value_fn=value_fn)
    assert sensor.native_value == 5


def test_sync_remaining_zero_when_no_sync() -> None:
    """Returns 0 when sync is None."""
    value_fn = _SYNC_SENSORS[1][2]
    sensor = _make_sync_sensor(_SimpleSensor, sync_mock=None, value_fn=value_fn)
    assert sensor.native_value == 0


def test_sync_size_returns_mb() -> None:
    """Returns library_size_mb from sync."""
    sync = MagicMock()
    sync.library_size_mb = 123.4
    value_fn = _SYNC_SENSORS[2][2]  # sync_size
    sensor = _make_sync_sensor(_SimpleSensor, sync_mock=sync, value_fn=value_fn)
    assert sensor.native_value == 123.4


def test_sync_size_zero_when_no_sync() -> None:
    """Returns 0.0 when sync is None."""
    value_fn = _SYNC_SENSORS[2][2]
    sensor = _make_sync_sensor(_SimpleSensor, sync_mock=None, value_fn=value_fn)
    assert sensor.native_value == 0.0


# ── Sync last_run and result sensors ───────────────────────────────


def test_sync_last_run_returns_datetime() -> None:
    """Returns parsed datetime from sync.last_sync."""
    from datetime import datetime

    sync = MagicMock()
    sync.last_sync = "2026-03-22T08:00:00+00:00"
    value_fn = _SYNC_SENSORS[3][2]  # sync_last_run
    sensor = _make_sync_sensor(_SimpleSensor, sync_mock=sync, value_fn=value_fn)
    result = sensor.native_value
    assert isinstance(result, datetime)
    assert result.year == 2026
    assert result.tzinfo is not None


def test_sync_last_run_none_when_no_sync() -> None:
    """Returns None when sync is None."""
    value_fn = _SYNC_SENSORS[3][2]
    sensor = _make_sync_sensor(_SimpleSensor, sync_mock=None, value_fn=value_fn)
    assert sensor.native_value is None


def test_sync_result_returns_summary() -> None:
    """Returns last_result string from sync."""
    sync = MagicMock()
    sync.last_result = "3 new songs, 1 removal"
    value_fn = _SYNC_SENSORS[4][2]  # sync_result
    sensor = _make_sync_sensor(_SimpleSensor, sync_mock=sync, value_fn=value_fn)
    assert sensor.native_value == "3 new songs, 1 removal"


def test_sync_result_empty_when_no_sync() -> None:
    """Returns empty string when sync is None."""
    value_fn = _SYNC_SENSORS[4][2]
    sensor = _make_sync_sensor(_SimpleSensor, sync_mock=None, value_fn=value_fn)
    assert sensor.native_value == ""


# ── Source breakdown + sensor device/state class ───────────────────


def test_sync_files_breakdown_returns_source_counts() -> None:
    """sync_files attr_fn returns source counts from sync."""
    sync = MagicMock()
    sync.source_breakdown = {"liked": 10, "latest": 50, "playlist:abc": 5}
    coordinator = MagicMock(spec=SunoCoordinator)
    coordinator.sync = sync
    assert _sync_files_breakdown(coordinator) == {"liked": 10, "latest": 50, "playlist:abc": 5}


def test_sync_files_breakdown_empty_when_no_sync() -> None:
    """Returns empty dict when sync is None."""
    coordinator = MagicMock(spec=SunoCoordinator)
    coordinator.sync = None
    assert _sync_files_breakdown(coordinator) == {}


def test_parse_last_sync_returns_datetime() -> None:
    """Parses ISO string to datetime."""
    from datetime import datetime

    coordinator = MagicMock(spec=SunoCoordinator)
    sync = MagicMock()
    sync.last_sync = "2026-03-22T10:00:00+00:00"
    coordinator.sync = sync
    result = _parse_last_sync(coordinator)
    assert isinstance(result, datetime)


def test_parse_last_sync_none_when_no_sync() -> None:
    """Returns None when sync is None."""
    coordinator = MagicMock(spec=SunoCoordinator)
    coordinator.sync = None
    assert _parse_last_sync(coordinator) is None


def test_sync_last_run_has_no_state_class() -> None:
    """sync_last_run should NOT have state_class=MEASUREMENT."""
    from homeassistant.components.sensor import SensorDeviceClass

    cfg = _SYNC_SENSORS[3]  # sync_last_run
    # Tuple: (key, icon, value_fn, unit, state_class, device_class)
    assert cfg[4] is None  # state_class
    assert cfg[5] is SensorDeviceClass.TIMESTAMP  # device_class


def test_sync_result_has_no_state_class() -> None:
    """sync_result should NOT have state_class=MEASUREMENT."""
    cfg = _SYNC_SENSORS[4]  # sync_result
    # Tuple: (key, icon, value_fn, unit, state_class)
    assert cfg[4] is None  # state_class


def test_sync_status_has_enum_device_class() -> None:
    """sync_status sensor has ENUM device class and valid options."""
    sensor = _make_sync_sensor(SunoSyncStatusSensor, sync_mock=None)
    assert sensor.device_class is SensorDeviceClass.ENUM
    assert sensor._attr_options == ["idle", "syncing", "error"]


def test_sync_status_attributes_include_errors() -> None:
    """sync_status extra_state_attributes includes error count."""
    sync = MagicMock()
    sync.is_running = False
    sync.errors = 3
    sensor = _make_sync_sensor(SunoSyncStatusSensor, sync_mock=sync)
    attrs = sensor.extra_state_attributes
    assert attrs["errors"] == 3


def test_sync_status_attributes_no_errors_key_when_no_sync() -> None:
    """sync_status omits errors key when sync is None."""
    sensor = _make_sync_sensor(SunoSyncStatusSensor, sync_mock=None)
    attrs = sensor.extra_state_attributes
    assert "errors" not in attrs


# ── cached_files is NOT a standalone entity ───────────────────────


async def test_no_cached_files_entity(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """There is no standalone sensor.suno_cached_files entity; it's an attribute on cache_size."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    state_ids = {s.entity_id for s in hass.states.async_all("sensor")}
    assert "sensor.suno_cached_files" not in state_ids
    # cache_size sensor exists (cached_files is exposed as its attribute when cache is active)
    assert "sensor.suno_cache_size" in state_ids


def test_cache_size_sensor_exposes_cached_files_attr() -> None:
    """When cache is present, extra_state_attributes includes cached_files."""
    from custom_components.suno.sensor import SunoCacheSizeSensor

    coordinator = MagicMock(spec=SunoCoordinator)
    mock_cache = MagicMock()
    mock_cache.file_count = 42
    coordinator.cache = mock_cache
    coordinator.data = SunoData()

    sensor = SunoCacheSizeSensor.__new__(SunoCacheSizeSensor)
    sensor.coordinator = coordinator
    attrs = sensor.extra_state_attributes
    assert attrs["cached_files"] == 42
