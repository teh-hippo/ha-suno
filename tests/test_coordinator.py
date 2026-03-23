"""Tests for the Suno coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

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

    with pytest.raises(UpdateFailed):
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


async def test_coordinator_uses_default_cache_ttl(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Coordinator update_interval uses hardcoded DEFAULT_CACHE_TTL."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    assert coordinator.update_interval is not None
    # DEFAULT_CACHE_TTL is 30 minutes
    assert coordinator.update_interval.total_seconds() == 30 * 60


# ── Display name and title updates ────────────────────────────────────


async def test_display_name_from_clip_data(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """When client.suno_display_name returns a name, coordinator.user.display_name updates."""
    mock_suno_client.suno_display_name = "CoolArtist"
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    assert coordinator.user.display_name == "CoolArtist"


async def test_title_update_on_display_name_change(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Config entry title updates when display name changes."""
    mock_suno_client.suno_display_name = "NewName"
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    assert entry.title == "NewName"


async def test_title_no_update_when_unchanged(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """No async_update_entry call when title already matches display name."""
    mock_suno_client.suno_display_name = "Suno"
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data

    # Trigger another update — display name matches, so title shouldn't change
    mock_suno_client.suno_display_name = "Suno"
    with patch.object(
        hass.config_entries, "async_update_entry", wraps=hass.config_entries.async_update_entry
    ) as mock_update:
        await coordinator._async_update_data()
        # display_name == user.display_name so the update branch is skipped entirely
        for call in mock_update.call_args_list:
            if "title" in call.kwargs:
                pytest.fail("async_update_entry should not be called with title when unchanged")
