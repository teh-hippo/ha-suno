"""Tests for the ``audio_metadata`` module (ID3 header construction, APIC frames, FLAC normalisation).

Split from the legacy 1134-line ``test_audio.py`` by the Round 2 test
restructure. Tests that exercise multiple modules are bucketed by their
primary module under test.
"""

from __future__ import annotations

from custom_components.suno.audio_metadata import (
    build_id3_header,
    extract_apic,
    fix_flac_cover_type,
    fix_flac_total_samples,
    skip_existing_id3,
)
from custom_components.suno.models import TrackMetadata

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


# ── fix_flac_total_samples ────────────────────────────────────────


def testfix_flac_total_samples_patches_streaminfo() -> None:
    """Correctly patches total_samples into STREAMINFO block."""

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

    data = b"fLaC" + b"\x00" * 40
    assert fix_flac_total_samples(data, 0.0) is data


def testfix_flac_total_samples_not_flac() -> None:
    """Non-FLAC data is returned unchanged."""

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
