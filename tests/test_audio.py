"""Tests for the Suno audio module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.suno.audio import (
    _build_id3_header,
    _fix_flac_cover_type,
    _skip_existing_id3,
    download_as_mp3,
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
        result = await wav_to_flac("ffmpeg", b"wav-data", "Title", "Artist", image_data=b"\xff\xd8")

    assert result is not None
    # The PICTURE block type should now be 3 (Front Cover)
    pic_block_offset = 4 + 4 + 34  # fLaC + streaminfo header + streaminfo data
    pic_type = struct.unpack(">I", result[pic_block_offset + 4 : pic_block_offset + 8])[0]
    assert pic_type == 3


# ── _fix_flac_cover_type ────────────────────────────────────────────


def test_fix_flac_cover_type_patches_type_zero() -> None:
    """PICTURE block type 0 is changed to 3 (Front Cover)."""
    import struct

    streaminfo = b"\x00" + (34).to_bytes(3, "big") + b"\x00" * 34
    mime = b"image/jpeg"
    pic_payload = (
        struct.pack(">I", 0)  # type 0
        + struct.pack(">I", len(mime))
        + mime
        + struct.pack(">I", 0)
        + struct.pack(">IIII", 1, 1, 24, 0)
        + struct.pack(">I", 2)
        + b"\xff\xd8"
    )
    picture = b"\x86" + len(pic_payload).to_bytes(3, "big") + pic_payload
    data = b"fLaC" + streaminfo + picture

    result = _fix_flac_cover_type(data)

    # Parse the picture type from result
    pic_offset = 4 + 4 + 34
    pic_type = struct.unpack(">I", result[pic_offset + 4 : pic_offset + 8])[0]
    assert pic_type == 3


def test_fix_flac_cover_type_no_picture_block() -> None:
    """Data without a PICTURE block is returned unchanged."""
    streaminfo = b"\x80" + (34).to_bytes(3, "big") + b"\x00" * 34  # is_last=True
    data = b"fLaC" + streaminfo
    assert _fix_flac_cover_type(data) == data


def test_fix_flac_cover_type_not_flac() -> None:
    """Non-FLAC data is returned unchanged."""
    data = b"RIFF" + b"\x00" * 20
    assert _fix_flac_cover_type(data) == data


def test_fix_flac_cover_type_too_short() -> None:
    """Data shorter than 8 bytes is returned unchanged."""
    assert _fix_flac_cover_type(b"fLaC") == b"fLaC"
    assert _fix_flac_cover_type(b"") == b""


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


def test_skip_existing_id3_large_tag() -> None:
    """Correctly strips an ID3 tag when the syncsafe size has overlapping bits."""
    # Tag body of 248 bytes: syncsafe = [0, 0, 1, 120]
    # This exercises the operator precedence fix: + 10 must apply after |
    tag_body = b"\xab" * 248
    syncsafe_bytes = bytes([0, 0, 1, 120])
    id3_header = b"ID3\x04\x00\x00" + syncsafe_bytes + tag_body
    audio_data = b"\xff\xfb\x90\x00" * 10

    chunk = id3_header + audio_data
    result = _skip_existing_id3(chunk)

    assert result == audio_data


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

    result = await download_as_mp3(session, "https://cdn.suno.ai/clip.mp3", "My Song", "Artist")

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

    result = await download_as_mp3(session, "https://cdn.suno.ai/clip.mp3", "Title")

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

    result = await download_as_mp3(session, "https://cdn.suno.ai/clip.mp3", "New Title")

    assert result is not None
    assert result[:3] == b"ID3"
    assert b"New Title" in result
    # Old null-filled tag body should not appear as a contiguous block
    assert old_id3 not in result
    assert result.endswith(audio_frames)


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


# ── _fix_flac_total_samples ────────────────────────────────────────


def test_fix_flac_total_samples_patches_streaminfo() -> None:
    """Correctly patches total_samples into STREAMINFO block."""
    from custom_components.suno.audio import _fix_flac_total_samples

    # Build a minimal valid FLAC header with STREAMINFO
    # STREAMINFO: 34 bytes
    streaminfo = bytearray(34)
    # Sample rate 48000 Hz at bytes 10-12 (offset from streaminfo start)
    # 48000 = 0xBB80, 20 bits → upper 20 of 24 bits
    sr_packed = 48000 << 4  # shift left 4 to fill upper 20 of 24 bits
    streaminfo[10:13] = sr_packed.to_bytes(3, "big")
    # channels-1 (1 = stereo) and bps-1 (15 = 16-bit) in remaining bits
    streaminfo[12] = (streaminfo[12] & 0xF0) | ((1 << 1) | (15 >> 4))
    streaminfo[13] = (15 & 0x0F) << 4  # upper nibble = lower 4 bits of bps-1
    # total_samples bytes 13-17 (lower nibble of 13 + bytes 14-17) = 0

    block_header = b"\x80"  # is_last=True, type=0 (STREAMINFO)
    block_length = len(streaminfo).to_bytes(3, "big")
    data = b"fLaC" + block_header + block_length + bytes(streaminfo)

    result = _fix_flac_total_samples(data, 120.0)
    # Should have patched total_samples = 120 * 48000 = 5760000
    buf = bytearray(result)
    total_samples = ((buf[21] & 0x0F) << 32) | int.from_bytes(buf[22:26], "big")
    assert total_samples == 5760000


def test_fix_flac_total_samples_zero_duration() -> None:
    """Duration 0 leaves data unchanged."""
    from custom_components.suno.audio import _fix_flac_total_samples

    data = b"fLaC" + b"\x00" * 40
    assert _fix_flac_total_samples(data, 0.0) is data


def test_fix_flac_total_samples_not_flac() -> None:
    """Non-FLAC data is returned unchanged."""
    from custom_components.suno.audio import _fix_flac_total_samples

    data = b"NOT_FLAC" + b"\x00" * 40
    assert _fix_flac_total_samples(data, 120.0) is data


# ── APIC frame in ID3 ──────────────────────────────────────────────


def test_build_id3_header_with_apic() -> None:
    """APIC frame is included when image_data is provided."""
    image = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # Fake JPEG
    header = _build_id3_header("Song", "Artist", image_data=image)

    assert b"APIC" in header
    assert b"image/jpeg" in header
    assert image in header


def test_build_id3_header_txxx_frames() -> None:
    """TXXX frames are included for Suno custom metadata."""
    header = _build_id3_header(
        "Song",
        "Artist",
        suno_style="Dark hardstyle",
        suno_style_summary="Hardstyle",
    )

    assert b"TXXX" in header
    assert b"SUNO_STYLE" in header
    assert b"Dark hardstyle" in header
    assert b"SUNO_STYLE_SUMMARY" in header
    assert b"Hardstyle" in header


def test_build_id3_header_no_genre() -> None:
    """Genre (TCON) frame is never written."""
    header = _build_id3_header("Song", "Artist")
    assert b"TCON" not in header
