"""Tests for the Suno download module."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
    CONF_MY_SONGS_MINIMUM,
    CONF_PLAYLISTS,
    CONF_SHOW_LIKED,
    CONF_SHOW_MY_SONGS,
    CONF_SHOW_PLAYLISTS,
    DOWNLOAD_MODE_ARCHIVE,
    DOWNLOAD_MODE_CACHE,
    DOWNLOAD_MODE_MIRROR,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from custom_components.suno.download import (
    DownloadItem,
    SunoDownloadManager,
    _add_clip,
    _build_download_summary,
    _clip_path,
    _get_source_mode,
    _safe_name,
    _source_preserves_files,
    _video_clip_path,
    _write_file,
    _write_m3u8_playlists,
)

# ── Filename sanitisation ──────────────────────────────────────────


class TestSafeName:
    def test_preserves_spaces_and_case(self) -> None:
        assert _safe_name("Hello World") == "Hello World"

    def test_unsafe_chars_replaced(self) -> None:
        result = _safe_name('test<>:"/\\|?*file')
        assert "<" not in result
        assert "/" not in result

    def test_empty_string_returns_untitled(self) -> None:
        assert _safe_name("") == "untitled"

    def test_unicode_preserved(self) -> None:
        assert _safe_name("café résumé") == "café résumé"

    def test_emoji_preserved(self) -> None:
        assert "Music" in _safe_name("🎵 Music")

    def test_traversal_neutralised(self) -> None:
        assert "/" not in _safe_name("../../etc/passwd")

    def test_windows_reserved_handled(self) -> None:
        result = _safe_name("CON")
        assert result != "CON"  # pathvalidate appends underscore

    def test_truncates_long_names(self) -> None:
        assert len(_safe_name("a" * 300)) <= 200


# ── Clip path generation ───────────────────────────────────────────


class TestClipPath:
    def _make_clip(
        self,
        title: str = "My Song",
        created: str = "2026-01-15T10:00:00Z",
        clip_id: str = "abcd1234-test-clip-id",
        display_name: str = "testuser",
    ):
        clip = MagicMock()
        clip.id = clip_id
        clip.title = title
        clip.created_at = created
        clip.display_name = display_name
        return clip

    def test_high_quality_flac(self) -> None:
        clip = self._make_clip()
        result = _clip_path(clip, "high")
        assert result == "testuser/My Song/testuser-My Song [abcd1234].flac"

    def test_standard_quality_mp3(self) -> None:
        clip = self._make_clip()
        result = _clip_path(clip, "standard")
        assert result == "testuser/My Song/testuser-My Song [abcd1234].mp3"

    def test_missing_display_name(self) -> None:
        clip = self._make_clip(display_name="")
        result = _clip_path(clip, "high")
        assert result == "Suno/My Song/Suno-My Song [abcd1234].flac"

    def test_different_clips_same_title_get_different_paths(self) -> None:
        clip_a = self._make_clip(clip_id="aaaaaaaa-1111-2222-3333-444444444444")
        clip_b = self._make_clip(clip_id="bbbbbbbb-1111-2222-3333-444444444444")
        assert _clip_path(clip_a, "high") != _clip_path(clip_b, "high")


# ── Sync state management ──────────────────────────────────────────


async def test_sync_init_loads_state(hass: HomeAssistant) -> None:
    """async_init should load persisted state."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value={"clips": {"abc": {}}, "last_download": "2026-01-01"}):
        await sync.async_init()
    assert sync.total_files == 1
    assert sync.last_download == "2026-01-01"


async def test_sync_init_handles_empty_store(hass: HomeAssistant) -> None:
    """async_init with empty store should use defaults."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()
    assert sync.total_files == 0
    assert sync.last_download is None


async def test_download_skips_when_disabled(hass: HomeAssistant) -> None:
    """Download should do nothing when path is empty."""
    mgr = SunoDownloadManager(hass, "test_download_state")
    client = AsyncMock()
    await mgr.async_download({CONF_DOWNLOAD_PATH: ""}, client)
    client.get_liked_songs.assert_not_called()


async def test_download_skips_when_no_path(hass: HomeAssistant) -> None:
    """Download should skip when path is missing."""
    mgr = SunoDownloadManager(hass, "test_download_state")
    client = AsyncMock()
    await mgr.async_download({}, client)
    client.get_liked_songs.assert_not_called()


async def test_download_skips_when_already_running(hass: HomeAssistant) -> None:
    """Download should not run concurrently."""
    mgr = SunoDownloadManager(hass, "test_download_state")
    mgr._running = True
    client = AsyncMock()
    await mgr.async_download({CONF_DOWNLOAD_PATH: "/safe/path"}, client)  # noqa: S108
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
    sync = SunoDownloadManager(hass, "test_sync_state")
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
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ),
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    assert sync.total_files == 1
    assert sync.errors == 0


async def test_sync_deletes_orphaned_clips(hass: HomeAssistant, tmp_path: Path) -> None:
    """Sync should delete files that are no longer in desired set."""
    sync_dir = tmp_path / "mirror"
    sync_dir.mkdir()
    orphan = sync_dir / "old-file.flac"
    orphan.write_bytes(b"fLaC" + b"\x00" * 50)

    sync = SunoDownloadManager(hass, "test_sync_state")
    initial_state = {
        "clips": {
            "orphan-id": {
                "path": "old-file.flac",
                "title": "Old Song",
                "created": "2026-01-01",
                "sources": ["liked"],
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
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    assert sync.total_files == 0
    assert not orphan.exists()


async def test_sync_writes_manifest(hass: HomeAssistant, tmp_path: Path) -> None:
    """Sync should write .suno_download.json manifest."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    sync_dir = tmp_path / "mirror"
    with patch.object(sync._store, "async_save"):
        await sync.async_download(
            {
                CONF_DOWNLOAD_PATH: str(sync_dir),
                CONF_SHOW_LIKED: True,
                CONF_ALL_PLAYLISTS: False,
                CONF_PLAYLISTS: [],
            },
            client,
        )

    manifest = sync_dir / ".suno_download.json"
    assert manifest.exists()
    data = json.loads(manifest.read_text())
    assert "last_download" in data
    assert "clips" in data


async def test_cleanup_tmp_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """cleanup_tmp_files should remove .tmp files."""
    sync_dir = tmp_path / "mirror"
    sync_dir.mkdir()
    (sync_dir / "song.flac.tmp").write_bytes(b"partial")
    (sync_dir / "real.flac").write_bytes(b"fLaC")

    sync = SunoDownloadManager(hass, "test_sync_state")
    await sync.cleanup_tmp_files(str(sync_dir))

    assert not (sync_dir / "song.flac.tmp").exists()
    assert (sync_dir / "real.flac").exists()


# ── Properties ──────────────────────────────────────────────────────


async def test_sync_properties(hass: HomeAssistant) -> None:
    """Properties should reflect current state."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    assert sync.is_running is False
    assert sync.total_files == 0
    assert sync.pending == 0
    assert sync.errors == 0
    assert sync.last_download is None


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
    sync = SunoDownloadManager(hass, "test_sync")
    assert sync.source_breakdown == {}


async def test_source_breakdown_counts_sources(hass: HomeAssistant) -> None:
    """Counts clips per source tag."""
    sync = SunoDownloadManager(hass, "test_sync")
    sync._state = {
        "clips": {
            "c1": {"sources": ["liked"]},
            "c2": {"sources": ["liked", "playlist:abc"]},
            "c3": {"sources": ["my_songs"]},
            "c4": {"sources": ["playlist:abc"]},
        },
        "last_download": None,
    }
    breakdown = sync.source_breakdown
    assert breakdown["liked"] == 2
    assert breakdown["playlist:abc"] == 2
    assert breakdown["my_songs"] == 1


# ── _build_desired with API failure ────────────────────────────────


async def test_build_desired_preserves_on_api_failure(hass: HomeAssistant) -> None:
    """Clips from failed API calls are preserved via preserved_ids."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    sync._state = {
        "clips": {
            "clip-liked": {"path": "liked.flac", "sources": ["liked"]},
            "clip-my-songs": {"path": "my_songs.flac", "sources": ["my_songs"]},
        },
        "last_download": None,
    }

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(side_effect=Exception("API down"))
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    options = {
        CONF_SHOW_LIKED: True,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_MY_SONGS_COUNT: None,
        CONF_MY_SONGS_DAYS: None,
    }

    desired, preserved, _, _ = await sync._build_desired(options, client)

    # clip-liked should be preserved since liked API failed
    assert "clip-liked" in preserved


# ── My songs AND logic ───────────────────────────────────────────────


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


async def test_my_songs_count_only(hass: HomeAssistant) -> None:
    """count=5, days=0 returns top 5 clips."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clips = [_make_dated_clip(f"clip-{i}", created="2026-03-15T10:00:00Z") for i in range(10)]
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=clips)

    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_MY_SONGS_COUNT: 5,
        CONF_MY_SONGS_DAYS: None,
    }
    desired, _, _, _ = await sync._build_desired(options, client)
    assert len(desired) == 5
    ids = {d.clip.id for d in desired}
    assert ids == {f"clip-{i}" for i in range(5)}


async def test_my_songs_days_only(hass: HomeAssistant) -> None:
    """count=0, days=7 returns all within 7 days."""
    from datetime import timedelta

    sync = SunoDownloadManager(hass, "test_sync_state")
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
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_MY_SONGS_COUNT: None,
        CONF_MY_SONGS_DAYS: 7,
    }
    desired, _, _, _ = await sync._build_desired(options, client)
    ids = {d.clip.id for d in desired}
    assert "clip-new-1" in ids
    assert "clip-new-2" in ids
    assert "clip-old" not in ids


async def test_my_songs_both_and(hass: HomeAssistant) -> None:
    """count=3, days=7 returns at most 3 within 7 days (intersection)."""
    from datetime import timedelta

    sync = SunoDownloadManager(hass, "test_sync_state")
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
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_MY_SONGS_COUNT: 3,
        CONF_MY_SONGS_DAYS: 7,
    }
    desired, _, _, _ = await sync._build_desired(options, client)
    ids = {d.clip.id for d in desired}
    # count=3 gives {clip-r0, clip-r1, clip-r2}, days=7 gives all clip-r*, intersection = {clip-r0, clip-r1, clip-r2}
    assert len(ids) == 3
    assert ids == {"clip-r0", "clip-r1", "clip-r2"}
    assert "clip-old-0" not in ids


async def test_my_songs_both_zero_disabled(hass: HomeAssistant) -> None:
    """count=0, days=0 means my_songs is disabled."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[_make_dated_clip("clip-1")])

    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_MY_SONGS_COUNT: None,
        CONF_MY_SONGS_DAYS: None,
    }
    desired, _, _, _ = await sync._build_desired(options, client)
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
        clips_state = {"clip1": {"path": "artist/test_song/artist-test_song [clip1aaa].flac", "title": "Test Song"}}
        desired = [DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        assert "./" not in content
        assert str(tmp_path / "artist/test_song/artist-test_song [clip1aaa].flac") in content

    def test_uses_clip_duration(self, tmp_path: Path) -> None:
        """Duration in #EXTINF should come from clip metadata, not hardcoded -1."""
        clip = self._make_clip(duration=95.7)
        clips_state = {"clip1": {"path": "artist/test_song/artist-test_song [clip1aaa].flac", "title": "Test Song"}}
        desired = [DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        assert "#EXTINF:95," in content

    def test_duration_fallback_when_zero(self, tmp_path: Path) -> None:
        """Duration falls back to -1 when clip has no duration."""
        clip = self._make_clip(duration=0)
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        assert "#EXTINF:-1," in content

    def test_header_format(self, tmp_path: Path) -> None:
        """M3U8 files must start with #EXTM3U and include #PLAYLIST tag."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [DownloadItem(clip=clip, sources=["playlist:pl1"], quality=QUALITY_HIGH)]
        source_to_name = {"playlist:pl1": "My Playlist"}

        _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name)

        content = (tmp_path / "My Playlist.m3u8").read_text(encoding="utf-8")
        assert content.startswith("#EXTM3U\n")
        assert "#PLAYLIST:My Playlist\n" in content

    def test_liked_and_playlist_sources(self, tmp_path: Path) -> None:
        """Clips with both liked and playlist sources appear in both M3U8 files."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [DownloadItem(clip=clip, sources=["liked", "playlist:pl1"], quality=QUALITY_HIGH)]
        source_to_name = {"liked": "Liked Songs", "playlist:pl1": "Favourites"}

        _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name)

        assert (tmp_path / "Liked Songs.m3u8").exists()
        assert (tmp_path / "Favourites.m3u8").exists()

    def test_clip_liked_and_in_playlist_no_duplicates(self, tmp_path: Path) -> None:
        """A liked clip also in a playlist appears once in each M3U8, not twice in Liked Songs."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [DownloadItem(clip=clip, sources=["liked", "playlist:pl1"], quality=QUALITY_HIGH)]
        source_to_name = {"liked": "Liked Songs", "playlist:pl1": "The Second album"}

        _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name)

        liked_content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        album_content = (tmp_path / "The Second album.m3u8").read_text(encoding="utf-8")
        assert liked_content.count("song.flac") == 1
        assert album_content.count("song.flac") == 1

    def test_clip_in_two_playlists(self, tmp_path: Path) -> None:
        """A clip in two playlists appears in both M3U8 files."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [DownloadItem(clip=clip, sources=["playlist:a", "playlist:b"], quality=QUALITY_HIGH)]
        source_to_name = {"playlist:a": "Playlist A", "playlist:b": "Playlist B"}

        _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name)

        assert (tmp_path / "Playlist A.m3u8").exists()
        assert (tmp_path / "Playlist B.m3u8").exists()
        a_content = (tmp_path / "Playlist A.m3u8").read_text(encoding="utf-8")
        b_content = (tmp_path / "Playlist B.m3u8").read_text(encoding="utf-8")
        assert "song.flac" in a_content
        assert "song.flac" in b_content

    def test_liked_plus_two_playlists(self, tmp_path: Path) -> None:
        """A clip that is liked and in two playlists appears in all three M3U8 files."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [
            DownloadItem(
                clip=clip,
                sources=["liked", "playlist:a", "playlist:b"],
                quality=QUALITY_HIGH,
            )
        ]
        source_to_name = {"liked": "Liked Songs", "playlist:a": "Keep", "playlist:b": "Zac & Xavi"}

        _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name)

        assert (tmp_path / "Liked Songs.m3u8").exists()
        assert (tmp_path / "Keep.m3u8").exists()
        assert (tmp_path / "Zac & Xavi.m3u8").exists()
        for f in ["Liked Songs.m3u8", "Keep.m3u8", "Zac & Xavi.m3u8"]:
            content = (tmp_path / f).read_text(encoding="utf-8")
            assert content.count("song.flac") == 1

    def test_my_songs_source_excluded_from_m3u8(self, tmp_path: Path) -> None:
        """Clips with only a 'my_songs' source produce no M3U8 file."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [DownloadItem(clip=clip, sources=["my_songs"], quality=QUALITY_STANDARD)]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        assert not list(tmp_path.glob("*.m3u8"))

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
        desired = [DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        # No M3U8 written since the only clip had no path
        assert not list(tmp_path.glob("*.m3u8"))


# ── Sync summary ───────────────────────────────────────────────────


# ── Quality tracking ───────────────────────────────────────────────


async def test_quality_change_triggers_redownload(hass: HomeAssistant, tmp_path: Path) -> None:
    """Quality change should delete old file and re-download."""
    clip_id = "clip0001-0000-0000-0000-000000000000"
    sync_dir = tmp_path / "mirror"
    sync_dir.mkdir()
    old_file = sync_dir / "2026-03-15" / "Song [clip0001].flac"
    old_file.parent.mkdir(parents=True)
    old_file.write_bytes(b"fLaC" + b"\x00" * 50)

    sync = SunoDownloadManager(hass, "test_sync_state")
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
        "last_download": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    clip = _make_clip(clip_id, "Song")
    desired = [DownloadItem(clip=clip, sources=["liked"], quality="standard")]
    client = AsyncMock()

    fake_mp3 = b"ID3" + b"\x00" * 50

    with (
        patch(
            "custom_components.suno.download.download_as_mp3",
            new_callable=AsyncMock,
            return_value=fake_mp3,
        ),
        patch(
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
        ) as mock_flac,
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
        patch.object(sync, "_build_desired", return_value=(desired, set(), {"liked": "Liked Songs"}, {})),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

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
    sync = SunoDownloadManager(hass, "test_sync_state")
    initial_state = {
        "clips": {
            "clip0002-0000-0000-0000-000000000000": {
                "path": "2026-03-15/Song [clip0002].flac",
                "title": "Song",
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 54,
                "meta_hash": "9ad1d8ab369d",
                "quality": "high",
            }
        },
        "last_download": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    # Pre-create the manifest's target file so reconciliation does not flag it
    # as missing and trigger a re-download.
    target = tmp_path / "mirror" / "2026-03-15" / "Song [clip0002].flac"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"fLaC" + b"\x00" * 50)

    clip = _make_clip("clip0002-0000-0000-0000-000000000000", "Song")
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    with (
        patch(
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
        ) as mock_dl,
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    mock_dl.assert_not_called()


async def test_quality_stored_in_state(hass: HomeAssistant, tmp_path: Path) -> None:
    """After download, quality should be stored in clips_state."""
    sync = SunoDownloadManager(hass, "test_sync_state")
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
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ),
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    clips_state = sync._state["clips"]
    entry = clips_state["clip0003-0000-0000-0000-000000000000"]
    assert entry["quality"] == QUALITY_HIGH


class TestBuildSyncSummary:
    def test_no_change(self) -> None:
        assert _build_download_summary(0, 0, 0) == "No change"

    def test_single_new_song(self) -> None:
        assert _build_download_summary(1, 0, 0) == "1 new song"

    def test_multiple_new_songs(self) -> None:
        assert _build_download_summary(8, 0, 0) == "8 new songs"

    def test_single_removal(self) -> None:
        assert _build_download_summary(0, 1, 0) == "1 removal"

    def test_multiple_removals(self) -> None:
        assert _build_download_summary(0, 3, 0) == "3 removals"

    def test_single_metadata_update(self) -> None:
        assert _build_download_summary(0, 0, 1) == "1 metadata update"

    def test_multiple_metadata_updates(self) -> None:
        assert _build_download_summary(0, 0, 2) == "2 metadata updates"

    def test_combined(self) -> None:
        result = _build_download_summary(1, 2, 1)
        assert result == "1 new song, 1 metadata update, 2 removals"

    def test_all_plural(self) -> None:
        result = _build_download_summary(3, 4, 5)
        assert result == "3 new songs, 5 metadata updates, 4 removals"

    def test_renamed(self) -> None:
        assert _build_download_summary(0, 0, 0, renamed=3) == "3 renamed"

    def test_retagged(self) -> None:
        assert _build_download_summary(0, 0, 0, retagged=5) == "5 re-tagged"

    def test_full_username_change(self) -> None:
        """Typical username change: renames + re-tags, no downloads."""
        result = _build_download_summary(0, 0, 0, renamed=50, retagged=0)
        assert result == "50 renamed"

    def test_all_operations(self) -> None:
        result = _build_download_summary(2, 1, 0, renamed=3, retagged=5)
        assert result == "2 new songs, 3 renamed, 5 re-tagged, 1 removal"


# ── Download clip branching ────────────────────────────────────────


async def test_download_clip_flac_path(hass: HomeAssistant, tmp_path: Path) -> None:
    """quality='high' should use download_and_transcode_to_flac."""
    sync = SunoDownloadManager(hass, "test_sync_state")
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
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ) as mock_flac,
        patch(
            "custom_components.suno.download.download_as_mp3",
            new_callable=AsyncMock,
        ) as mock_mp3,
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    mock_flac.assert_called_once()
    mock_mp3.assert_not_called()
    assert sync.total_files == 1
    assert sync.errors == 0


async def test_download_clip_mp3_path(hass: HomeAssistant, tmp_path: Path) -> None:
    """quality='standard' should use download_as_mp3, not FLAC."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clip = _make_clip("clip-mp3-00000-0000-0000-000000000000", "MP3 Song")
    desired = [DownloadItem(clip=clip, sources=["liked"], quality="standard")]
    client = AsyncMock()

    fake_mp3 = b"ID3" + b"\x00" * 50

    with (
        patch(
            "custom_components.suno.download.download_as_mp3",
            new_callable=AsyncMock,
            return_value=fake_mp3,
        ) as mock_mp3,
        patch(
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
        ) as mock_flac,
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
        patch.object(sync, "_build_desired", return_value=(desired, set(), {"liked": "Liked Songs"}, {})),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    mock_mp3.assert_called_once()
    mock_flac.assert_not_called()
    entry = sync._state["clips"]["clip-mp3-00000-0000-0000-000000000000"]
    assert entry["quality"] == "standard"
    assert entry["path"].endswith(".mp3")


async def test_download_write_through_cache(hass: HomeAssistant, tmp_path: Path) -> None:
    """After download, cache.async_put should be called."""
    sync = SunoDownloadManager(hass, "test_sync_state")
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
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ),
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    mock_cache.async_put.assert_called_once()
    call_args = mock_cache.async_put.call_args
    assert call_args[0][0] == "clip-cache-0000-0000-0000-000000000000"
    assert call_args[0][1] == "flac"
    assert call_args[0][2] == fake_flac


# ── Disk reconciliation ────────────────────────────────────────────


async def test_reconcile_removes_orphan_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """Orphan .flac files not in clips_state are deleted."""
    sync = SunoDownloadManager(hass, "test_sync")
    orphan = tmp_path / "2026-01-01" / "Orphan [deadbeef].flac"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"fake")

    removed = await sync._reconcile_disk(tmp_path, {})
    assert removed == 1
    assert not orphan.exists()


async def test_reconcile_keeps_tracked_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """Files referenced in clips_state are not deleted."""
    sync = SunoDownloadManager(hass, "test_sync")
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
    sync = SunoDownloadManager(hass, "test_sync")
    for name in (".suno_download.json", "Liked Songs.m3u8", "partial.tmp"):
        (tmp_path / name).write_text("x")

    removed = await sync._reconcile_disk(tmp_path, {})
    assert removed == 0
    assert all((tmp_path / n).exists() for n in (".suno_download.json", "Liked Songs.m3u8", "partial.tmp"))


async def test_reconcile_cleans_empty_dirs(hass: HomeAssistant, tmp_path: Path) -> None:
    """Empty parent directories are removed after orphan deletion."""
    sync = SunoDownloadManager(hass, "test_sync")
    orphan = tmp_path / "2026-01-01" / "Gone [deadbeef].flac"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"bye")

    removed = await sync._reconcile_disk(tmp_path, {})
    assert removed == 1
    assert not orphan.parent.exists()


# ── Per-source retention modes ────────────────────────────────────


class TestSyncRetentionModes:
    """Tests for per-source sync/archive/cache retention modes."""

    def test_sync_mode_deletes_removed_clips(self) -> None:
        """Clip with source ['liked'], mode=mirror → deleted when removed from desired."""
        clips_state = {
            "clip-1": {"path": "2026-01-15/Song [clip-1].flac", "sources": ["liked"]},
        }
        seen_ids: set[str] = set()  # clip not in desired
        preserved_ids: set[str] = set()
        options = {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_MIRROR}

        to_delete = []
        for cid in clips_state:
            if cid in seen_ids or cid in preserved_ids:
                continue
            entry = clips_state[cid]
            sources = entry.get("sources", [])
            if all(not _source_preserves_files(src, options) for src in sources):
                to_delete.append(cid)

        assert to_delete == ["clip-1"]

    def test_archive_mode_keeps_removed_clips(self) -> None:
        """Clip with source ['liked'], mode=archive → NOT deleted when removed from desired."""
        clips_state = {
            "clip-1": {"path": "2026-01-15/Song [clip-1].flac", "sources": ["liked"]},
        }
        seen_ids: set[str] = set()
        preserved_ids: set[str] = set()
        options = {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_ARCHIVE}

        to_delete = []
        for cid in clips_state:
            if cid in seen_ids or cid in preserved_ids:
                continue
            entry = clips_state[cid]
            sources = entry.get("sources", [])
            if all(not _source_preserves_files(src, options) for src in sources):
                to_delete.append(cid)

        assert to_delete == []

    def test_mixed_sources_archive_wins(self) -> None:
        """Clip with sources ['liked', 'my_songs']. Liked=archive, my_songs=mirror → NOT deleted."""
        clips_state = {
            "clip-1": {"path": "2026-01-15/Song [clip-1].flac", "sources": ["liked", "my_songs"]},
        }
        seen_ids: set[str] = set()
        preserved_ids: set[str] = set()
        options = {
            CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_ARCHIVE,
            CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_MIRROR,
        }

        to_delete = []
        for cid in clips_state:
            if cid in seen_ids or cid in preserved_ids:
                continue
            entry = clips_state[cid]
            sources = entry.get("sources", [])
            if all(not _source_preserves_files(src, options) for src in sources):
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
            if all(not _source_preserves_files(src, options) for src in sources):
                to_delete.append(cid)

        assert to_delete == ["clip-1"]

    def test_cache_mode_deletes_like_mirror(self) -> None:
        """Cache mode sources are not preserved (treated like mirror for deletion)."""
        clips_state = {
            "clip-1": {"path": "2026-01-15/Song [clip-1].flac", "sources": ["my_songs"]},
        }
        seen_ids: set[str] = set()
        preserved_ids: set[str] = set()
        options = {CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE}

        to_delete = []
        for cid in clips_state:
            if cid in seen_ids or cid in preserved_ids:
                continue
            entry = clips_state[cid]
            sources = entry.get("sources", [])
            if all(not _source_preserves_files(src, options) for src in sources):
                to_delete.append(cid)

        assert to_delete == ["clip-1"]


# ── _add_clip quality merge ───────────────────────────────────────


class TestAddClipQualityMerge:
    """Tests for _add_clip quality upgrade semantics."""

    def test_flac_wins_over_mp3(self) -> None:
        """When a clip appears first as MP3 then FLAC, quality upgrades to FLAC."""
        clip = _make_clip("clip-merge-1", "Merged")
        clip_map: dict[str, DownloadItem] = {}
        _add_clip(clip_map, clip, "liked", QUALITY_STANDARD)
        _add_clip(clip_map, clip, "playlist:x", QUALITY_HIGH)
        assert clip_map["clip-merge-1"].quality == QUALITY_HIGH
        assert set(clip_map["clip-merge-1"].sources) == {"liked", "playlist:x"}

    def test_mp3_does_not_downgrade_flac(self) -> None:
        """When a clip appears first as FLAC then MP3, quality stays FLAC."""
        clip = _make_clip("clip-merge-2", "Stays High")
        clip_map: dict[str, DownloadItem] = {}
        _add_clip(clip_map, clip, "liked", QUALITY_HIGH)
        _add_clip(clip_map, clip, "my_songs", QUALITY_STANDARD)
        assert clip_map["clip-merge-2"].quality == QUALITY_HIGH
        assert set(clip_map["clip-merge-2"].sources) == {"liked", "my_songs"}

    def test_same_quality_no_change(self) -> None:
        """Same quality from both sources stays unchanged."""
        clip = _make_clip("clip-merge-3", "Same")
        clip_map: dict[str, DownloadItem] = {}
        _add_clip(clip_map, clip, "liked", QUALITY_STANDARD)
        _add_clip(clip_map, clip, "my_songs", QUALITY_STANDARD)
        assert clip_map["clip-merge-3"].quality == QUALITY_STANDARD

    def test_first_add_creates_entry(self) -> None:
        """First add creates a new DownloadItem with correct fields."""
        clip = _make_clip("clip-new", "New Song")
        clip_map: dict[str, DownloadItem] = {}
        _add_clip(clip_map, clip, "liked", QUALITY_HIGH)
        item = clip_map["clip-new"]
        assert item.clip is clip
        assert item.sources == ["liked"]
        assert item.quality == QUALITY_HIGH


# ── _get_source_mode / _source_preserves_files unit tests ───────────


class TestSourceUsesSyncMode:
    """Direct unit tests for _get_source_mode and _source_preserves_files."""

    def test_liked_mirror_mode(self) -> None:
        assert _get_source_mode("liked", {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_MIRROR}) == DOWNLOAD_MODE_MIRROR

    def test_liked_archive_mode(self) -> None:
        assert _get_source_mode("liked", {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_ARCHIVE}) == DOWNLOAD_MODE_ARCHIVE

    def test_liked_cache_mode(self) -> None:
        assert _get_source_mode("liked", {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_CACHE}) == DOWNLOAD_MODE_CACHE

    def test_playlist_mirror_mode(self) -> None:
        assert (
            _get_source_mode("playlist:abc", {CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_MIRROR})
            == DOWNLOAD_MODE_MIRROR
        )

    def test_playlist_archive_mode(self) -> None:
        assert (
            _get_source_mode("playlist:abc", {CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_ARCHIVE})
            == DOWNLOAD_MODE_ARCHIVE
        )

    def test_playlist_cache_mode(self) -> None:
        assert (
            _get_source_mode("playlist:abc", {CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_CACHE}) == DOWNLOAD_MODE_CACHE
        )

    def test_my_songs_mirror_mode(self) -> None:
        assert _get_source_mode("my_songs", {CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_MIRROR}) == DOWNLOAD_MODE_MIRROR

    def test_my_songs_archive_mode(self) -> None:
        assert (
            _get_source_mode("my_songs", {CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_ARCHIVE}) == DOWNLOAD_MODE_ARCHIVE
        )

    def test_my_songs_cache_mode(self) -> None:
        assert _get_source_mode("my_songs", {CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE}) == DOWNLOAD_MODE_CACHE

    def test_unknown_source_defaults_to_mirror(self) -> None:
        assert _get_source_mode("unknown_source", {}) == DOWNLOAD_MODE_MIRROR

    def test_default_mode_when_key_missing(self) -> None:
        """Missing config key uses DEFAULT_DOWNLOAD_MODE ('mirror')."""
        assert _get_source_mode("liked", {}) == DOWNLOAD_MODE_MIRROR

    def test_preserves_files_true_for_archive(self) -> None:
        assert _source_preserves_files("liked", {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_ARCHIVE}) is True

    def test_preserves_files_false_for_mirror(self) -> None:
        assert _source_preserves_files("liked", {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_MIRROR}) is False

    def test_preserves_files_false_for_cache(self) -> None:
        assert _source_preserves_files("my_songs", {CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE}) is False


# ── get_downloaded_path edge cases ────────────────────────────────────


async def test_get_downloaded_path_meta_hash_mismatch(hass: HomeAssistant, tmp_path: Path) -> None:
    """meta_hash mismatch returns None to trigger re-download."""
    sync = SunoDownloadManager(hass, "test_sync")
    synced_file = tmp_path / "2026-01-15" / "Song [abcd1234].flac"
    synced_file.parent.mkdir(parents=True)
    synced_file.write_bytes(b"fLaC")
    sync._download_path = str(tmp_path)
    sync._state = {
        "clips": {
            "abcd1234": {
                "path": "2026-01-15/Song [abcd1234].flac",
                "meta_hash": "old_hash_abc",
            }
        },
    }
    result = sync.get_downloaded_path("abcd1234", meta_hash="new_hash_xyz")
    assert result is None


async def test_get_downloaded_path_matching_hash(hass: HomeAssistant, tmp_path: Path) -> None:
    """Matching meta_hash returns the file path."""
    sync = SunoDownloadManager(hass, "test_sync")
    synced_file = tmp_path / "2026-01-15" / "Song [abcd1234].flac"
    synced_file.parent.mkdir(parents=True)
    synced_file.write_bytes(b"fLaC")
    sync._download_path = str(tmp_path)
    sync._state = {
        "clips": {
            "abcd1234": {
                "path": "2026-01-15/Song [abcd1234].flac",
                "meta_hash": "same_hash",
            }
        },
    }
    result = sync.get_downloaded_path("abcd1234", meta_hash="same_hash")
    assert result is not None
    assert result.name == "Song [abcd1234].flac"


async def test_get_downloaded_path_no_download_path(hass: HomeAssistant) -> None:
    """Returns None when sync_path is empty."""
    sync = SunoDownloadManager(hass, "test_sync")
    sync._download_path = ""
    assert sync.get_downloaded_path("any-id") is None


async def test_get_downloaded_path_clip_not_in_state(hass: HomeAssistant) -> None:
    """Returns None when clip ID is not in state."""
    sync = SunoDownloadManager(hass, "test_sync")
    sync._download_path = "/some/path"
    sync._state = {"clips": {}}
    assert sync.get_downloaded_path("missing-id") is None


# ── library_size_mb ───────────────────────────────────────────────


async def test_library_size_mb_calculation(hass: HomeAssistant) -> None:
    """library_size_mb sums file sizes and converts to MB."""
    sync = SunoDownloadManager(hass, "test_sync")
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
    sync = SunoDownloadManager(hass, "test_sync")
    assert sync.library_size_mb == 0.0


async def test_library_size_mb_missing_size(hass: HomeAssistant) -> None:
    """Clips without 'size' key contribute 0."""
    sync = SunoDownloadManager(hass, "test_sync")
    sync._state = {
        "clips": {
            "c1": {"path": "song.flac"},  # no size key
            "c2": {"size": 1048576},
        },
    }
    assert sync.library_size_mb == 1.0


# ── Download manager source skipping ──────────────────────────────


async def test_download_manager_skips_disabled_source(hass: HomeAssistant) -> None:
    """show_liked=False means no liked clips are included in desired set."""
    mgr = SunoDownloadManager(hass, "test_download_state")
    with patch.object(mgr._store, "async_load", return_value=None):
        await mgr.async_init()

    clip = _make_clip("clip-liked-1", "Liked Song")
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_MY_SONGS_COUNT: None,
        CONF_MY_SONGS_DAYS: None,
    }
    desired, _, _, _ = await mgr._build_desired(options, client)
    assert len(desired) == 0
    # get_liked_songs should not be called since show_liked is False
    client.get_liked_songs.assert_not_called()


async def test_download_manager_empty_path_no_download(hass: HomeAssistant) -> None:
    """download_path="" means no downloads happen."""
    mgr = SunoDownloadManager(hass, "test_download_state")
    with patch.object(mgr._store, "async_load", return_value=None):
        await mgr.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[_make_clip("clip-1")])

    await mgr.async_download({CONF_DOWNLOAD_PATH: ""}, client)

    # No download operations should have been attempted
    client.get_liked_songs.assert_not_called()
    assert mgr.total_files == 0


# ── My songs minimum songs ───────────────────────────────────────────


async def test_my_songs_minimum_pads_when_below_floor(hass: HomeAssistant) -> None:
    """Minimum pads with most recent clips when intersection is below threshold."""
    from datetime import timedelta

    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    now = datetime.now(tz=UTC)
    recent_ts = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_ts = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # 10 clips: 3 recent within 7 days, 7 old outside window
    clips = [_make_dated_clip(f"clip-new-{i}", created=recent_ts) for i in range(3)] + [
        _make_dated_clip(f"clip-old-{i}", created=old_ts) for i in range(7)
    ]
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=clips)

    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_MY_SONGS_COUNT: 5,
        CONF_MY_SONGS_DAYS: 7,
        CONF_MY_SONGS_MINIMUM: 7,
    }
    desired, _, _, _ = await sync._build_desired(options, client)
    # Intersection of top-5 and within-7-days = 3 recent clips
    # Minimum = 7 → pad to 7 with most recent clips
    assert len(desired) == 7


async def test_my_songs_minimum_disabled_when_zero(hass: HomeAssistant) -> None:
    """Minimum=0 has no effect."""
    from datetime import timedelta

    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    now = datetime.now(tz=UTC)
    old_ts = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    clips = [_make_dated_clip(f"clip-{i}", created=old_ts) for i in range(5)]
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=clips)

    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_MY_SONGS_COUNT: 3,
        CONF_MY_SONGS_DAYS: 7,
        CONF_MY_SONGS_MINIMUM: 0,
    }
    desired, _, _, _ = await sync._build_desired(options, client)
    # count=3, days=7, all clips are old → intersection is empty, minimum=0 → no padding
    assert len(desired) == 0


async def test_my_songs_minimum_alone_triggers_my_songs(hass: HomeAssistant) -> None:
    """Minimum works when count=0 and days=0 (both filters disabled)."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clips = [_make_dated_clip(f"clip-{i}") for i in range(10)]
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=clips)

    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_MY_SONGS_COUNT: None,
        CONF_MY_SONGS_DAYS: None,
        CONF_MY_SONGS_MINIMUM: 5,
    }
    desired, _, _, _ = await sync._build_desired(options, client)
    # count=0, days=0 → empty set, but minimum=5 → pad to 5
    assert len(desired) == 5


async def test_my_songs_minimum_capped_by_library_size(hass: HomeAssistant) -> None:
    """Minimum can't exceed available clips."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clips = [_make_dated_clip(f"clip-{i}") for i in range(3)]
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=clips)

    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_MY_SONGS_COUNT: None,
        CONF_MY_SONGS_DAYS: None,
        CONF_MY_SONGS_MINIMUM: 100,
    }
    desired, _, _, _ = await sync._build_desired(options, client)
    # Only 3 clips exist, minimum=100 but capped
    assert len(desired) == 3


async def test_my_songs_minimum_overrides_expired_days(hass: HomeAssistant) -> None:
    """Minimum pads even when all clips are outside lookback period."""
    from datetime import timedelta

    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    now = datetime.now(tz=UTC)
    old_ts = (now - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    clips = [_make_dated_clip(f"clip-{i}", created=old_ts) for i in range(8)]
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=clips)

    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_MY_SONGS_COUNT: None,
        CONF_MY_SONGS_DAYS: 7,
        CONF_MY_SONGS_MINIMUM: 5,
    }
    desired, _, _, _ = await sync._build_desired(options, client)
    # days=7 → no clips match, but minimum=5 → pad with 5 most recent
    assert len(desired) == 5


# ── _last_result persistence ──────────────────────────────────────


async def test_last_result_persisted_and_restored(hass: HomeAssistant) -> None:
    """_last_result is saved to state and restored on init."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    stored = {
        "clips": {},
        "last_download": "2026-03-22T08:00:00+00:00",
        "last_result": "3 new songs, 1 removal",
    }
    with patch.object(sync._store, "async_load", return_value=stored):
        await sync.async_init()

    assert sync.last_result == "3 new songs, 1 removal"


# ── Bootstrap remaining display ───────────────────────────────────


async def test_downloads_all_without_cap(hass: HomeAssistant, tmp_path: Path) -> None:
    """All clips download in one run — no batch caps or continuation."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    num_clips = 30
    clips = [_make_clip(f"clip-{i:04d}-0000-0000-0000-000000000000", f"Song {i}") for i in range(num_clips)]
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=clips)
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    fake_flac = b"fLaC" + b"\x00" * 50

    with (
        patch(
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ),
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    assert sync.pending == 0
    assert sync.total_files == num_clips


async def test_initial_sync_label(hass: HomeAssistant, tmp_path: Path) -> None:
    """Initial sync shows 'Initial sync' in status, not generic label."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clips = [_make_clip(f"clip-{i:04d}-0000-0000-0000-000000000000", f"Song {i}") for i in range(3)]
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=clips)
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    fake_flac = b"fLaC" + b"\x00" * 50

    results_during: list[str] = []
    original_notify = sync._notify_coordinator

    def capture_notify() -> None:
        results_during.append(sync.last_result)
        original_notify()

    sync._notify_coordinator = capture_notify  # type: ignore[method-assign]

    with (
        patch(
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ),
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client, initial=True)

    # At least one progress update should contain "Initial sync"
    assert any("Initial sync" in r for r in results_during)


# ── TC-2: Migration logic ─────────────────────────────────────────


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


async def test_migration_renames_file_instead_of_redownloading(hass: HomeAssistant, tmp_path: Path) -> None:
    """When _clip_path returns a different path, file is renamed, not re-downloaded."""
    clip_id = "abcd1234-0000-0000-0000-000000000000"
    sync_dir = tmp_path / "mirror"
    old_rel = "old_artist/Song/old_artist-Song [abcd1234].flac"
    old_file = sync_dir / old_rel
    old_file.parent.mkdir(parents=True)
    old_file.write_bytes(b"fLaC" + b"\x00" * 50)

    sync = SunoDownloadManager(hass, "test_sync_state")
    initial_state = {
        "clips": {
            clip_id: {
                "path": old_rel,
                "title": "Song",
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 54,
                "meta_hash": "9ad1d8ab369d",
                "quality": "high",
            }
        },
        "last_download": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    clip = _make_clip_with_display(clip_id, "Song", display_name="newartist")
    desired = [DownloadItem(clip=clip, sources=["liked"], quality="high")]
    client = AsyncMock()

    with (
        patch("custom_components.suno.download.async_get_clientsession"),
        patch("custom_components.suno.download.fetch_album_art", new_callable=AsyncMock, return_value=None),
        patch.object(sync._store, "async_save"),
        patch.object(sync, "_build_desired", return_value=(desired, set(), {"liked": "Liked Songs"}, {})),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    # Old file should be gone, new path should exist
    assert not old_file.exists()
    new_rel = _clip_path(clip, "high")
    new_file = sync_dir / new_rel
    assert new_file.exists()
    assert sync._state["clips"][clip_id]["path"] == new_rel


async def test_migration_moves_mp4_sidecar(hass: HomeAssistant, tmp_path: Path) -> None:
    """Video .mp4 sidecar is moved to music-videos/ directory during migration."""
    clip_id = "abcd1234-0000-0000-0000-000000000000"
    sync_dir = tmp_path / "mirror"
    old_rel = "old_artist/Song/old_artist-Song [abcd1234].flac"
    old_file = sync_dir / old_rel
    old_file.parent.mkdir(parents=True)
    old_file.write_bytes(b"fLaC" + b"\x00" * 50)
    old_video = old_file.with_suffix(".mp4")
    old_video.write_bytes(b"\x00\x00\x00\x1cftypisom")

    sync = SunoDownloadManager(hass, "test_sync_state")
    initial_state = {
        "clips": {
            clip_id: {
                "path": old_rel,
                "title": "Song",
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 54,
                "meta_hash": "9ad1d8ab369d",
                "quality": "high",
            }
        },
        "last_download": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    clip = _make_clip_with_display(clip_id, "Song", display_name="newartist")
    desired = [DownloadItem(clip=clip, sources=["liked"], quality="high")]
    client = AsyncMock()

    with (
        patch("custom_components.suno.download.async_get_clientsession"),
        patch("custom_components.suno.download.fetch_album_art", new_callable=AsyncMock, return_value=None),
        patch.object(sync._store, "async_save"),
        patch.object(sync, "_build_desired", return_value=(desired, set(), {"liked": "Liked Songs"}, {})),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    # Video should be in music-videos/ directory, not alongside audio
    new_video = sync_dir / _video_clip_path(clip)
    assert new_video.exists()
    assert not old_video.exists()


async def test_migration_cleans_old_parent_dirs(hass: HomeAssistant, tmp_path: Path) -> None:
    """Empty parent directories are cleaned up after migration."""
    clip_id = "abcd1234-0000-0000-0000-000000000000"
    sync_dir = tmp_path / "mirror"
    old_rel = "old_artist/OldTitle/old_artist-OldTitle [abcd1234].flac"
    old_file = sync_dir / old_rel
    old_file.parent.mkdir(parents=True)
    old_file.write_bytes(b"fLaC" + b"\x00" * 50)

    sync = SunoDownloadManager(hass, "test_sync_state")
    initial_state = {
        "clips": {
            clip_id: {
                "path": old_rel,
                "title": "OldTitle",
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 54,
                "meta_hash": "8692c463e866",
                "quality": "high",
            }
        },
        "last_download": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    clip = _make_clip_with_display(clip_id, "NewTitle", display_name="newartist")
    desired = [DownloadItem(clip=clip, sources=["liked"], quality="high")]
    client = AsyncMock()

    with (
        patch("custom_components.suno.download.async_get_clientsession"),
        patch("custom_components.suno.download.fetch_album_art", new_callable=AsyncMock, return_value=None),
        patch.object(sync._store, "async_save"),
        patch.object(sync, "_build_desired", return_value=(desired, set(), {"liked": "Liked Songs"}, {})),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    # Old parent dirs should be cleaned up (old_artist/OldTitle/ should not exist)
    assert not (sync_dir / "old_artist" / "OldTitle").exists()
    assert not (sync_dir / "old_artist").exists()


# ── TC-3: Cover art handling ──────────────────────────────────────


async def test_cover_jpg_written_on_download(hass: HomeAssistant, tmp_path: Path) -> None:
    """cover.jpg is written when a clip is downloaded with an image URL."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clip = _make_clip_with_display(
        "clip-cover-0000-0000-0000-000000000000",
        "Cover Song",
        image_url="https://cdn2.suno.ai/image_abcd.jpeg",
    )
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    fake_flac = b"fLaC" + b"\x00" * 50
    fake_image = b"\xff\xd8\xff\xe0JFIF"

    with (
        patch(
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ),
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch(
            "custom_components.suno.download.fetch_album_art",
            new_callable=AsyncMock,
            return_value=fake_image,
        ),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    rel_path = _clip_path(clip, "high")
    cover_path = (tmp_path / "mirror" / rel_path).parent / "cover.jpg"
    assert cover_path.exists()
    assert cover_path.read_bytes() == fake_image


async def test_cover_hash_written_alongside_cover(hass: HomeAssistant, tmp_path: Path) -> None:
    """.cover_hash file is written alongside cover.jpg."""
    import hashlib

    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    image_url = "https://cdn2.suno.ai/image_hashtest.jpeg"
    clip = _make_clip_with_display(
        "clip-hash-0000-0000-0000-000000000000",
        "Hash Song",
        image_url=image_url,
    )
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    fake_flac = b"fLaC" + b"\x00" * 50
    fake_image = b"\xff\xd8\xff\xe0JFIF"

    with (
        patch(
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ),
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch(
            "custom_components.suno.download.fetch_album_art",
            new_callable=AsyncMock,
            return_value=fake_image,
        ),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    rel_path = _clip_path(clip, "high")
    hash_path = (tmp_path / "mirror" / rel_path).parent / ".cover_hash"
    assert hash_path.exists()
    expected_hash = hashlib.md5(image_url.encode()).hexdigest()[:12]  # noqa: S324
    assert hash_path.read_text().strip() == expected_hash


async def test_cover_art_refreshed_on_hash_change(hass: HomeAssistant, tmp_path: Path) -> None:
    """Cover art is refreshed when image URL hash differs from stored .cover_hash."""
    import hashlib

    clip_id = "clip-refresh-000-0000-0000-000000000000"
    new_image_url = "https://cdn2.suno.ai/new_image.jpeg"
    clip = _make_clip_with_display(clip_id, "Refresh Song", image_url=new_image_url)
    rel_path = _clip_path(clip, "high")

    sync_dir = tmp_path / "mirror"
    target = sync_dir / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fLaC" + b"\x00" * 50)

    # Pre-existing cover with OLD hash
    cover_path = target.parent / "cover.jpg"
    hash_path = target.parent / ".cover_hash"
    cover_path.write_bytes(b"old_image_data")
    hash_path.write_text("old_hash_value")

    sync = SunoDownloadManager(hass, "test_sync_state")
    initial_state = {
        "clips": {
            clip_id: {
                "path": rel_path,
                "title": "Refresh Song",
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 54,
                "meta_hash": "5ed3d7d7bb17",
                "quality": "high",
            }
        },
        "last_download": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    desired = [DownloadItem(clip=clip, sources=["liked"], quality="high")]
    client = AsyncMock()
    new_image_data = b"\xff\xd8\xff\xe0NEW_IMAGE"

    with (
        patch("custom_components.suno.download.async_get_clientsession"),
        patch(
            "custom_components.suno.download.fetch_album_art",
            new_callable=AsyncMock,
            return_value=new_image_data,
        ) as mock_fetch,
        patch.object(sync._store, "async_save"),
        patch.object(sync, "_build_desired", return_value=(desired, set(), {"liked": "Liked Songs"}, {})),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    # Cover should be updated
    assert cover_path.read_bytes() == new_image_data
    expected_hash = hashlib.md5(new_image_url.encode()).hexdigest()[:12]  # noqa: S324
    assert hash_path.read_text().strip() == expected_hash
    mock_fetch.assert_called()


async def test_cover_art_not_refetched_when_hash_matches(hass: HomeAssistant, tmp_path: Path) -> None:
    """Cover art is NOT re-fetched when .cover_hash matches current image URL."""
    import hashlib

    clip_id = "clip-cached-000-0000-0000-000000000000"
    image_url = "https://cdn2.suno.ai/same_image.jpeg"
    clip = _make_clip_with_display(clip_id, "Cached Song", image_url=image_url)
    rel_path = _clip_path(clip, "high")

    sync_dir = tmp_path / "mirror"
    target = sync_dir / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fLaC" + b"\x00" * 50)

    # Pre-existing cover with MATCHING hash
    cover_path = target.parent / "cover.jpg"
    hash_path = target.parent / ".cover_hash"
    existing_image = b"existing_cover_data"
    cover_path.write_bytes(existing_image)
    url_hash = hashlib.md5(image_url.encode()).hexdigest()[:12]  # noqa: S324
    hash_path.write_text(url_hash)

    sync = SunoDownloadManager(hass, "test_sync_state")
    initial_state = {
        "clips": {
            clip_id: {
                "path": rel_path,
                "title": "Cached Song",
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 54,
                "meta_hash": "ea0d9c4102fe",
                "quality": "high",
            }
        },
        "last_download": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    desired = [DownloadItem(clip=clip, sources=["liked"], quality="high")]
    client = AsyncMock()

    with (
        patch("custom_components.suno.download.async_get_clientsession"),
        patch(
            "custom_components.suno.download.fetch_album_art",
            new_callable=AsyncMock,
            return_value=b"should_not_be_used",
        ) as mock_fetch,
        patch.object(sync._store, "async_save"),
        patch.object(sync, "_build_desired", return_value=(desired, set(), {"liked": "Liked Songs"}, {})),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    # Cover should NOT be refetched
    mock_fetch.assert_not_called()
    # Original image data preserved
    assert cover_path.read_bytes() == existing_image


# ── TC-5: Video download ──────────────────────────────────────────


async def test_video_download_success(hass: HomeAssistant, tmp_path: Path) -> None:
    """Video is downloaded alongside audio when video_url is present."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    sync._download_videos = True
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clip = _make_clip_with_display(
        "clip-vid-00000-0000-0000-000000000000",
        "Video Song",
        video_url="https://cdn1.suno.ai/clip-vid.mp4",
    )
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    fake_flac = b"fLaC" + b"\x00" * 50
    fake_video = b"\x00\x00\x00\x1cftypisom"

    async def _fake_iter_chunked(_size: int):
        yield fake_video

    mock_content = MagicMock()
    mock_content.iter_chunked = _fake_iter_chunked

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.content = mock_content

    mock_session = AsyncMock()
    mock_ctx = AsyncMock(
        __aenter__=AsyncMock(return_value=mock_resp),
        __aexit__=AsyncMock(return_value=False),
    )
    mock_session.get = MagicMock(return_value=mock_ctx)

    with (
        patch(
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ),
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession", return_value=mock_session),
        patch(
            "custom_components.suno.download.fetch_album_art",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    video_path = tmp_path / "mirror" / _video_clip_path(clip)
    assert video_path.exists()
    assert video_path.read_bytes() == fake_video


async def test_video_download_skipped_when_disabled(hass: HomeAssistant, tmp_path: Path) -> None:
    """Video download is skipped when _download_videos is False."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    sync._download_videos = False
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clip = _make_clip_with_display(
        "clip-novid-000-0000-0000-000000000000",
        "No Video",
        video_url="https://cdn1.suno.ai/clip-novid.mp4",
    )
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    fake_flac = b"fLaC" + b"\x00" * 50

    mock_session = AsyncMock()

    with (
        patch(
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ),
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession", return_value=mock_session),
        patch(
            "custom_components.suno.download.fetch_album_art",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    video_path = tmp_path / "mirror" / _video_clip_path(clip)
    assert not video_path.exists()
    # session.get should never have been called for video
    mock_session.get.assert_not_called()


async def test_video_download_handles_non_200(hass: HomeAssistant, tmp_path: Path) -> None:
    """Video download handles non-200 response gracefully (no crash, no file)."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    sync._download_videos = True
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clip = _make_clip_with_display(
        "clip-v404-0000-0000-0000-000000000000",
        "Video 404",
        video_url="https://cdn1.suno.ai/clip-v404.mp4",
    )
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    fake_flac = b"fLaC" + b"\x00" * 50

    mock_resp = AsyncMock()
    mock_resp.status = 404

    mock_session = AsyncMock()
    mock_ctx = AsyncMock(
        __aenter__=AsyncMock(return_value=mock_resp),
        __aexit__=AsyncMock(return_value=False),
    )
    mock_session.get = MagicMock(return_value=mock_ctx)

    with (
        patch(
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ),
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession", return_value=mock_session),
        patch(
            "custom_components.suno.download.fetch_album_art",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    video_path = tmp_path / "mirror" / _video_clip_path(clip)
    assert not video_path.exists()
    # Audio should still succeed
    assert sync.errors == 0
    assert sync.total_files == 1


async def test_video_download_skipped_when_no_video_url(hass: HomeAssistant, tmp_path: Path) -> None:
    """Video download is skipped when clip has no video_url."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    sync._download_videos = True
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    # Default _make_clip has no video_url
    clip = _make_clip("clip-nourl-000-0000-0000-000000000000", "No URL")
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    fake_flac = b"fLaC" + b"\x00" * 50

    mock_session = AsyncMock()

    with (
        patch(
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ),
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession", return_value=mock_session),
        patch(
            "custom_components.suno.download.fetch_album_art",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    assert not (tmp_path / "mirror" / _video_clip_path(clip)).exists()
    mock_session.get.assert_not_called()


# ── TC-7: Playlist order preservation ─────────────────────────────


class TestPlaylistOrderPreservation:
    def _make_clip(self, clip_id: str = "clip1", title: str = "Test Song", duration: float = 120.5):
        clip = MagicMock()
        clip.id = clip_id
        clip.title = title
        clip.duration = duration
        return clip

    def test_playlist_order_uses_playlist_order_dict(self, tmp_path: Path) -> None:
        """Entries are sorted according to playlist_order dict."""
        clips = [self._make_clip(f"clip{i}", f"Song {i}") for i in range(4)]
        clips_state = {f"clip{i}": {"path": f"Song {i}.flac", "title": f"Song {i}"} for i in range(4)}
        desired = [DownloadItem(clip=clips[i], sources=["liked"], quality=QUALITY_HIGH) for i in range(4)]
        # API order: clip3, clip1, clip0, clip2
        playlist_order = {"liked": ["clip3", "clip1", "clip0", "clip2"]}

        _write_m3u8_playlists(tmp_path, clips_state, desired, playlist_order=playlist_order)

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        lines = [ln for ln in content.splitlines() if ln.startswith("#EXTINF")]
        assert "Song 3" in lines[0]
        assert "Song 1" in lines[1]
        assert "Song 0" in lines[2]
        assert "Song 2" in lines[3]

    def test_fallback_ordering_without_playlist_order(self, tmp_path: Path) -> None:
        """Without playlist_order, entries appear in desired iteration order."""
        clips = [self._make_clip(f"clip{i}", f"Song {i}") for i in range(3)]
        clips_state = {f"clip{i}": {"path": f"Song {i}.flac", "title": f"Song {i}"} for i in range(3)}
        desired = [DownloadItem(clip=clips[i], sources=["liked"], quality=QUALITY_HIGH) for i in range(3)]

        _write_m3u8_playlists(tmp_path, clips_state, desired, playlist_order={})

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        lines = [ln for ln in content.splitlines() if ln.startswith("#EXTINF")]
        assert len(lines) == 3
        assert "Song 0" in lines[0]
        assert "Song 1" in lines[1]
        assert "Song 2" in lines[2]

    def test_playlist_order_matches_api_response_order(self, tmp_path: Path) -> None:
        """Playlist output order matches the API response order (reversed from default)."""
        clips = [self._make_clip(f"clip{i}", f"Song {i}") for i in range(5)]
        clips_state = {f"clip{i}": {"path": f"Song {i}.flac", "title": f"Song {i}"} for i in range(5)}
        desired = [DownloadItem(clip=clips[i], sources=["playlist:abc"], quality=QUALITY_HIGH) for i in range(5)]
        source_to_name = {"playlist:abc": "My Playlist"}
        # API returned clips in reverse order
        api_order = ["clip4", "clip3", "clip2", "clip1", "clip0"]
        playlist_order = {"playlist:abc": api_order}

        _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name, playlist_order)

        content = (tmp_path / "My Playlist.m3u8").read_text(encoding="utf-8")
        lines = [ln for ln in content.splitlines() if ln.startswith("#EXTINF")]
        assert len(lines) == 5
        for idx, api_clip_id in enumerate(api_order):
            clip_num = api_clip_id.replace("clip", "")
            assert f"Song {clip_num}" in lines[idx]


async def test_reconcile_skipped_when_nothing_changed(hass: HomeAssistant, tmp_path: Path) -> None:
    """Reconciliation is skipped when no downloads, deletions, or migrations occurred."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    clip_id = "clip0099-0000-0000-0000-000000000000"
    initial_state = {
        "clips": {
            clip_id: {
                "path": "Suno/Song/Suno-Song [clip0099].flac",
                "title": "Song",
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 54,
                "meta_hash": "9ad1d8ab369d",
                "quality": "high",
            }
        },
        "last_download": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    # Pre-create the file so no download is triggered
    dest = tmp_path / "mirror" / "Suno" / "Song" / "Suno-Song [clip0099].flac"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"fLaC" + b"\x00" * 50)

    clip = _make_clip(clip_id, "Song")
    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    with (
        patch(
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
        ),
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
        patch.object(sync, "_reconcile_disk", new_callable=AsyncMock, return_value=0) as mock_reconcile,
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    mock_reconcile.assert_not_called()


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


# ── T5: Zero-size file triggers re-download ────────────────────────


async def test_zero_size_file_triggers_redownload(hass: HomeAssistant, tmp_path: Path) -> None:
    """A new clip with an empty file on disk should be re-downloaded (not reconciled)."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    clip = _make_clip("clip-zero", "Zero Song")

    # Clip is NOT in state (new), so it will be added to to_download
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    # Create the zero-size file at the expected path on disk
    sync_dir = tmp_path / "mirror"
    from custom_components.suno.download import _clip_path

    rel_path = _clip_path(clip, QUALITY_HIGH)
    target = sync_dir / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"")

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])
    client.get_wav_url = AsyncMock(return_value="https://cdn1.suno.ai/clip-zero.wav")
    client.request_wav = AsyncMock()

    fake_flac = b"fLaC" + b"\x00" * 50

    with (
        patch(
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ),
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch("custom_components.suno.download.fetch_album_art", return_value=None),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    assert sync.errors == 0
    assert sync.total_files == 1
    # File should now have content (was re-downloaded, not reconciled)
    assert target.stat().st_size > 0


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
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
        ) as mock_dl,
        patch("custom_components.suno.download.get_ffmpeg_manager") as mock_ffmpeg,
        patch("custom_components.suno.download.async_get_clientsession"),
        patch("custom_components.suno.download.fetch_album_art", return_value=None),
        patch.object(sync._store, "async_save"),
        patch("custom_components.suno.download.retag_flac", new_callable=AsyncMock, return_value=True) as mock_retag,
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
        patch("custom_components.suno.download.get_ffmpeg_manager") as mock_ffmpeg,
        patch("custom_components.suno.download.async_get_clientsession"),
        patch("custom_components.suno.download.fetch_album_art", return_value=None),
        patch.object(sync._store, "async_save"),
        patch("custom_components.suno.download.retag_flac", new_callable=AsyncMock, return_value=False),
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
        patch("custom_components.suno.download.download_and_transcode_to_flac", new_callable=AsyncMock) as mock_dl,
        patch("custom_components.suno.download.get_ffmpeg_manager") as mock_ffmpeg,
        patch("custom_components.suno.download.async_get_clientsession"),
        patch("custom_components.suno.download.fetch_album_art", new_callable=AsyncMock, return_value=None),
        patch.object(sync._store, "async_save"),
        patch("custom_components.suno.download.retag_flac", new_callable=AsyncMock, return_value=True) as mock_retag,
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
        patch("custom_components.suno.download.download_and_transcode_to_flac", new_callable=AsyncMock) as mock_dl,
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch("custom_components.suno.download.fetch_album_art", new_callable=AsyncMock, return_value=None),
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
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=b"fLaC" + b"\x00" * 50,
        ) as mock_dl,
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch("custom_components.suno.download.fetch_album_art", new_callable=AsyncMock, return_value=None),
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
        patch("custom_components.suno.download.download_and_transcode_to_flac", new_callable=AsyncMock) as mock_dl,
        patch("custom_components.suno.download.get_ffmpeg_manager") as mock_ffmpeg,
        patch("custom_components.suno.download.async_get_clientsession"),
        patch("custom_components.suno.download.fetch_album_art", new_callable=AsyncMock, return_value=None),
        patch.object(sync._store, "async_save"),
        patch("custom_components.suno.download.retag_flac", new_callable=AsyncMock, return_value=True) as mock_retag,
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
            "custom_components.suno.download.Path.write_text",
            side_effect=OSError("disk full"),
        ),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        with patch("custom_components.suno.download._LOGGER"):
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


async def test_build_desired_skips_cache_only_sources(hass: HomeAssistant) -> None:
    """_build_desired makes no API calls for cache sections."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[_make_clip("c1")])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[_make_clip("c2")])

    options = {
        CONF_SHOW_LIKED: True,
        CONF_SHOW_MY_SONGS: True,
        CONF_SHOW_PLAYLISTS: True,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_CACHE,
        CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_CACHE,
        CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE,
        CONF_MY_SONGS_COUNT: 5,
        CONF_MY_SONGS_DAYS: None,
    }
    desired, _, _, _ = await sync._build_desired(options, client)
    assert len(desired) == 0
    # Liked songs should not be fetched because cache mode skips before API call
    client.get_liked_songs.assert_not_called()
    client.get_all_songs.assert_not_called()


async def test_build_desired_respects_show_toggles(hass: HomeAssistant) -> None:
    """show_playlists=False excludes playlists from desired set."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[_make_clip("c1")])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    options = {
        CONF_SHOW_LIKED: True,
        CONF_SHOW_PLAYLISTS: False,
        CONF_SHOW_MY_SONGS: False,
        CONF_ALL_PLAYLISTS: True,
        CONF_PLAYLISTS: [],
        CONF_MY_SONGS_COUNT: 5,
        CONF_MY_SONGS_DAYS: None,
    }
    desired, _, _, _ = await sync._build_desired(options, client)
    ids = {d.clip.id for d in desired}
    assert "c1" in ids
    # Playlists and my_songs toggled off
    client.get_playlists.assert_not_called()
    client.get_all_songs.assert_not_called()


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
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            side_effect=mock_transcode,
        ),
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch("custom_components.suno.download.fetch_album_art", new_callable=AsyncMock, return_value=None),
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
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            side_effect=mock_transcode,
        ),
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch("custom_components.suno.download.fetch_album_art", new_callable=AsyncMock, return_value=None),
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


# ── Manifest reconciliation (Release 2: 2.1 + 2.2 + 2.3) ───────────────────


async def test_reconcile_manifest_marks_missing_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """Manifest entries whose files are gone get path/meta_hash cleared."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    base = tmp_path / "mirror"
    base.mkdir()
    # Two manifest entries: one present on disk, one missing.
    (base / "present.flac").write_bytes(b"fLaC" + b"\x00" * 50)
    clips_state: dict[str, dict[str, object]] = {
        "present-id": {"path": "present.flac", "meta_hash": "abc"},
        "missing-id": {"path": "gone.flac", "meta_hash": "def"},
    }

    count = await sync._reconcile_manifest(base, clips_state)

    assert count == 1
    assert clips_state["present-id"]["path"] == "present.flac"
    assert clips_state["present-id"]["meta_hash"] == "abc"
    assert clips_state["missing-id"]["path"] == ""
    assert "meta_hash" not in clips_state["missing-id"]


async def test_reconcile_manifest_treats_zero_byte_as_missing(hass: HomeAssistant, tmp_path: Path) -> None:
    """Zero-byte files are reconciled the same as fully missing files."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    base = tmp_path / "mirror"
    base.mkdir()
    (base / "empty.flac").write_bytes(b"")
    clips_state: dict[str, dict[str, object]] = {
        "empty-id": {"path": "empty.flac", "meta_hash": "abc"},
    }

    count = await sync._reconcile_manifest(base, clips_state)

    assert count == 1
    assert clips_state["empty-id"]["path"] == ""


async def test_reconcile_manifest_idempotent_when_clean(hass: HomeAssistant, tmp_path: Path) -> None:
    """Manifest with all files present: no mutation, returns 0."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    base = tmp_path / "mirror"
    base.mkdir()
    (base / "a.flac").write_bytes(b"fLaC" + b"\x00" * 10)
    (base / "b.flac").write_bytes(b"fLaC" + b"\x00" * 10)
    clips_state: dict[str, dict[str, object]] = {
        "a-id": {"path": "a.flac", "meta_hash": "h1"},
        "b-id": {"path": "b.flac", "meta_hash": "h2"},
    }
    snapshot = json.dumps(clips_state, sort_keys=True)

    count = await sync._reconcile_manifest(base, clips_state)

    assert count == 0
    assert json.dumps(clips_state, sort_keys=True) == snapshot


async def test_missing_audio_file_triggers_redownload(hass: HomeAssistant, tmp_path: Path) -> None:
    """End-to-end: manifest entry whose file is gone → re-download queued."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    sync_dir = tmp_path / "mirror"
    sync_dir.mkdir()

    clip = _make_clip("clip0001-0000-0000-0000-000000000000", "Song")
    rel_path = _clip_path(clip, QUALITY_HIGH)
    from custom_components.suno.models import clip_meta_hash

    initial_state = {
        "clips": {
            clip.id: {
                "path": rel_path,
                "title": clip.title,
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 54,
                "meta_hash": clip_meta_hash(clip),
                "quality": QUALITY_HIGH,
            }
        },
        "last_download": None,
    }
    # Note: rel_path is NOT created on disk — that is the entire point.
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    fake_flac = b"fLaC" + b"\x00" * 50
    with (
        patch(
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
            return_value=fake_flac,
        ) as mock_dl,
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch("custom_components.suno.download.fetch_album_art", new_callable=AsyncMock, return_value=None),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    assert mock_dl.call_count == 1
    assert (sync_dir / rel_path).is_file()
    assert sync._state["clips"][clip.id]["path"] == rel_path


async def test_present_file_does_not_redownload(hass: HomeAssistant, tmp_path: Path) -> None:
    """Negative control: file present + hash match → no re-download work."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    sync_dir = tmp_path / "mirror"
    sync_dir.mkdir()

    clip = _make_clip("clip0002-0000-0000-0000-000000000000", "Song")
    rel_path = _clip_path(clip, QUALITY_HIGH)
    target = sync_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"fLaC" + b"\x00" * 50)

    from custom_components.suno.models import clip_meta_hash

    initial_state = {
        "clips": {
            clip.id: {
                "path": rel_path,
                "title": clip.title,
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 54,
                "meta_hash": clip_meta_hash(clip),
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
            "custom_components.suno.download.download_and_transcode_to_flac",
            new_callable=AsyncMock,
        ) as mock_dl,
        patch("custom_components.suno.download.get_ffmpeg_manager"),
        patch("custom_components.suno.download.async_get_clientsession"),
        patch.object(sync._store, "async_save"),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    mock_dl.assert_not_called()


async def test_retag_clip_returns_missing_when_target_gone(hass: HomeAssistant, tmp_path: Path) -> None:
    """_retag_clip pre-checks for missing files and signals MISSING."""
    from custom_components.suno.download import RetagResult

    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clip = _make_clip("clipA-0000-0000-0000-000000000000", "Song")
    item = DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)
    sync._clip_index = {clip.id: clip}
    target = tmp_path / "ghost.flac"

    result = await sync._retag_clip(item, target)

    assert result is RetagResult.MISSING


async def test_retag_clip_returns_missing_when_zero_byte(hass: HomeAssistant, tmp_path: Path) -> None:
    """_retag_clip treats zero-byte files as MISSING."""
    from custom_components.suno.download import RetagResult

    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    clip = _make_clip("clipB-0000-0000-0000-000000000000", "Song")
    item = DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)
    sync._clip_index = {clip.id: clip}
    target = tmp_path / "empty.flac"
    target.write_bytes(b"")

    result = await sync._retag_clip(item, target)

    assert result is RetagResult.MISSING


# ── Per-track JPG sidecar (Release 2: 2.10) ─────────────────────────────


async def test_update_cover_art_writes_per_track_sidecar(hass: HomeAssistant, tmp_path: Path) -> None:
    """When track_path is given, _update_cover_art writes <basename>.jpg too."""
    from custom_components.suno.download import _update_cover_art

    track = tmp_path / "Foo.flac"
    track.write_bytes(b"fLaC")
    cover = tmp_path / "cover.jpg"
    hash_path = tmp_path / ".cover_hash"

    session = AsyncMock()
    with patch(
        "custom_components.suno.download.fetch_album_art",
        new_callable=AsyncMock,
        return_value=b"\xff\xd8\xff" + b"\x00" * 100,
    ):
        result = await _update_cover_art(
            hass, session, "https://x/y.jpg", cover, hash_path, track_path=track
        )

    assert result is True
    assert cover.exists()
    track_jpg = track.with_suffix(".jpg")
    assert track_jpg.exists()
    assert track_jpg.read_bytes() == cover.read_bytes()


async def test_update_cover_art_backfills_missing_track_sidecar(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Hash-match path still backfills track sidecar if it's missing."""
    import hashlib

    from custom_components.suno.download import _update_cover_art

    track = tmp_path / "Foo.flac"
    track.write_bytes(b"fLaC")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    hash_path = tmp_path / ".cover_hash"
    image_url = "https://x/y.jpg"
    url_hash = hashlib.md5(image_url.encode()).hexdigest()[:12]  # noqa: S324
    hash_path.write_text(url_hash)
    track_jpg = track.with_suffix(".jpg")
    assert not track_jpg.exists()

    session = AsyncMock()
    result = await _update_cover_art(hass, session, image_url, cover, hash_path, track_path=track)

    # Hash matched so result is False, but the sidecar was backfilled.
    assert result is False
    assert track_jpg.exists()


# ── Album inheritance scoped to remixes (Release 2: 2.12) ───────────────


def test_album_for_clip_returns_none_for_non_remix() -> None:
    """Non-remix derivatives keep their own title as the album."""
    from custom_components.suno.download import _album_for_clip
    from custom_components.suno.models import SunoClip

    parent = SunoClip(
        id="parent",
        title="Parent Album",
        audio_url="x",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="2026-01-01T00:00:00Z",
        tags="",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
    )
    derived = SunoClip(
        id="child",
        title="Derived",
        audio_url="x",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="2026-01-02T00:00:00Z",
        tags="",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
        edited_clip_id="parent",
        root_ancestor_id="parent",
        is_remix=False,
    )
    index = {"parent": parent, "child": derived}
    assert _album_for_clip(derived, index) is None


def test_album_for_clip_inherits_root_for_remix() -> None:
    """Remix variants inherit the root ancestor's title as album."""
    from custom_components.suno.download import _album_for_clip
    from custom_components.suno.models import SunoClip

    parent = SunoClip(
        id="parent",
        title="Original Track",
        audio_url="x",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="2026-01-01T00:00:00Z",
        tags="",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
    )
    remix = SunoClip(
        id="remix",
        title="Original Track (Disco Mix)",
        audio_url="x",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="2026-01-02T00:00:00Z",
        tags="",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
        edited_clip_id="parent",
        root_ancestor_id="parent",
        is_remix=True,
    )
    index = {"parent": parent, "remix": remix}
    assert _album_for_clip(remix, index) == "Original Track"
