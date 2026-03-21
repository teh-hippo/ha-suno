"""Tests for the Suno sensors."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.suno.api import SunoCredits
from custom_components.suno.coordinator import SunoData

from .conftest import make_entry, setup_entry


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
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
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
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    state = hass.states.get("sensor.suno_credits")
    assert state is not None
    assert state.state == "1500"
    assert state.attributes["monthly_limit"] == 2500
    assert state.attributes["monthly_usage"] == 1000


async def test_total_songs_sensor(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Total songs sensor reports clip count."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    state = hass.states.get("sensor.suno_total_songs")
    assert state is not None
    assert state.state == "2"  # sample_clips returns 2


async def test_liked_songs_sensor(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Liked songs sensor reports liked clip count."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    state = hass.states.get("sensor.suno_liked_songs")
    assert state is not None
    assert state.state == "1"  # sample_liked_clips returns 1


async def test_sensor_no_credits(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Credits sensor returns unknown when credits unavailable."""
    mock_suno_client.get_credits.side_effect = Exception("Credits error")
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    state = hass.states.get("sensor.suno_credits")
    assert state is not None
    assert state.state == "unknown"


async def test_sensor_unique_ids(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """All sensors have unique IDs based on entry unique_id."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    from homeassistant.helpers import entity_registry as er_mod

    registry = er_mod.async_get(hass)
    entities = er_mod.async_entries_for_config_entry(registry, entry.entry_id)
    unique_ids = {e.unique_id for e in entities}
    assert "test-user-id-123_credits" in unique_ids
    assert "test-user-id-123_total_songs" in unique_ids
    assert "test-user-id-123_liked_songs" in unique_ids
    assert "test-user-id-123_cache_size" in unique_ids
