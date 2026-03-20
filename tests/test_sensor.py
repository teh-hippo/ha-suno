"""Tests for the Suno credits sensor."""

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
    assert credits.monthly_usage == 1000
    assert credits.period == "2026-03"


def test_suno_data_defaults() -> None:
    """Test SunoData defaults."""
    data = SunoData()
    assert data.clips == []
    assert data.playlists == []
    assert data.credits is None


async def test_sensor_setup_and_state(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Sensor is created via platform setup and reports credits_left."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    states = hass.states.async_all("sensor")
    assert len(states) == 1

    state = states[0]
    assert state.state == "1500"
    assert state.attributes["monthly_limit"] == 2500
    assert state.attributes["monthly_usage"] == 1000
    assert state.attributes["period"] == "2026-03"


async def test_sensor_no_credits(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Sensor returns None when credits are unavailable."""
    mock_suno_client.get_credits.side_effect = Exception("Credits error")
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    states = hass.states.async_all("sensor")
    assert len(states) == 1
    assert states[0].state == "unknown"


async def test_sensor_unique_id(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Sensor unique_id is based on entry unique_id."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    from homeassistant.helpers import entity_registry as er_mod

    registry = er_mod.async_get(hass)
    entities = er_mod.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(entities) == 1
    assert entities[0].unique_id == "test-user-id-123_credits"
