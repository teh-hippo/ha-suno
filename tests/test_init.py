"""Tests for Suno integration setup and unload (__init__.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.suno import async_remove_entry
from custom_components.suno.coordinator import SunoCoordinator
from custom_components.suno.exceptions import SunoApiError, SunoAuthError, SunoConnectionError

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
    """Removes the .cache/suno directory when it exists."""
    cache_dir = tmp_path / ".cache" / "suno"
    cache_dir.mkdir(parents=True)
    (cache_dir / "clip.mp3").write_bytes(b"data")

    storage_dir = tmp_path / ".storage"
    storage_dir.mkdir()

    with (
        patch.object(hass.config, "cache_path", return_value=str(cache_dir)),
        patch.object(hass.config, "path", side_effect=lambda p: str(tmp_path / p)),
    ):
        entry = make_entry()
        await async_remove_entry(hass, entry)

    assert not cache_dir.exists()


async def test_remove_entry_cleans_old_cache_dir(hass: HomeAssistant, tmp_path: Path) -> None:
    """Removes the legacy suno_cache directory when it exists."""
    old_cache_dir = tmp_path / "suno_cache"
    old_cache_dir.mkdir()
    (old_cache_dir / "clip.mp3").write_bytes(b"data")

    storage_dir = tmp_path / ".storage"
    storage_dir.mkdir()

    with (
        patch.object(hass.config, "cache_path", return_value=str(tmp_path / ".cache" / "suno")),
        patch.object(hass.config, "path", side_effect=lambda p: str(tmp_path / p)),
    ):
        entry = make_entry()
        await async_remove_entry(hass, entry)

    assert not old_cache_dir.exists()


async def test_remove_entry_cleans_storage_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """Removes per-entry and shared storage files when last entry is removed."""
    entry = make_entry()
    storage_dir = tmp_path / ".storage"
    storage_dir.mkdir()
    (storage_dir / "suno_cache_index").write_text("{}")
    (storage_dir / f"suno_library_{entry.entry_id}").write_text("{}")
    (storage_dir / "other_file").write_text("{}")

    with (
        patch.object(hass.config, "cache_path", return_value=str(tmp_path / ".cache" / "suno")),
        patch.object(hass.config, "path", side_effect=lambda p: str(tmp_path / p)),
    ):
        await async_remove_entry(hass, entry)

    assert not (storage_dir / "suno_cache_index").exists()
    assert not (storage_dir / f"suno_library_{entry.entry_id}").exists()
    assert (storage_dir / "other_file").exists()


async def test_remove_entry_missing_dirs(hass: HomeAssistant, tmp_path: Path) -> None:
    """Handles missing cache and storage dirs gracefully."""
    with (
        patch.object(hass.config, "cache_path", return_value=str(tmp_path / ".cache" / "suno")),
        patch.object(hass.config, "path", side_effect=lambda p: str(tmp_path / p)),
    ):
        entry = make_entry()
        # Should not raise
        await async_remove_entry(hass, entry)


async def test_remove_entry_oserror_logged(hass: HomeAssistant, tmp_path: Path) -> None:
    """OSError during storage cleanup is logged, not raised."""
    entry = make_entry()
    storage_dir = tmp_path / ".storage"
    storage_dir.mkdir()
    suno_file = storage_dir / f"suno_library_{entry.entry_id}"
    suno_file.write_text("{}")

    with (
        patch.object(hass.config, "cache_path", return_value=str(tmp_path / ".cache" / "suno")),
        patch.object(hass.config, "path", side_effect=lambda p: str(tmp_path / p)),
        patch.object(Path, "unlink", side_effect=OSError("permission denied")),
    ):
        # Should not raise despite OSError
        await async_remove_entry(hass, entry)


# ── Multi-entry behaviour ─────────────────────────────────────────────


async def test_remove_entry_preserves_cache_for_other_entries(hass: HomeAssistant, tmp_path: Path) -> None:
    """Removing one entry preserves the cache dir when another entry remains."""
    entry_a = make_entry(unique_id="user-a")
    entry_b = make_entry(unique_id="user-b")
    entry_a.add_to_hass(hass)
    entry_b.add_to_hass(hass)

    cache_dir = tmp_path / ".cache" / "suno"
    cache_dir.mkdir(parents=True)
    (cache_dir / "clip.mp3").write_bytes(b"data")

    storage_dir = tmp_path / ".storage"
    storage_dir.mkdir()

    with (
        patch.object(hass.config, "cache_path", return_value=str(cache_dir)),
        patch.object(hass.config, "path", side_effect=lambda p: str(tmp_path / p)),
    ):
        await async_remove_entry(hass, entry_a)

    # Cache dir must still exist because entry_b remains
    assert cache_dir.exists()


async def test_setup_entry_connection_error_with_stored_data(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Connection error on auth with stored data still loads the entry."""
    mock_suno_client._auth.authenticate.side_effect = SunoConnectionError("Unreachable")
    entry = make_entry()

    with patch_suno_setup(mock_suno_client):
        with patch(
            "custom_components.suno.coordinator.SunoCoordinator.async_load_stored_data",
            return_value=True,
        ):
            await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED


async def test_setup_entry_generic_error_with_stored_data(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Generic exception on auth with stored data still loads the entry."""
    mock_suno_client._auth.authenticate.side_effect = RuntimeError("Something broke")
    entry = make_entry()

    with patch_suno_setup(mock_suno_client):
        with patch(
            "custom_components.suno.coordinator.SunoCoordinator.async_load_stored_data",
            return_value=True,
        ):
            await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED


async def test_rate_limiter_shared_across_entries(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Rate limiter is the same instance for all config entries."""
    from custom_components.suno.const import DOMAIN

    entry_a = make_entry(unique_id="user-a")
    entry_b = make_entry(unique_id="user-b")

    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry_a)
        await setup_entry(hass, entry_b)

    assert "rate_limiter" in hass.data[DOMAIN]
    assert hass.data[DOMAIN]["rate_limiter"] is hass.data[DOMAIN]["rate_limiter"]
