"""Tests for Suno integration setup and unload (__init__.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.suno import async_remove_entry
from custom_components.suno.const import (
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_MY_SONGS,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    DOWNLOAD_MODE_CACHE,
    DOWNLOAD_MODE_MIRROR,
)
from custom_components.suno.exceptions import SunoApiError, SunoAuthError, SunoConnectionError
from custom_components.suno.runtime import HomeAssistantRuntime

from .conftest import make_entry, patch_suno_setup, setup_entry


async def test_setup_entry_success(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Successful setup creates a Home Assistant Runtime in runtime_data."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, HomeAssistantRuntime)
    assert entry.runtime_data.coordinator is not None
    mock_suno_client._auth.authenticate.assert_awaited_once()


async def test_setup_entry_auth_failure(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Auth failure on startup puts entry in SETUP_ERROR state."""
    mock_suno_client._auth.authenticate.side_effect = SunoAuthError("Cookie expired")
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_setup_entry_api_failure_loads_partial_library(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Transient section failure on startup loads a Partial Suno Library."""
    mock_suno_client.get_all_songs.side_effect = SunoApiError("Server error")
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.data.clips == []
    assert "clips" in entry.runtime_data.data.stale_sections


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


# ── Download manager creation logic ───────────────────────────────


async def test_dm_created_when_mirror_or_archive(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """DM instantiated when any section uses mirror/archive with path set."""
    entry = make_entry(
        options={
            **make_entry().options,
            CONF_DOWNLOAD_PATH: "/music/suno",
            CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_MIRROR,
            CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_CACHE,
            CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE,
        }
    )
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    runtime = entry.runtime_data
    assert runtime.download_manager is not None


async def test_dm_not_created_all_cache_with_path(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """No DM when all sections cache, even with download_path set."""
    entry = make_entry(
        options={
            **make_entry().options,
            CONF_DOWNLOAD_PATH: "/music/suno",
            CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_CACHE,
            CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_CACHE,
            CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE,
        }
    )
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    runtime = entry.runtime_data
    assert runtime.download_manager is None


async def test_all_cache_transition_cleanup(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Options change to all-cache with existing state triggers setup cleanup."""
    entry = make_entry(
        options={
            **make_entry().options,
            CONF_DOWNLOAD_PATH: "/music/suno",
            CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_MIRROR,
            CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_CACHE,
            CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE,
        }
    )
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    runtime = entry.runtime_data
    old_options = dict(entry.options)
    await runtime.async_unload()

    new_options = {
        **entry.options,
        CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_CACHE,
    }
    hass.config_entries.async_update_entry(entry, options=new_options)

    with patch(
        "custom_components.suno.download.SunoDownloadManager.async_cleanup_disabled_downloads",
        new_callable=AsyncMock,
    ) as cleanup:
        await runtime._async_setup_downloaded_library()

    cleanup.assert_awaited_once_with(new_options, old_options)


async def test_setup_uses_previous_options_for_all_cache_cleanup(
    hass: HomeAssistant, mock_suno_client: AsyncMock
) -> None:
    """Reload setup uses the previous loaded options when cleaning legacy download state."""
    old_options = {
        **make_entry().options,
        CONF_DOWNLOAD_PATH: "/music/suno",
        CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_MIRROR,
        CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_CACHE,
        CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE,
    }
    entry = make_entry(options=old_options)
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.data = None
    cache = MagicMock()
    cache.async_flush = AsyncMock()
    runtime = HomeAssistantRuntime(
        hass,
        entry,
        coordinator,
        mock_suno_client,
        cache,
        MagicMock(),
    )
    await runtime.async_unload()
    new_options = {
        **old_options,
        CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_CACHE,
    }
    hass.config_entries.async_update_entry(entry, options=new_options)

    with (
        patch_suno_setup(mock_suno_client),
        patch(
            "custom_components.suno.download.SunoDownloadManager.async_cleanup_disabled_downloads",
            new_callable=AsyncMock,
        ) as cleanup,
    ):
        await runtime._async_setup_downloaded_library()

    cleanup.assert_awaited_once_with(new_options, old_options)


async def test_force_download_refreshes_library_before_reconcile(
    hass: HomeAssistant, tmp_path: Path, mock_suno_client: AsyncMock
) -> None:
    """async_force_download refreshes the Suno Library before forcing reconciliation."""
    from custom_components.suno.download import SunoDownloadManager
    from custom_components.suno.library_refresh import SunoData
    from custom_components.suno.models import SunoClip

    clip = SunoClip(
        id="clip-force-0000-0000-0000-000000000000",
        title="Song",
        audio_url="https://cdn1.suno.ai/clip-force.mp3",
        image_url=None,
        image_large_url=None,
        is_liked=True,
        status="complete",
        created_at="2026-03-15T10:00:00Z",
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
    )
    fresh_data = SunoData(liked_clips=[clip])

    coordinator = MagicMock()
    coordinator.data = SunoData()
    coordinator.data_version = 2
    coordinator._refresh_task = None
    coordinator._async_fetch_remote_data = AsyncMock(return_value=fresh_data)
    coordinator.async_set_updated_data = MagicMock()

    entry = make_entry(options={CONF_DOWNLOAD_PATH: str(tmp_path)})
    entry.add_to_hass(hass)

    manager = SunoDownloadManager(hass, "test_force_download")
    runtime = HomeAssistantRuntime(
        hass,
        entry,
        coordinator,
        mock_suno_client,
        MagicMock(),
        MagicMock(),
        download_manager=manager,
    )

    with patch.object(manager._downloaded_library, "async_reconcile", new_callable=AsyncMock) as reconcile:
        await runtime.async_force_download()

    coordinator._async_fetch_remote_data.assert_awaited_once()
    coordinator.async_set_updated_data.assert_called_once_with(fresh_data)
    reconcile.assert_awaited_once()
    assert reconcile.await_args.args[1] is fresh_data
    assert reconcile.await_args.kwargs["force"] is True
