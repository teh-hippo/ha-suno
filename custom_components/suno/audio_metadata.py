"""Byte-poking helpers for ID3v2 and FLAC metadata blocks.

These helpers manipulate the binary container format directly so that the
streaming and retag layers can rewrite metadata frames without re-encoding
audio. Callers across the integration (proxy, retag flows, transcoding) all
share these primitives, so they're public.
"""

from __future__ import annotations

from .models import TrackMetadata

_FLAC_PICTURE_TYPE = 6
_FLAC_COVER_FRONT = 3


def build_id3_header(meta: TrackMetadata) -> bytes:
    """Build a minimal ID3v2.4 header with metadata frames."""
    tag_fields: list[tuple[str, str]] = [("TIT2", meta.title), ("TPE1", meta.artist)]
    if meta.album:
        tag_fields.append(("TALB", meta.album))
    if meta.album_artist:
        tag_fields.append(("TPE2", meta.album_artist))
    if meta.date:
        tag_fields.append(("TDRC", meta.date))
    if meta.comment:
        tag_fields.append(("COMM", meta.comment))
    frames = b""
    for frame_id, text in tag_fields:
        text_bytes = b"\x03" + text.encode("utf-8")
        frames += frame_id.encode("ascii") + len(text_bytes).to_bytes(4, "big") + b"\x00\x00" + text_bytes
    if meta.lyrics:
        uslt_body = b"\x03" + b"eng" + b"\x00" + meta.lyrics.encode("utf-8")
        frames += b"USLT" + len(uslt_body).to_bytes(4, "big") + b"\x00\x00" + uslt_body
    custom_fields = [
        ("SUNO_STYLE", meta.suno_style),
        ("SUNO_STYLE_SUMMARY", meta.suno_style_summary),
        ("SUNO_MODEL", meta.suno_model),
        ("SUNO_HANDLE", meta.suno_handle),
        ("SUNO_PARENT", meta.suno_parent),
        ("SUNO_LINEAGE", meta.suno_lineage),
    ]
    for desc, value in custom_fields:
        if value:
            txxx_body = b"\x03" + desc.encode("utf-8") + b"\x00" + value.encode("utf-8")
            frames += b"TXXX" + len(txxx_body).to_bytes(4, "big") + b"\x00\x00" + txxx_body
    if meta.image_data:
        apic_body = b"\x00" + b"image/jpeg\x00" + b"\x03" + b"\x00" + meta.image_data
        frames += b"APIC" + len(apic_body).to_bytes(4, "big") + b"\x00\x00" + apic_body
    size = len(frames)
    syncsafe = (
        ((size & 0x0FE00000) << 3) | ((size & 0x001FC000) << 2) | ((size & 0x00003F80) << 1) | (size & 0x0000007F)
    )
    return b"ID3\x03\x00\x00" + syncsafe.to_bytes(4, "big") + frames


def skip_existing_id3(chunk: bytes) -> bytes:
    """Strip a leading ID3v2 tag from the first chunk."""
    if len(chunk) < 10 or chunk[:3] != b"ID3":
        return chunk
    raw = chunk[6:10]
    return chunk[((raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]) + 10 :]


def fix_flac_cover_type(data: bytes) -> bytes:
    """Set the first PICTURE block's type to Front Cover (3).

    ffmpeg's FLAC muxer writes picture type 0 ("Other") regardless of
    stream disposition.  Jellyfin and many players only display art
    tagged as type 3 ("Cover (front)").
    """
    if len(data) < 8 or data[:4] != b"fLaC":
        return data
    buf = bytearray(data)
    pos = 4
    while pos + 4 <= len(buf):
        header_byte = buf[pos]
        is_last = (header_byte & 0x80) != 0
        block_type = header_byte & 0x7F
        block_length = int.from_bytes(buf[pos + 1 : pos + 4], "big")
        if block_type == _FLAC_PICTURE_TYPE and block_length >= 4:
            buf[pos + 4 : pos + 8] = _FLAC_COVER_FRONT.to_bytes(4, "big")
            break
        pos += 4 + block_length
        if is_last:
            break
    return bytes(buf)


def fix_flac_total_samples(data: bytes, duration: float) -> bytes:
    """Write the correct total_samples into the FLAC STREAMINFO block.

    When ffmpeg outputs FLAC to a pipe (non-seekable), it cannot seek
    back to write total_samples, leaving it as 0.  This causes players
    like Jellyfin to report unknown/zero duration.

    We read the sample rate from STREAMINFO and compute total_samples
    from the known clip duration.
    """
    if duration <= 0 or len(data) < 26 or data[:4] != b"fLaC":
        return data
    block_type = data[4] & 0x7F
    if block_type != 0:
        return data
    sample_rate = int.from_bytes(data[18:21], "big") >> 4
    if sample_rate == 0:
        return data
    total_samples = int(duration * sample_rate)
    buf = bytearray(data)
    buf[21] = (buf[21] & 0xF0) | ((total_samples >> 32) & 0x0F)
    buf[22:26] = (total_samples & 0xFFFFFFFF).to_bytes(4, "big")
    return bytes(buf)


def extract_apic(data: bytes) -> bytes | None:
    """Extract the first APIC (album art) frame from an ID3v2 tag."""
    if len(data) < 10 or data[:3] != b"ID3":
        return None
    raw = data[6:10]
    tag_size = (raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]
    pos = 10
    end = min(10 + tag_size, len(data))
    while pos + 10 <= end:
        frame_id = data[pos : pos + 4]
        frame_size = int.from_bytes(data[pos + 4 : pos + 8], "big")
        if frame_size <= 0 or pos + 10 + frame_size > end:
            break
        if frame_id == b"APIC":
            frame_data = data[pos + 10 : pos + 10 + frame_size]
            idx = 1
            while idx < len(frame_data) and frame_data[idx] != 0:
                idx += 1
            idx += 1
            idx += 1
            while idx < len(frame_data) and frame_data[idx] != 0:
                idx += 1
            idx += 1
            return frame_data[idx:] if idx < len(frame_data) else None
        pos += 10 + frame_size
    return None
