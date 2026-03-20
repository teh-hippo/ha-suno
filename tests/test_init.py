"""Tests for Suno integration setup and unload (__init__.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.suno.coordinator import SunoCoordinator
from custom_components.suno.exceptions import SunoApiError, SunoAuthError

from .conftest import make_entry, setup_entry


def _patch_client(client: AsyncMock):
    """Patch SunoClient constructor to return the given mock."""
    return patch("custom_components.suno.SunoClient", return_value=client)


async def test_setup_entry_success(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Successful setup creates a coordinator in runtime_data."""
    entry = make_entry()
    with _patch_client(mock_suno_client):
        await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, SunoCoordinator)
    mock_suno_client.authenticate.assert_awaited_once()


async def test_setup_entry_auth_failure(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Auth failure on startup puts entry in SETUP_ERROR state."""
    mock_suno_client.authenticate.side_effect = SunoAuthError("Cookie expired")
    entry = make_entry()
    with _patch_client(mock_suno_client):
        await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_setup_entry_api_failure(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Transient API failure on startup puts entry in SETUP_RETRY state."""
    mock_suno_client.get_all_songs.side_effect = SunoApiError("Server error")
    entry = make_entry()
    with _patch_client(mock_suno_client):
        await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_entry(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Unloading an entry succeeds and cleans up platforms."""
    entry = make_entry()
    with _patch_client(mock_suno_client):
        await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED

    result = await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert result is True
    assert entry.state is ConfigEntryState.NOT_LOADED
