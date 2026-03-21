"""Tests for the Suno coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.suno.coordinator import SunoCoordinator, SunoData
from custom_components.suno.exceptions import SunoApiError, SunoAuthError

from .conftest import make_entry, patch_suno_setup, sample_credits, setup_entry


def test_suno_data_defaults() -> None:
    """Test SunoData initialises with empty defaults."""
    data = SunoData()
    assert data.clips == []
    assert data.liked_clips == []
    assert data.playlists == []
    assert data.credits is None


async def test_coordinator_successful_update(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Coordinator fetches clips, liked clips, playlists, and credits."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    data = coordinator.data

    assert len(data.clips) == 2
    assert len(data.liked_clips) == 1
    assert len(data.playlists) == 1
    assert data.credits is not None
    assert data.credits.credits_left == 1500
    mock_suno_client.get_all_songs.assert_awaited()
    mock_suno_client.get_liked_songs.assert_awaited()
    mock_suno_client.get_playlists.assert_awaited()
    mock_suno_client.get_credits.assert_awaited()


async def test_coordinator_auth_failure_raises_config_entry_auth_failed(
    hass: HomeAssistant, mock_suno_client: AsyncMock
) -> None:
    """SunoAuthError during update raises ConfigEntryAuthFailed."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    mock_suno_client.get_all_songs.side_effect = SunoAuthError("Token expired")

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_coordinator_generic_error_raises_update_failed(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Generic exception during update raises UpdateFailed."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    mock_suno_client.get_all_songs.side_effect = SunoApiError("Server error")

    with pytest.raises(UpdateFailed, match="Error fetching Suno data"):
        await coordinator._async_update_data()


async def test_coordinator_empty_library(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Coordinator handles empty library gracefully."""
    mock_suno_client.get_all_songs.return_value = []
    mock_suno_client.get_liked_songs.return_value = []
    mock_suno_client.get_playlists.return_value = []
    mock_suno_client.get_credits.return_value = sample_credits()

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    assert coordinator.data.clips == []
    assert coordinator.data.liked_clips == []
    assert coordinator.data.playlists == []


async def test_coordinator_credits_failure_graceful(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Credits fetch failure is swallowed; clips and playlists still available."""
    mock_suno_client.get_credits.side_effect = Exception("Credits error")
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    assert len(coordinator.data.clips) == 2
    assert coordinator.data.credits is None


async def test_coordinator_liked_songs_failure_graceful(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Liked songs fetch failure is swallowed; clips and playlists still available."""
    mock_suno_client.get_liked_songs.side_effect = Exception("Liked error")
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    assert len(coordinator.data.clips) == 2
    assert coordinator.data.liked_clips == []


async def test_coordinator_playlists_failure_graceful(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Playlists fetch failure is swallowed; clips still available."""
    mock_suno_client.get_playlists.side_effect = Exception("Playlists error")
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    assert len(coordinator.data.clips) == 2
    assert coordinator.data.playlists == []


async def test_coordinator_uses_cache_ttl_from_options(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Coordinator update_interval comes from options cache_ttl."""
    entry = make_entry(
        options={
            "show_liked": True,
            "show_recent": True,
            "recent_count": 20,
            "show_playlists": True,
            "cache_ttl_minutes": 45,
        }
    )
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    assert coordinator.update_interval is not None
    assert coordinator.update_interval.total_seconds() == 45 * 60
