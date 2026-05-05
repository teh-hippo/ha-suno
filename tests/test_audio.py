"""Tests for the Suno audio module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.suno.audio_metadata import (
    build_id3_header,
    extract_apic,
    fix_flac_cover_type,
    skip_existing_id3,
)
from custom_components.suno.audio_retag import retag_flac, retag_mp3
from custom_components.suno.audio_stream import (
    download_as_mp3,
    ensure_wav_url,
    fetch_album_art,
    wav_to_flac,
)
from custom_components.suno.models import SunoClip, TrackMetadata, clip_meta_hash

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


# ── fix_flac_cover_type ────────────────────────────────────────────


def testfix_flac_cover_type_patches_type_zero() -> None:
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

    result = fix_flac_cover_type(data)

    # Parse the picture type from result
    pic_offset = 4 + 4 + 34
    pic_type = struct.unpack(">I", result[pic_offset + 4 : pic_offset + 8])[0]
    assert pic_type == 3


def testfix_flac_cover_type_no_picture_block() -> None:
    """Data without a PICTURE block is returned unchanged."""
    streaminfo = b"\x80" + (34).to_bytes(3, "big") + b"\x00" * 34  # is_last=True
    data = b"fLaC" + streaminfo
    assert fix_flac_cover_type(data) == data


def testfix_flac_cover_type_not_flac() -> None:
    """Non-FLAC data is returned unchanged."""
    data = b"RIFF" + b"\x00" * 20
    assert fix_flac_cover_type(data) == data


def testfix_flac_cover_type_too_short() -> None:
    """Data shorter than 8 bytes is returned unchanged."""
    assert fix_flac_cover_type(b"fLaC") == b"fLaC"
    assert fix_flac_cover_type(b"") == b""


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


# ── build_id3_header ───────────────────────────────────────────────


def testbuild_id3_header_valid() -> None:
    """Produces bytes starting with ID3 magic."""
    header = build_id3_header(TrackMetadata(title="My Song", artist="My Artist"))

    assert header[:3] == b"ID3"
    assert header[3] == 0x03  # Version 2.3
    assert len(header) > 10
    # Should contain the title and artist text
    assert b"My Song" in header
    assert b"My Artist" in header
    # Should contain frame IDs
    assert b"TIT2" in header
    assert b"TPE1" in header


# ── skip_existing_id3 ─────────────────────────────────────────────


def testskip_existing_id3_strips_tag() -> None:
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
    result = skip_existing_id3(chunk)

    assert result == audio_data


def testskip_existing_id3_no_tag() -> None:
    """Returns the chunk unchanged when no ID3 tag is present."""
    audio_data = b"\xff\xfb\x90\x00" * 10
    result = skip_existing_id3(audio_data)
    assert result == audio_data


def testskip_existing_id3_large_tag() -> None:
    """Correctly strips an ID3 tag when the syncsafe size has overlapping bits."""
    # Tag body of 248 bytes: syncsafe = [0, 0, 1, 120]
    # This exercises the operator precedence fix: + 10 must apply after |
    tag_body = b"\xab" * 248
    syncsafe_bytes = bytes([0, 0, 1, 120])
    id3_header = b"ID3\x04\x00\x00" + syncsafe_bytes + tag_body
    audio_data = b"\xff\xfb\x90\x00" * 10

    chunk = id3_header + audio_data
    result = skip_existing_id3(chunk)

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


# ── fix_flac_total_samples ────────────────────────────────────────


def testfix_flac_total_samples_patches_streaminfo() -> None:
    """Correctly patches total_samples into STREAMINFO block."""
    from custom_components.suno.audio_metadata import fix_flac_total_samples

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

    result = fix_flac_total_samples(data, 120.0)
    # Should have patched total_samples = 120 * 48000 = 5760000
    buf = bytearray(result)
    total_samples = ((buf[21] & 0x0F) << 32) | int.from_bytes(buf[22:26], "big")
    assert total_samples == 5760000


def testfix_flac_total_samples_zero_duration() -> None:
    """Duration 0 leaves data unchanged."""
    from custom_components.suno.audio_metadata import fix_flac_total_samples

    data = b"fLaC" + b"\x00" * 40
    assert fix_flac_total_samples(data, 0.0) is data


def testfix_flac_total_samples_not_flac() -> None:
    """Non-FLAC data is returned unchanged."""
    from custom_components.suno.audio_metadata import fix_flac_total_samples

    data = b"NOT_FLAC" + b"\x00" * 40
    assert fix_flac_total_samples(data, 120.0) is data


# ── APIC frame in ID3 ──────────────────────────────────────────────


def testbuild_id3_header_with_apic() -> None:
    """APIC frame is included when image_data is provided."""
    image = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # Fake JPEG
    header = build_id3_header(TrackMetadata(title="Song", artist="Artist", image_data=image))

    assert b"APIC" in header
    assert b"image/jpeg" in header
    assert image in header


def testbuild_id3_header_txxx_frames() -> None:
    """TXXX frames are included for Suno custom metadata."""
    header = build_id3_header(
        TrackMetadata(
            title="Song",
            artist="Artist",
            suno_style="Dark hardstyle",
            suno_style_summary="Hardstyle",
        )
    )

    assert b"TXXX" in header
    assert b"SUNO_STYLE" in header
    assert b"Dark hardstyle" in header
    assert b"SUNO_STYLE_SUMMARY" in header
    assert b"Hardstyle" in header


def testbuild_id3_header_no_genre() -> None:
    """Genre (TCON) frame is never written."""
    header = build_id3_header(TrackMetadata(title="Song", artist="Artist"))
    assert b"TCON" not in header


def testbuild_id3_header_all_custom_fields() -> None:
    """All Suno custom metadata fields produce TXXX frames."""
    header = build_id3_header(
        TrackMetadata(
            title="Song",
            artist="Artist",
            suno_model="chirp-crow (v5)",
            suno_handle="myhandle",
            suno_parent="abcd1234",
            suno_lineage="Remix of abcd1234",
        )
    )
    assert b"SUNO_MODEL" in header
    assert b"chirp-crow (v5)" in header
    assert b"SUNO_HANDLE" in header
    assert b"myhandle" in header
    assert b"SUNO_PARENT" in header
    assert b"SUNO_LINEAGE" in header


# ── SunoClip properties ───────────────────────────────────────────


def test_suno_model_combined() -> None:
    """suno_model property combines model_name and major_model_version."""
    clip = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        model_name="chirp-crow",
        major_model_version="v5",
    )
    assert clip.suno_model == "chirp-crow (v5)"


def test_suno_model_no_major() -> None:
    """suno_model with empty major_model_version returns just model_name."""
    clip = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        model_name="chirp-chirp",
        major_model_version="",
    )
    assert clip.suno_model == "chirp-chirp"


def test_suno_lineage_remix() -> None:
    """suno_lineage formats remix with parent ID."""
    clip = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        is_remix=True,
        edited_clip_id="d57c503f-cbaa-4651-aaf4-628d363ccf4c",
    )
    assert clip.suno_lineage == "Remix of d57c503f"


def test_suno_lineage_with_history() -> None:
    """suno_lineage formats edit history with time ranges and lyrics."""
    clip = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        edited_clip_id="d57c503f-cbaa-4651-aaf4-628d363ccf4c",
        history=[
            {
                "id": "d57c503f-cbaa-4651-aaf4-628d363ccf4c",
                "infill_start_s": 58.44,
                "infill_end_s": 61.8,
                "infill_lyrics": "everybody said\nHISSSSS!",
            }
        ],
    )
    result = clip.suno_lineage
    assert "Derived from d57c503f" in result
    assert "Edit 00:58-01:01" in result
    assert "everybody said" in result


def test_clip_meta_hash_excludes_display_name() -> None:
    """Meta hash does NOT change when only display_name changes.

    Path-affecting fields like display_name are handled by path comparison,
    not the content hash.
    """
    clip1 = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="img.jpg",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        display_name="user1",
    )
    clip2 = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="img.jpg",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        display_name="user2",
    )
    assert clip_meta_hash(clip1) == clip_meta_hash(clip2)


# ── TC-11: from_api_response new fields ───────────────────────────


def test_from_api_response_parses_video_url() -> None:
    """from_api_response correctly parses video_url from API data."""
    raw = {
        "id": "vid-clip",
        "status": "complete",
        "video_url": "https://cdn2.suno.ai/vid-clip.mp4",
        "metadata": {"type": "gen"},
    }
    clip = SunoClip.from_api_response(raw)
    assert clip.video_url == "https://cdn1.suno.ai/vid-clip.mp4"


def test_from_api_response_parses_display_name_and_handle() -> None:
    """from_api_response correctly parses display_name and handle."""
    raw = {
        "id": "dn-clip",
        "status": "complete",
        "display_name": "Cool Artist",
        "handle": "cool-artist-42",
        "metadata": {"type": "gen"},
    }
    clip = SunoClip.from_api_response(raw)
    assert clip.display_name == "Cool Artist"
    assert clip.handle == "cool-artist-42"


def test_from_api_response_parses_edited_clip_id_and_is_remix() -> None:
    """from_api_response correctly parses edited_clip_id and is_remix."""
    raw = {
        "id": "remix-clip",
        "status": "complete",
        "metadata": {
            "type": "gen",
            "edited_clip_id": "parent-abcd-1234",
            "is_remix": True,
        },
    }
    clip = SunoClip.from_api_response(raw)
    assert clip.edited_clip_id == "parent-abcd-1234"
    assert clip.is_remix is True


# ── TC-12: suno_lineage edge cases ────────────────────────────────


def test_suno_lineage_multiple_history_entries() -> None:
    """suno_lineage with multiple history entries."""
    clip = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        edited_clip_id="aaaa1111-bbbb-cccc-dddd-eeee2222ffff",
        history=[
            {
                "id": "aaaa1111-bbbb-cccc-dddd-eeee2222ffff",
                "infill_start_s": 0,
                "infill_end_s": 30,
                "infill_lyrics": "first edit",
            },
            {
                "id": "bbbb2222-cccc-dddd-eeee-ffff3333aaaa",
                "infill_start_s": 60,
                "infill_end_s": 90,
                "infill_lyrics": "second edit",
            },
        ],
    )
    result = clip.suno_lineage
    lines = result.split("\n")
    assert len(lines) == 3
    assert "Derived from aaaa1111" in lines[0]
    assert "Edit 00:00-00:30" in lines[1]
    assert "first edit" in lines[1]
    assert "Edit 01:00-01:30" in lines[2]
    assert "second edit" in lines[2]


def test_suno_lineage_remix_empty_edited_clip_id() -> None:
    """suno_lineage when is_remix=True but edited_clip_id is empty."""
    clip = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        is_remix=True,
        edited_clip_id="",
    )
    assert clip.suno_lineage == ""


def test_suno_lineage_history_none() -> None:
    """suno_lineage when history is None."""
    clip = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        edited_clip_id="",
        history=None,
    )
    assert clip.suno_lineage == ""


def test_clip_meta_hash_changes_when_video_url_changes() -> None:
    """clip_meta_hash changes when video_url changes."""
    base = dict(
        id="test",
        title="T",
        audio_url="",
        image_url="img.jpg",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        display_name="user",
    )
    clip1 = SunoClip(**base, video_url="https://cdn1.suno.ai/v1.mp4")
    clip2 = SunoClip(**base, video_url="https://cdn1.suno.ai/v2.mp4")
    assert clip_meta_hash(clip1) != clip_meta_hash(clip2)


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


# ── T13: from_api_response CDN rewrite ──────────────────────────────


def test_from_api_response_cdn_rewrite() -> None:
    """from_api_response rewrites cdn2 URLs and handles None URLs."""
    raw = {
        "id": "clip-cdn-test",
        "title": "CDN Test",
        "audio_url": "https://cdn1.suno.ai/clip-cdn-test.mp3",
        "image_url": "https://cdn2.suno.ai/image_clip-cdn-test.jpeg",
        "image_large_url": "https://cdn2.suno.ai/image_large_clip-cdn-test.jpeg",
        "video_url": "https://cdn2.suno.ai/clip-cdn-test.mp4",
        "video_cover_url": None,
        "is_liked": True,
        "status": "complete",
        "created_at": "2026-03-19T10:00:00Z",
        "metadata": {
            "tags": "pop",
            "duration": 120.0,
            "type": "gen",
            "has_vocal": True,
        },
    }

    clip = SunoClip.from_api_response(raw)

    # cdn2 URLs should be rewritten to cdn1
    assert "cdn2" not in clip.image_url
    assert clip.image_url == "https://cdn1.suno.ai/image_clip-cdn-test.jpeg"
    assert "cdn2" not in clip.image_large_url
    assert clip.image_large_url == "https://cdn1.suno.ai/image_large_clip-cdn-test.jpeg"
    assert "cdn2" not in clip.video_url
    assert clip.video_url == "https://cdn1.suno.ai/clip-cdn-test.mp4"
    # None video_cover_url should become empty string
    assert clip.video_cover_url == ""


# ── retag_mp3 ──────────────────────────────────────────────────────


def test_retag_mp3_updates_metadata(tmp_path) -> None:
    """retag_mp3 strips old ID3 and writes new metadata."""
    # Build an MP3 with old metadata
    old_meta = TrackMetadata(title="Old Title", artist="Old Artist", album="Old Album")
    old_header = build_id3_header(old_meta)
    audio_data = b"\xff\xfb\x90\x00" + b"\x00" * 100  # Fake MP3 frame
    mp3_path = tmp_path / "test.mp3"
    mp3_path.write_bytes(old_header + audio_data)

    # Re-tag with new metadata
    new_meta = TrackMetadata(title="New Title", artist="New Artist", album="New Album")
    assert retag_mp3(mp3_path, new_meta) is True

    # Verify new file has correct structure
    result = mp3_path.read_bytes()
    assert result[:3] == b"ID3"
    # Audio data should be preserved
    assert result.endswith(audio_data)


def test_retag_mp3_preserves_existing_album_art(tmp_path) -> None:
    """retag_mp3 preserves album art from the existing ID3 APIC frame."""
    image_data = b"\xff\xd8\xff\xe0" + b"\x42" * 50  # Fake JPEG
    old_meta = TrackMetadata(title="Song", artist="Old", album="Album", image_data=image_data)
    old_header = build_id3_header(old_meta)
    audio_data = b"\xff\xfb\x90\x00" + b"\x00" * 100
    mp3_path = tmp_path / "test.mp3"
    mp3_path.write_bytes(old_header + audio_data)

    # Re-tag WITHOUT providing image_data
    new_meta = TrackMetadata(title="Song", artist="New Artist", album="Album")
    assert retag_mp3(mp3_path, new_meta) is True

    # The re-tagged file should still contain the APIC frame
    result = mp3_path.read_bytes()
    extracted = extract_apic(result)
    assert extracted is not None
    assert extracted == image_data


def test_retag_mp3_falls_back_to_cover_jpg(tmp_path) -> None:
    """retag_mp3 reads cover.jpg as fallback when no APIC frame exists."""
    # MP3 with no album art
    old_meta = TrackMetadata(title="Song", artist="Old")
    old_header = build_id3_header(old_meta)
    audio_data = b"\xff\xfb\x90\x00" + b"\x00" * 100
    mp3_dir = tmp_path / "artist" / "album"
    mp3_dir.mkdir(parents=True)
    mp3_path = mp3_dir / "test.mp3"
    mp3_path.write_bytes(old_header + audio_data)

    # Place a cover.jpg sidecar
    cover_data = b"\xff\xd8\xff\xe0COVER"
    (mp3_dir / "cover.jpg").write_bytes(cover_data)

    new_meta = TrackMetadata(title="Song", artist="New")
    assert retag_mp3(mp3_path, new_meta) is True

    # Should have embedded the cover.jpg data
    result = mp3_path.read_bytes()
    extracted = extract_apic(result)
    assert extracted == cover_data


def test_retag_mp3_missing_file(tmp_path) -> None:
    """retag_mp3 returns False for non-existent file."""
    meta = TrackMetadata(title="Song", artist="Artist")
    assert retag_mp3(tmp_path / "nonexistent.mp3", meta) is False


def test_retag_mp3_atomic_write(tmp_path) -> None:
    """retag_mp3 uses atomic write -- no .tmp file remains on success."""
    old_meta = TrackMetadata(title="Old", artist="Old")
    audio_data = b"\xff\xfb\x90\x00" + b"\x00" * 50
    mp3_path = tmp_path / "test.mp3"
    mp3_path.write_bytes(build_id3_header(old_meta) + audio_data)

    new_meta = TrackMetadata(title="New", artist="New")
    retag_mp3(mp3_path, new_meta)

    assert not (tmp_path / "test.tmp").exists()
    assert mp3_path.exists()


# ── extract_apic ──────────────────────────────────────────────────


def testextract_apic_from_valid_id3() -> None:
    """Extracts image data from a valid APIC frame."""
    image_data = b"\xff\xd8\xff\xe0TESTIMAGE"
    meta = TrackMetadata(title="T", artist="A", image_data=image_data)
    header = build_id3_header(meta)
    result = extract_apic(header + b"\xff\xfb\x90\x00")
    assert result == image_data


def testextract_apic_no_id3() -> None:
    """Returns None for data without ID3 header."""
    assert extract_apic(b"\xff\xfb\x90\x00" + b"\x00" * 100) is None


def testextract_apic_no_apic_frame() -> None:
    """Returns None when ID3 exists but has no APIC frame."""
    meta = TrackMetadata(title="T", artist="A")  # No image_data
    header = build_id3_header(meta)
    assert extract_apic(header + b"\xff\xfb\x90\x00") is None


# ── retag_flac ─────────────────────────────────────────────────────


async def test_retag_flac_calls_ffmpeg_with_copy(tmp_path) -> None:
    """retag_flac uses ffmpeg with -c:a copy (no transcoding)."""
    flac_path = tmp_path / "test.flac"
    flac_data = b"fLaC" + b"\x00" * 50
    flac_path.write_bytes(flac_data)

    meta = TrackMetadata(title="New Title", artist="New Artist", album="New Album")

    proc_mock = AsyncMock()
    proc_mock.communicate = AsyncMock(return_value=(b"", b""))
    proc_mock.returncode = 0

    with patch(
        "custom_components.suno.audio_retag.asyncio.create_subprocess_exec", return_value=proc_mock
    ) as mock_exec:
        # The retag writes to .retag.tmp which we need to simulate
        tmp_out = flac_path.with_suffix(".retag.tmp")

        async def _fake_communicate(*args, **kwargs):
            tmp_out.write_bytes(flac_data)
            return b"", b""

        proc_mock.communicate = _fake_communicate

        result = await retag_flac("/usr/bin/ffmpeg", flac_path, meta)

    assert result is True
    # Verify -c:a copy was in the ffmpeg args (no transcoding)
    call_args = mock_exec.call_args[0]
    assert "-c:a" in call_args
    copy_idx = list(call_args).index("-c:a")
    assert call_args[copy_idx + 1] == "copy"
    # Verify metadata flags
    assert f"title={meta.title}" in " ".join(str(a) for a in call_args)
    assert f"artist={meta.artist}" in " ".join(str(a) for a in call_args)


async def test_retag_flac_applies_cover_type_fix(tmp_path) -> None:
    """retag_flac re-applies fix_flac_cover_type when image_data is provided."""
    flac_path = tmp_path / "test.flac"
    flac_data = b"fLaC" + b"\x00" * 50
    flac_path.write_bytes(flac_data)

    image_data = b"\xff\xd8\xff\xe0IMG"
    meta = TrackMetadata(title="T", artist="A", album="A", image_data=image_data)

    proc_mock = AsyncMock()
    proc_mock.returncode = 0

    with (
        patch("custom_components.suno.audio_retag.asyncio.create_subprocess_exec", return_value=proc_mock),
        patch("custom_components.suno.audio_retag.fix_flac_cover_type", return_value=flac_data) as mock_fix,
    ):
        tmp_out = flac_path.with_suffix(".retag.tmp")

        async def _fake_communicate(*args, **kwargs):
            tmp_out.write_bytes(flac_data)
            return b"", b""

        proc_mock.communicate = _fake_communicate

        result = await retag_flac("/usr/bin/ffmpeg", flac_path, meta)

    assert result is True
    mock_fix.assert_called_once()


async def test_retag_flac_returns_false_on_ffmpeg_failure(tmp_path) -> None:
    """retag_flac returns False when ffmpeg exits with non-zero."""
    flac_path = tmp_path / "test.flac"
    flac_path.write_bytes(b"fLaC" + b"\x00" * 50)

    meta = TrackMetadata(title="T", artist="A", album="A")
    proc_mock = AsyncMock()
    proc_mock.communicate = AsyncMock(return_value=(b"", b"error output"))
    proc_mock.returncode = 1

    with patch("custom_components.suno.audio_retag.asyncio.create_subprocess_exec", return_value=proc_mock):
        result = await retag_flac("/usr/bin/ffmpeg", flac_path, meta)

    assert result is False


async def test_retag_flac_missing_file(tmp_path) -> None:
    """retag_flac returns False for non-existent file."""
    meta = TrackMetadata(title="T", artist="A", album="A")
    result = await retag_flac("/usr/bin/ffmpeg", tmp_path / "missing.flac", meta)
    assert result is False


async def test_retag_flac_ffmpeg_not_found(tmp_path) -> None:
    """retag_flac returns False when ffmpeg binary doesn't exist."""
    flac_path = tmp_path / "test.flac"
    flac_path.write_bytes(b"fLaC" + b"\x00" * 50)

    meta = TrackMetadata(title="T", artist="A", album="A")

    with patch(
        "custom_components.suno.audio_retag.asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError,
    ):
        result = await retag_flac("/nonexistent/ffmpeg", flac_path, meta)

    assert result is False
