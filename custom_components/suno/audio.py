"""Audio processing utilities for the Suno integration."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

from .const import SYNC_FFMPEG_TIMEOUT

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)


def _build_id3_header(title: str, artist: str, genre: str = "") -> bytes:
    """Build a minimal ID3v2.4 header with title, artist, and genre frames."""
    tag_fields = [("TIT2", title), ("TPE1", artist)] + ([("TCON", genre)] if genre else [])
    frames = b""
    for frame_id, text in tag_fields:
        text_bytes = b"\x03" + text.encode("utf-8")
        frames += frame_id.encode("ascii") + len(text_bytes).to_bytes(4, "big") + b"\x00\x00" + text_bytes
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
    return chunk[(raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3] + 10 :]


async def wav_to_flac(
    ffmpeg_binary: str,
    wav_data: bytes,
    title: str,
    artist: str = "Suno",
    album: str = "Suno",
    genre: str = "",
    image_data: bytes | None = None,
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
            args.extend(["-map", "1:v:0", "-c:v", "mjpeg", "-disposition:v:0", "attached_pic"])
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
        if genre:
            meta.extend(["-metadata", f"genre={genre}"])
        args.extend(meta + ["-compression_level", "5", "-f", "flac", "pipe:1"])
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(input=wav_data), timeout=SYNC_FFMPEG_TIMEOUT)
        if proc.returncode != 0:
            _LOGGER.warning("ffmpeg transcode failed: %s", stderr.decode()[:200])
            return None
        return stdout
    except TimeoutError:
        _LOGGER.error("ffmpeg transcode timed out after %ds", SYNC_FFMPEG_TIMEOUT)
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
    genre: str = "",
    image_url: str | None = None,
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
    return await wav_to_flac(ffmpeg_binary, wav_data, title, artist, genre=genre, image_data=image_data)
