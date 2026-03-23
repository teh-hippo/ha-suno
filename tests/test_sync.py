"""Tests for the Suno sync module."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.suno.const import (
    CONF_SYNC_ALL_PLAYLISTS,
    CONF_SYNC_ENABLED,
    CONF_SYNC_LIKED,
    CONF_SYNC_MODE_LATEST,
    CONF_SYNC_MODE_LIKED,
    CONF_SYNC_MODE_PLAYLISTS,
    CONF_SYNC_PATH,
    CONF_SYNC_PLAYLISTS,
    QUALITY_HIGH,
    QUALITY_STANDARD,
    SYNC_MODE_COPY,
    SYNC_MODE_SYNC,
)
from custom_components.suno.sync import (
    SunoSync,
    SyncItem,
    _add_clip,
    _build_sync_summary,
    _clip_path,
    _sanitise_filename,
    _source_uses_sync_mode,
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
    def _make_clip(
        self,
        title: str = "My Song",
        created: str = "2026-01-15T10:00:00Z",
        clip_id: str = "abcd1234-test-clip-id",
    ):
        clip = MagicMock()
        clip.id = clip_id
        clip.title = title
        clip.created_at = created
        return clip

    def test_high_quality_flac(self) -> None:
        clip = self._make_clip()
        result = _clip_path(clip, "high")
        assert result == "2026-01-15/My Song [abcd1234].flac"

    def test_standard_quality_mp3(self) -> None:
        clip = self._make_clip()
        result = _clip_path(clip, "standard")
        assert result == "2026-01-15/My Song [abcd1234].mp3"

    def test_missing_created_date(self) -> None:
        clip = self._make_clip(created=None)
        result = _clip_path(clip, "high")
        assert result == "unknown/My Song [abcd1234].flac"

    def test_different_clips_same_title_get_different_paths(self) -> None:
        clip_a = self._make_clip(clip_id="aaaaaaaa-1111-2222-3333-444444444444")
        clip_b = self._make_clip(clip_id="bbbbbbbb-1111-2222-3333-444444444444")
        assert _clip_path(clip_a, "high") != _clip_path(clip_b, "high")


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

    fake_flac = b"fLaC" + b"\x00" * 50

    with (
        patch(
            "custom_components.suno.sync.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ),
        patch("custom_components.suno.sync.get_ffmpeg_manager"),
        patch("custom_components.suno.sync.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
    ):
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


# ── source_breakdown ──────────────────────────────────────────────


async def test_source_breakdown_empty(hass: HomeAssistant) -> None:
    """Empty state returns empty breakdown."""
    sync = SunoSync(hass, "test_sync")
    assert sync.source_breakdown == {}


async def test_source_breakdown_counts_sources(hass: HomeAssistant) -> None:
    """Counts clips per source tag."""
    sync = SunoSync(hass, "test_sync")
    sync._state = {
        "clips": {
            "c1": {"sources": ["liked"]},
            "c2": {"sources": ["liked", "playlist:abc"]},
            "c3": {"sources": ["latest"]},
            "c4": {"sources": ["playlist:abc"]},
        },
        "last_sync": None,
    }
    breakdown = sync.source_breakdown
    assert breakdown["liked"] == 2
    assert breakdown["playlist:abc"] == 2
    assert breakdown["latest"] == 1


# ── _build_desired with API failure ────────────────────────────────


async def test_build_desired_preserves_on_api_failure(hass: HomeAssistant) -> None:
    """Clips from failed API calls are preserved via preserved_ids."""
    sync = SunoSync(hass, "test_sync_state")
    sync._state = {
        "clips": {
            "clip-liked": {"path": "liked.flac", "sources": ["liked"]},
            "clip-latest": {"path": "latest.flac", "sources": ["latest"]},
        },
        "last_sync": None,
    }

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(side_effect=Exception("API down"))
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    from custom_components.suno.const import CONF_SYNC_LATEST_COUNT, CONF_SYNC_LATEST_DAYS

    options = {
        CONF_SYNC_LIKED: True,
        CONF_SYNC_ALL_PLAYLISTS: False,
        CONF_SYNC_PLAYLISTS: [],
        CONF_SYNC_LATEST_COUNT: None,
        CONF_SYNC_LATEST_DAYS: None,
    }

    desired, preserved = await sync._build_desired(options, client)

    # clip-liked should be preserved since liked API failed
    assert "clip-liked" in preserved


# ── Latest AND logic ───────────────────────────────────────────────


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


async def test_latest_count_only(hass: HomeAssistant) -> None:
    """count=5, days=0 returns top 5 clips."""
    from custom_components.suno.const import CONF_SYNC_LATEST_COUNT, CONF_SYNC_LATEST_DAYS

    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clips = [_make_dated_clip(f"clip-{i}", created="2026-03-15T10:00:00Z") for i in range(10)]
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=clips)

    options = {
        CONF_SYNC_LIKED: False,
        CONF_SYNC_ALL_PLAYLISTS: False,
        CONF_SYNC_PLAYLISTS: [],
        CONF_SYNC_LATEST_COUNT: 5,
        CONF_SYNC_LATEST_DAYS: None,
    }
    desired, _ = await sync._build_desired(options, client)
    assert len(desired) == 5
    ids = {d.clip.id for d in desired}
    assert ids == {f"clip-{i}" for i in range(5)}


async def test_latest_days_only(hass: HomeAssistant) -> None:
    """count=0, days=7 returns all within 7 days."""
    from datetime import timedelta

    from custom_components.suno.const import CONF_SYNC_LATEST_COUNT, CONF_SYNC_LATEST_DAYS

    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    now = datetime.now(tz=UTC)
    recent_ts = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_ts = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    clips = [
        _make_dated_clip("clip-new-1", created=recent_ts),
        _make_dated_clip("clip-new-2", created=recent_ts),
        _make_dated_clip("clip-old", created=old_ts),
    ]
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=clips)

    options = {
        CONF_SYNC_LIKED: False,
        CONF_SYNC_ALL_PLAYLISTS: False,
        CONF_SYNC_PLAYLISTS: [],
        CONF_SYNC_LATEST_COUNT: None,
        CONF_SYNC_LATEST_DAYS: 7,
    }
    desired, _ = await sync._build_desired(options, client)
    ids = {d.clip.id for d in desired}
    assert "clip-new-1" in ids
    assert "clip-new-2" in ids
    assert "clip-old" not in ids


async def test_latest_both_and(hass: HomeAssistant) -> None:
    """count=3, days=7 returns at most 3 within 7 days (intersection)."""
    from datetime import timedelta

    from custom_components.suno.const import CONF_SYNC_LATEST_COUNT, CONF_SYNC_LATEST_DAYS

    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    now = datetime.now(tz=UTC)
    recent_ts = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_ts = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # 5 recent clips + 2 old clips; count=3 takes top 3, days=7 takes recent only
    clips = [
        _make_dated_clip("clip-r0", created=recent_ts),
        _make_dated_clip("clip-r1", created=recent_ts),
        _make_dated_clip("clip-r2", created=recent_ts),
        _make_dated_clip("clip-r3", created=recent_ts),
        _make_dated_clip("clip-r4", created=recent_ts),
        _make_dated_clip("clip-old-0", created=old_ts),
        _make_dated_clip("clip-old-1", created=old_ts),
    ]
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=clips)

    options = {
        CONF_SYNC_LIKED: False,
        CONF_SYNC_ALL_PLAYLISTS: False,
        CONF_SYNC_PLAYLISTS: [],
        CONF_SYNC_LATEST_COUNT: 3,
        CONF_SYNC_LATEST_DAYS: 7,
    }
    desired, _ = await sync._build_desired(options, client)
    ids = {d.clip.id for d in desired}
    # count=3 gives {clip-r0, clip-r1, clip-r2}, days=7 gives all clip-r*, intersection = {clip-r0, clip-r1, clip-r2}
    assert len(ids) == 3
    assert ids == {"clip-r0", "clip-r1", "clip-r2"}
    assert "clip-old-0" not in ids


async def test_latest_both_zero_disabled(hass: HomeAssistant) -> None:
    """count=0, days=0 means latest is disabled."""
    from custom_components.suno.const import CONF_SYNC_LATEST_COUNT, CONF_SYNC_LATEST_DAYS

    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[_make_dated_clip("clip-1")])

    options = {
        CONF_SYNC_LIKED: False,
        CONF_SYNC_ALL_PLAYLISTS: False,
        CONF_SYNC_PLAYLISTS: [],
        CONF_SYNC_LATEST_COUNT: None,
        CONF_SYNC_LATEST_DAYS: None,
    }
    desired, _ = await sync._build_desired(options, client)
    assert len(desired) == 0
    client.get_all_songs.assert_not_called()


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
        clips_state = {"clip1": {"path": "2026-03-15/Test Song [clip1].flac", "title": "Test Song"}}
        desired = [SyncItem(clip=clip, collection="Liked Songs", sources=["liked"], quality=QUALITY_HIGH)]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        assert "./" not in content
        assert str(tmp_path / "2026-03-15/Test Song [clip1].flac") in content

    def test_uses_clip_duration(self, tmp_path: Path) -> None:
        """Duration in #EXTINF should come from clip metadata, not hardcoded -1."""
        clip = self._make_clip(duration=95.7)
        clips_state = {"clip1": {"path": "2026-03-15/Test Song [clip1].flac", "title": "Test Song"}}
        desired = [SyncItem(clip=clip, collection="Liked Songs", sources=["liked"], quality=QUALITY_HIGH)]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        assert "#EXTINF:95," in content

    def test_duration_fallback_when_zero(self, tmp_path: Path) -> None:
        """Duration falls back to -1 when clip has no duration."""
        clip = self._make_clip(duration=0)
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [SyncItem(clip=clip, collection="Liked Songs", sources=["liked"], quality=QUALITY_HIGH)]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        assert "#EXTINF:-1," in content

    def test_header_format(self, tmp_path: Path) -> None:
        """M3U8 files must start with #EXTM3U and include #PLAYLIST tag."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [SyncItem(clip=clip, collection="My Playlist", sources=["playlist:pl1"], quality=QUALITY_HIGH)]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        content = (tmp_path / "My Playlist.m3u8").read_text(encoding="utf-8")
        assert content.startswith("#EXTM3U\n")
        assert "#PLAYLIST:My Playlist\n" in content

    def test_liked_and_playlist_sources(self, tmp_path: Path) -> None:
        """Clips with both liked and playlist sources appear in both M3U8 files."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [
            SyncItem(clip=clip, collection="Favourites", sources=["liked", "playlist:pl1"], quality=QUALITY_HIGH)
        ]

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
        desired = [SyncItem(clip=clip, collection="Liked Songs", sources=["liked"], quality=QUALITY_HIGH)]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        # No M3U8 written since the only clip had no path
        assert not list(tmp_path.glob("*.m3u8"))


# ── Sync summary ───────────────────────────────────────────────────


# ── Quality tracking ───────────────────────────────────────────────


async def test_quality_change_triggers_redownload(hass: HomeAssistant, tmp_path: Path) -> None:
    """Quality change should delete old file and re-download."""
    clip_id = "clip0001-0000-0000-0000-000000000000"
    sync_dir = tmp_path / "sync"
    sync_dir.mkdir()
    old_file = sync_dir / "2026-03-15" / "Song [clip0001].flac"
    old_file.parent.mkdir(parents=True)
    old_file.write_bytes(b"fLaC" + b"\x00" * 50)

    sync = SunoSync(hass, "test_sync_state")
    initial_state = {
        "clips": {
            clip_id: {
                "path": "2026-03-15/Song [clip0001].flac",
                "title": "Song",
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 54,
                "meta_hash": "abc",
                "quality": "high",
            }
        },
        "last_sync": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    clip = _make_clip(clip_id, "Song")
    desired = [SyncItem(clip=clip, collection="Liked Songs", sources=["liked"], quality="standard")]
    client = AsyncMock()

    fake_mp3 = b"ID3" + b"\x00" * 50

    with (
        patch(
            "custom_components.suno.sync.download_as_mp3",
            new_callable=AsyncMock,
            return_value=fake_mp3,
        ),
        patch(
            "custom_components.suno.sync.download_and_transcode_to_flac",
            new_callable=AsyncMock,
        ) as mock_flac,
        patch("custom_components.suno.sync.get_ffmpeg_manager"),
        patch("custom_components.suno.sync.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
        patch.object(sync, "_build_desired", return_value=(desired, set())),
    ):
        opts = {
            CONF_SYNC_ENABLED: True,
            CONF_SYNC_PATH: str(sync_dir),
            CONF_SYNC_LIKED: True,
            CONF_SYNC_ALL_PLAYLISTS: False,
            CONF_SYNC_PLAYLISTS: [],
        }
        await sync.async_sync(opts, client)

    # FLAC download should NOT have been called (standard quality → MP3 path)
    mock_flac.assert_not_called()
    # Old flac file should be deleted
    assert not old_file.exists()
    # New file should exist (mp3 extension since quality=standard)
    entry = sync._state["clips"][clip_id]
    assert entry["quality"] == "standard"
    assert entry["path"].endswith(".mp3")


async def test_quality_match_skips_download(hass: HomeAssistant, tmp_path: Path) -> None:
    """Same quality should not trigger re-download."""
    sync = SunoSync(hass, "test_sync_state")
    initial_state = {
        "clips": {
            "clip0002-0000-0000-0000-000000000000": {
                "path": "2026-03-15/Song [clip0002].flac",
                "title": "Song",
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 54,
                "meta_hash": "abc",
                "quality": "high",
            }
        },
        "last_sync": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    clip = _make_clip("clip0002-0000-0000-0000-000000000000", "Song")
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    with (
        patch(
            "custom_components.suno.sync.download_and_transcode_to_flac",
            new_callable=AsyncMock,
        ) as mock_dl,
        patch("custom_components.suno.sync.get_ffmpeg_manager"),
        patch("custom_components.suno.sync.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_SYNC_ENABLED: True,
            CONF_SYNC_PATH: str(tmp_path / "sync"),
            CONF_SYNC_LIKED: True,
            CONF_SYNC_ALL_PLAYLISTS: False,
            CONF_SYNC_PLAYLISTS: [],
        }
        await sync.async_sync(opts, client)

    mock_dl.assert_not_called()


async def test_quality_stored_in_state(hass: HomeAssistant, tmp_path: Path) -> None:
    """After download, quality should be stored in clips_state."""
    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clip = _make_clip("clip0003-0000-0000-0000-000000000000", "Song")
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    fake_flac = b"fLaC" + b"\x00" * 50

    with (
        patch(
            "custom_components.suno.sync.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ),
        patch("custom_components.suno.sync.get_ffmpeg_manager"),
        patch("custom_components.suno.sync.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_SYNC_ENABLED: True,
            CONF_SYNC_PATH: str(tmp_path / "sync"),
            CONF_SYNC_LIKED: True,
            CONF_SYNC_ALL_PLAYLISTS: False,
            CONF_SYNC_PLAYLISTS: [],
        }
        await sync.async_sync(opts, client)

    clips_state = sync._state["clips"]
    entry = clips_state["clip0003-0000-0000-0000-000000000000"]
    assert entry["quality"] == QUALITY_HIGH


class TestBuildSyncSummary:
    def test_no_change(self) -> None:
        assert _build_sync_summary(0, 0, 0) == "No change"

    def test_single_new_song(self) -> None:
        assert _build_sync_summary(1, 0, 0) == "1 new song"

    def test_multiple_new_songs(self) -> None:
        assert _build_sync_summary(8, 0, 0) == "8 new songs"

    def test_single_removal(self) -> None:
        assert _build_sync_summary(0, 1, 0) == "1 removal"

    def test_multiple_removals(self) -> None:
        assert _build_sync_summary(0, 3, 0) == "3 removals"

    def test_single_metadata_update(self) -> None:
        assert _build_sync_summary(0, 0, 1) == "1 metadata update"

    def test_multiple_metadata_updates(self) -> None:
        assert _build_sync_summary(0, 0, 2) == "2 metadata updates"

    def test_combined(self) -> None:
        result = _build_sync_summary(1, 2, 1)
        assert result == "1 new song, 1 metadata update, 2 removals"

    def test_all_plural(self) -> None:
        result = _build_sync_summary(3, 4, 5)
        assert result == "3 new songs, 5 metadata updates, 4 removals"


# ── Download clip branching ────────────────────────────────────────


async def test_download_clip_flac_path(hass: HomeAssistant, tmp_path: Path) -> None:
    """quality='high' should use download_and_transcode_to_flac."""
    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clip = _make_clip("clip-flac-0000-0000-0000-000000000000", "FLAC Song")
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    fake_flac = b"fLaC" + b"\x00" * 50

    with (
        patch(
            "custom_components.suno.sync.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ) as mock_flac,
        patch(
            "custom_components.suno.sync.download_as_mp3",
            new_callable=AsyncMock,
        ) as mock_mp3,
        patch("custom_components.suno.sync.get_ffmpeg_manager"),
        patch("custom_components.suno.sync.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_SYNC_ENABLED: True,
            CONF_SYNC_PATH: str(tmp_path / "sync"),
            CONF_SYNC_LIKED: True,
            CONF_SYNC_ALL_PLAYLISTS: False,
            CONF_SYNC_PLAYLISTS: [],
        }
        await sync.async_sync(opts, client)

    mock_flac.assert_called_once()
    mock_mp3.assert_not_called()
    assert sync.total_files == 1
    assert sync.errors == 0


async def test_download_clip_mp3_path(hass: HomeAssistant, tmp_path: Path) -> None:
    """quality='standard' should use download_as_mp3, not FLAC."""
    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clip = _make_clip("clip-mp3-00000-0000-0000-000000000000", "MP3 Song")
    desired = [SyncItem(clip=clip, collection="Liked Songs", sources=["liked"], quality="standard")]
    client = AsyncMock()

    fake_mp3 = b"ID3" + b"\x00" * 50

    with (
        patch(
            "custom_components.suno.sync.download_as_mp3",
            new_callable=AsyncMock,
            return_value=fake_mp3,
        ) as mock_mp3,
        patch(
            "custom_components.suno.sync.download_and_transcode_to_flac",
            new_callable=AsyncMock,
        ) as mock_flac,
        patch("custom_components.suno.sync.get_ffmpeg_manager"),
        patch("custom_components.suno.sync.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
        patch.object(sync, "_build_desired", return_value=(desired, set())),
    ):
        opts = {
            CONF_SYNC_ENABLED: True,
            CONF_SYNC_PATH: str(tmp_path / "sync"),
            CONF_SYNC_LIKED: True,
            CONF_SYNC_ALL_PLAYLISTS: False,
            CONF_SYNC_PLAYLISTS: [],
        }
        await sync.async_sync(opts, client)

    mock_mp3.assert_called_once()
    mock_flac.assert_not_called()
    entry = sync._state["clips"]["clip-mp3-00000-0000-0000-000000000000"]
    assert entry["quality"] == "standard"
    assert entry["path"].endswith(".mp3")


async def test_download_write_through_cache(hass: HomeAssistant, tmp_path: Path) -> None:
    """After download, cache.async_put should be called."""
    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    mock_cache = AsyncMock()
    sync._cache = mock_cache

    clip = _make_clip("clip-cache-0000-0000-0000-000000000000", "Cached Song")
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    fake_flac = b"fLaC" + b"\x00" * 50

    with (
        patch(
            "custom_components.suno.sync.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ),
        patch("custom_components.suno.sync.get_ffmpeg_manager"),
        patch("custom_components.suno.sync.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_SYNC_ENABLED: True,
            CONF_SYNC_PATH: str(tmp_path / "sync"),
            CONF_SYNC_LIKED: True,
            CONF_SYNC_ALL_PLAYLISTS: False,
            CONF_SYNC_PLAYLISTS: [],
        }
        await sync.async_sync(opts, client)

    mock_cache.async_put.assert_called_once()
    call_args = mock_cache.async_put.call_args
    assert call_args[0][0] == "clip-cache-0000-0000-0000-000000000000"
    assert call_args[0][1] == "flac"
    assert call_args[0][2] == fake_flac


# ── Disk reconciliation ────────────────────────────────────────────


async def test_reconcile_removes_orphan_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """Orphan .flac files not in clips_state are deleted."""
    sync = SunoSync(hass, "test_sync")
    orphan = tmp_path / "2026-01-01" / "Orphan [deadbeef].flac"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"fake")

    removed = await sync._reconcile_disk(tmp_path, {})
    assert removed == 1
    assert not orphan.exists()


async def test_reconcile_keeps_tracked_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """Files referenced in clips_state are not deleted."""
    sync = SunoSync(hass, "test_sync")
    rel = "2026-01-01/Tracked [abcd1234].flac"
    tracked = tmp_path / rel
    tracked.parent.mkdir(parents=True)
    tracked.write_bytes(b"real")

    clips_state = {"clip-id": {"path": rel}}
    removed = await sync._reconcile_disk(tmp_path, clips_state)
    assert removed == 0
    assert tracked.exists()


async def test_reconcile_skips_non_audio(hass: HomeAssistant, tmp_path: Path) -> None:
    """Non-audio files (.json, .m3u8, .tmp) are left alone."""
    sync = SunoSync(hass, "test_sync")
    for name in (".suno_sync.json", "Liked Songs.m3u8", "partial.tmp"):
        (tmp_path / name).write_text("x")

    removed = await sync._reconcile_disk(tmp_path, {})
    assert removed == 0
    assert all((tmp_path / n).exists() for n in (".suno_sync.json", "Liked Songs.m3u8", "partial.tmp"))


async def test_reconcile_cleans_empty_dirs(hass: HomeAssistant, tmp_path: Path) -> None:
    """Empty parent directories are removed after orphan deletion."""
    sync = SunoSync(hass, "test_sync")
    orphan = tmp_path / "2026-01-01" / "Gone [deadbeef].flac"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"bye")

    removed = await sync._reconcile_disk(tmp_path, {})
    assert removed == 1
    assert not orphan.parent.exists()


# ── Per-source retention modes ────────────────────────────────────


class TestSyncRetentionModes:
    """Tests for per-source sync/copy retention modes."""

    def test_sync_mode_deletes_removed_clips(self) -> None:
        """Clip with source ['liked'], mode=sync → deleted when removed from desired."""
        clips_state = {
            "clip-1": {"path": "2026-01-15/Song [clip-1].flac", "sources": ["liked"]},
        }
        seen_ids: set[str] = set()  # clip not in desired
        preserved_ids: set[str] = set()
        options = {CONF_SYNC_MODE_LIKED: SYNC_MODE_SYNC}

        to_delete = []
        for cid in clips_state:
            if cid in seen_ids or cid in preserved_ids:
                continue
            entry = clips_state[cid]
            sources = entry.get("sources", [])
            if all(_source_uses_sync_mode(src, options) for src in sources):
                to_delete.append(cid)

        assert to_delete == ["clip-1"]

    def test_copy_mode_keeps_removed_clips(self) -> None:
        """Clip with source ['liked'], mode=copy → NOT deleted when removed from desired."""
        clips_state = {
            "clip-1": {"path": "2026-01-15/Song [clip-1].flac", "sources": ["liked"]},
        }
        seen_ids: set[str] = set()
        preserved_ids: set[str] = set()
        options = {CONF_SYNC_MODE_LIKED: SYNC_MODE_COPY}

        to_delete = []
        for cid in clips_state:
            if cid in seen_ids or cid in preserved_ids:
                continue
            entry = clips_state[cid]
            sources = entry.get("sources", [])
            if all(_source_uses_sync_mode(src, options) for src in sources):
                to_delete.append(cid)

        assert to_delete == []

    def test_mixed_sources_copy_wins(self) -> None:
        """Clip with sources ['liked', 'latest']. Liked=copy, latest=sync → NOT deleted."""
        clips_state = {
            "clip-1": {"path": "2026-01-15/Song [clip-1].flac", "sources": ["liked", "latest"]},
        }
        seen_ids: set[str] = set()
        preserved_ids: set[str] = set()
        options = {
            CONF_SYNC_MODE_LIKED: SYNC_MODE_COPY,
            CONF_SYNC_MODE_LATEST: SYNC_MODE_SYNC,
        }

        to_delete = []
        for cid in clips_state:
            if cid in seen_ids or cid in preserved_ids:
                continue
            entry = clips_state[cid]
            sources = entry.get("sources", [])
            if all(_source_uses_sync_mode(src, options) for src in sources):
                to_delete.append(cid)

        assert to_delete == []

    def test_empty_sources_deleted(self) -> None:
        """Clip with sources [] → deleted (orphan cleanup via all() on empty)."""
        clips_state = {
            "clip-1": {"path": "2026-01-15/Song [clip-1].flac", "sources": []},
        }
        seen_ids: set[str] = set()
        preserved_ids: set[str] = set()
        options = {}

        to_delete = []
        for cid in clips_state:
            if cid in seen_ids or cid in preserved_ids:
                continue
            entry = clips_state[cid]
            sources = entry.get("sources", [])
            if all(_source_uses_sync_mode(src, options) for src in sources):
                to_delete.append(cid)

        assert to_delete == ["clip-1"]


# ── _add_clip quality merge ───────────────────────────────────────


class TestAddClipQualityMerge:
    """Tests for _add_clip quality upgrade semantics."""

    def test_flac_wins_over_mp3(self) -> None:
        """When a clip appears first as MP3 then FLAC, quality upgrades to FLAC."""
        clip = _make_clip("clip-merge-1", "Merged")
        clip_map: dict[str, SyncItem] = {}
        _add_clip(clip_map, clip, "Liked Songs", "liked", QUALITY_STANDARD)
        _add_clip(clip_map, clip, "Playlist X", "playlist:x", QUALITY_HIGH)
        assert clip_map["clip-merge-1"].quality == QUALITY_HIGH
        assert set(clip_map["clip-merge-1"].sources) == {"liked", "playlist:x"}

    def test_mp3_does_not_downgrade_flac(self) -> None:
        """When a clip appears first as FLAC then MP3, quality stays FLAC."""
        clip = _make_clip("clip-merge-2", "Stays High")
        clip_map: dict[str, SyncItem] = {}
        _add_clip(clip_map, clip, "Liked Songs", "liked", QUALITY_HIGH)
        _add_clip(clip_map, clip, "Latest", "latest", QUALITY_STANDARD)
        assert clip_map["clip-merge-2"].quality == QUALITY_HIGH
        assert set(clip_map["clip-merge-2"].sources) == {"liked", "latest"}

    def test_same_quality_no_change(self) -> None:
        """Same quality from both sources stays unchanged."""
        clip = _make_clip("clip-merge-3", "Same")
        clip_map: dict[str, SyncItem] = {}
        _add_clip(clip_map, clip, "Liked Songs", "liked", QUALITY_STANDARD)
        _add_clip(clip_map, clip, "Latest", "latest", QUALITY_STANDARD)
        assert clip_map["clip-merge-3"].quality == QUALITY_STANDARD

    def test_first_add_creates_entry(self) -> None:
        """First add creates a new SyncItem with correct fields."""
        clip = _make_clip("clip-new", "New Song")
        clip_map: dict[str, SyncItem] = {}
        _add_clip(clip_map, clip, "Liked Songs", "liked", QUALITY_HIGH)
        item = clip_map["clip-new"]
        assert item.clip is clip
        assert item.collection == "Liked Songs"
        assert item.sources == ["liked"]
        assert item.quality == QUALITY_HIGH


# ── _source_uses_sync_mode unit tests ─────────────────────────────


class TestSourceUsesSyncMode:
    """Direct unit tests for _source_uses_sync_mode."""

    def test_liked_sync_mode(self) -> None:
        assert _source_uses_sync_mode("liked", {CONF_SYNC_MODE_LIKED: SYNC_MODE_SYNC}) is True

    def test_liked_copy_mode(self) -> None:
        assert _source_uses_sync_mode("liked", {CONF_SYNC_MODE_LIKED: SYNC_MODE_COPY}) is False

    def test_playlist_sync_mode(self) -> None:
        assert _source_uses_sync_mode("playlist:abc", {CONF_SYNC_MODE_PLAYLISTS: SYNC_MODE_SYNC}) is True

    def test_playlist_copy_mode(self) -> None:
        assert _source_uses_sync_mode("playlist:abc", {CONF_SYNC_MODE_PLAYLISTS: SYNC_MODE_COPY}) is False

    def test_latest_sync_mode(self) -> None:
        assert _source_uses_sync_mode("latest", {CONF_SYNC_MODE_LATEST: SYNC_MODE_SYNC}) is True

    def test_latest_copy_mode(self) -> None:
        assert _source_uses_sync_mode("latest", {CONF_SYNC_MODE_LATEST: SYNC_MODE_COPY}) is False

    def test_unknown_source_defaults_to_sync(self) -> None:
        assert _source_uses_sync_mode("unknown_source", {}) is True

    def test_default_mode_when_key_missing(self) -> None:
        """Missing config key uses DEFAULT_SYNC_MODE ('sync')."""
        assert _source_uses_sync_mode("liked", {}) is True


# ── get_synced_path edge cases ────────────────────────────────────


async def test_get_synced_path_meta_hash_mismatch(hass: HomeAssistant, tmp_path: Path) -> None:
    """meta_hash mismatch returns None to trigger re-download."""
    sync = SunoSync(hass, "test_sync")
    synced_file = tmp_path / "2026-01-15" / "Song [abcd1234].flac"
    synced_file.parent.mkdir(parents=True)
    synced_file.write_bytes(b"fLaC")
    sync._sync_path = str(tmp_path)
    sync._state = {
        "clips": {
            "abcd1234": {
                "path": "2026-01-15/Song [abcd1234].flac",
                "meta_hash": "old_hash_abc",
            }
        },
    }
    result = sync.get_synced_path("abcd1234", meta_hash="new_hash_xyz")
    assert result is None


async def test_get_synced_path_matching_hash(hass: HomeAssistant, tmp_path: Path) -> None:
    """Matching meta_hash returns the file path."""
    sync = SunoSync(hass, "test_sync")
    synced_file = tmp_path / "2026-01-15" / "Song [abcd1234].flac"
    synced_file.parent.mkdir(parents=True)
    synced_file.write_bytes(b"fLaC")
    sync._sync_path = str(tmp_path)
    sync._state = {
        "clips": {
            "abcd1234": {
                "path": "2026-01-15/Song [abcd1234].flac",
                "meta_hash": "same_hash",
            }
        },
    }
    result = sync.get_synced_path("abcd1234", meta_hash="same_hash")
    assert result is not None
    assert result.name == "Song [abcd1234].flac"


async def test_get_synced_path_no_sync_path(hass: HomeAssistant) -> None:
    """Returns None when sync_path is empty."""
    sync = SunoSync(hass, "test_sync")
    sync._sync_path = ""
    assert sync.get_synced_path("any-id") is None


async def test_get_synced_path_clip_not_in_state(hass: HomeAssistant) -> None:
    """Returns None when clip ID is not in state."""
    sync = SunoSync(hass, "test_sync")
    sync._sync_path = "/some/path"
    sync._state = {"clips": {}}
    assert sync.get_synced_path("missing-id") is None


# ── library_size_mb ───────────────────────────────────────────────


async def test_library_size_mb_calculation(hass: HomeAssistant) -> None:
    """library_size_mb sums file sizes and converts to MB."""
    sync = SunoSync(hass, "test_sync")
    sync._state = {
        "clips": {
            "c1": {"size": 1048576},  # 1 MB
            "c2": {"size": 2097152},  # 2 MB
            "c3": {"size": 524288},  # 0.5 MB
        },
    }
    assert sync.library_size_mb == 3.5


async def test_library_size_mb_empty(hass: HomeAssistant) -> None:
    """library_size_mb is 0.0 when no clips."""
    sync = SunoSync(hass, "test_sync")
    assert sync.library_size_mb == 0.0


async def test_library_size_mb_missing_size(hass: HomeAssistant) -> None:
    """Clips without 'size' key contribute 0."""
    sync = SunoSync(hass, "test_sync")
    sync._state = {
        "clips": {
            "c1": {"path": "song.flac"},  # no size key
            "c2": {"size": 1048576},
        },
    }
    assert sync.library_size_mb == 1.0
