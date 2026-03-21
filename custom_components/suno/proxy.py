"""HTTP proxy that injects metadata into Suno audio streams.

Suno's CDN serves audio files with no tags, so media players (e.g. Sonos)
display the filename instead of the song title.  This view sits in front of
the CDN and prepends metadata before streaming the audio data to the client.

Supports MP3 (ID3v2.4 header injection) and high-quality mode which downloads
WAV from the CDN and transcodes to FLAC via ffmpeg with embedded metadata.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import ClientResponse, web
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.http import HomeAssistantView  # type: ignore[attr-defined]
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .audio import (
    _build_id3_header,
    _skip_existing_id3,
    ensure_wav_url,
    fetch_album_art,
    wav_to_flac,
)
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
from .models import SunoClip, clip_meta_hash

if TYPE_CHECKING:
    from .cache import SunoCache
    from .sync import SunoSync

_LOGGER = logging.getLogger(__name__)

_SUNO_CACHE_KEY = f"{DOMAIN}_cache"


class SunoMediaProxyView(HomeAssistantView):
    """Proxy Suno CDN audio with injected metadata."""

    url = "/api/suno/media/{clip_id}.{ext}"
    name = "api:suno:media"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._inflight: dict[str, asyncio.Future[bytes | None]] = {}
        self._clips_by_id: dict[str, SunoClip] = {}
        self._clips_generation: int = -1

    def _find_clip(self, clip_id: str) -> SunoClip | None:
        """Look up a clip across all active coordinators using a cached dict."""
        # Rebuild the lookup dict when coordinator data has changed
        generation = 0
        entries = self.hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            coordinator: SunoCoordinator | None = getattr(entry, "runtime_data", None)
            if coordinator is not None:
                generation += id(coordinator.data)

        if generation != self._clips_generation:
            lookup: dict[str, SunoClip] = {}
            for entry in entries:
                coordinator = getattr(entry, "runtime_data", None)
                if coordinator is None:
                    continue
                for clip in coordinator.data.clips:
                    lookup[clip.id] = clip
                for clip in coordinator.data.liked_clips:
                    lookup.setdefault(clip.id, clip)
            self._clips_by_id = lookup
            self._clips_generation = generation

        return self._clips_by_id.get(clip_id)

    def _get_entry_options(self) -> dict[str, Any]:
        """Return options from the first loaded config entry."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            if getattr(entry, "runtime_data", None) is not None:
                return dict(entry.options)
        return {}

    def _get_cache(self) -> SunoCache | None:
        """Return the SunoCache from the first loaded coordinator that has one."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            runtime_data = getattr(entry, "runtime_data", None)
            if not isinstance(runtime_data, SunoCoordinator):
                continue
            if runtime_data.cache is not None:
                return runtime_data.cache
        return None

    def _get_sync(self) -> SunoSync | None:
        """Return the SunoSync from the first loaded coordinator that has one."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            runtime_data = getattr(entry, "runtime_data", None)
            if not isinstance(runtime_data, SunoCoordinator):
                continue
            if runtime_data.sync is not None:
                return runtime_data.sync
        return None

    def _get_client(self) -> Any:
        """Return the SunoClient from the first loaded coordinator."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            coordinator: SunoCoordinator | None = getattr(entry, "runtime_data", None)
            if coordinator is not None:
                return coordinator.client
        return None

    async def get(self, request: web.Request, clip_id: str, ext: str) -> web.StreamResponse:
        """Stream audio with injected metadata tags."""
        clip = self._find_clip(clip_id)
        title = clip.title if clip else "Suno"
        artist = (clip.tags if clip else None) or "Suno"
        meta_hash = clip_meta_hash(clip) if clip else ""

        opts = self._get_entry_options()
        quality = opts.get(CONF_AUDIO_QUALITY, DEFAULT_AUDIO_QUALITY)
        cache_enabled = opts.get(CONF_CACHE_ENABLED, DEFAULT_CACHE_ENABLED)
        is_hq = quality == QUALITY_HIGH
        content_type = "audio/flac" if is_hq else "audio/mpeg"

        # For HQ: check sync directory first (FLAC files ready to serve)
        if is_hq:
            sync = self._get_sync()
            if sync is not None:
                synced_path = sync.get_synced_path(clip_id, meta_hash)
                if synced_path is not None:
                    try:
                        return web.FileResponse(
                            synced_path,
                            headers={"Content-Type": "audio/flac"},
                        )
                    except FileNotFoundError, OSError:
                        _LOGGER.debug("Sync file vanished for %s, falling through", clip_id)

        cache = self._get_cache() if cache_enabled else None

        # Try cache (HQ cached as FLAC, standard as MP3)
        if cache is not None:
            cache_fmt = "flac" if is_hq else "mp3"
            cached_path = await cache.async_get(clip_id, cache_fmt, meta_hash=meta_hash)
            if cached_path is not None:
                return web.FileResponse(
                    cached_path,
                    headers={"Content-Type": content_type},
                )

        if is_hq:
            return await self._handle_hq(
                request,
                clip_id,
                clip,
                title,
                artist,
                content_type,
                cache,
                meta_hash,
            )

        # Standard quality: stream MP3 from CDN
        audio_url = clip.audio_url if clip else f"{CDN_BASE_URL}/{clip_id}.mp3"
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

        return await self._handle_mp3(
            request,
            upstream,
            clip_id,
            title,
            artist,
            content_type,
            cache,
            meta_hash,
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
        meta_hash: str = "",
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
            await self._save_to_cache(cache, clip_id, "mp3", b"".join(collected), meta_hash)

        return response

    async def _handle_hq(
        self,
        request: web.Request,
        clip_id: str,
        clip: SunoClip | None,
        title: str,
        artist: str,
        content_type: str,
        cache: SunoCache | None,
        meta_hash: str,
    ) -> web.Response:
        """Get WAV via Suno API, transcode to FLAC with metadata and art.

        Uses request coalescing: if another request for the same clip is
        already in flight, wait for it to finish and serve from cache.
        """
        key = f"{clip_id}.flac"

        # If another request is already processing this clip, wait for it
        if key in self._inflight:
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(self._inflight[key]),
                    timeout=150,
                )
            except TimeoutError, asyncio.CancelledError, Exception:
                result = None

            if result is not None:
                return web.Response(body=result, content_type=content_type)
            # Leader failed or timed out — fall through to our own attempt

        # We are the leader: run the pipeline
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bytes | None] = loop.create_future()
        self._inflight[key] = fut

        try:
            flac_data = await self._run_hq_pipeline(clip_id, clip, title, artist)
            fut.set_result(flac_data)
        except BaseException as exc:
            fut.set_result(None)
            if isinstance(exc, asyncio.CancelledError):
                raise
            _LOGGER.exception("HQ pipeline failed for %s", clip_id)
            return web.Response(status=502, text="HQ pipeline failed")
        finally:
            self._inflight.pop(key, None)

        if flac_data is None:
            return web.Response(status=502, text="FLAC transcode failed")

        if cache is not None:
            await self._save_to_cache(cache, clip_id, "flac", flac_data, meta_hash)

        return web.Response(
            body=flac_data,
            content_type=content_type,
        )

    async def _run_hq_pipeline(
        self,
        clip_id: str,
        clip: SunoClip | None,
        title: str,
        artist: str,
    ) -> bytes | None:
        """Execute the full WAV→FLAC pipeline. Returns FLAC bytes or None."""
        client = self._get_client()
        if client is None:
            return None

        wav_url = await ensure_wav_url(client, clip_id)
        if not wav_url:
            _LOGGER.warning("WAV generation timed out for %s", clip_id)
            return None

        session = async_get_clientsession(self.hass)
        try:
            upstream = await session.get(wav_url)
        except Exception:
            _LOGGER.exception("Failed to fetch WAV for %s", clip_id)
            return None

        if upstream.status != 200:
            upstream.close()
            _LOGGER.warning("WAV upstream returned %d for %s", upstream.status, clip_id)
            return None

        try:
            wav_data = await upstream.read()
        except Exception:
            _LOGGER.exception("Failed to read WAV for %s", clip_id)
            return None
        finally:
            upstream.close()

        image_url = clip.image_large_url or clip.image_url if clip else None
        image_data = await fetch_album_art(session, image_url) if image_url else None

        return await wav_to_flac(
            get_ffmpeg_manager(self.hass).binary,
            wav_data,
            title,
            artist,
            image_data=image_data,
        )

    @staticmethod
    async def _save_to_cache(cache: SunoCache, clip_id: str, fmt: str, data: bytes, meta_hash: str = "") -> None:
        """Write bytes to cache, logging any failure."""
        try:
            await cache.async_put(clip_id, fmt, data, meta_hash=meta_hash)
        except Exception:
            _LOGGER.debug("Cache write failed for %s.%s", clip_id, fmt)
