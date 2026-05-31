"""Tests for the paths submodule of the Downloaded Library engine.

Split from the legacy 5129-line ``test_downloaded_library.py`` by the
Round 2 test restructure.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from custom_components.suno.downloaded_library.paths import _clip_path, _safe_name, _video_clip_path
from custom_components.suno.models import (
    SunoClip,
)

from .conftest import make_clip

# ── Shared test fixtures (from legacy test_downloaded_library.py) ──


def _clip_path_make_clip(
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


def _video_clip_path_make_clip(
    clip_id: str = "abcd1234-test-clip-id",
    title: str = "My Song",
    display: str = "testuser",
):
    clip = MagicMock()
    clip.id = clip_id
    clip.title = title
    clip.display_name = display
    return clip


def _make_clip(clip_id: str, title: str = "Song", created: str = "2026-03-15T10:00:00Z") -> SunoClip:
    """Construct a minimal SunoClip for path/playlist/helper tests."""
    return make_clip(clip_id, title=title, created_at=created, image_url="", image_large_url="")


# ── TestSafeName (converted to free functions) ────────────────────


def test_safe_name_preserves_spaces_and_case() -> None:
    assert _safe_name("Hello World") == "Hello World"


def test_safe_name_unsafe_chars_replaced() -> None:
    result = _safe_name('test<>:"/\\|?*file')
    assert "<" not in result
    assert "/" not in result


def test_safe_name_empty_string_returns_untitled() -> None:
    assert _safe_name("") == "untitled"


def test_safe_name_unicode_preserved() -> None:
    assert _safe_name("café résumé") == "café résumé"


def test_safe_name_emoji_preserved() -> None:
    assert "Music" in _safe_name("🎵 Music")


def test_safe_name_traversal_neutralised() -> None:
    assert "/" not in _safe_name("../../etc/passwd")


def test_safe_name_windows_reserved_handled() -> None:
    result = _safe_name("CON")
    assert result != "CON"  # pathvalidate appends underscore


def test_safe_name_truncates_long_names() -> None:
    assert len(_safe_name("a" * 300)) <= 200


def test_clip_path_high_quality_flac() -> None:
    clip = _clip_path_make_clip()
    result = _clip_path(clip, "high")
    assert result == "testuser/My Song/testuser-My Song [abcd1234].flac"


def test_clip_path_standard_quality_mp3() -> None:
    clip = _clip_path_make_clip()
    result = _clip_path(clip, "standard")
    assert result == "testuser/My Song/testuser-My Song [abcd1234].mp3"


def test_clip_path_missing_display_name() -> None:
    clip = _clip_path_make_clip(display_name="")
    result = _clip_path(clip, "high")
    assert result == "Suno/My Song/Suno-My Song [abcd1234].flac"


def test_clip_path_different_clips_same_title_get_different_paths() -> None:
    clip_a = _clip_path_make_clip(clip_id="aaaaaaaa-1111-2222-3333-444444444444")
    clip_b = _clip_path_make_clip(clip_id="bbbbbbbb-1111-2222-3333-444444444444")
    assert _clip_path(clip_a, "high") != _clip_path(clip_b, "high")


def test_video_clip_path_video_path_alongside_audio_flac() -> None:
    clip = _video_clip_path_make_clip()
    assert _video_clip_path(clip) == "testuser/My Song/testuser-My Song [abcd1234].mp4"


def test_video_clip_path_video_path_basename_matches_audio_basename() -> None:
    clip = _video_clip_path_make_clip()
    flac = _clip_path(clip, "high")
    mp3 = _clip_path(clip, "standard")
    video = _video_clip_path(clip)
    # Same parent directory and same basename — only suffix differs.
    assert Path(video).parent == Path(flac).parent == Path(mp3).parent
    assert Path(video).stem == Path(flac).stem == Path(mp3).stem
    assert Path(video).suffix == ".mp4"


def test_video_clip_path_no_music_videos_directory_in_path() -> None:
    """The legacy music-videos/ directory should never appear in the path."""
    clip = _video_clip_path_make_clip()
    assert "music-videos" not in _video_clip_path(clip)


def test_video_clip_path_video_path_missing_display_name_falls_back_to_suno() -> None:
    clip = _video_clip_path_make_clip(display="")
    assert _video_clip_path(clip) == "Suno/My Song/Suno-My Song [abcd1234].mp4"
