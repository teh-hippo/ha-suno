"""Tests for Suno integration setup and unload (__init__.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.suno import async_remove_entry
from custom_components.suno.coordinator import SunoCoordinator
from custom_components.suno.exceptions import SunoApiError, SunoAuthError

from .conftest import make_entry, patch_suno_setup, setup_entry


async def test_setup_entry_success(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Successful setup creates a coordinator in runtime_data."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, SunoCoordinator)
    mock_suno_client._auth.authenticate.assert_awaited_once()


async def test_setup_entry_auth_failure(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Auth failure on startup puts entry in SETUP_ERROR state."""
    mock_suno_client._auth.authenticate.side_effect = SunoAuthError("Cookie expired")
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_setup_entry_api_failure(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Transient API failure on startup puts entry in SETUP_RETRY state."""
    mock_suno_client.get_all_songs.side_effect = SunoApiError("Server error")
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_entry(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Unloading an entry succeeds and cleans up platforms."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED

    result = await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert result is True
    assert entry.state is ConfigEntryState.NOT_LOADED


# ── async_remove_entry ─────────────────────────────────────────────


async def test_remove_entry_cleans_cache_dir(hass: HomeAssistant, tmp_path: Path) -> None:
    """Removes the suno_cache directory when it exists."""
    cache_dir = tmp_path / "suno_cache"
    cache_dir.mkdir()
    (cache_dir / "clip.mp3").write_bytes(b"data")

    storage_dir = tmp_path / ".storage"
    storage_dir.mkdir()

    with patch.object(hass.config, "path", side_effect=lambda p: str(tmp_path / p)):
        entry = make_entry()
        await async_remove_entry(hass, entry)

    assert not cache_dir.exists()


async def test_remove_entry_cleans_storage_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """Removes .storage/suno_* files."""
    storage_dir = tmp_path / ".storage"
    storage_dir.mkdir()
    (storage_dir / "suno_cache_index").write_text("{}")
    (storage_dir / "suno_sync_abc").write_text("{}")
    (storage_dir / "other_file").write_text("{}")

    with patch.object(hass.config, "path", side_effect=lambda p: str(tmp_path / p)):
        entry = make_entry()
        await async_remove_entry(hass, entry)

    assert not (storage_dir / "suno_cache_index").exists()
    assert not (storage_dir / "suno_sync_abc").exists()
    assert (storage_dir / "other_file").exists()


async def test_remove_entry_missing_dirs(hass: HomeAssistant, tmp_path: Path) -> None:
    """Handles missing cache and storage dirs gracefully."""
    with patch.object(hass.config, "path", side_effect=lambda p: str(tmp_path / p)):
        entry = make_entry()
        # Should not raise
        await async_remove_entry(hass, entry)


async def test_remove_entry_oserror_logged(hass: HomeAssistant, tmp_path: Path) -> None:
    """OSError during storage cleanup is logged, not raised."""
    storage_dir = tmp_path / ".storage"
    storage_dir.mkdir()
    suno_file = storage_dir / "suno_test"
    suno_file.write_text("{}")

    with (
        patch.object(hass.config, "path", side_effect=lambda p: str(tmp_path / p)),
        patch.object(Path, "unlink", side_effect=OSError("permission denied")),
    ):
        entry = make_entry()
        # Should not raise despite OSError
        await async_remove_entry(hass, entry)
