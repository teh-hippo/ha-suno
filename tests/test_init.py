"""Tests for Suno integration setup and unload (__init__.py)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.suno import async_remove_entry
from custom_components.suno.const import (
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_MY_SONGS,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    DOMAIN,
    DOWNLOAD_MODE_CACHE,
    DOWNLOAD_MODE_MIRROR,
)
from custom_components.suno.downloaded_library import (
    DownloadedLibrary,
    InMemoryDownloadedLibraryStorage,
)
from custom_components.suno.exceptions import SunoApiError, SunoAuthError, SunoConnectionError
from custom_components.suno.models import SunoClip, SunoData
from custom_components.suno.runtime import _SERVICE_DOWNLOAD, HomeAssistantRuntime

from .conftest import (
    make_entry,
    make_mock_auth,
    patch_suno_setup,
    sample_clips,
    sample_credits,
    sample_playlists,
    setup_entry,
)


def _mock_client(user_id: str) -> AsyncMock:
    """Build a mock SunoClient whose auth resolves to ``user_id``."""
    client = AsyncMock()
    client.user_id = user_id
    client.display_name = "Suno"
    client.suno_display_name = None
    auth = make_mock_auth()
    auth.user_id = user_id
    auth.authenticate = AsyncMock(return_value=user_id)
    client._auth = auth
    client.authenticate = AsyncMock(return_value=user_id)
    client.get_feed = AsyncMock(return_value=(sample_clips(), False))
    client.get_all_songs = AsyncMock(return_value=sample_clips())
    client.get_liked_songs = AsyncMock(return_value=sample_clips(1))
    client.get_playlists = AsyncMock(return_value=sample_playlists())
    client.get_playlist_clips = AsyncMock(return_value=sample_clips()[:1])
    client.get_credits = AsyncMock(return_value=sample_credits())
    client.get_clip_parent_raw = AsyncMock(return_value=None)
    client.get_wav_url = AsyncMock(return_value="https://cdn1.suno.ai/clip-aaa-111.wav")
    client.request_wav = AsyncMock()
    return client


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
    """Removing one entry deletes only its own per-entry cache dir."""
    entry_a = make_entry(unique_id="user-a")
    entry_b = make_entry(unique_id="user-b")
    entry_a.add_to_hass(hass)
    entry_b.add_to_hass(hass)

    cache_root = tmp_path / ".cache"
    dir_a = cache_root / f"suno/{entry_a.entry_id}"
    dir_b = cache_root / f"suno/{entry_b.entry_id}"
    dir_a.mkdir(parents=True)
    dir_b.mkdir(parents=True)
    (dir_a / "clip.mp3").write_bytes(b"data")
    (dir_b / "clip.mp3").write_bytes(b"data")

    storage_dir = tmp_path / ".storage"
    storage_dir.mkdir()

    with (
        patch.object(hass.config, "cache_path", side_effect=lambda p: str(cache_root / p)),
        patch.object(hass.config, "path", side_effect=lambda *p: str(tmp_path.joinpath(*p))),
    ):
        await async_remove_entry(hass, entry_a)

    # Only entry_a's directory is removed; entry_b's cache (and the shared
    # parent) survive because entry_b remains.
    assert not dir_a.exists()
    assert dir_b.exists()


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


async def test_rate_limiter_per_account_with_shared_gate(hass: HomeAssistant) -> None:
    """Each entry gets its own rate limiter, but all share one concurrency gate."""
    entry_a = make_entry(unique_id="user-a")
    entry_b = make_entry(unique_id="user-b")

    with patch_suno_setup(_mock_client("user-a")):
        await setup_entry(hass, entry_a)
    with patch_suno_setup(_mock_client("user-b")):
        await setup_entry(hass, entry_b)

    rt_a = entry_a.runtime_data
    rt_b = entry_b.runtime_data

    # Per-account throttle state: distinct limiter instances.
    assert rt_a.rate_limiter is not rt_b.rate_limiter

    # Shared global concurrency cap: one semaphore for all accounts.
    gate = hass.data[DOMAIN]["concurrency_gate"]
    assert isinstance(gate, asyncio.Semaphore)
    assert rt_a.rate_limiter._semaphore is gate
    assert rt_b.rate_limiter._semaphore is gate


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
    assert runtime.downloaded_library is not None


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
    assert runtime.downloaded_library is None


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
        "custom_components.suno.downloaded_library.DownloadedLibrary.async_cleanup_disabled_downloads",
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
            "custom_components.suno.downloaded_library.DownloadedLibrary.async_cleanup_disabled_downloads",
            new_callable=AsyncMock,
        ) as cleanup,
    ):
        await runtime._async_setup_downloaded_library()

    cleanup.assert_awaited_once_with(new_options, old_options)


async def test_force_download_refreshes_library_before_reconcile(
    hass: HomeAssistant, tmp_path: Path, mock_suno_client: AsyncMock
) -> None:
    """async_force_download refreshes the Suno Library before forcing reconciliation."""

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
    coordinator.async_fetch_remote = AsyncMock(return_value=fresh_data)
    coordinator.async_set_updated_data = MagicMock()

    entry = make_entry(options={CONF_DOWNLOAD_PATH: str(tmp_path)})
    entry.add_to_hass(hass)

    engine = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    await engine.async_load()
    runtime = HomeAssistantRuntime(
        hass,
        entry,
        coordinator,
        mock_suno_client,
        MagicMock(),
        MagicMock(),
        downloaded_library=engine,
    )

    with patch.object(engine, "async_reconcile", new_callable=AsyncMock) as reconcile:
        await runtime.async_force_download()

    coordinator.async_fetch_remote.assert_awaited_once()
    coordinator.async_set_updated_data.assert_called_once_with(fresh_data)
    reconcile.assert_awaited_once()
    assert reconcile.await_args.args[1] is fresh_data
    assert reconcile.await_args.kwargs["force"] is True


# ── Service lifecycle ────────────────────────────────────────────────


def _download_entry(unique_id: str, path: str) -> MockConfigEntry:
    """Build a download-enabled entry (mirror) pointed at ``path``."""
    options = {**make_entry().options, CONF_DOWNLOAD_PATH: path}
    return make_entry(unique_id=unique_id, options=options)


async def test_download_service_kept_while_another_entry_remains(hass: HomeAssistant, tmp_path: Path) -> None:
    """Unloading one account keeps the shared service while another remains."""
    entry_a = _download_entry("user-a", str(tmp_path / "a"))
    entry_b = _download_entry("user-b", str(tmp_path / "b"))

    with patch(
        "custom_components.suno.downloaded_library.DownloadedLibrary.async_reconcile",
        new_callable=AsyncMock,
    ):
        with patch_suno_setup(_mock_client("user-a")):
            await setup_entry(hass, entry_a)
        with patch_suno_setup(_mock_client("user-b")):
            await setup_entry(hass, entry_b)

        assert hass.services.has_service(DOMAIN, _SERVICE_DOWNLOAD)

        assert await hass.config_entries.async_unload(entry_a.entry_id)
        await hass.async_block_till_done()

    # entry_b still configured, so the service persists and is not stale.
    assert hass.services.has_service(DOMAIN, _SERVICE_DOWNLOAD)


async def test_download_service_removed_when_only_entry_unloads(hass: HomeAssistant, tmp_path: Path) -> None:
    """The service is removed once the last download entry unloads."""
    entry = _download_entry("user-a", str(tmp_path / "a"))

    with patch(
        "custom_components.suno.downloaded_library.DownloadedLibrary.async_reconcile",
        new_callable=AsyncMock,
    ):
        with patch_suno_setup(_mock_client("user-a")):
            await setup_entry(hass, entry)
        assert hass.services.has_service(DOMAIN, _SERVICE_DOWNLOAD)

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert not hass.services.has_service(DOMAIN, _SERVICE_DOWNLOAD)
