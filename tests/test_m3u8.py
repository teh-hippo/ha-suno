"""Tests for the m3u8 submodule of the Downloaded Library engine.

Split from the legacy 5129-line ``test_downloaded_library.py`` by the
Round 2 test restructure.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from custom_components.suno.const import (
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from custom_components.suno.downloaded_library import (
    DownloadItem,
    ManifestEntry,
)
from custom_components.suno.downloaded_library.m3u8 import _write_m3u8_playlists
from custom_components.suno.models import (
    SunoClip,
)

from .conftest import make_clip

# ── Shared test fixtures (from legacy test_downloaded_library.py) ──


def _write_m3u8_playlists_make_clip(clip_id: str = "clip1", title: str = "Test Song", duration: float = 120.5):
    clip = MagicMock()
    clip.id = clip_id
    clip.title = title
    clip.duration = duration
    return clip


def _playlist_order_preservation_make_clip(clip_id: str = "clip1", title: str = "Test Song", duration: float = 120.5):
    clip = MagicMock()
    clip.id = clip_id
    clip.title = title
    clip.duration = duration
    return clip


def _playlist_order_preservation_clip_with_art(
    clip_id: str,
    title: str = "Song",
    image_large_url: str = "https://cdn1.suno.ai/img.jpg",
) -> SunoClip:
    return SunoClip(
        id=clip_id,
        title=title,
        audio_url=f"https://cdn1.suno.ai/{clip_id}.mp3",
        image_url="",
        image_large_url=image_large_url,
        is_liked=True,
        status="complete",
        created_at="2026-03-15T10:00:00Z",
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
        display_name="artist",
    )


def _clip(clip_id: str, title: str = "Song") -> SunoClip:
    return make_clip(
        clip_id,
        title=title,
        audio_url=f"https://cdn1.suno.ai/{clip_id}.mp3",
        display_name="artist",
    )


def _clip_with_display(
    clip_id: str,
    title: str = "Song",
    created: str = "2026-03-15T10:00:00Z",
    display_name: str = "testuser",
    image_url: str | None = None,
    image_large_url: str | None = None,
    video_url: str = "",
    video_cover_url: str = "",
) -> SunoClip:
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
        video_cover_url=video_cover_url,
    )


def test_write_m3u8_playlists_writes_absolute_paths(tmp_path: Path) -> None:
    """Playlist entries must use absolute paths for Jellyfin compatibility."""
    clip = _write_m3u8_playlists_make_clip()
    clips_state = {
        "clip1": ManifestEntry.from_dict(
            {"path": "artist/test_song/artist-test_song [clip1aaa].flac", "title": "Test Song"}
        )
    }
    desired = [DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)]

    _write_m3u8_playlists(tmp_path, clips_state, desired)

    content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
    assert "./" not in content
    assert str(tmp_path / "artist/test_song/artist-test_song [clip1aaa].flac") in content


def test_write_m3u8_playlists_uses_clip_duration(tmp_path: Path) -> None:
    """Duration in #EXTINF should come from clip metadata, not hardcoded -1."""
    clip = _write_m3u8_playlists_make_clip(duration=95.7)
    clips_state = {
        "clip1": ManifestEntry.from_dict(
            {"path": "artist/test_song/artist-test_song [clip1aaa].flac", "title": "Test Song"}
        )
    }
    desired = [DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)]

    _write_m3u8_playlists(tmp_path, clips_state, desired)

    content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
    assert "#EXTINF:95," in content


def test_write_m3u8_playlists_duration_fallback_when_zero(tmp_path: Path) -> None:
    """Duration falls back to -1 when clip has no duration."""
    clip = _write_m3u8_playlists_make_clip(duration=0)
    clips_state = {"clip1": ManifestEntry.from_dict({"path": "song.flac", "title": "Song"})}
    desired = [DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)]

    _write_m3u8_playlists(tmp_path, clips_state, desired)

    content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
    assert "#EXTINF:-1," in content


def test_write_m3u8_playlists_header_format(tmp_path: Path) -> None:
    """M3U8 files must start with #EXTM3U and include #PLAYLIST tag."""
    clip = _write_m3u8_playlists_make_clip()
    clips_state = {"clip1": ManifestEntry.from_dict({"path": "song.flac", "title": "Song"})}
    desired = [DownloadItem(clip=clip, sources=["playlist:pl1"], quality=QUALITY_HIGH)]
    source_to_name = {"playlist:pl1": "My Playlist"}

    _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name)

    content = (tmp_path / "My Playlist.m3u8").read_text(encoding="utf-8")
    assert content.startswith("#EXTM3U\n")
    assert "#PLAYLIST:My Playlist\n" in content


def test_write_m3u8_playlists_liked_and_playlist_sources(tmp_path: Path) -> None:
    """Clips with both liked and playlist sources appear in both M3U8 files."""
    clip = _write_m3u8_playlists_make_clip()
    clips_state = {"clip1": ManifestEntry.from_dict({"path": "song.flac", "title": "Song"})}
    desired = [DownloadItem(clip=clip, sources=["liked", "playlist:pl1"], quality=QUALITY_HIGH)]
    source_to_name = {"liked": "Liked Songs", "playlist:pl1": "Favourites"}

    _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name)

    assert (tmp_path / "Liked Songs.m3u8").exists()
    assert (tmp_path / "Favourites.m3u8").exists()


def test_write_m3u8_playlists_clip_liked_and_in_playlist_no_duplicates(tmp_path: Path) -> None:
    """A liked clip also in a playlist appears once in each M3U8, not twice in Liked Songs."""
    clip = _write_m3u8_playlists_make_clip()
    clips_state = {"clip1": ManifestEntry.from_dict({"path": "song.flac", "title": "Song"})}
    desired = [DownloadItem(clip=clip, sources=["liked", "playlist:pl1"], quality=QUALITY_HIGH)]
    source_to_name = {"liked": "Liked Songs", "playlist:pl1": "The Second album"}

    _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name)

    liked_content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
    album_content = (tmp_path / "The Second album.m3u8").read_text(encoding="utf-8")
    assert liked_content.count("song.flac") == 1
    assert album_content.count("song.flac") == 1


def test_write_m3u8_playlists_clip_in_two_playlists(tmp_path: Path) -> None:
    """A clip in two playlists appears in both M3U8 files."""
    clip = _write_m3u8_playlists_make_clip()
    clips_state = {"clip1": ManifestEntry.from_dict({"path": "song.flac", "title": "Song"})}
    desired = [DownloadItem(clip=clip, sources=["playlist:a", "playlist:b"], quality=QUALITY_HIGH)]
    source_to_name = {"playlist:a": "Playlist A", "playlist:b": "Playlist B"}

    _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name)

    assert (tmp_path / "Playlist A.m3u8").exists()
    assert (tmp_path / "Playlist B.m3u8").exists()
    a_content = (tmp_path / "Playlist A.m3u8").read_text(encoding="utf-8")
    b_content = (tmp_path / "Playlist B.m3u8").read_text(encoding="utf-8")
    assert "song.flac" in a_content
    assert "song.flac" in b_content


def test_write_m3u8_playlists_liked_plus_two_playlists(tmp_path: Path) -> None:
    """A clip that is liked and in two playlists appears in all three M3U8 files."""
    clip = _write_m3u8_playlists_make_clip()
    clips_state = {"clip1": ManifestEntry.from_dict({"path": "song.flac", "title": "Song"})}
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


def test_write_m3u8_playlists_my_songs_source_excluded_from_m3u8(tmp_path: Path) -> None:
    """Clips with only a 'my_songs' source produce no M3U8 file."""
    clip = _write_m3u8_playlists_make_clip()
    clips_state = {"clip1": ManifestEntry.from_dict({"path": "song.flac", "title": "Song"})}
    desired = [DownloadItem(clip=clip, sources=["my_songs"], quality=QUALITY_STANDARD)]

    _write_m3u8_playlists(tmp_path, clips_state, desired)

    assert not list(tmp_path.glob("*.m3u8"))


def test_write_m3u8_playlists_cleans_stale_m3u8(tmp_path: Path) -> None:
    """Stale M3U8 files from previous runs are removed."""
    stale = tmp_path / "Old Playlist.m3u8"
    stale.write_text("#EXTM3U\n", encoding="utf-8")

    _write_m3u8_playlists(tmp_path, {}, [])

    assert not stale.exists()


def test_write_m3u8_playlists_skips_clips_without_path(tmp_path: Path) -> None:
    """Clips missing a path in state are excluded from playlists."""
    clip = _write_m3u8_playlists_make_clip()
    clips_state = {"clip1": ManifestEntry.from_dict({"title": "Song"})}  # no "path" key
    desired = [DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)]

    _write_m3u8_playlists(tmp_path, clips_state, desired)

    # No M3U8 written since the only clip had no path
    assert not list(tmp_path.glob("*.m3u8"))


def test_playlist_order_preservation_playlist_order_uses_playlist_order_dict(tmp_path: Path) -> None:
    """Entries are sorted according to playlist_order dict."""
    clips = [_playlist_order_preservation_make_clip(f"clip{i}", f"Song {i}") for i in range(4)]
    clips_state = {
        f"clip{i}": ManifestEntry.from_dict({"path": f"Song {i}.flac", "title": f"Song {i}"}) for i in range(4)
    }
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


def test_playlist_order_preservation_fallback_ordering_without_playlist_order(tmp_path: Path) -> None:
    """Without playlist_order, entries appear in desired iteration order."""
    clips = [_playlist_order_preservation_make_clip(f"clip{i}", f"Song {i}") for i in range(3)]
    clips_state = {
        f"clip{i}": ManifestEntry.from_dict({"path": f"Song {i}.flac", "title": f"Song {i}"}) for i in range(3)
    }
    desired = [DownloadItem(clip=clips[i], sources=["liked"], quality=QUALITY_HIGH) for i in range(3)]

    _write_m3u8_playlists(tmp_path, clips_state, desired, playlist_order={})

    content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
    lines = [ln for ln in content.splitlines() if ln.startswith("#EXTINF")]
    assert len(lines) == 3
    assert "Song 0" in lines[0]
    assert "Song 1" in lines[1]
    assert "Song 2" in lines[2]


def test_playlist_order_preservation_playlist_order_matches_api_response_order(tmp_path: Path) -> None:
    """Playlist output order matches the API response order (reversed from default)."""
    clips = [_playlist_order_preservation_make_clip(f"clip{i}", f"Song {i}") for i in range(5)]
    clips_state = {
        f"clip{i}": ManifestEntry.from_dict({"path": f"Song {i}.flac", "title": f"Song {i}"}) for i in range(5)
    }
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
