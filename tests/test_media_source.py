"""Tests for the Suno media source."""

from __future__ import annotations

from custom_components.suno.api import SunoClip
from custom_components.suno.media_source import _clip_to_media, _folder


def test_clip_to_media() -> None:
    """Test converting a clip to a media item."""
    clip = SunoClip(
        id="test-123",
        title="Test Song",
        audio_url="https://cdn1.suno.ai/test-123.mp3",
        image_url="https://cdn1.suno.ai/image_test-123.jpeg",
        image_large_url="",
        is_liked=True,
        status="complete",
        created_at="2026-01-01T00:00:00Z",
        tags="pop",
        duration=60.0,
        clip_type="gen",
        has_vocal=True,
    )
    media = _clip_to_media(clip)
    assert media.identifier == "clip/test-123"
    assert media.title == "Test Song"
    assert media.can_play is True
    assert media.can_expand is False
    assert media.thumbnail == "https://cdn1.suno.ai/image_test-123.jpeg"


def test_clip_to_media_no_audio() -> None:
    """Clip with no audio URL should not be playable."""
    clip = SunoClip(
        id="no-audio",
        title="No Audio",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="2026-01-01T00:00:00Z",
        tags="",
        duration=0.0,
        clip_type="gen",
        has_vocal=False,
    )
    media = _clip_to_media(clip)
    assert media.can_play is False


def test_folder_creation() -> None:
    """Test folder helper."""
    folder = _folder("test-id", "Test Folder")
    assert folder.identifier == "test-id"
    assert folder.title == "Test Folder"
    assert folder.can_play is False
    assert folder.can_expand is True
