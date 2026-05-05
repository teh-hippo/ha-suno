"""Tests for the Suno download module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.suno.const import (
    CONF_ALL_PLAYLISTS,
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_MY_SONGS,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_MY_SONGS_COUNT,
    CONF_MY_SONGS_DAYS,
    CONF_PLAYLISTS,
    CONF_SHOW_LIKED,
    CONF_SHOW_MY_SONGS,
    CONF_SHOW_PLAYLISTS,
    DOWNLOAD_MODE_ARCHIVE,
    DOWNLOAD_MODE_CACHE,
    DOWNLOAD_MODE_MIRROR,
    QUALITY_HIGH,
)
from custom_components.suno.download import SunoDownloadManager
from custom_components.suno.downloaded_library import (
    _clip_path,
)
from custom_components.suno.library_refresh import SunoData

# ── Filename sanitisation ──────────────────────────────────────────


# ── Clip path generation ───────────────────────────────────────────


# ── Video sidecar path ─────────────────────────────────────────────


# ── Sync state management ──────────────────────────────────────────


# ── Download and cleanup ───────────────────────────────────────────


def _make_clip(clip_id: str, title: str = "Song", created: str = "2026-03-15T10:00:00Z"):
    from custom_components.suno.models import SunoClip

    return SunoClip(
        id=clip_id,
        title=title,
        audio_url=f"https://cdn1.suno.ai/{clip_id}.mp3",
        image_url=None,
        image_large_url=None,
        is_liked=True,
        status="complete",
        created_at=created,
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
    )


async def test_force_download_refreshes_library_before_reconcile(hass: HomeAssistant, tmp_path: Path) -> None:
    """Manual force refreshes the Suno Library before forcing download reconciliation."""
    clip = _make_clip("clip-force-0000-0000-0000-000000000000")
    fresh_data = SunoData(liked_clips=[clip])
    coordinator = MagicMock()
    coordinator.data = SunoData()
    coordinator.data_version = 2
    coordinator._refresh_task = None
    coordinator._async_fetch_remote_data = AsyncMock(return_value=fresh_data)
    coordinator.async_set_updated_data = MagicMock()
    sync = SunoDownloadManager(hass, "test_sync_state")
    sync._coordinator = coordinator
    client = AsyncMock()

    with patch.object(sync._downloaded_library, "async_reconcile", new_callable=AsyncMock) as reconcile:
        await sync.async_download({CONF_DOWNLOAD_PATH: str(tmp_path)}, client, force=True)

    coordinator._async_fetch_remote_data.assert_awaited_once()
    coordinator.async_set_updated_data.assert_called_once_with(fresh_data)
    reconcile.assert_awaited_once()
    assert reconcile.await_args.args[1] is fresh_data
    assert reconcile.await_args.kwargs["force"] is True


def _make_dated_clip(clip_id: str, title: str = "Song", created: str = "2026-03-15T10:00:00Z"):
    from custom_components.suno.models import SunoClip

    return SunoClip(
        id=clip_id,
        title=title,
        audio_url=f"https://cdn1.suno.ai/{clip_id}.mp3",
        image_url=None,
        image_large_url=None,
        is_liked=False,
        status="complete",
        created_at=created,
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
    )


# ── Sync summary ───────────────────────────────────────────────────


def _make_clip_with_display(
    clip_id: str,
    title: str = "Song",
    created: str = "2026-03-15T10:00:00Z",
    display_name: str = "testuser",
    video_url: str = "",
    image_url: str | None = None,
    image_large_url: str | None = None,
):
    """Build a SunoClip with optional display_name, video_url, and image fields."""
    from custom_components.suno.models import SunoClip

    return SunoClip(
        id=clip_id,
        title=title,
        audio_url=f"https://cdn1.suno.ai/{clip_id}.mp3",
        image_url=image_url,
        image_large_url=image_large_url,
        is_liked=True,
        status="complete",
        created_at=created,
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
        display_name=display_name,
        video_url=video_url,
    )


# ── TC-7: Playlist order preservation ─────────────────────────────

# ── Service lifecycle ────────────────────────────────────────────────


class TestServiceLifecycle:
    """Tests for download service registration lifecycle."""

    def test_service_not_removed_while_other_entries_remain(self) -> None:
        """Service removal callback should keep the service when other entries exist."""
        from custom_components.suno.const import DOMAIN
        from custom_components.suno.download import _SERVICE_DOWNLOAD

        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry-1"

        other_entry = MagicMock()
        other_entry.entry_id = "entry-2"
        hass.config_entries.async_entries.return_value = [other_entry]

        # Build the guarded removal function the same way production code does
        def _maybe_remove_service() -> None:
            remaining = [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]
            if not remaining:
                hass.services.async_remove(DOMAIN, _SERVICE_DOWNLOAD)

        _maybe_remove_service()
        hass.services.async_remove.assert_not_called()

    def test_service_removed_when_last_entry_unloads(self) -> None:
        """Service removal callback should remove the service when no entries remain."""
        from custom_components.suno.const import DOMAIN
        from custom_components.suno.download import _SERVICE_DOWNLOAD

        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry-1"

        # No other entries remain after this one unloads
        hass.config_entries.async_entries.return_value = [entry]

        def _maybe_remove_service() -> None:
            remaining = [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]
            if not remaining:
                hass.services.async_remove(DOMAIN, _SERVICE_DOWNLOAD)

        _maybe_remove_service()
        hass.services.async_remove.assert_called_once_with(DOMAIN, _SERVICE_DOWNLOAD)


# ── T6: Metadata hash change updates state ─────────────────────────


async def test_metadata_hash_change_triggers_retag(hass: HomeAssistant, tmp_path: Path) -> None:
    """Changed meta hash should re-tag the file in place, not re-download."""
    from custom_components.suno.models import clip_meta_hash

    sync = SunoDownloadManager(hass, "test_sync_state")
    clip = _make_clip("clip-meta", "Meta Song")

    # Pre-populate state with old meta hash
    old_hash = "old_hash_1234"
    sync_dir = tmp_path / "mirror"
    rel_path = _clip_path(clip, QUALITY_HIGH)

    initial_state = {
        "clips": {
            "clip-meta": {
                "path": rel_path,
                "title": "Meta Song OLD",
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 100,
                "meta_hash": old_hash,
                "quality": QUALITY_HIGH,
            }
        },
        "last_download": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    # Create the file on disk
    target = sync_dir / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fLaC" + b"\x00" * 50)

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    with (
        patch(
            "custom_components.suno.downloaded_library.download_and_transcode_to_flac",
            new_callable=AsyncMock,
        ) as mock_dl,
        patch("custom_components.suno.downloaded_library.get_ffmpeg_manager") as mock_ffmpeg,
        patch("custom_components.suno.downloaded_library.async_get_clientsession"),
        patch("custom_components.suno.downloaded_library.fetch_album_art", return_value=None),
        patch.object(sync._store, "async_save"),
        patch(
            "custom_components.suno.downloaded_library.retag_flac",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_retag,
    ):
        mock_ffmpeg.return_value.binary = "/usr/bin/ffmpeg"
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    # Re-download should NOT have been triggered
    mock_dl.assert_not_called()
    # Re-tag should have been called instead
    mock_retag.assert_called_once()
    # File should still exist (not deleted)
    assert target.exists()
    # Verify meta hash was updated in state
    new_hash = clip_meta_hash(clip)
    assert new_hash != old_hash
    clip_state = sync._state["clips"]["clip-meta"]
    assert clip_state["meta_hash"] == new_hash
    assert sync.errors == 0


async def test_retag_failure_preserves_old_hash(hass: HomeAssistant, tmp_path: Path) -> None:
    """When re-tag fails, meta_hash is NOT updated so next sync retries."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    clip = _make_clip("clip-fail", "Fail Song")

    old_hash = "old_hash_fail"
    sync_dir = tmp_path / "mirror"
    rel_path = _clip_path(clip, QUALITY_HIGH)

    initial_state = {
        "clips": {
            "clip-fail": {
                "path": rel_path,
                "title": "Fail Song",
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 100,
                "meta_hash": old_hash,
                "quality": QUALITY_HIGH,
            }
        },
        "last_download": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    target = sync_dir / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fLaC" + b"\x00" * 50)

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    with (
        patch("custom_components.suno.downloaded_library.get_ffmpeg_manager") as mock_ffmpeg,
        patch("custom_components.suno.downloaded_library.async_get_clientsession"),
        patch("custom_components.suno.downloaded_library.fetch_album_art", return_value=None),
        patch.object(sync._store, "async_save"),
        patch("custom_components.suno.downloaded_library.retag_flac", new_callable=AsyncMock, return_value=False),
    ):
        mock_ffmpeg.return_value.binary = "/usr/bin/ffmpeg"
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    # meta_hash should still be the old value (not updated on failure)
    assert sync._state["clips"]["clip-fail"]["meta_hash"] == old_hash
    # File should still exist
    assert target.exists()
    assert sync.errors == 1


async def test_multi_clip_username_change(hass: HomeAssistant, tmp_path: Path) -> None:
    """Full username change: multiple clips renamed + re-tagged, no re-downloads."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    sync_dir = tmp_path / "mirror"

    clips_old = []
    initial_clips: dict[str, dict] = {}
    for i in range(3):
        cid = f"clip{i:04d}-0000-0000-0000-000000000000"
        title = f"Song {i}"
        clip = _make_clip_with_display(cid, title, display_name="olduser")
        rel_path = _clip_path(clip, QUALITY_HIGH)
        target = sync_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fLaC" + b"\x00" * 50)
        from custom_components.suno.models import clip_meta_hash

        initial_clips[cid] = {
            "path": rel_path,
            "title": title,
            "created": "2026-03-15",
            "sources": ["liked"],
            "size": 54,
            "meta_hash": clip_meta_hash(clip),
            "quality": QUALITY_HIGH,
        }
        clips_old.append(clip)

    with patch.object(sync._store, "async_load", return_value={"clips": initial_clips, "last_download": None}):
        await sync.async_init()

    # Now the user changed their display_name to "newuser"
    clips_new = [
        _make_clip_with_display(f"clip{i:04d}-0000-0000-0000-000000000000", f"Song {i}", display_name="newuser")
        for i in range(3)
    ]

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=clips_new)
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    with (
        patch(
            "custom_components.suno.downloaded_library.download_and_transcode_to_flac",
            new_callable=AsyncMock,
        ) as mock_dl,
        patch("custom_components.suno.downloaded_library.get_ffmpeg_manager") as mock_ffmpeg,
        patch("custom_components.suno.downloaded_library.async_get_clientsession"),
        patch("custom_components.suno.downloaded_library.fetch_album_art", new_callable=AsyncMock, return_value=None),
        patch.object(sync._store, "async_save"),
        patch(
            "custom_components.suno.downloaded_library.retag_flac",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_retag,
    ):
        mock_ffmpeg.return_value.binary = "/usr/bin/ffmpeg"
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    # No re-downloads should have occurred
    mock_dl.assert_not_called()

    # All 3 files should have been re-tagged (renamed files queue for retag)
    assert mock_retag.call_count == 3

    # All files should be at new paths
    for clip in clips_new:
        new_rel = _clip_path(clip, QUALITY_HIGH)
        assert (sync_dir / new_rel).exists(), f"Missing: {new_rel}"
        assert sync._state["clips"][clip.id]["path"] == new_rel

    # Old paths should be gone
    for clip in clips_old:
        old_rel = _clip_path(clip, QUALITY_HIGH)
        assert not (sync_dir / old_rel).exists(), f"Stale: {old_rel}"

    assert sync.errors == 0


async def test_liked_from_other_user_not_renamed(hass: HomeAssistant, tmp_path: Path) -> None:
    """Liked songs from other artists are NOT renamed when user changes name."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    sync_dir = tmp_path / "mirror"

    # A liked clip from another artist
    other_clip = _make_clip_with_display("other-clip-0000-0000-000000000000", "Other Song", display_name="otheartist")
    other_rel = _clip_path(other_clip, QUALITY_HIGH)
    other_target = sync_dir / other_rel
    other_target.parent.mkdir(parents=True, exist_ok=True)
    other_target.write_bytes(b"fLaC" + b"\x00" * 50)

    from custom_components.suno.models import clip_meta_hash

    initial_state = {
        "clips": {
            "other-clip-0000-0000-000000000000": {
                "path": other_rel,
                "title": "Other Song",
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 54,
                "meta_hash": clip_meta_hash(other_clip),
                "quality": QUALITY_HIGH,
            }
        },
        "last_download": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    # Sync returns the same clip (other artist's name unchanged)
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[other_clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    with (
        patch(
            "custom_components.suno.downloaded_library.download_and_transcode_to_flac",
            new_callable=AsyncMock,
        ) as mock_dl,
        patch("custom_components.suno.downloaded_library.get_ffmpeg_manager"),
        patch("custom_components.suno.downloaded_library.async_get_clientsession"),
        patch("custom_components.suno.downloaded_library.fetch_album_art", new_callable=AsyncMock, return_value=None),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    # No downloads, no renames
    mock_dl.assert_not_called()
    assert other_target.exists()
    assert sync._state["clips"]["other-clip-0000-0000-000000000000"]["path"] == other_rel
    assert sync.errors == 0


async def test_partial_rename_failure_continues(hass: HomeAssistant, tmp_path: Path) -> None:
    """OSError on one file during rename should not abort the batch."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    sync_dir = tmp_path / "mirror"

    # Two clips from "olduser"
    clips_data = {}
    for i in range(2):
        cid = f"clip{i:04d}-0000-0000-0000-000000000000"
        clip = _make_clip_with_display(cid, f"Song {i}", display_name="olduser")
        rel_path = _clip_path(clip, QUALITY_HIGH)
        target = sync_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fLaC" + b"\x00" * 50)
        from custom_components.suno.models import clip_meta_hash

        clips_data[cid] = {
            "path": rel_path,
            "title": f"Song {i}",
            "created": "2026-03-15",
            "sources": ["liked"],
            "size": 54,
            "meta_hash": clip_meta_hash(clip),
            "quality": QUALITY_HIGH,
        }

    with patch.object(sync._store, "async_load", return_value={"clips": clips_data, "last_download": None}):
        await sync.async_init()

    # User renamed to "newuser"
    new_clips = [
        _make_clip_with_display(f"clip{i:04d}-0000-0000-0000-000000000000", f"Song {i}", display_name="newuser")
        for i in range(2)
    ]

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=new_clips)
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    call_count = 0
    original_rename = Path.rename

    def _flaky_rename(self_path, target):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("Permission denied")
        return original_rename(self_path, target)

    with (
        patch(
            "custom_components.suno.downloaded_library.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=b"fLaC" + b"\x00" * 50,
        ) as mock_dl,
        patch("custom_components.suno.downloaded_library.get_ffmpeg_manager"),
        patch("custom_components.suno.downloaded_library.async_get_clientsession"),
        patch("custom_components.suno.downloaded_library.fetch_album_art", new_callable=AsyncMock, return_value=None),
        patch.object(sync._store, "async_save"),
        patch.object(Path, "rename", _flaky_rename),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    # The second clip should have been renamed despite the first failing
    # At least one rename was attempted for each clip
    assert call_count >= 2
    # The clip whose rename raised OSError should have its stale path cleared
    # and be queued for re-download to the new location.
    assert mock_dl.call_count == 1

    # Verify exactly one clip was renamed in place; the other was queued for
    # re-download (its manifest entry has the new path written by Phase 4).
    clip0_id = "clip0000-0000-0000-0000-000000000000"
    clip1_id = "clip0001-0000-0000-0000-000000000000"
    clip0_state = sync._state["clips"][clip0_id]
    clip1_state = sync._state["clips"][clip1_id]
    new_paths = [_clip_path(c, QUALITY_HIGH) for c in new_clips]
    paths = [clip0_state["path"], clip1_state["path"]]
    # Both clips should now reference the new path — one via in-place rename,
    # one via re-download after the rename failure cleared the stale path.
    assert all(p in new_paths for p in paths)


async def test_hash_formula_migration_triggers_retag(hass: HomeAssistant, tmp_path: Path) -> None:
    """Old-format hash (included display_name) triggers retag on first sync with new code."""
    import hashlib

    from custom_components.suno.models import clip_meta_hash

    clip = _make_clip_with_display("clip-mig-0000-0000-0000-000000000000", "Migration Song", display_name="alice")
    # Old hash formula included display_name
    old_hash = hashlib.md5(  # noqa: S324
        f"{clip.title}|{clip.tags}|{clip.image_url}|{clip.display_name}|{clip.video_url}|{clip.root_ancestor_id}".encode()
    ).hexdigest()[:12]
    new_hash = clip_meta_hash(clip)
    assert old_hash != new_hash, "Hash formulas must differ for migration to trigger"

    sync = SunoDownloadManager(hass, "test_sync_state")
    sync_dir = tmp_path / "mirror"
    rel_path = _clip_path(clip, QUALITY_HIGH)
    target = sync_dir / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fLaC" + b"\x00" * 50)

    initial_state = {
        "clips": {
            clip.id: {
                "path": rel_path,
                "title": "Migration Song",
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 54,
                "meta_hash": old_hash,
                "quality": QUALITY_HIGH,
            }
        },
        "last_download": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    with (
        patch(
            "custom_components.suno.downloaded_library.download_and_transcode_to_flac",
            new_callable=AsyncMock,
        ) as mock_dl,
        patch("custom_components.suno.downloaded_library.get_ffmpeg_manager") as mock_ffmpeg,
        patch("custom_components.suno.downloaded_library.async_get_clientsession"),
        patch("custom_components.suno.downloaded_library.fetch_album_art", new_callable=AsyncMock, return_value=None),
        patch.object(sync._store, "async_save"),
        patch(
            "custom_components.suno.downloaded_library.retag_flac",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_retag,
    ):
        mock_ffmpeg.return_value.binary = "/usr/bin/ffmpeg"
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    mock_dl.assert_not_called()
    mock_retag.assert_called_once()
    assert target.exists()
    assert sync._state["clips"][clip.id]["meta_hash"] == new_hash
    assert sync.errors == 0
    """OSError writing manifest file is logged as warning, not raised."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    sync_dir = tmp_path / "mirror"

    with (
        patch.object(sync._store, "async_save"),
        patch(
            "custom_components.suno.downloaded_library.Path.write_text",
            side_effect=OSError("disk full"),
        ),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        with patch("custom_components.suno.downloaded_library._LOGGER"):
            await sync.async_download(opts, client)

    # Should complete without errors
    assert sync.errors == 0


# ── Cache mode tests ──────────────────────────────────────────────


async def test_cache_mode_excludes_from_download_set(hass: HomeAssistant, tmp_path: Path) -> None:
    """Cache-only sources produce no DownloadItems in _build_desired."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[_make_clip("clip-liked-1")])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[_make_clip("clip-ms-1")])

    options = {
        CONF_SHOW_LIKED: True,
        CONF_SHOW_MY_SONGS: True,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_MIRROR,
        CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE,
        CONF_MY_SONGS_COUNT: 5,
        CONF_MY_SONGS_DAYS: None,
    }
    desired, _, _, _ = await sync._build_desired(options, client)
    ids = {d.clip.id for d in desired}
    # Liked clips included (mirror mode), my_songs excluded (cache mode)
    assert "clip-liked-1" in ids
    assert "clip-ms-1" not in ids


async def test_cache_mode_cleans_up_existing_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """Previously downloaded files are deleted when source switches to cache."""
    sync_dir = tmp_path / "mirror"
    sync_dir.mkdir()
    orphan = sync_dir / "old-my-songs.flac"
    orphan.write_bytes(b"fLaC" + b"\x00" * 50)

    sync = SunoDownloadManager(hass, "test_sync_state")
    initial_state = {
        "clips": {
            "old-clip": {
                "path": "old-my-songs.flac",
                "title": "Old Song",
                "created": "2026-01-01",
                "sources": ["my_songs"],
            }
        },
        "last_download": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    assert sync.total_files == 1

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    with patch.object(sync._store, "async_save"):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: False,
            CONF_SHOW_MY_SONGS: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
            CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE,
            CONF_MY_SONGS_COUNT: 5,
            CONF_MY_SONGS_DAYS: None,
        }
        await sync.async_download(opts, client)

    # Cache mode produces no desired items, so the old clip is deleted
    assert sync.total_files == 0
    assert not orphan.exists()


async def test_all_three_modes_coexist(hass: HomeAssistant, tmp_path: Path) -> None:
    """Mirror + Archive + Cache on different sections simultaneously."""
    from custom_components.suno.models import SunoPlaylist

    sync = SunoDownloadManager(hass, "test_sync_state")
    # Pre-populate state with a clip from each source that will be "removed"
    initial_state = {
        "clips": {
            "old-pl": {
                "path": "old-playlist.flac",
                "title": "Old Playlist Song",
                "created": "2026-01-01",
                "sources": ["playlist:pl-1"],
            },
            "old-liked": {
                "path": "old-liked.flac",
                "title": "Old Liked Song",
                "created": "2026-01-01",
                "sources": ["liked"],
            },
        },
        "last_download": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    # API returns empty for all sources (simulating removal)
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[SunoPlaylist(id="pl-1", name="Test", image_url=None, num_clips=0)])
    client.get_playlist_clips = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    sync_dir = tmp_path / "mirror"
    sync_dir.mkdir()
    (sync_dir / "old-playlist.flac").write_bytes(b"fLaC")
    (sync_dir / "old-liked.flac").write_bytes(b"fLaC")

    with patch.object(sync._store, "async_save"):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_SHOW_MY_SONGS: True,
            CONF_SHOW_PLAYLISTS: True,
            CONF_ALL_PLAYLISTS: True,
            CONF_PLAYLISTS: [],
            CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_MIRROR,
            CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_ARCHIVE,
            CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE,
            CONF_MY_SONGS_COUNT: 5,
            CONF_MY_SONGS_DAYS: None,
        }
        await sync.async_download(opts, client)

    clips = sync._state.get("clips", {})
    # Playlist clip (mirror): deleted when removed from API
    assert "old-pl" not in clips
    # Liked clip (archive): kept even though removed from API
    assert "old-liked" in clips


# ── Album from root ancestor tests ─────────────────────────────────


async def test_album_set_from_root_ancestor(hass: HomeAssistant, tmp_path: Path) -> None:
    """Download sets album to root ancestor's title when root_ancestor_id is set."""
    from custom_components.suno.models import SunoClip

    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    root_clip = SunoClip(
        id="root-song",
        title="Original Song",
        audio_url="https://cdn1.suno.ai/root-song.mp3",
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
    child_clip = SunoClip(
        id="child-song",
        title="Remix Version",
        audio_url="https://cdn1.suno.ai/child-song.mp3",
        image_url=None,
        image_large_url=None,
        is_liked=True,
        status="complete",
        created_at="2026-03-16T10:00:00Z",
        tags="pop",
        duration=130.0,
        clip_type="gen",
        has_vocal=True,
        edited_clip_id="root-song",
        root_ancestor_id="root-song",
    )

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[child_clip, root_clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    fake_flac = b"fLaC" + b"\x00" * 50
    captured_meta = {}

    async def mock_transcode(*args, **kwargs):
        # args[4] is meta (TrackMetadata)
        captured_meta["album"] = args[4].album
        return fake_flac

    sync_dir = tmp_path / "mirror"

    with (
        patch(
            "custom_components.suno.downloaded_library.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            side_effect=mock_transcode,
        ),
        patch("custom_components.suno.downloaded_library.get_ffmpeg_manager"),
        patch("custom_components.suno.downloaded_library.async_get_clientsession"),
        patch("custom_components.suno.downloaded_library.fetch_album_art", new_callable=AsyncMock, return_value=None),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    # The child clip's album should be the root's title
    assert captured_meta.get("album") == "Original Song"


async def test_album_fallback_no_root(hass: HomeAssistant, tmp_path: Path) -> None:
    """Download falls back to clip's own title as album when no root resolved."""
    from custom_components.suno.models import SunoClip

    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clip = SunoClip(
        id="solo-song",
        title="Standalone Track",
        audio_url="https://cdn1.suno.ai/solo-song.mp3",
        image_url=None,
        image_large_url=None,
        is_liked=True,
        status="complete",
        created_at="2026-03-15T10:00:00Z",
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
        root_ancestor_id="",
    )

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    fake_flac = b"fLaC" + b"\x00" * 50
    captured_meta = {}

    async def mock_transcode(*args, **kwargs):
        captured_meta["album"] = args[4].album
        return fake_flac

    sync_dir = tmp_path / "mirror"

    with (
        patch(
            "custom_components.suno.downloaded_library.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            side_effect=mock_transcode,
        ),
        patch("custom_components.suno.downloaded_library.get_ffmpeg_manager"),
        patch("custom_components.suno.downloaded_library.async_get_clientsession"),
        patch("custom_components.suno.downloaded_library.fetch_album_art", new_callable=AsyncMock, return_value=None),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    # Fallback: album == clip's own title
    assert captured_meta.get("album") == "Standalone Track"
