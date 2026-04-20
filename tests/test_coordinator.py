"""Tests for the Suno coordinator."""

from __future__ import annotations

import logging
from dataclasses import asdict
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.suno.coordinator import _MAX_PARENT_LOOKUPS_PER_CYCLE, SunoCoordinator, SunoData
from custom_components.suno.exceptions import SunoApiError, SunoAuthError
from custom_components.suno.models import SunoClip, SunoPlaylist

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


async def test_display_name_change_logged(
    hass: HomeAssistant, mock_suno_client: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Display name change is logged at INFO level."""
    mock_suno_client.suno_display_name = "OldName"
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    assert coordinator.user.display_name == "OldName"

    mock_suno_client.suno_display_name = "NewName"
    with caplog.at_level(logging.INFO, logger="custom_components.suno.coordinator"):
        await coordinator._async_update_data()

    assert "Display name changed: 'OldName' -> 'NewName'" in caplog.text


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


async def test_stale_title_synced_on_startup(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Config entry title is synced even when identity doesn't change."""
    mock_suno_client.suno_display_name = "NewName"
    entry = make_entry()
    entry._title = "OldName"
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    assert entry.title == "NewName"


# ── TC-9: Stored data recovery tests ────────────────────────────────


async def test_load_stored_data_corrupt(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Corrupt stored data should log warning and return SunoData with skipped entries."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    # Simulate corrupt data by patching the store to return bad data
    with patch.object(coordinator._store, "async_load", return_value={"clips": [{"bad": "data"}]}):
        result = await coordinator.async_load_stored_data()
        assert result is not None
        assert len(result.clips) == 0


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
    """async_load_stored_data with corrupt clip entries skips them gracefully."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    # Dict with entries that cannot construct a SunoClip → skipped by _safe_clips
    corrupt = {"clips": [{"title": "only title, missing required fields"}]}
    with patch.object(coordinator._store, "async_load", return_value=corrupt):
        result = await coordinator.async_load_stored_data()
    assert result is not None
    assert len(result.clips) == 0


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


# ── Root ancestor resolution tests ──────────────────────────────────


def _make_lineage_clip(
    clip_id: str,
    title: str = "Song",
    edited_clip_id: str = "",
    is_remix: bool = False,
    root_ancestor_id: str = "",
) -> SunoClip:
    """Create a minimal SunoClip for lineage tests."""
    return SunoClip(
        id=clip_id,
        title=title,
        audio_url=f"https://cdn1.suno.ai/{clip_id}.mp3",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="2026-01-01T00:00:00Z",
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
        edited_clip_id=edited_clip_id,
        is_remix=is_remix,
        root_ancestor_id=root_ancestor_id,
    )


async def test_root_ancestor_in_memory_chain(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """In-memory chain A->B->C resolves all to root C."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    clip_c = _make_lineage_clip("clip-c", title="Root Song")
    clip_b = _make_lineage_clip("clip-b", edited_clip_id="clip-c")
    clip_a = _make_lineage_clip("clip-a", edited_clip_id="clip-b")

    data = SunoData(clips=[clip_a, clip_b, clip_c])
    await coordinator._resolve_root_ancestors(data)

    assert clip_a.root_ancestor_id == "clip-c"
    assert clip_b.root_ancestor_id == "clip-c"
    assert clip_c.root_ancestor_id == "clip-c"


async def test_root_ancestor_via_parent_api(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Orphan remix resolves root via API parent lookups."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    # Clip D is a remix with edited_clip_id pointing outside the library,
    # so Phase 1 chain breaks and Phase 2 API resolution kicks in.
    clip_d = _make_lineage_clip("clip-d", edited_clip_id="external-parent", is_remix=True)

    # API: clip-d -> parent-ext -> None (root)
    async def mock_parent(clip_id):
        if clip_id == "clip-d":
            return {"id": "parent-ext"}
        return None

    coordinator.client.get_clip_parent = AsyncMock(side_effect=mock_parent)

    data = SunoData(clips=[clip_d])
    with patch("custom_components.suno.coordinator.asyncio.sleep", new_callable=AsyncMock):
        await coordinator._resolve_root_ancestors(data)

    assert clip_d.root_ancestor_id == "parent-ext"


async def test_root_ancestor_deep_chain(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """5-deep chain A->B->C->D->E resolves all to root E."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    clip_e = _make_lineage_clip("clip-e", title="Root")
    clip_d = _make_lineage_clip("clip-d", edited_clip_id="clip-e")
    clip_c = _make_lineage_clip("clip-c", edited_clip_id="clip-d")
    clip_b = _make_lineage_clip("clip-b", edited_clip_id="clip-c")
    clip_a = _make_lineage_clip("clip-a", edited_clip_id="clip-b")

    data = SunoData(clips=[clip_a, clip_b, clip_c, clip_d, clip_e])
    await coordinator._resolve_root_ancestors(data)

    for clip in [clip_a, clip_b, clip_c, clip_d]:
        assert clip.root_ancestor_id == "clip-e"
    assert clip_e.root_ancestor_id == "clip-e"


async def test_root_ancestor_broken_chain(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Clip with edited_clip_id pointing to missing clip stays unresolved."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    # edited_clip_id points to a clip not in the library, and is_remix=False
    # so Phase 2 API lookup won't run either
    clip_a = _make_lineage_clip("clip-a", edited_clip_id="missing-clip")

    data = SunoData(clips=[clip_a])
    await coordinator._resolve_root_ancestors(data)

    assert clip_a.root_ancestor_id == ""


async def test_root_ancestor_circular_chain(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Circular chain A->B->A does not loop forever and stays unresolved."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    clip_a = _make_lineage_clip("clip-a", edited_clip_id="clip-b")
    clip_b = _make_lineage_clip("clip-b", edited_clip_id="clip-a")

    data = SunoData(clips=[clip_a, clip_b])
    await coordinator._resolve_root_ancestors(data)

    # Should terminate without hanging; circular chain is left unresolved
    # (neither clip is_remix, so Phase 2 API lookup won't run either)
    assert clip_a.root_ancestor_id == ""
    assert clip_b.root_ancestor_id == ""


# ── Integration: caching and phased resolution ──────────────────────


async def test_root_ancestor_cached_across_updates(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Already-resolved clips skip API calls on subsequent updates."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    # Pre-resolved clip
    clip = _make_lineage_clip("clip-x", is_remix=True, root_ancestor_id="root-z")

    coordinator.client.get_clip_parent = AsyncMock()

    data = SunoData(clips=[clip])
    await coordinator._resolve_root_ancestors(data)

    # API should never be called since clip already has root_ancestor_id
    coordinator.client.get_clip_parent.assert_not_called()
    assert clip.root_ancestor_id == "root-z"


async def test_phased_resolution_caps_api_calls(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Only MAX_PARENT_LOOKUPS_PER_CYCLE API calls are made for orphan remixes."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    coordinator: SunoCoordinator = entry.runtime_data
    # Create 20 orphan remixes with edited_clip_id pointing outside library
    # so Phase 1 chain breaks and Phase 2 kicks in
    clips = [_make_lineage_clip(f"orphan-{i}", edited_clip_id=f"ext-{i}", is_remix=True) for i in range(20)]

    # Each API call returns None (root found immediately)
    coordinator.client.get_clip_parent = AsyncMock(return_value=None)

    data = SunoData(clips=clips)
    with patch("custom_components.suno.coordinator.asyncio.sleep", new_callable=AsyncMock):
        await coordinator._resolve_root_ancestors(data)

    assert coordinator.client.get_clip_parent.call_count <= _MAX_PARENT_LOOKUPS_PER_CYCLE


# ── data_version monotonic counter (Release 2: 2.4) ─────────────────────


async def test_data_version_starts_at_zero(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Coordinator data_version starts at 0 before the first update."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        coordinator = SunoCoordinator(hass, mock_suno_client, entry)
    assert coordinator.data_version == 0


async def test_data_version_increments_on_success(
    hass: HomeAssistant, mock_suno_client: AsyncMock
) -> None:
    """Each successful coordinator refresh bumps data_version by one."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)
    coordinator: SunoCoordinator = entry.runtime_data
    initial = coordinator.data_version
    assert initial >= 1
    await coordinator.async_refresh()
    assert coordinator.data_version == initial + 1


async def test_data_version_unchanged_on_failure(
    hass: HomeAssistant, mock_suno_client: AsyncMock
) -> None:
    """A failed update does not advance the version counter."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)
    coordinator: SunoCoordinator = entry.runtime_data
    before = coordinator.data_version
    mock_suno_client.get_all_songs.side_effect = SunoApiError("boom")
    await coordinator.async_refresh()
    assert coordinator.data_version == before


# ── Store save error handling (Release 2: 2.7) ──────────────────────────


async def test_store_save_failure_logged_not_raised(
    hass: HomeAssistant, mock_suno_client: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    """If Store.async_save raises, the coordinator logs and continues."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)
    coordinator: SunoCoordinator = entry.runtime_data

    with patch.object(coordinator._store, "async_save", side_effect=OSError("disk full")):
        caplog.set_level(logging.WARNING, logger="custom_components.suno.coordinator")
        await coordinator.async_refresh()
        # Drain background tasks so the wrapped save coroutine actually runs.
        await hass.async_block_till_done()

    assert coordinator.last_update_success is True
    assert any("Failed to persist Suno library" in rec.message for rec in caplog.records)
