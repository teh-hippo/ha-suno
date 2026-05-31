"""Streaming download and FLAC transcoding for the Suno integration."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .audio_metadata import (
    FFMPEG_METADATA_FIELDS,
    build_id3_header,
    fix_flac_cover_type,
    fix_flac_total_samples,
    skip_existing_id3,
)
from .const import DOWNLOAD_FFMPEG_TIMEOUT
from .models import TrackMetadata

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)


async def wav_to_flac(
    ffmpeg_binary: str,
    wav_data: bytes,
    meta: TrackMetadata,
    duration: float = 0.0,
) -> bytes | None:
    """Transcode WAV bytes to FLAC with metadata and optional album art."""
    tmp_img_path: str | None = None
    proc: asyncio.subprocess.Process | None = None
    try:
        args = [ffmpeg_binary, "-i", "pipe:0"]
        if meta.image_data:
            fd, tmp_img_path = tempfile.mkstemp(suffix=".jpg")
            os.write(fd, meta.image_data)
            os.close(fd)
            args.extend(["-i", tmp_img_path])
        args.extend(["-map", "0:a:0"])
        if meta.image_data:
            args.extend(["-map", "1:v:0", "-c:v", "copy", "-disposition:v:0", "attached_pic"])
        cmd = [
            "-c:a",
            "flac",
            "-metadata",
            f"title={meta.title}",
            "-metadata",
            f"artist={meta.artist}",
            "-metadata",
            f"album={meta.album}",
        ]
        for key, attr in FFMPEG_METADATA_FIELDS:
            cmd.extend(["-metadata", f"{key}={getattr(meta, attr)}"])
        args.extend(cmd + ["-f", "flac", "pipe:1"])

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(input=wav_data), timeout=DOWNLOAD_FFMPEG_TIMEOUT)
        if proc.returncode != 0:
            _LOGGER.warning("ffmpeg failed: %s", stderr.decode()[:200])
            return None
        result = fix_flac_cover_type(stdout) if meta.image_data else stdout
        return fix_flac_total_samples(result, duration)
    except TimeoutError:
        _LOGGER.error("ffmpeg timed out for FLAC transcode")
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
        return None
    except FileNotFoundError:
        _LOGGER.error("ffmpeg not found")
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
    meta: TrackMetadata,
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

    header = build_id3_header(meta)
    body = skip_existing_id3(raw)
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
    meta: TrackMetadata,
    duration: float = 0.0,
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
    if meta.image_data is None and image_url:
        meta = replace(meta, image_data=await fetch_album_art(session, image_url))
    return await wav_to_flac(ffmpeg_binary, wav_data, meta, duration=duration)
