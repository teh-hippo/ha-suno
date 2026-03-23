"""Tests for the Suno sensors."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant

from custom_components.suno.coordinator import SunoCoordinator, SunoData
from custom_components.suno.models import SunoCredits
from custom_components.suno.sensor import (
    _DOWNLOAD_SENSORS,
    SunoDownloadStatusSensor,
    _download_files_breakdown,
    _parse_last_download,
    _SimpleSensor,
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
    """Platform setup creates credits, liked_songs, and cache_size sensors."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    states = hass.states.async_all("sensor")
    # credits, liked_songs, cache_size (download disabled by default - no path)
    assert len(states) == 3

    state_ids = {s.entity_id for s in states}
    assert "sensor.suno_credits" in state_ids
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
    assert "test-user-id-123_liked_songs" in unique_ids
    assert "test-user-id-123_cache_size" in unique_ids


# ── Download sensor unit tests ─────────────────────────────────────


def _make_download_sensor(sensor_cls, dm_mock=None, **kwargs):
    """Create a download sensor with a mocked coordinator."""
    coordinator = MagicMock(spec=SunoCoordinator)
    coordinator.download_manager = dm_mock
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


def test_download_status_idle_when_no_dm() -> None:
    """Returns 'idle' when download_manager is None."""
    sensor = _make_download_sensor(SunoDownloadStatusSensor, dm_mock=None)
    assert sensor.native_value == "idle"


def test_download_status_downloading_when_running() -> None:
    """Returns 'downloading' when download manager is running."""
    dm = MagicMock()
    dm.is_running = True
    dm.errors = 0
    sensor = _make_download_sensor(SunoDownloadStatusSensor, dm_mock=dm)
    assert sensor.native_value == "downloading"


def test_download_status_error_when_errors() -> None:
    """Returns 'error' when download manager has errors."""
    dm = MagicMock()
    dm.is_running = False
    dm.errors = 3
    sensor = _make_download_sensor(SunoDownloadStatusSensor, dm_mock=dm)
    assert sensor.native_value == "error"


def test_download_status_idle_when_no_errors() -> None:
    """Returns 'idle' when download manager finished with no errors."""
    dm = MagicMock()
    dm.is_running = False
    dm.errors = 0
    sensor = _make_download_sensor(SunoDownloadStatusSensor, dm_mock=dm)
    assert sensor.native_value == "idle"


def test_download_files_returns_total() -> None:
    """Returns total_files count from download manager."""
    dm = MagicMock()
    dm.total_files = 42
    value_fn = _DOWNLOAD_SENSORS[0][2]  # downloaded_files
    sensor = _make_download_sensor(_SimpleSensor, dm_mock=dm, value_fn=value_fn)
    assert sensor.native_value == 42


def test_download_files_zero_when_no_dm() -> None:
    """Returns 0 when download_manager is None."""
    value_fn = _DOWNLOAD_SENSORS[0][2]
    sensor = _make_download_sensor(_SimpleSensor, dm_mock=None, value_fn=value_fn)
    assert sensor.native_value == 0


def test_download_remaining_returns_count() -> None:
    """Returns pending count from download manager."""
    dm = MagicMock()
    dm.pending = 5
    value_fn = _DOWNLOAD_SENSORS[1][2]  # download_remaining
    sensor = _make_download_sensor(_SimpleSensor, dm_mock=dm, value_fn=value_fn)
    assert sensor.native_value == 5


def test_download_remaining_zero_when_no_dm() -> None:
    """Returns 0 when download_manager is None."""
    value_fn = _DOWNLOAD_SENSORS[1][2]
    sensor = _make_download_sensor(_SimpleSensor, dm_mock=None, value_fn=value_fn)
    assert sensor.native_value == 0


def test_download_size_returns_mb() -> None:
    """Returns library_size_mb from download manager."""
    dm = MagicMock()
    dm.library_size_mb = 123.4
    value_fn = _DOWNLOAD_SENSORS[2][2]  # download_size
    sensor = _make_download_sensor(_SimpleSensor, dm_mock=dm, value_fn=value_fn)
    assert sensor.native_value == 123.4


def test_download_size_zero_when_no_dm() -> None:
    """Returns 0.0 when download_manager is None."""
    value_fn = _DOWNLOAD_SENSORS[2][2]
    sensor = _make_download_sensor(_SimpleSensor, dm_mock=None, value_fn=value_fn)
    assert sensor.native_value == 0.0


# ── Download last_run and result sensors ───────────────────────────


def test_download_last_run_returns_datetime() -> None:
    """Returns parsed datetime from download_manager.last_download."""
    from datetime import datetime

    dm = MagicMock()
    dm.last_download = "2026-03-22T08:00:00+00:00"
    value_fn = _DOWNLOAD_SENSORS[3][2]  # last_download_run
    sensor = _make_download_sensor(_SimpleSensor, dm_mock=dm, value_fn=value_fn)
    result = sensor.native_value
    assert isinstance(result, datetime)
    assert result.year == 2026
    assert result.tzinfo is not None


def test_download_last_run_none_when_no_dm() -> None:
    """Returns None when download_manager is None."""
    value_fn = _DOWNLOAD_SENSORS[3][2]
    sensor = _make_download_sensor(_SimpleSensor, dm_mock=None, value_fn=value_fn)
    assert sensor.native_value is None


def test_download_result_returns_summary() -> None:
    """Returns last_result string from download manager."""
    dm = MagicMock()
    dm.last_result = "3 new songs, 1 removal"
    value_fn = _DOWNLOAD_SENSORS[4][2]  # last_download_result
    sensor = _make_download_sensor(_SimpleSensor, dm_mock=dm, value_fn=value_fn)
    assert sensor.native_value == "3 new songs, 1 removal"


def test_download_result_empty_when_no_dm() -> None:
    """Returns empty string when download_manager is None."""
    value_fn = _DOWNLOAD_SENSORS[4][2]
    sensor = _make_download_sensor(_SimpleSensor, dm_mock=None, value_fn=value_fn)
    assert sensor.native_value == ""


# ── Source breakdown + sensor device/state class ───────────────────


def test_download_files_breakdown_returns_source_counts() -> None:
    """downloaded_files attr_fn returns source counts from download manager."""
    dm = MagicMock()
    dm.source_breakdown = {"liked": 10, "latest": 50, "playlist:abc": 5}
    coordinator = MagicMock(spec=SunoCoordinator)
    coordinator.download_manager = dm
    assert _download_files_breakdown(coordinator) == {"liked": 10, "latest": 50, "playlist:abc": 5}


def test_download_files_breakdown_empty_when_no_dm() -> None:
    """Returns empty dict when download_manager is None."""
    coordinator = MagicMock(spec=SunoCoordinator)
    coordinator.download_manager = None
    assert _download_files_breakdown(coordinator) == {}


def test_parse_last_download_returns_datetime() -> None:
    """Parses ISO string to datetime."""
    from datetime import datetime

    coordinator = MagicMock(spec=SunoCoordinator)
    dm = MagicMock()
    dm.last_download = "2026-03-22T10:00:00+00:00"
    coordinator.download_manager = dm
    result = _parse_last_download(coordinator)
    assert isinstance(result, datetime)


def test_parse_last_download_none_when_no_dm() -> None:
    """Returns None when download_manager is None."""
    coordinator = MagicMock(spec=SunoCoordinator)
    coordinator.download_manager = None
    assert _parse_last_download(coordinator) is None


def test_download_last_run_has_no_state_class() -> None:
    """last_download_run should NOT have state_class=MEASUREMENT."""
    cfg = _DOWNLOAD_SENSORS[3]  # last_download_run
    # Tuple: (key, icon, value_fn, unit, state_class, device_class)
    assert cfg[4] is None  # state_class
    assert cfg[5] is SensorDeviceClass.TIMESTAMP  # device_class


def test_download_result_has_no_state_class() -> None:
    """last_download_result should NOT have state_class=MEASUREMENT."""
    cfg = _DOWNLOAD_SENSORS[4]  # last_download_result
    # Tuple: (key, icon, value_fn, unit, state_class)
    assert cfg[4] is None  # state_class


def test_download_status_has_enum_device_class() -> None:
    """download_status sensor has ENUM device class and valid options."""
    sensor = _make_download_sensor(SunoDownloadStatusSensor, dm_mock=None)
    assert sensor.device_class is SensorDeviceClass.ENUM
    assert sensor._attr_options == ["idle", "downloading", "error"]


def test_download_status_attributes_include_errors() -> None:
    """download_status extra_state_attributes includes error count."""
    dm = MagicMock()
    dm.is_running = False
    dm.errors = 3
    sensor = _make_download_sensor(SunoDownloadStatusSensor, dm_mock=dm)
    attrs = sensor.extra_state_attributes
    assert attrs["errors"] == 3


def test_download_status_attributes_no_errors_key_when_no_dm() -> None:
    """download_status omits errors key when download_manager is None."""
    sensor = _make_download_sensor(SunoDownloadStatusSensor, dm_mock=None)
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
    # cache_size sensor exists
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
