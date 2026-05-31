"""Tests for the source_modes submodule of the Downloaded Library engine.

Split from the legacy 5129-line ``test_downloaded_library.py`` by the
Round 2 test restructure.
"""

from __future__ import annotations

from pathlib import Path

from custom_components.suno.const import (
    CONF_ALL_PLAYLISTS,
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_MY_SONGS,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_PLAYLISTS,
    CONF_SHOW_LIKED,
    CONF_SHOW_MY_SONGS,
    CONF_SHOW_PLAYLISTS,
    DOWNLOAD_MODE_ARCHIVE,
    DOWNLOAD_MODE_CACHE,
    DOWNLOAD_MODE_MIRROR,
)
from custom_components.suno.downloaded_library.source_modes import _get_source_mode, _source_preserves_files
from custom_components.suno.models import (
    SunoClip,
)

from .conftest import make_clip

# ── Shared test fixtures (from legacy test_downloaded_library.py) ──


def _clip(clip_id: str, title: str = "Song") -> SunoClip:
    return make_clip(
        clip_id,
        title=title,
        audio_url=f"https://cdn1.suno.ai/{clip_id}.mp3",
        display_name="artist",
    )


def _options(download_path: Path) -> dict[str, object]:
    return {
        CONF_DOWNLOAD_PATH: str(download_path),
        CONF_SHOW_LIKED: True,
        CONF_SHOW_MY_SONGS: False,
        CONF_SHOW_PLAYLISTS: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
    }


def test_source_uses_sync_mode_liked_mirror_mode() -> None:
    assert _get_source_mode("liked", {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_MIRROR}) == DOWNLOAD_MODE_MIRROR


def test_source_uses_sync_mode_liked_archive_mode() -> None:
    assert _get_source_mode("liked", {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_ARCHIVE}) == DOWNLOAD_MODE_ARCHIVE


def test_source_uses_sync_mode_liked_cache_mode() -> None:
    assert _get_source_mode("liked", {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_CACHE}) == DOWNLOAD_MODE_CACHE


def test_source_uses_sync_mode_playlist_mirror_mode() -> None:
    assert (
        _get_source_mode("playlist:abc", {CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_MIRROR}) == DOWNLOAD_MODE_MIRROR
    )


def test_source_uses_sync_mode_playlist_archive_mode() -> None:
    assert (
        _get_source_mode("playlist:abc", {CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_ARCHIVE}) == DOWNLOAD_MODE_ARCHIVE
    )


def test_source_uses_sync_mode_playlist_cache_mode() -> None:
    assert _get_source_mode("playlist:abc", {CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_CACHE}) == DOWNLOAD_MODE_CACHE


def test_source_uses_sync_mode_my_songs_mirror_mode() -> None:
    assert _get_source_mode("my_songs", {CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_MIRROR}) == DOWNLOAD_MODE_MIRROR


def test_source_uses_sync_mode_my_songs_archive_mode() -> None:
    assert _get_source_mode("my_songs", {CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_ARCHIVE}) == DOWNLOAD_MODE_ARCHIVE


def test_source_uses_sync_mode_my_songs_cache_mode() -> None:
    assert _get_source_mode("my_songs", {CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE}) == DOWNLOAD_MODE_CACHE


def test_source_uses_sync_mode_unknown_source_defaults_to_mirror() -> None:
    assert _get_source_mode("unknown_source", {}) == DOWNLOAD_MODE_MIRROR


def test_source_uses_sync_mode_default_mode_when_key_missing() -> None:
    """Missing config key uses DEFAULT_DOWNLOAD_MODE ('mirror')."""
    assert _get_source_mode("liked", {}) == DOWNLOAD_MODE_MIRROR


def test_source_uses_sync_mode_preserves_files_true_for_archive() -> None:
    assert _source_preserves_files("liked", {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_ARCHIVE}) is True


def test_source_uses_sync_mode_preserves_files_false_for_mirror() -> None:
    assert _source_preserves_files("liked", {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_MIRROR}) is False


def test_source_uses_sync_mode_preserves_files_false_for_cache() -> None:
    assert _source_preserves_files("my_songs", {CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE}) is False
