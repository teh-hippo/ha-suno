"""Tests for the Suno sync module."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.suno.const import (
    CONF_SYNC_ALL_PLAYLISTS,
    CONF_SYNC_ENABLED,
    CONF_SYNC_LIKED,
    CONF_SYNC_PATH,
    CONF_SYNC_PLAYLISTS,
)
from custom_components.suno.sync import (
    SunoSync,
    _clip_path,
    _purge_trash,
    _restore_from_trash,
    _sanitise_filename,
    _trash_file,
    _write_file,
    _write_m3u8_playlists,
)

# ── Filename sanitisation ───────────────────────────────────────────


class TestSanitiseFilename:
    def test_removes_unsafe_chars(self) -> None:
        assert _sanitise_filename('test<>:"/\\|?*file') == "test_________file"

    def test_strips_dots_and_spaces(self) -> None:
        assert _sanitise_filename("  ..hello.. ") == "hello"

    def test_truncates_long_names(self) -> None:
        result = _sanitise_filename("a" * 300)
        assert len(result) == 200

    def test_empty_string_returns_untitled(self) -> None:
        assert _sanitise_filename("") == "untitled"

    def test_unicode_preserved(self) -> None:
        assert _sanitise_filename("日本語タイトル") == "日本語タイトル"


# ── Clip path generation ───────────────────────────────────────────


class TestClipPath:
    def _make_clip(self, title: str = "My Song", created: str = "2026-03-15T10:00:00Z"):
        clip = MagicMock()
        clip.title = title
        clip.created_at = created
        return clip

    def test_date_organisation(self) -> None:
        clip = self._make_clip()
        result = _clip_path(clip, 0)
        assert result == "2026-03-15/01 - My Song.flac"

    def test_index_numbering(self) -> None:
        clip = self._make_clip()
        result = _clip_path(clip, 2)
        assert result == "2026-03-15/03 - My Song.flac"

    def test_missing_created_date(self) -> None:
        clip = self._make_clip(created=None)
        result = _clip_path(clip, 0)
        assert result == "unknown/01 - My Song.flac"


# ── Sync state management ──────────────────────────────────────────


async def test_sync_init_loads_state(hass: HomeAssistant) -> None:
    """async_init should load persisted state."""
    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value={"clips": {"abc": {}}, "last_sync": "2026-01-01"}):
        await sync.async_init()
    assert sync.total_files == 1
    assert sync.last_sync == "2026-01-01"


async def test_sync_init_handles_empty_store(hass: HomeAssistant) -> None:
    """async_init with empty store should use defaults."""
    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()
    assert sync.total_files == 0
    assert sync.last_sync is None


async def test_sync_skips_when_disabled(hass: HomeAssistant) -> None:
    """Sync should do nothing when disabled."""
    sync = SunoSync(hass, "test_sync_state")
    client = AsyncMock()
    await sync.async_sync({CONF_SYNC_ENABLED: False}, client)
    client.get_liked_songs.assert_not_called()


async def test_sync_skips_when_no_path(hass: HomeAssistant) -> None:
    """Sync should skip when path is empty."""
    sync = SunoSync(hass, "test_sync_state")
    client = AsyncMock()
    await sync.async_sync({CONF_SYNC_ENABLED: True, CONF_SYNC_PATH: ""}, client)
    client.get_liked_songs.assert_not_called()


async def test_sync_skips_when_already_running(hass: HomeAssistant) -> None:
    """Sync should not run concurrently."""
    sync = SunoSync(hass, "test_sync_state")
    sync._running = True
    client = AsyncMock()
    await sync.async_sync({CONF_SYNC_ENABLED: True, CONF_SYNC_PATH: "/safe/path"}, client)  # noqa: S108
    client.get_liked_songs.assert_not_called()


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


async def test_sync_downloads_new_clips(hass: HomeAssistant, tmp_path: Path) -> None:
    """Sync should download clips not in state."""
    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[_make_clip("clip-1", "Test Song")])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])
    client.get_wav_url = AsyncMock(return_value="https://cdn1.suno.ai/clip-1.wav")
    client.request_wav = AsyncMock()

    fake_wav = b"RIFF" + b"\x00" * 100
    fake_flac = b"fLaC" + b"\x00" * 50

    with (
        patch("custom_components.suno.sync.wav_to_flac", new_callable=AsyncMock, return_value=fake_flac),
        patch("custom_components.suno.sync.get_ffmpeg_manager") as mock_ffmpeg,
        patch("custom_components.suno.sync.async_get_clientsession") as mock_session,
        patch.object(sync._store, "async_save"),
    ):
        mock_ffmpeg.return_value.binary = "ffmpeg"
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=fake_wav)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value.get = MagicMock(return_value=mock_resp)

        opts = {
            CONF_SYNC_ENABLED: True,
            CONF_SYNC_PATH: str(tmp_path / "sync"),
            CONF_SYNC_LIKED: True,
            CONF_SYNC_ALL_PLAYLISTS: False,
            CONF_SYNC_PLAYLISTS: [],
        }
        await sync.async_sync(opts, client)

    assert sync.total_files == 1
    assert sync.errors == 0


async def test_sync_deletes_orphaned_clips(hass: HomeAssistant, tmp_path: Path) -> None:
    """Sync should delete files that are no longer in desired set."""
    sync_dir = tmp_path / "sync"
    sync_dir.mkdir()
    orphan = sync_dir / "old-file.flac"
    orphan.write_bytes(b"fLaC" + b"\x00" * 50)

    sync = SunoSync(hass, "test_sync_state")
    initial_state = {
        "clips": {
            "orphan-id": {
                "path": "old-file.flac",
                "title": "Old Song",
                "created": "2026-01-01",
                "sources": ["liked"],
            }
        },
        "last_sync": None,
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
            CONF_SYNC_ENABLED: True,
            CONF_SYNC_PATH: str(sync_dir),
            CONF_SYNC_LIKED: True,
            CONF_SYNC_ALL_PLAYLISTS: False,
            CONF_SYNC_PLAYLISTS: [],
        }
        await sync.async_sync(opts, client)

    assert sync.total_files == 0
    assert not orphan.exists()


async def test_sync_writes_manifest(hass: HomeAssistant, tmp_path: Path) -> None:
    """Sync should write .suno_sync.json manifest."""
    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    sync_dir = tmp_path / "sync"
    with patch.object(sync._store, "async_save"):
        await sync.async_sync(
            {
                CONF_SYNC_ENABLED: True,
                CONF_SYNC_PATH: str(sync_dir),
                CONF_SYNC_LIKED: True,
                CONF_SYNC_ALL_PLAYLISTS: False,
                CONF_SYNC_PLAYLISTS: [],
            },
            client,
        )

    manifest = sync_dir / ".suno_sync.json"
    assert manifest.exists()
    data = json.loads(manifest.read_text())
    assert "last_sync" in data
    assert "clips" in data


async def test_cleanup_tmp_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """cleanup_tmp_files should remove .tmp files."""
    sync_dir = tmp_path / "sync"
    sync_dir.mkdir()
    (sync_dir / "song.flac.tmp").write_bytes(b"partial")
    (sync_dir / "real.flac").write_bytes(b"fLaC")

    sync = SunoSync(hass, "test_sync_state")
    await sync.cleanup_tmp_files(str(sync_dir))

    assert not (sync_dir / "song.flac.tmp").exists()
    assert (sync_dir / "real.flac").exists()


# ── Properties ──────────────────────────────────────────────────────


async def test_sync_properties(hass: HomeAssistant) -> None:
    """Properties should reflect current state."""
    sync = SunoSync(hass, "test_sync_state")
    assert sync.is_running is False
    assert sync.total_files == 0
    assert sync.pending == 0
    assert sync.errors == 0
    assert sync.last_sync is None


# ── _write_file ────────────────────────────────────────────────────


async def test_write_file_creates_file(hass: HomeAssistant, tmp_path: Path) -> None:
    """Atomic write creates the target file with correct data."""
    target = tmp_path / "subdir" / "output.flac"
    data = b"fLaC" + b"\x00" * 50

    await _write_file(hass, target, data)

    assert target.exists()
    assert target.read_bytes() == data
    # No .tmp file should remain
    assert not target.with_suffix(".tmp").exists()


async def test_write_file_failure_cleans_tmp(hass: HomeAssistant, tmp_path: Path) -> None:
    """Write failure removes the .tmp file."""
    target = tmp_path / "output.flac"

    with patch.object(Path, "write_bytes", side_effect=OSError("disk full")):
        try:
            await _write_file(hass, target, b"data")
        except OSError:
            pass

    assert not target.with_suffix(".tmp").exists()
    assert not target.exists()


# ── _trash_file ────────────────────────────────────────────────────


async def test_trash_file_moves_to_trash(hass: HomeAssistant, tmp_path: Path) -> None:
    """Trashing moves the file to .trash/ and updates state."""
    (tmp_path / "2026-03-15").mkdir()
    source = tmp_path / "2026-03-15" / "song.flac"
    source.write_bytes(b"fLaC" + b"\x00" * 20)

    entry = {"path": "2026-03-15/song.flac", "title": "Song"}
    trash_state: dict = {}

    await _trash_file(hass, tmp_path, "clip-1", entry, trash_state)

    assert not source.exists()
    assert (tmp_path / ".trash" / "song.flac").exists()
    assert "clip-1" in trash_state
    assert trash_state["clip-1"]["original_path"] == "2026-03-15/song.flac"


# ── _restore_from_trash ───────────────────────────────────────────


async def test_restore_from_trash(hass: HomeAssistant, tmp_path: Path) -> None:
    """Restoring moves file from .trash/ back to original path."""
    trash_dir = tmp_path / ".trash"
    trash_dir.mkdir()
    (trash_dir / "song.flac").write_bytes(b"fLaC" + b"\x00" * 20)

    trash_state = {
        "clip-1": {
            "path": ".trash/song.flac",
            "original_path": "2026-03-15/song.flac",
            "trashed_at": datetime.now(tz=UTC).isoformat(),
            "title": "Song",
        }
    }

    result = await _restore_from_trash(hass, tmp_path, "clip-1", trash_state)

    assert result is not None
    assert result["path"] == "2026-03-15/song.flac"
    assert (tmp_path / "2026-03-15" / "song.flac").exists()
    assert "clip-1" not in trash_state


async def test_restore_from_trash_not_in_trash(hass: HomeAssistant, tmp_path: Path) -> None:
    """Returns None when clip is not in trash state."""
    trash_state: dict = {}
    result = await _restore_from_trash(hass, tmp_path, "clip-1", trash_state)
    assert result is None


# ── _purge_trash ───────────────────────────────────────────────────


async def test_purge_trash_removes_old_entries(hass: HomeAssistant, tmp_path: Path) -> None:
    """Purges trash entries older than max_days."""
    trash_dir = tmp_path / ".trash"
    trash_dir.mkdir()
    (trash_dir / "old.flac").write_bytes(b"fLaC")

    old_time = (datetime.now(tz=UTC) - timedelta(days=10)).isoformat()
    trash_state = {
        "old-clip": {
            "path": ".trash/old.flac",
            "original_path": "old.flac",
            "trashed_at": old_time,
        }
    }

    await _purge_trash(hass, tmp_path, trash_state, max_days=7)

    assert "old-clip" not in trash_state
    assert not (trash_dir / "old.flac").exists()


async def test_purge_trash_keeps_recent(hass: HomeAssistant, tmp_path: Path) -> None:
    """Keeps trash entries newer than max_days."""
    recent_time = (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()
    trash_state = {
        "new-clip": {
            "path": ".trash/new.flac",
            "original_path": "new.flac",
            "trashed_at": recent_time,
        }
    }

    await _purge_trash(hass, tmp_path, trash_state, max_days=7)

    assert "new-clip" in trash_state


async def test_purge_trash_invalid_date(hass: HomeAssistant, tmp_path: Path) -> None:
    """Entries with invalid dates are purged."""
    trash_state = {
        "bad-clip": {
            "path": ".trash/bad.flac",
            "original_path": "bad.flac",
            "trashed_at": "not-a-date",
        }
    }

    await _purge_trash(hass, tmp_path, trash_state, max_days=7)

    assert "bad-clip" not in trash_state


# ── _build_desired with API failure ────────────────────────────────


async def test_build_desired_preserves_on_api_failure(hass: HomeAssistant) -> None:
    """Clips from failed API calls are preserved via preserved_ids."""
    sync = SunoSync(hass, "test_sync_state")
    sync._state = {
        "clips": {
            "clip-liked": {"path": "liked.flac", "sources": ["liked"]},
            "clip-recent": {"path": "recent.flac", "sources": ["recent"]},
        },
        "last_sync": None,
    }

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(side_effect=Exception("API down"))
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    from custom_components.suno.const import CONF_SYNC_RECENT_COUNT, CONF_SYNC_RECENT_DAYS

    options = {
        CONF_SYNC_LIKED: True,
        CONF_SYNC_ALL_PLAYLISTS: False,
        CONF_SYNC_PLAYLISTS: [],
        CONF_SYNC_RECENT_COUNT: None,
        CONF_SYNC_RECENT_DAYS: None,
    }

    desired, preserved = await sync._build_desired(options, client)

    # clip-liked should be preserved since liked API failed
    assert "clip-liked" in preserved


# ── M3U8 playlist writing ──────────────────────────────────────────


class TestWriteM3u8Playlists:
    def _make_clip(self, clip_id: str = "clip1", title: str = "Test Song", duration: float = 120.5):
        clip = MagicMock()
        clip.id = clip_id
        clip.title = title
        clip.duration = duration
        return clip

    def test_writes_absolute_paths(self, tmp_path: Path) -> None:
        """Playlist entries must use absolute paths for Jellyfin compatibility."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "2026-03-15/01 - Test Song.flac", "title": "Test Song"}}
        desired = [(clip, 0, "Liked Songs", ["liked"])]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        assert "./" not in content
        assert str(tmp_path / "2026-03-15/01 - Test Song.flac") in content

    def test_uses_clip_duration(self, tmp_path: Path) -> None:
        """Duration in #EXTINF should come from clip metadata, not hardcoded -1."""
        clip = self._make_clip(duration=95.7)
        clips_state = {"clip1": {"path": "2026-03-15/01 - Test Song.flac", "title": "Test Song"}}
        desired = [(clip, 0, "Liked Songs", ["liked"])]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        assert "#EXTINF:95," in content

    def test_duration_fallback_when_zero(self, tmp_path: Path) -> None:
        """Duration falls back to -1 when clip has no duration."""
        clip = self._make_clip(duration=0)
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [(clip, 0, "Liked Songs", ["liked"])]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        assert "#EXTINF:-1," in content

    def test_header_format(self, tmp_path: Path) -> None:
        """M3U8 files must start with #EXTM3U and include #PLAYLIST tag."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [(clip, 0, "My Playlist", ["playlist:pl1"])]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        content = (tmp_path / "My Playlist.m3u8").read_text(encoding="utf-8")
        assert content.startswith("#EXTM3U\n")
        assert "#PLAYLIST:My Playlist\n" in content

    def test_liked_and_playlist_sources(self, tmp_path: Path) -> None:
        """Clips with both liked and playlist sources appear in both M3U8 files."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [(clip, 0, "Favourites", ["liked", "playlist:pl1"])]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        assert (tmp_path / "Liked Songs.m3u8").exists()
        assert (tmp_path / "Favourites.m3u8").exists()

    def test_cleans_stale_m3u8(self, tmp_path: Path) -> None:
        """Stale M3U8 files from previous runs are removed."""
        stale = tmp_path / "Old Playlist.m3u8"
        stale.write_text("#EXTM3U\n", encoding="utf-8")

        _write_m3u8_playlists(tmp_path, {}, [])

        assert not stale.exists()

    def test_skips_clips_without_path(self, tmp_path: Path) -> None:
        """Clips missing a path in state are excluded from playlists."""
        clip = self._make_clip()
        clips_state = {"clip1": {"title": "Song"}}  # no "path" key
        desired = [(clip, 0, "Liked Songs", ["liked"])]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        # No M3U8 written since the only clip had no path
        assert not list(tmp_path.glob("*.m3u8"))
