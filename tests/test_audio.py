"""Tests for the Suno audio module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.suno.audio import (
    _build_id3_header,
    _skip_existing_id3,
    ensure_wav_url,
    fetch_album_art,
    wav_to_flac,
)
from custom_components.suno.models import SunoClip, clip_meta_hash

# ── wav_to_flac ─────────────────────────────────────────────────────


async def test_wav_to_flac_happy_path() -> None:
    """ffmpeg succeeds and returns FLAC data."""
    fake_flac = b"fLaC" + b"\x00" * 100

    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(fake_flac, b""))
    proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await wav_to_flac("ffmpeg", b"wav-data", "Title", "Artist")

    assert result == fake_flac


async def test_wav_to_flac_ffmpeg_not_found() -> None:
    """FileNotFoundError when ffmpeg binary is missing."""
    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
        result = await wav_to_flac("ffmpeg", b"wav-data", "Title", "Artist")

    assert result is None


async def test_wav_to_flac_non_zero_exit() -> None:
    """ffmpeg returns non-zero exit code."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", b"error details"))
    proc.returncode = 1

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await wav_to_flac("ffmpeg", b"wav-data", "Title", "Artist")

    assert result is None


async def test_wav_to_flac_timeout() -> None:
    """ffmpeg transcode times out."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    proc.returncode = None
    proc.kill = MagicMock()
    proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await wav_to_flac("ffmpeg", b"wav-data", "Title", "Artist")

    assert result is None
    proc.kill.assert_called_once()


async def test_wav_to_flac_with_album_art(tmp_path: object) -> None:
    """Album art creates a temp file that gets cleaned up."""
    fake_flac = b"fLaC" + b"\x00" * 50
    image_data = b"\xff\xd8\xff\xe0" + b"\x00" * 50

    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(fake_flac, b""))
    proc.returncode = 0

    created_tmp_files: list[str] = []

    original_mkstemp = None

    import tempfile

    original_mkstemp = tempfile.mkstemp

    def tracking_mkstemp(suffix: str = "") -> tuple[int, str]:
        fd, path = original_mkstemp(suffix=suffix)
        created_tmp_files.append(path)
        return fd, path

    with (
        patch("asyncio.create_subprocess_exec", return_value=proc),
        patch("tempfile.mkstemp", side_effect=tracking_mkstemp),
    ):
        result = await wav_to_flac("ffmpeg", b"wav-data", "Title", "Artist", image_data=image_data)

    assert result == fake_flac
    assert len(created_tmp_files) == 1
    # Temp file should be cleaned up
    import os

    assert not os.path.exists(created_tmp_files[0])


# ── ensure_wav_url ──────────────────────────────────────────────────


async def test_ensure_wav_url_available_immediately() -> None:
    """WAV URL returned on first call."""
    client = AsyncMock()
    client.get_wav_url = AsyncMock(return_value="https://cdn.suno.ai/clip.wav")

    result = await ensure_wav_url(client, "clip-1")

    assert result == "https://cdn.suno.ai/clip.wav"
    client.request_wav.assert_not_called()


async def test_ensure_wav_url_after_polling() -> None:
    """WAV URL becomes available after a few polls."""
    client = AsyncMock()
    client.get_wav_url = AsyncMock(side_effect=[None, None, "https://cdn.suno.ai/clip.wav"])
    client.request_wav = AsyncMock()

    with patch("custom_components.suno.audio.asyncio.sleep", new_callable=AsyncMock):
        result = await ensure_wav_url(client, "clip-1", polls=5, interval=0.0)

    assert result == "https://cdn.suno.ai/clip.wav"
    client.request_wav.assert_awaited_once()


async def test_ensure_wav_url_never_available() -> None:
    """WAV URL never becomes available, returns None."""
    client = AsyncMock()
    client.get_wav_url = AsyncMock(return_value=None)
    client.request_wav = AsyncMock()

    with patch("custom_components.suno.audio.asyncio.sleep", new_callable=AsyncMock):
        result = await ensure_wav_url(client, "clip-1", polls=3, interval=0.0)

    assert result is None


# ── fetch_album_art ─────────────────────────────────────────────────


async def test_fetch_album_art_success() -> None:
    """Successful album art download."""
    image_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.read = AsyncMock(return_value=image_bytes)

    session = AsyncMock()
    session.get = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        )
    )

    result = await fetch_album_art(session, "https://cdn.suno.ai/image.jpg")

    assert result == image_bytes


async def test_fetch_album_art_network_error() -> None:
    """Network error returns None."""
    session = AsyncMock()
    session.get = MagicMock(side_effect=Exception("Connection reset"))

    result = await fetch_album_art(session, "https://cdn.suno.ai/image.jpg")

    assert result is None


# ── _build_id3_header ───────────────────────────────────────────────


def test_build_id3_header_valid() -> None:
    """Produces bytes starting with ID3 magic."""
    header = _build_id3_header("My Song", "My Artist")

    assert header[:3] == b"ID3"
    assert header[3] == 0x04  # Version 2.4
    assert len(header) > 10
    # Should contain the title and artist text
    assert b"My Song" in header
    assert b"My Artist" in header
    # Should contain frame IDs
    assert b"TIT2" in header
    assert b"TPE1" in header


# ── _skip_existing_id3 ─────────────────────────────────────────────


def test_skip_existing_id3_strips_tag() -> None:
    """Correctly skips an ID3v2 header."""
    # Build a fake ID3 header with syncsafe size
    tag_body = b"\x00" * 20
    tag_size = len(tag_body)
    syncsafe_bytes = bytes(
        [
            (tag_size >> 21) & 0x7F,
            (tag_size >> 14) & 0x7F,
            (tag_size >> 7) & 0x7F,
            tag_size & 0x7F,
        ]
    )
    id3_header = b"ID3\x04\x00\x00" + syncsafe_bytes + tag_body
    audio_data = b"\xff\xfb\x90\x00" * 10  # Fake MP3 frames

    chunk = id3_header + audio_data
    result = _skip_existing_id3(chunk)

    assert result == audio_data


def test_skip_existing_id3_no_tag() -> None:
    """Returns the chunk unchanged when no ID3 tag is present."""
    audio_data = b"\xff\xfb\x90\x00" * 10
    result = _skip_existing_id3(audio_data)
    assert result == audio_data


# ── clip_meta_hash ──────────────────────────────────────────────────


def test_clip_meta_hash_deterministic() -> None:
    """Same clip metadata always produces the same hash."""
    clip = SunoClip(
        id="clip-aaa-111",
        title="Test Song",
        audio_url="https://cdn1.suno.ai/clip-aaa-111.mp3",
        image_url="https://cdn1.suno.ai/image.jpeg",
        image_large_url="https://cdn1.suno.ai/image_large.jpeg",
        is_liked=True,
        status="complete",
        created_at="2026-03-19T10:00:00Z",
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
    )

    hash1 = clip_meta_hash(clip)
    hash2 = clip_meta_hash(clip)

    assert hash1 == hash2
    assert len(hash1) == 12
    assert isinstance(hash1, str)
