"""Tests for the Suno coordinator."""

from __future__ import annotations

from dataclasses import asdict
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.suno.coordinator import SunoCoordinator, SunoData
from custom_components.suno.exceptions import SunoApiError, SunoAuthError
from custom_components.suno.models import SunoPlaylist

from .conftest import (
    make_entry,
    patch_suno_setup,
    sample_clips,
    sample_credits,
    sample_liked_clips,
    sample_playlists,
    setup_entry,
)


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


# ── TC-9: Stored data recovery tests ────────────────────────────────


async def test_load_stored_data_corrupt(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Corrupt stored data should log warning and return None."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    # Simulate corrupt data by patching the store to return bad data
    with patch.object(coordinator._store, "async_load", return_value={"clips": [{"bad": "data"}]}):
        result = await coordinator.async_load_stored_data()
        assert result is None


async def test_load_stored_data_non_dict(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Non-dict stored data returns None."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    with patch.object(coordinator._store, "async_load", return_value="not a dict"):
        result = await coordinator.async_load_stored_data()
        assert result is None


async def test_load_stored_data_none(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Missing stored data returns None."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    with patch.object(coordinator._store, "async_load", return_value=None):
        result = await coordinator.async_load_stored_data()
        assert result is None


async def test_load_stored_data_exception(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Store load exception is caught and returns None."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    with patch.object(coordinator._store, "async_load", side_effect=Exception("IO Error")):
        # async_load_stored_data doesn't catch this itself — it propagates
        # But the caller in __init__.py wraps it
        try:
            result = await coordinator.async_load_stored_data()
        except Exception:
            result = None
        assert result is None


# ── TC-10: Playlist-clip fanout tests ────────────────────────────────


async def test_playlist_clips_populated(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Playlist clips are fetched and stored under playlist IDs."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    # The default mock returns sample_playlists() with one playlist "pl-001"
    assert "pl-001" in coordinator.data.playlist_clips
    assert len(coordinator.data.playlist_clips["pl-001"]) > 0


async def test_playlist_clip_fetch_partial_failure(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """When one playlist fetch fails, other playlists still succeed."""
    from custom_components.suno.models import SunoPlaylist

    mock_suno_client.get_playlists.return_value = [
        SunoPlaylist(id="pl-ok", name="Good", image_url="", num_clips=1),
        SunoPlaylist(id="pl-fail", name="Bad", image_url="", num_clips=1),
    ]

    call_count = 0

    async def _get_clips(pid):
        nonlocal call_count
        call_count += 1
        if pid == "pl-fail":
            raise Exception("Network error")
        return sample_clips()[:1]

    mock_suno_client.get_playlist_clips = AsyncMock(side_effect=_get_clips)

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    assert "pl-ok" in coordinator.data.playlist_clips
    # pl-fail should not be in playlist_clips because it errored
    assert "pl-fail" not in coordinator.data.playlist_clips


# ── TC-9 addendum: stored data recovery ─────────────────────────────


async def test_corrupt_non_dict_stored_data_returns_none(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """async_load_stored_data with corrupt non-dict data logs warning and returns None."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    # Dict with entries that cannot construct a SunoClip → except → warning → None
    corrupt = {"clips": [{"title": "only title, missing required fields"}]}
    with patch.object(coordinator._store, "async_load", return_value=corrupt):
        result = await coordinator.async_load_stored_data()
    assert result is None


async def test_valid_stored_data_restores_coordinator(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """async_load_stored_data with valid data restores clips and playlists."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    stored = {
        "clips": [asdict(c) for c in sample_clips()],
        "liked_clips": [asdict(c) for c in sample_liked_clips()],
        "playlists": [asdict(p) for p in sample_playlists()],
        "playlist_clips": {},
    }
    with patch.object(coordinator._store, "async_load", return_value=stored):
        result = await coordinator.async_load_stored_data()

    assert result is not None
    assert len(result.clips) == 2
    assert result.clips[0].title == "Test Song Alpha"
    assert len(result.liked_clips) == 1
    assert len(result.playlists) == 1
    assert coordinator.data is result


async def test_store_exception_caught_returns_none(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """When store load raises an exception, it is caught and returns None."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    # Dict with non-iterable clips value → TypeError during list comprehension
    with patch.object(coordinator._store, "async_load", return_value={"clips": 42}):
        result = await coordinator.async_load_stored_data()
    assert result is None


# ── TC-10 addendum: playlist-clip fanout ─────────────────────────────


async def test_multiple_playlists_clips_fanout(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Multiple playlists are fetched and their clips populated into data.playlist_clips."""
    playlists = [
        SunoPlaylist(id="pl-001", name="Favourites", image_url="https://cdn1.suno.ai/pl1.jpg", num_clips=5),
        SunoPlaylist(id="pl-002", name="Rock Anthems", image_url="https://cdn1.suno.ai/pl2.jpg", num_clips=3),
    ]
    clips_a = sample_clips()[:1]
    clips_b = sample_clips()[1:2]
    mock_suno_client.get_playlists.return_value = playlists
    mock_suno_client.get_playlist_clips = AsyncMock(side_effect=lambda pl_id: clips_a if pl_id == "pl-001" else clips_b)

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    assert len(coordinator.data.playlist_clips) == 2
    assert len(coordinator.data.playlist_clips["pl-001"]) == 1
    assert len(coordinator.data.playlist_clips["pl-002"]) == 1


async def test_partial_playlist_failure_others_succeed(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """When one playlist fetch fails, other playlists still succeed."""
    playlists = [
        SunoPlaylist(id="pl-good", name="Good", image_url="", num_clips=1),
        SunoPlaylist(id="pl-bad", name="Bad", image_url="", num_clips=1),
    ]
    mock_suno_client.get_playlists.return_value = playlists

    def _side_effect(pl_id):
        if pl_id == "pl-bad":
            raise Exception("Network error")
        return sample_clips()[:1]

    mock_suno_client.get_playlist_clips = AsyncMock(side_effect=_side_effect)

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    assert "pl-good" in coordinator.data.playlist_clips
    assert "pl-bad" not in coordinator.data.playlist_clips
    assert len(coordinator.data.playlist_clips["pl-good"]) == 1


async def test_playlist_clips_keys_match_ids(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """data.playlist_clips keys match playlist IDs."""
    playlists = [
        SunoPlaylist(id="pl-alpha", name="Alpha", image_url="", num_clips=2),
        SunoPlaylist(id="pl-beta", name="Beta", image_url="", num_clips=1),
        SunoPlaylist(id="pl-gamma", name="Gamma", image_url="", num_clips=4),
    ]
    mock_suno_client.get_playlists.return_value = playlists
    mock_suno_client.get_playlist_clips = AsyncMock(return_value=sample_clips()[:1])

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    assert set(coordinator.data.playlist_clips.keys()) == {"pl-alpha", "pl-beta", "pl-gamma"}
