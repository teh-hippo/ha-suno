"""HTTP proxy that injects ID3 metadata into Suno audio streams.

Suno's CDN serves audio files with no tags, so media players (e.g. Sonos)
display the filename instead of the song title.  This view sits in front of
the CDN and prepends metadata before streaming the audio data to the client.

Supports both MP3 (ID3v2.4 injection) and WAV (RIFF INFO injection when
served from cache).  WAV metadata injection requires buffering the whole
file, so it only happens for cached files; streamed WAV is served raw.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aiohttp import ClientResponse, web
from homeassistant.components.http import HomeAssistantView  # type: ignore[attr-defined]
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CDN_BASE_URL,
    CONF_AUDIO_QUALITY,
    CONF_CACHE_ENABLED,
    DEFAULT_AUDIO_QUALITY,
    DEFAULT_CACHE_ENABLED,
    DOMAIN,
    QUALITY_HIGH,
)
from .coordinator import SunoCoordinator
from .models import SunoClip

if TYPE_CHECKING:
    from .cache import SunoCache

_LOGGER = logging.getLogger(__name__)

_SUNO_CACHE_KEY = f"{DOMAIN}_cache"


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


def _build_riff_info(title: str, artist: str) -> bytes:
    """Build a RIFF LIST/INFO chunk with INAM and IART sub-chunks."""
    chunks = b""
    for chunk_id, text in [("INAM", title), ("IART", artist)]:
        text_bytes = text.encode("utf-8") + b"\x00"  # null-terminated
        if len(text_bytes) % 2:
            text_bytes += b"\x00"  # pad to even length
        chunks += chunk_id.encode("ascii") + len(text_bytes).to_bytes(4, "little") + text_bytes
    info_data = b"INFO" + chunks
    return b"LIST" + len(info_data).to_bytes(4, "little") + info_data


def _inject_riff_info(wav_data: bytes, title: str, artist: str) -> bytes:
    """Insert a LIST/INFO chunk into a WAV file's RIFF container.

    The INFO chunk is placed immediately after the initial 12-byte RIFF
    header (before the fmt chunk).  The outer RIFF size field is updated
    to account for the extra bytes.  Most players will read INFO from
    anywhere in the RIFF container.
    """
    if len(wav_data) < 12 or wav_data[:4] != b"RIFF":
        return wav_data

    info_chunk = _build_riff_info(title, artist)
    original_riff_size = int.from_bytes(wav_data[4:8], "little")
    new_riff_size = original_riff_size + len(info_chunk)

    return wav_data[:4] + new_riff_size.to_bytes(4, "little") + wav_data[8:12] + info_chunk + wav_data[12:]


class SunoMediaProxyView(HomeAssistantView):
    """Proxy Suno CDN audio with injected metadata."""

    url = "/api/suno/media/{clip_id}"
    name = "api:suno:media"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def _find_clip(self, clip_id: str) -> SunoClip | None:
        """Look up a clip across all active coordinators."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            coordinator: SunoCoordinator | None = getattr(entry, "runtime_data", None)
            if coordinator is None:
                continue
            for clip in coordinator.data.clips:
                if clip.id == clip_id:
                    return clip
            for clip in coordinator.data.liked_clips:
                if clip.id == clip_id:
                    return clip
        return None

    def _get_entry_options(self) -> dict[str, Any]:
        """Return options from the first loaded config entry."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            if getattr(entry, "runtime_data", None) is not None:
                return dict(entry.options)
        return {}

    def _get_cache(self) -> SunoCache | None:
        """Return the shared SunoCache instance if available."""
        return self.hass.data.get(_SUNO_CACHE_KEY)

    async def get(self, request: web.Request, clip_id: str) -> web.StreamResponse:
        """Stream audio with injected metadata tags."""
        clip = self._find_clip(clip_id)
        title = clip.title if clip else "Suno"
        artist = (clip.tags if clip else None) or "Suno"

        opts = self._get_entry_options()
        quality = opts.get(CONF_AUDIO_QUALITY, DEFAULT_AUDIO_QUALITY)
        cache_enabled = opts.get(CONF_CACHE_ENABLED, DEFAULT_CACHE_ENABLED)
        is_wav = quality == QUALITY_HIGH
        fmt = "wav" if is_wav else "mp3"
        content_type = "audio/wav" if is_wav else "audio/mpeg"

        cache = self._get_cache() if cache_enabled else None

        # Try cache first
        if cache is not None:
            cached_path = await cache.async_get(clip_id, fmt)
            if cached_path is not None:
                return web.FileResponse(
                    cached_path,
                    headers={"Content-Type": content_type},
                )

        # Build upstream URL
        if clip and not is_wav:
            audio_url = clip.audio_url
        else:
            audio_url = f"{CDN_BASE_URL}/{clip_id}.{fmt}"

        session = async_get_clientsession(self.hass)
        try:
            upstream: ClientResponse = await session.get(audio_url)
        except Exception:
            _LOGGER.exception("Failed to fetch upstream audio for %s", clip_id)
            return web.Response(status=502, text="Upstream fetch failed")

        if upstream.status != 200:
            upstream.close()
            return web.Response(
                status=502,
                text=f"Upstream returned {upstream.status}",
            )

        if is_wav:
            return await self._handle_wav(
                request,
                upstream,
                clip_id,
                title,
                artist,
                content_type,
                cache,
            )
        return await self._handle_mp3(
            request,
            upstream,
            clip_id,
            title,
            artist,
            content_type,
            cache,
        )

    async def _handle_mp3(
        self,
        request: web.Request,
        upstream: ClientResponse,
        clip_id: str,
        title: str,
        artist: str,
        content_type: str,
        cache: SunoCache | None,
    ) -> web.StreamResponse:
        """Stream MP3 with ID3 header injection and optional caching."""
        id3_header = _build_id3_header(title=title, artist=artist)
        collected: list[bytes] = [id3_header] if cache is not None else []

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": content_type,
                "Accept-Ranges": "none",
                "Cache-Control": "no-cache",
            },
        )
        await response.prepare(request)

        try:
            await response.write(id3_header)

            first_chunk = True
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                if first_chunk:
                    first_chunk = False
                    chunk = _skip_existing_id3(chunk)
                    if not chunk:
                        continue
                await response.write(chunk)
                if cache is not None:
                    collected.append(chunk)
        except ConnectionResetError:
            _LOGGER.debug("Client disconnected while streaming %s", clip_id)
            collected.clear()
        finally:
            upstream.close()

        if cache is not None and collected:
            await self._save_to_cache(cache, clip_id, "mp3", collected)

        return response

    async def _handle_wav(
        self,
        request: web.Request,
        upstream: ClientResponse,
        clip_id: str,
        title: str,
        artist: str,
        content_type: str,
        cache: SunoCache | None,
    ) -> web.Response:
        """Buffer WAV and return with Content-Length (browsers need it for WAV)."""
        try:
            raw = await upstream.read()
        except Exception:
            _LOGGER.exception("Failed to read upstream WAV for %s", clip_id)
            return web.Response(status=502, text="Upstream read failed")
        finally:
            upstream.close()

        # Inject RIFF INFO metadata
        tagged = _inject_riff_info(raw, title, artist)

        if cache is not None:
            await self._save_to_cache_bytes(cache, clip_id, "wav", tagged)

        return web.Response(
            body=tagged,
            content_type=content_type,
        )

    @staticmethod
    async def _save_to_cache(cache: SunoCache, clip_id: str, fmt: str, chunks: list[bytes]) -> None:
        """Join chunks and write to cache, logging any failure."""
        data = b"".join(chunks)
        try:
            await cache.async_put(clip_id, fmt, data)
        except Exception:
            _LOGGER.debug("Cache write failed for %s.%s", clip_id, fmt)

    @staticmethod
    async def _save_to_cache_bytes(cache: SunoCache, clip_id: str, fmt: str, data: bytes) -> None:
        """Write raw bytes to cache, logging any failure."""
        try:
            await cache.async_put(clip_id, fmt, data)
        except Exception:
            _LOGGER.debug("Cache write failed for %s.%s", clip_id, fmt)
