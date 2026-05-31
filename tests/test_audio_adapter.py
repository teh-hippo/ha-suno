"""Tests for the production HomeAssistantDownloadedLibraryAudio adapter.

The adapter is a thin delegation layer over functions tested elsewhere
(``audio_stream``, ``audio_retag``). These tests pin the routing
contract — that the adapter picks the right downstream function based
on quality and file extension — without re-testing the downstream
behaviour.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.suno.downloaded_library.audio_adapter import (
    HomeAssistantDownloadedLibraryAudio,
)
from custom_components.suno.downloaded_library.contracts import RenderedAudio
from custom_components.suno.models import TrackMetadata

from .conftest import make_clip


@pytest.fixture
def fake_ffmpeg_manager() -> MagicMock:
    manager = MagicMock()
    manager.binary = "/usr/bin/ffmpeg"
    return manager


@pytest.mark.asyncio
async def test_render_high_quality_routes_to_flac_transcode(
    hass: HomeAssistant, fake_ffmpeg_manager: MagicMock
) -> None:
    client = MagicMock()
    adapter = HomeAssistantDownloadedLibraryAudio(hass, client)
    clip = make_clip("clip-hq")
    meta = TrackMetadata(title="Song", artist="Artist")

    with (
        patch(
            "custom_components.suno.downloaded_library.audio_adapter.get_ffmpeg_manager",
            return_value=fake_ffmpeg_manager,
        ),
        patch(
            "custom_components.suno.downloaded_library.audio_adapter.download_and_transcode_to_flac",
            new=AsyncMock(return_value=b"flac-bytes"),
        ) as mock_flac,
    ):
        result = await adapter.render(clip, "high", meta, image_url="https://x/cover.jpg")

    assert result == RenderedAudio(b"flac-bytes", "flac")
    mock_flac.assert_awaited_once()


@pytest.mark.asyncio
async def test_render_standard_quality_routes_to_mp3(hass: HomeAssistant) -> None:
    client = MagicMock()
    adapter = HomeAssistantDownloadedLibraryAudio(hass, client)
    clip = make_clip("clip-mp3", audio_url="https://cdn1.suno.ai/clip-mp3.mp3")
    meta = TrackMetadata(title="Song", artist="Artist")

    with patch(
        "custom_components.suno.downloaded_library.audio_adapter.download_as_mp3",
        new=AsyncMock(return_value=b"mp3-bytes"),
    ) as mock_mp3:
        result = await adapter.render(clip, "standard", meta, image_url=None)

    assert result == RenderedAudio(b"mp3-bytes", "mp3")
    mock_mp3.assert_awaited_once()


@pytest.mark.asyncio
async def test_render_returns_none_on_failure(hass: HomeAssistant, fake_ffmpeg_manager: MagicMock) -> None:
    client = MagicMock()
    adapter = HomeAssistantDownloadedLibraryAudio(hass, client)
    clip = make_clip("clip-fail")
    meta = TrackMetadata(title="Song", artist="Artist")

    with (
        patch(
            "custom_components.suno.downloaded_library.audio_adapter.get_ffmpeg_manager",
            return_value=fake_ffmpeg_manager,
        ),
        patch(
            "custom_components.suno.downloaded_library.audio_adapter.download_and_transcode_to_flac",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await adapter.render(clip, "high", meta, image_url=None)

    assert result is None


@pytest.mark.asyncio
async def test_retag_routes_flac_through_ffmpeg(
    hass: HomeAssistant, fake_ffmpeg_manager: MagicMock, tmp_path: Path
) -> None:
    client = MagicMock()
    adapter = HomeAssistantDownloadedLibraryAudio(hass, client)
    flac_path = tmp_path / "song.flac"
    flac_path.write_bytes(b"fLaC")
    meta = TrackMetadata(title="Song", artist="Artist")

    with (
        patch(
            "custom_components.suno.downloaded_library.audio_adapter.get_ffmpeg_manager",
            return_value=fake_ffmpeg_manager,
        ),
        patch(
            "custom_components.suno.downloaded_library.audio_adapter.retag_flac",
            new=AsyncMock(return_value=True),
        ) as mock_retag,
    ):
        ok = await adapter.retag(flac_path, meta)

    assert ok is True
    mock_retag.assert_awaited_once_with(fake_ffmpeg_manager.binary, flac_path, meta)


@pytest.mark.asyncio
async def test_retag_routes_mp3_through_executor(hass: HomeAssistant, tmp_path: Path) -> None:
    client = MagicMock()
    adapter = HomeAssistantDownloadedLibraryAudio(hass, client)
    mp3_path = tmp_path / "song.mp3"
    mp3_path.write_bytes(b"ID3")
    meta = TrackMetadata(title="Song", artist="Artist")

    with patch(
        "custom_components.suno.downloaded_library.audio_adapter.retag_mp3",
        return_value=True,
    ) as mock_retag:
        ok = await adapter.retag(mp3_path, meta)

    assert ok is True
    mock_retag.assert_called_once_with(mp3_path, meta)


@pytest.mark.asyncio
async def test_fetch_image_delegates_to_audio_stream(hass: HomeAssistant) -> None:
    client = MagicMock()
    adapter = HomeAssistantDownloadedLibraryAudio(hass, client)

    with patch(
        "custom_components.suno.downloaded_library.audio_adapter.fetch_album_art",
        new=AsyncMock(return_value=b"image-bytes"),
    ) as mock_fetch:
        result = await adapter.fetch_image("https://x/cover.jpg")

    assert result == b"image-bytes"
    mock_fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_video_skips_when_file_exists(hass: HomeAssistant, tmp_path: Path) -> None:
    client = MagicMock()
    adapter = HomeAssistantDownloadedLibraryAudio(hass, client)
    existing = tmp_path / "video.mp4"
    existing.write_bytes(b"existing")

    # Should return early without raising; nothing else to assert beyond
    # that the existing file is not re-downloaded.
    await adapter.download_video("https://x/video.mp4", existing)

    assert existing.read_bytes() == b"existing"


# ── download_video happy path + error branches ────────────────────────


class _FakeResponse:
    """Async context manager that yields chunks via iter_chunked."""

    def __init__(self, status: int, chunks: list[bytes]) -> None:
        self.status = status
        self.content = _FakeContent(chunks)

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _size: int):
        for c in self._chunks:
            yield c


@pytest.mark.asyncio
async def test_download_video_writes_atomically(hass: HomeAssistant, tmp_path: Path) -> None:
    """Successful download writes the full MP4 via .tmp + os.replace."""
    target = tmp_path / "dir" / "video.mp4"
    client = MagicMock()
    adapter = HomeAssistantDownloadedLibraryAudio(hass, client)

    fake_session = MagicMock()
    fake_session.get = MagicMock(return_value=_FakeResponse(200, [b"abc", b"def", b"ghi"]))

    with patch(
        "custom_components.suno.downloaded_library.audio_adapter.async_get_clientsession",
        return_value=fake_session,
    ):
        await adapter.download_video("https://x/video.mp4", target)

    assert target.read_bytes() == b"abcdefghi"
    # No tmp file left behind
    assert not target.with_suffix(".mp4.tmp").exists()


@pytest.mark.asyncio
async def test_download_video_skips_non_200_response(hass: HomeAssistant, tmp_path: Path) -> None:
    """Non-200 responses log a debug line and return without writing."""
    target = tmp_path / "video.mp4"
    client = MagicMock()
    adapter = HomeAssistantDownloadedLibraryAudio(hass, client)

    fake_session = MagicMock()
    fake_session.get = MagicMock(return_value=_FakeResponse(404, []))

    with patch(
        "custom_components.suno.downloaded_library.audio_adapter.async_get_clientsession",
        return_value=fake_session,
    ):
        await adapter.download_video("https://x/missing.mp4", target)

    assert not target.exists()


@pytest.mark.asyncio
async def test_download_video_silences_network_exceptions(hass: HomeAssistant, tmp_path: Path) -> None:
    """A raised exception from session.get is logged and swallowed."""
    target = tmp_path / "video.mp4"
    client = MagicMock()
    adapter = HomeAssistantDownloadedLibraryAudio(hass, client)

    fake_session = MagicMock()
    fake_session.get = MagicMock(side_effect=ConnectionError("dns fail"))

    with patch(
        "custom_components.suno.downloaded_library.audio_adapter.async_get_clientsession",
        return_value=fake_session,
    ):
        await adapter.download_video("https://x/video.mp4", target)

    assert not target.exists()
