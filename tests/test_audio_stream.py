"""Tests for the ``audio_stream`` module (download_as_mp3, ensure_wav_url, fetch_album_art, wav_to_flac).

Split from the legacy 1134-line ``test_audio.py`` by the Round 2 test
restructure.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.suno.audio_stream import (
    download_as_mp3,
    ensure_wav_url,
    fetch_album_art,
    wav_to_flac,
)
from custom_components.suno.models import TrackMetadata

# ── wav_to_flac ─────────────────────────────────────────────────────


async def test_wav_to_flac_happy_path() -> None:
    """ffmpeg succeeds and returns FLAC data."""
    fake_flac = b"fLaC" + b"\x00" * 100

    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(fake_flac, b""))
    proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await wav_to_flac("ffmpeg", b"wav-data", TrackMetadata(title="Title", artist="Artist"))

    assert result == fake_flac


async def test_wav_to_flac_ffmpeg_not_found() -> None:
    """FileNotFoundError when ffmpeg binary is missing."""
    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
        result = await wav_to_flac("ffmpeg", b"wav-data", TrackMetadata(title="Title", artist="Artist"))

    assert result is None


async def test_wav_to_flac_non_zero_exit() -> None:
    """ffmpeg returns non-zero exit code."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", b"error details"))
    proc.returncode = 1

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await wav_to_flac("ffmpeg", b"wav-data", TrackMetadata(title="Title", artist="Artist"))

    assert result is None


async def test_wav_to_flac_timeout() -> None:
    """ffmpeg transcode times out."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    proc.returncode = None
    proc.kill = MagicMock()
    proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await wav_to_flac("ffmpeg", b"wav-data", TrackMetadata(title="Title", artist="Artist"))

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
        meta = TrackMetadata(title="Title", artist="Artist", image_data=image_data)
        result = await wav_to_flac("ffmpeg", b"wav-data", meta)

    assert result == fake_flac
    assert len(created_tmp_files) == 1
    # Temp file should be cleaned up
    import os

    assert not os.path.exists(created_tmp_files[0])


async def test_wav_to_flac_with_album_art_fixes_picture_type() -> None:
    """wav_to_flac patches PICTURE block type 0 to Front Cover (3)."""
    import struct

    # Build a minimal FLAC with a PICTURE block (type=0)
    streaminfo_data = b"\x00" * 34
    streaminfo_block = b"\x00" + len(streaminfo_data).to_bytes(3, "big") + streaminfo_data

    mime = b"image/jpeg"
    pic_data = b"\xff\xd8" + b"\x00" * 10
    picture_payload = (
        struct.pack(">I", 0)  # picture type 0 (Other)
        + struct.pack(">I", len(mime))
        + mime
        + struct.pack(">I", 0)  # description length
        + struct.pack(">III", 100, 100, 24)  # width, height, depth
        + struct.pack(">I", 0)  # colors
        + struct.pack(">I", len(pic_data))
        + pic_data
    )
    # is_last=1 for PICTURE block (type 6 | 0x80 = 0x86)
    picture_block = b"\x86" + len(picture_payload).to_bytes(3, "big") + picture_payload

    fake_flac = b"fLaC" + streaminfo_block + picture_block

    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(fake_flac, b""))
    proc.returncode = 0

    with (
        patch("asyncio.create_subprocess_exec", return_value=proc),
        patch("tempfile.mkstemp", return_value=(999, "/fake/img.jpg")),
        patch("os.write"),
        patch("os.close"),
        patch("os.unlink"),
    ):
        meta = TrackMetadata(title="Title", artist="Artist", image_data=b"\xff\xd8")
        result = await wav_to_flac("ffmpeg", b"wav-data", meta)

    assert result is not None
    # The PICTURE block type should now be 3 (Front Cover)
    pic_block_offset = 4 + 4 + 34  # fLaC + streaminfo header + streaminfo data
    pic_type = struct.unpack(">I", result[pic_block_offset + 4 : pic_block_offset + 8])[0]
    assert pic_type == 3


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

    with patch("custom_components.suno.audio_stream.asyncio.sleep", new_callable=AsyncMock):
        result = await ensure_wav_url(client, "clip-1", polls=5, interval=0.0)

    assert result == "https://cdn.suno.ai/clip.wav"
    client.request_wav.assert_awaited_once()


async def test_ensure_wav_url_never_available() -> None:
    """WAV URL never becomes available, returns None."""
    client = AsyncMock()
    client.get_wav_url = AsyncMock(return_value=None)
    client.request_wav = AsyncMock()

    with patch("custom_components.suno.audio_stream.asyncio.sleep", new_callable=AsyncMock):
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


# ── download_as_mp3 ─────────────────────────────────────────────────


async def test_download_as_mp3_happy_path() -> None:
    """Successful download returns ID3 header + MP3 body."""
    mp3_body = b"\xff\xfb\x90\x00" * 10

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.read = AsyncMock(return_value=mp3_body)

    session = AsyncMock()
    session.get = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        )
    )

    result = await download_as_mp3(
        session, "https://cdn.suno.ai/clip.mp3", TrackMetadata(title="My Song", artist="Artist")
    )

    assert result is not None
    assert result[:3] == b"ID3"
    assert b"My Song" in result
    assert b"Artist" in result
    assert result.endswith(mp3_body)


async def test_download_as_mp3_cdn_failure() -> None:
    """404 from CDN returns None."""
    mock_resp = AsyncMock()
    mock_resp.status = 404

    session = AsyncMock()
    session.get = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        )
    )

    result = await download_as_mp3(session, "https://cdn.suno.ai/clip.mp3", TrackMetadata(title="Title"))

    assert result is None


async def test_download_as_mp3_strips_existing_id3() -> None:
    """Old ID3 header is stripped and replaced with new metadata."""
    # Build a fake ID3 header
    old_tag_body = b"\x00" * 20
    tag_size = len(old_tag_body)
    syncsafe_bytes = bytes(
        [
            (tag_size >> 21) & 0x7F,
            (tag_size >> 14) & 0x7F,
            (tag_size >> 7) & 0x7F,
            tag_size & 0x7F,
        ]
    )
    old_id3 = b"ID3\x04\x00\x00" + syncsafe_bytes + old_tag_body
    audio_frames = b"\xff\xfb\x90\x00" * 10
    raw_mp3 = old_id3 + audio_frames

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.read = AsyncMock(return_value=raw_mp3)

    session = AsyncMock()
    session.get = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        )
    )

    result = await download_as_mp3(session, "https://cdn.suno.ai/clip.mp3", TrackMetadata(title="New Title"))

    assert result is not None
    assert result[:3] == b"ID3"
    assert b"New Title" in result
    # Old null-filled tag body should not appear as a contiguous block
    assert old_id3 not in result
    assert result.endswith(audio_frames)


# ── T7: download_as_mp3 network exception ──────────────────────────


async def test_download_as_mp3_network_exception() -> None:
    """Network exception during MP3 download returns None."""
    import aiohttp

    session = AsyncMock()
    session.get = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(side_effect=aiohttp.ClientError("Connection reset")),
            __aexit__=AsyncMock(return_value=False),
        )
    )

    result = await download_as_mp3(session, "https://cdn.suno.ai/clip.mp3", TrackMetadata(title="My Song"))

    assert result is None


# ── T8: wav_to_flac generic exception ──────────────────────────────


async def test_wav_to_flac_generic_exception() -> None:
    """Generic RuntimeError during subprocess creation returns None."""
    with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("Unexpected")):
        result = await wav_to_flac("ffmpeg", b"wav-data", TrackMetadata(title="Title", artist="Artist"))

    assert result is None
