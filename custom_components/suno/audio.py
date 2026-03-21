"""Audio processing utilities for the Suno integration.

Contains ID3v2 header manipulation, WAV-to-FLAC transcoding via ffmpeg,
and shared helpers for WAV URL polling and album art download.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

from .const import SYNC_FFMPEG_TIMEOUT

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)


def _build_id3_header(title: str, artist: str) -> bytes:
    """Build a minimal ID3v2.4 header with title and artist frames."""
    frames = b""
    for frame_id, text in [("TIT2", title), ("TPE1", artist)]:
        text_bytes = b"\x03" + text.encode("utf-8")
        frame_header = frame_id.encode("ascii") + len(text_bytes).to_bytes(4, "big") + b"\x00\x00"
        frames += frame_header + text_bytes

    # ID3v2.4 header: "ID3" + version 2.4 + no flags + syncsafe size
    size = len(frames)
    syncsafe = (
        ((size & 0x0FE00000) << 3) | ((size & 0x001FC000) << 2) | ((size & 0x00003F80) << 1) | (size & 0x0000007F)
    )
    header = b"ID3\x04\x00\x00" + syncsafe.to_bytes(4, "big")
    return header + frames


def _skip_existing_id3(chunk: bytes) -> bytes:
    """Strip a leading ID3v2 tag from the first chunk of upstream data."""
    if len(chunk) < 10 or chunk[:3] != b"ID3":
        return chunk
    raw = chunk[6:10]
    tag_size = (raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]
    skip = tag_size + 10
    return chunk[skip:]


async def wav_to_flac(
    ffmpeg_binary: str,
    wav_data: bytes,
    title: str,
    artist: str,
    album: str = "Suno",
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

        args.extend(
            [
                "-c:a",
                "flac",
                "-metadata",
                f"title={title}",
                "-metadata",
                f"artist={artist}",
                "-metadata",
                f"album={album}",
                "-compression_level",
                "5",
                "-f",
                "flac",
                "pipe:1",
            ]
        )

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=wav_data),
            timeout=SYNC_FFMPEG_TIMEOUT,
        )
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
    wav_url = await client.get_wav_url(clip_id)
    if wav_url:
        return str(wav_url)
    await client.request_wav(clip_id)
    for _ in range(polls):
        await asyncio.sleep(interval)
        wav_url = await client.get_wav_url(clip_id)
        if wav_url:
            return str(wav_url)
    return None


async def fetch_album_art(session: ClientSession, image_url: str) -> bytes | None:
    """Download album art, returning raw bytes or None on failure."""
    try:
        async with session.get(image_url) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception:
        _LOGGER.debug("Failed to download album art from %s", image_url)
    return None
