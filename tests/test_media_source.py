"""Tests for the Suno media source."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.media_player import BrowseError
from homeassistant.components.media_source import MediaSourceItem
from homeassistant.core import HomeAssistant

from custom_components.suno.media_source import (
    SunoMediaSource,
    _clip_to_media,
    _folder,
    async_get_media_source,
)
from custom_components.suno.models import SunoClip

from .conftest import make_entry, patch_suno_setup, setup_entry


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


def test_clip_to_media_no_image() -> None:
    """Clip with empty image_url gets None thumbnail."""
    clip = SunoClip(
        id="no-img",
        title="No Image",
        audio_url="https://cdn1.suno.ai/no-img.mp3",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="2026-01-01T00:00:00Z",
        tags="",
        duration=30.0,
        clip_type="gen",
        has_vocal=False,
    )
    media = _clip_to_media(clip)
    assert media.thumbnail is None


def test_folder_creation() -> None:
    """Test folder helper."""
    folder = _folder("test-id", "Test Folder")
    assert folder.identifier == "test-id"
    assert folder.title == "Test Folder"
    assert folder.can_play is False
    assert folder.can_expand is True
    assert folder.children == []


def test_folder_with_children() -> None:
    """Folder can be created with children."""
    child = _folder("child", "Child")
    folder = _folder("parent", "Parent", [child])
    assert len(folder.children) == 1


# ── Integration tests using real HA lifecycle ────────────────────────


async def test_async_get_media_source(hass: HomeAssistant) -> None:
    """async_get_media_source returns a SunoMediaSource."""
    source = await async_get_media_source(hass)
    assert isinstance(source, SunoMediaSource)


async def test_browse_root(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Browsing root shows liked, recent, playlists, all."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "", None)
    result = await source.async_browse_media(item)

    assert result.title == "Suno"
    titles = [c.title for c in result.children]
    assert any("Liked" in t for t in titles)
    assert any("Recent" in t for t in titles)
    assert any("Playlists" in t for t in titles)
    assert any("All Songs" in t for t in titles)


async def test_browse_root_no_entry(hass: HomeAssistant) -> None:
    """Browsing root with no config entry returns empty folder."""
    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "", None)
    result = await source.async_browse_media(item)
    assert result.title == "Suno"
    assert result.children == []


async def test_browse_root_options_disable_folders(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Disabling options hides the corresponding folders."""
    entry = make_entry(
        options={
            "show_liked": False,
            "show_recent": False,
            "show_playlists": False,
            "recent_count": 20,
            "cache_ttl_minutes": 30,
        }
    )
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "", None)
    result = await source.async_browse_media(item)

    titles = [c.title for c in result.children]
    assert not any("Liked" in t for t in titles)
    assert not any("Recent" in t for t in titles)
    assert not any("Playlists" in t for t in titles)
    # "All Songs" is always present
    assert any("All Songs" in t for t in titles)


async def test_browse_liked(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Browsing 'liked' shows only liked clips."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "liked", None)
    result = await source.async_browse_media(item)

    assert "Liked" in result.title
    # Only clip-aaa-111 is liked
    assert len(result.children) == 1
    assert result.children[0].identifier == "clip/clip-aaa-111"


async def test_browse_recent(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Browsing 'recent' fetches live from get_feed."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "recent", None)
    result = await source.async_browse_media(item)

    assert "Recent" in result.title
    assert len(result.children) == 2
    mock_suno_client.get_feed.assert_awaited()


async def test_browse_recent_fallback_on_error(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Recent falls back to cached data when live fetch fails."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    # Make live fetch fail after initial setup
    mock_suno_client.get_feed.side_effect = Exception("Network error")

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "recent", None)
    result = await source.async_browse_media(item)

    # Falls back to cached clips
    assert "Recent" in result.title
    assert len(result.children) == 2


async def test_browse_all_small_library(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """All songs with <=50 clips shows flat list."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "all", None)
    result = await source.async_browse_media(item)

    assert "All Songs" in result.title
    # 2 clips, under _CHUNK_SIZE of 50 → flat list of clips
    assert len(result.children) == 2
    assert result.children[0].identifier == "clip/clip-aaa-111"


async def test_browse_all_large_library_chunks(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """All songs with >50 clips creates chunked virtual folders."""
    # Generate 75 clips to exceed _CHUNK_SIZE of 50
    many_clips = [
        SunoClip(
            id=f"clip-{i:03d}",
            title=f"Song {i}",
            audio_url=f"https://cdn1.suno.ai/clip-{i:03d}.mp3",
            image_url="",
            image_large_url="",
            is_liked=False,
            status="complete",
            created_at="2026-01-01T00:00:00Z",
            tags="",
            duration=60.0,
            clip_type="gen",
            has_vocal=True,
        )
        for i in range(75)
    ]
    mock_suno_client.get_all_songs.return_value = many_clips

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "all", None)
    result = await source.async_browse_media(item)

    # Should be 2 folders: Songs 1-50 and Songs 51-75
    assert len(result.children) == 2
    assert result.children[0].identifier == "all/page/0"
    assert result.children[1].identifier == "all/page/1"
    assert "1-50" in result.children[0].title
    assert "51-75" in result.children[1].title


async def test_browse_all_page(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Browsing a page returns the correct chunk of clips."""
    many_clips = [
        SunoClip(
            id=f"clip-{i:03d}",
            title=f"Song {i}",
            audio_url=f"https://cdn1.suno.ai/clip-{i:03d}.mp3",
            image_url="",
            image_large_url="",
            is_liked=False,
            status="complete",
            created_at="2026-01-01T00:00:00Z",
            tags="",
            duration=60.0,
            clip_type="gen",
            has_vocal=True,
        )
        for i in range(75)
    ]
    mock_suno_client.get_all_songs.return_value = many_clips

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "all/page/1", None)
    result = await source.async_browse_media(item)

    # Page 1 = clips 50-74 (25 clips)
    assert len(result.children) == 25
    assert result.children[0].identifier == "clip/clip-050"


async def test_browse_unknown_identifier(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Unknown identifier returns empty folder."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "nonexistent/path", None)
    result = await source.async_browse_media(item)

    assert result.title == "Suno"
    assert result.children == []


# ── Resolve media ────────────────────────────────────────────────────


async def test_resolve_media_success(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Resolving a clip returns a PlayMedia with the audio URL."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "clip/clip-aaa-111", None)
    result = await source.async_resolve_media(item)

    assert result.url == "/api/suno/media/clip-aaa-111.mp3"
    assert result.mime_type == "audio/mpeg"


async def test_resolve_media_unknown_clip(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Resolving a clip not in cache still returns a proxy URL."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "clip/nonexistent", None)

    result = await source.async_resolve_media(item)
    assert result.url == "/api/suno/media/nonexistent.mp3"
    assert result.mime_type == "audio/mpeg"


async def test_resolve_media_bad_identifier(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Resolving with a non-clip identifier raises BrowseError."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "playlist/pl-001", None)

    with pytest.raises(BrowseError, match="Unknown media identifier"):
        await source.async_resolve_media(item)


async def test_resolve_media_no_entry(hass: HomeAssistant) -> None:
    """Resolving with no config entry raises BrowseError."""
    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "clip/some-id", None)

    with pytest.raises(BrowseError, match="not configured"):
        await source.async_resolve_media(item)


async def test_resolve_media_empty_identifier(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Resolving with empty identifier raises BrowseError."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "", None)

    with pytest.raises(BrowseError, match="Unknown media identifier"):
        await source.async_resolve_media(item)


async def test_resolve_media_from_liked_clips(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Resolving a clip found only in liked_clips succeeds."""
    # Return empty main clips so it must be found in liked_clips
    mock_suno_client.get_all_songs.return_value = []

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "clip/clip-aaa-111", None)
    result = await source.async_resolve_media(item)

    assert result.url == "/api/suno/media/clip-aaa-111.mp3"
    assert result.mime_type == "audio/mpeg"


async def test_browse_playlists(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Browsing playlists shows playlist folders."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "playlists", None)
    result = await source.async_browse_media(item)

    assert "Playlists" in result.title
    assert len(result.children) == 1
    assert result.children[0].identifier == "playlist/pl-001"


async def test_browse_playlist_detail(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Browsing a specific playlist shows its clips."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "playlist/pl-001", None)
    result = await source.async_browse_media(item)

    assert result.title == "My Favourites (1)"
    assert len(result.children) == 1


async def test_browse_playlist_error(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Browsing a playlist with no clips in coordinator returns empty children."""
    entry = make_entry()
    # Make playlist clip fetch fail during coordinator refresh
    mock_suno_client.get_playlist_clips.side_effect = Exception("Network error")
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "playlist/pl-001", None)
    result = await source.async_browse_media(item)

    assert "My Favourites" in result.title
    assert len(result.children) == 0


async def test_browse_playlist_unknown_id(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Browsing a playlist with unknown ID uses generic name."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    mock_suno_client.get_playlist_clips.return_value = []

    source = SunoMediaSource(hass)
    item = MediaSourceItem(hass, "suno", "playlist/unknown-id", None)
    result = await source.async_browse_media(item)

    assert result.title == "Playlist (0)"
