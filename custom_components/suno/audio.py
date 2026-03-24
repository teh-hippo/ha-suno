"""Audio processing utilities for the Suno integration."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

from .const import DOWNLOAD_FFMPEG_TIMEOUT

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)


def _build_id3_header(
    title: str,
    artist: str,
    album: str = "",
    album_artist: str = "",
    date: str = "",
    lyrics: str = "",
    comment: str = "",
    image_data: bytes | None = None,
    suno_style: str = "",
    suno_style_summary: str = "",
) -> bytes:
    """Build a minimal ID3v2.4 header with metadata frames."""
    tag_fields: list[tuple[str, str]] = [("TIT2", title), ("TPE1", artist)]
    if album:
        tag_fields.append(("TALB", album))
    if album_artist:
        tag_fields.append(("TPE2", album_artist))
    if date:
        tag_fields.append(("TDRC", date))
    if comment:
        tag_fields.append(("COMM", comment))
    frames = b""
    for frame_id, text in tag_fields:
        text_bytes = b"\x03" + text.encode("utf-8")
        frames += frame_id.encode("ascii") + len(text_bytes).to_bytes(4, "big") + b"\x00\x00" + text_bytes
    if lyrics:
        # USLT: encoding(1) + language(3) + content_descriptor(\x00) + text
        uslt_body = b"\x03" + b"eng" + b"\x00" + lyrics.encode("utf-8")
        frames += b"USLT" + len(uslt_body).to_bytes(4, "big") + b"\x00\x00" + uslt_body
    # TXXX frames for Suno-specific metadata
    for desc, value in [("SUNO_STYLE", suno_style), ("SUNO_STYLE_SUMMARY", suno_style_summary)]:
        if value:
            txxx_body = b"\x03" + desc.encode("utf-8") + b"\x00" + value.encode("utf-8")
            frames += b"TXXX" + len(txxx_body).to_bytes(4, "big") + b"\x00\x00" + txxx_body
    # APIC frame for album art
    if image_data:
        apic_body = b"\x00" + b"image/jpeg\x00" + b"\x03" + b"\x00" + image_data
        frames += b"APIC" + len(apic_body).to_bytes(4, "big") + b"\x00\x00" + apic_body
    size = len(frames)
    syncsafe = (
        ((size & 0x0FE00000) << 3) | ((size & 0x001FC000) << 2) | ((size & 0x00003F80) << 1) | (size & 0x0000007F)
    )
    return b"ID3\x04\x00\x00" + syncsafe.to_bytes(4, "big") + frames


def _skip_existing_id3(chunk: bytes) -> bytes:
    """Strip a leading ID3v2 tag from the first chunk."""
    if len(chunk) < 10 or chunk[:3] != b"ID3":
        return chunk
    raw = chunk[6:10]
    return chunk[((raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]) + 10 :]


_FLAC_PICTURE_TYPE = 6
_FLAC_COVER_FRONT = 3


def _fix_flac_cover_type(data: bytes) -> bytes:
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
            # Overwrite the 4-byte picture-type field at start of block data
            buf[pos + 4 : pos + 8] = _FLAC_COVER_FRONT.to_bytes(4, "big")
            break
        pos += 4 + block_length
        if is_last:
            break
    return bytes(buf)


def _fix_flac_total_samples(data: bytes, duration: float) -> bytes:
    """Write the correct total_samples into the FLAC STREAMINFO block.

    When ffmpeg outputs FLAC to a pipe (non-seekable), it cannot seek
    back to write total_samples, leaving it as 0.  This causes players
    like Jellyfin to report unknown/zero duration.

    We read the sample rate from STREAMINFO and compute total_samples
    from the known clip duration.
    """
    if duration <= 0 or len(data) < 26 or data[:4] != b"fLaC":
        return data
    # STREAMINFO is always the first metadata block (FLAC spec).
    # Block header at byte 4, data starts at byte 8.
    block_type = data[4] & 0x7F
    if block_type != 0:  # 0 = STREAMINFO
        return data
    # Sample rate is 20 bits at bytes 18-20 (upper 20 of 24 bits)
    sample_rate = int.from_bytes(data[18:21], "big") >> 4
    if sample_rate == 0:
        return data
    total_samples = int(duration * sample_rate)
    buf = bytearray(data)
    # total_samples is 36 bits: upper 4 in byte 21 lower nibble, lower 32 in bytes 22-25
    buf[21] = (buf[21] & 0xF0) | ((total_samples >> 32) & 0x0F)
    buf[22:26] = (total_samples & 0xFFFFFFFF).to_bytes(4, "big")
    return bytes(buf)


async def wav_to_flac(
    ffmpeg_binary: str,
    wav_data: bytes,
    title: str,
    artist: str = "Suno",
    album: str = "Suno",
    image_data: bytes | None = None,
    album_artist: str = "",
    date: str = "",
    lyrics: str = "",
    comment: str = "",
    duration: float = 0.0,
    suno_style: str = "",
    suno_style_summary: str = "",
) -> bytes | None:
    """Transcode WAV bytes to FLAC with metadata and optional album art."""
    import tempfile  # noqa: PLC0415

    tmp_img_path: str | None = None
    proc: asyncio.subprocess.Process | None = None
    try:
        args = [ffmpeg_binary, "-i", "pipe:0"]
        if image_data:
            fd, tmp_img_path = tempfile.mkstemp(suffix=".jpg")
            os.write(fd, image_data)
            os.close(fd)
            args.extend(["-i", tmp_img_path])
        args.extend(["-map", "0:a:0"])
        if image_data:
            args.extend(["-map", "1:v:0", "-c:v", "copy", "-disposition:v:0", "attached_pic"])
        meta = [
            "-c:a",
            "flac",
            "-metadata",
            f"title={title}",
            "-metadata",
            f"artist={artist}",
            "-metadata",
            f"album={album}",
        ]
        if album_artist:
            meta.extend(["-metadata", f"albumartist={album_artist}"])
        if date:
            meta.extend(["-metadata", f"date={date}"])
        if lyrics:
            meta.extend(["-metadata", f"LYRICS={lyrics}"])
        if comment:
            meta.extend(["-metadata", f"comment={comment}"])
        if suno_style:
            meta.extend(["-metadata", f"SUNO_STYLE={suno_style}"])
        if suno_style_summary:
            meta.extend(["-metadata", f"SUNO_STYLE_SUMMARY={suno_style_summary}"])
        args.extend(meta + ["-compression_level", "5", "-f", "flac", "pipe:1"])
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(input=wav_data), timeout=DOWNLOAD_FFMPEG_TIMEOUT)
        if proc.returncode != 0:
            _LOGGER.warning("ffmpeg transcode failed: %s", stderr.decode()[:200])
            return None
        result = _fix_flac_cover_type(stdout) if image_data else stdout
        return _fix_flac_total_samples(result, duration)
    except TimeoutError:
        _LOGGER.error("ffmpeg transcode timed out after %ds", DOWNLOAD_FFMPEG_TIMEOUT)
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
        return None
    except FileNotFoundError:
        _LOGGER.error("ffmpeg not found. Install ffmpeg for high quality audio.")
        return None
    except Exception:
        _LOGGER.exception("FLAC transcode error")
        return None
    finally:
        if tmp_img_path:
            try:
                os.unlink(tmp_img_path)
            except OSError:
                pass


async def ensure_wav_url(client: Any, clip_id: str, polls: int = 24, interval: float = 5.0) -> str | None:
    """Poll for a WAV URL, requesting server-side generation if needed."""
    if wav_url := await client.get_wav_url(clip_id):
        return str(wav_url)
    await client.request_wav(clip_id)
    for _ in range(polls):
        await asyncio.sleep(interval)
        if wav_url := await client.get_wav_url(clip_id):
            return str(wav_url)
    return None


async def download_as_mp3(
    session: ClientSession,
    audio_url: str,
    title: str,
    artist: str = "Suno",
    album: str = "",
    album_artist: str = "",
    date: str = "",
    lyrics: str = "",
    comment: str = "",
    image_data: bytes | None = None,
    suno_style: str = "",
    suno_style_summary: str = "",
) -> bytes | None:
    """Download MP3 from CDN and inject ID3 metadata tags.

    Downloads the MP3, strips any existing ID3v2 header, and prepends
    a new ID3v2.4 header with the provided metadata.
    No ffmpeg required.
    """
    try:
        async with session.get(audio_url) as resp:
            if resp.status != 200:
                _LOGGER.warning("MP3 download failed for %s: %d", audio_url, resp.status)
                return None
            raw = await resp.read()
    except Exception:
        _LOGGER.exception("Failed to download MP3 from %s", audio_url)
        return None

    header = _build_id3_header(
        title=title,
        artist=artist,
        album=album or title,
        album_artist=album_artist,
        date=date,
        lyrics=lyrics,
        comment=comment,
        image_data=image_data,
        suno_style=suno_style,
        suno_style_summary=suno_style_summary,
    )
    body = _skip_existing_id3(raw)
    return header + body


async def fetch_album_art(session: ClientSession, image_url: str) -> bytes | None:
    """Download album art, returning raw bytes or None on failure."""
    try:
        async with session.get(image_url) as resp:
            return await resp.read() if resp.status == 200 else None
    except Exception:
        _LOGGER.debug("Failed to download album art from %s", image_url)
    return None


async def download_and_transcode_to_flac(
    client: Any,
    session: ClientSession,
    ffmpeg_binary: str,
    clip_id: str,
    title: str,
    artist: str = "Suno",
    image_url: str | None = None,
    album: str = "",
    album_artist: str = "",
    date: str = "",
    lyrics: str = "",
    comment: str = "",
    duration: float = 0.0,
    suno_style: str = "",
    suno_style_summary: str = "",
) -> bytes | None:
    """Download WAV from Suno, fetch album art, and transcode to FLAC.

    Returns FLAC bytes or None on failure.
    """
    if not (wav_url := await ensure_wav_url(client, clip_id)):
        _LOGGER.warning("WAV generation timed out for %s", clip_id)
        return None
    try:
        upstream = await session.get(wav_url)
    except Exception:
        _LOGGER.exception("Failed to fetch WAV for %s", clip_id)
        return None
    if upstream.status != 200:
        upstream.close()
        _LOGGER.warning("WAV download failed for %s: %d", clip_id, upstream.status)
        return None
    try:
        wav_data = await upstream.read()
    except Exception:
        _LOGGER.exception("Failed to read WAV for %s", clip_id)
        return None
    finally:
        upstream.close()
    image_data = await fetch_album_art(session, image_url) if image_url else None
    return await wav_to_flac(
        ffmpeg_binary,
        wav_data,
        title,
        artist,
        album=album or title,
        image_data=image_data,
        album_artist=album_artist,
        date=date,
        lyrics=lyrics,
        comment=comment,
        duration=duration,
        suno_style=suno_style,
        suno_style_summary=suno_style_summary,
    )
