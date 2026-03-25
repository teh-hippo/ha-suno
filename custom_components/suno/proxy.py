"""HTTP proxy that injects metadata into Suno audio streams."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import ClientResponse, web
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.http import HomeAssistantView  # type: ignore[attr-defined]
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .audio import _build_id3_header, _skip_existing_id3, download_and_transcode_to_flac
from .const import CDN_BASE_URL, DOMAIN
from .coordinator import SunoCoordinator
from .models import SunoClip, TrackMetadata, clip_meta_hash

if TYPE_CHECKING:
    from .cache import SunoCache

_LOGGER = logging.getLogger(__name__)


class SunoMediaProxyView(HomeAssistantView):
    """Proxy Suno CDN audio with injected metadata."""

    url = "/api/suno/media/{clip_id}.{ext}"
    name = "api:suno:media"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._inflight: dict[str, asyncio.Future[bytes | None]] = {}
        self._clips_by_id: dict[str, tuple[SunoClip, SunoCoordinator]] = {}
        self._clips_generation: int = -1

    def _find_clip(self, clip_id: str) -> tuple[SunoClip | None, SunoCoordinator | None]:
        """Look up a clip and its owning coordinator across all active coordinators."""
        generation = 0
        entries = self.hass.config_entries.async_entries(DOMAIN)
        first_coordinator: SunoCoordinator | None = None
        for entry in entries:
            if (coordinator := getattr(entry, "runtime_data", None)) is not None:
                generation += id(coordinator.data)
                if first_coordinator is None and isinstance(coordinator, SunoCoordinator):
                    first_coordinator = coordinator
        if generation != self._clips_generation:
            lookup: dict[str, tuple[SunoClip, SunoCoordinator]] = {}
            for entry in entries:
                if (coordinator := getattr(entry, "runtime_data", None)) is None:
                    continue
                for clip in coordinator.data.clips:
                    lookup[clip.id] = (clip, coordinator)
                for clip in coordinator.data.liked_clips:
                    lookup.setdefault(clip.id, (clip, coordinator))
            self._clips_by_id = lookup
            self._clips_generation = generation
        result = self._clips_by_id.get(clip_id)
        if result is not None:
            return result
        return None, first_coordinator

    async def get(self, request: web.Request, clip_id: str, ext: str) -> web.StreamResponse:
        """Stream audio with injected metadata tags."""
        clip, coordinator = self._find_clip(clip_id)
        title = clip.title if clip else "Suno"
        artist = clip.display_name if clip and clip.display_name else "Suno"
        meta_hash = clip_meta_hash(clip) if clip else ""

        is_hq = ext == "flac"
        content_type = "audio/flac" if is_hq else "audio/mpeg"

        if (dm := coordinator.download_manager if coordinator else None) is not None:
            if (dl_path := dm.get_downloaded_path(clip_id, meta_hash)) is not None:
                dl_ext = dl_path.suffix.lstrip(".")
                if (is_hq and dl_ext == "flac") or (not is_hq and dl_ext == "mp3"):
                    mime = "audio/flac" if dl_ext == "flac" else "audio/mpeg"
                    try:
                        return web.FileResponse(dl_path, headers={"Content-Type": mime})
                    except FileNotFoundError, OSError:
                        _LOGGER.debug("Downloaded file vanished for %s, falling through", clip_id)

        cache = coordinator.cache if coordinator else None
        if cache is not None:
            cache_fmt = "flac" if is_hq else "mp3"
            if (cached_path := await cache.async_get(clip_id, cache_fmt, meta_hash=meta_hash)) is not None:
                return web.FileResponse(cached_path, headers={"Content-Type": content_type})

        if is_hq:
            client = coordinator.client if coordinator else None
            return await self._handle_hq(clip_id, clip, title, artist, content_type, cache, meta_hash, client)

        audio_url = clip.audio_url if clip else f"{CDN_BASE_URL}/{clip_id}.mp3"
        session = async_get_clientsession(self.hass)
        try:
            upstream: ClientResponse = await session.get(audio_url)
        except Exception:
            _LOGGER.exception("Failed to fetch upstream audio for %s", clip_id)
            return web.Response(status=502, text="Upstream fetch failed")

        if upstream.status != 200:
            upstream.close()
            return web.Response(status=502, text=f"Upstream returned {upstream.status}")

        return await self._handle_mp3(request, upstream, clip_id, clip, title, artist, content_type, cache, meta_hash)

    async def _handle_mp3(
        self,
        request: web.Request,
        upstream: ClientResponse,
        clip_id: str,
        clip: SunoClip | None,
        title: str,
        artist: str,
        content_type: str,
        cache: SunoCache | None,
        meta_hash: str = "",
    ) -> web.StreamResponse:
        """Stream MP3 with ID3 header injection and optional caching."""
        meta = clip.to_track_metadata(title, artist) if clip else TrackMetadata(title=title, artist=artist, album=title)
        id3_header = _build_id3_header(meta)
        cache_buf = bytearray(id3_header) if cache is not None else None
        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": content_type, "Accept-Ranges": "none", "Cache-Control": "no-cache"},
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
                if cache_buf is not None:
                    cache_buf.extend(chunk)
        except ConnectionResetError:
            _LOGGER.debug("Client disconnected while streaming %s", clip_id)
            cache_buf = None
        finally:
            upstream.close()
        if cache is not None and cache_buf is not None:
            await self._save_to_cache(cache, clip_id, "mp3", bytes(cache_buf), meta_hash)
        return response

    async def _handle_hq(
        self,
        clip_id: str,
        clip: SunoClip | None,
        title: str,
        artist: str,
        content_type: str,
        cache: SunoCache | None,
        meta_hash: str,
        client: Any = None,
    ) -> web.Response:
        """Transcode WAV to FLAC with metadata, using request coalescing."""
        key = f"{clip_id}.flac"
        if key in self._inflight:
            try:
                result = await asyncio.wait_for(asyncio.shield(self._inflight[key]), timeout=150)
            except TimeoutError, asyncio.CancelledError, Exception:
                result = None
            if result is not None:
                return web.Response(body=result, content_type=content_type)

        fut: asyncio.Future[bytes | None] = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            flac_data = await self._run_hq_pipeline(clip_id, clip, title, artist, client)
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
        return web.Response(body=flac_data, content_type=content_type)

    async def _run_hq_pipeline(
        self, clip_id: str, clip: SunoClip | None, title: str, artist: str, client: Any = None
    ) -> bytes | None:
        """Execute the full WAV-to-FLAC pipeline."""
        if client is None:
            return None
        meta = clip.to_track_metadata(title, artist) if clip else TrackMetadata(title=title, artist=artist, album=title)
        return await download_and_transcode_to_flac(
            client,
            async_get_clientsession(self.hass),
            get_ffmpeg_manager(self.hass).binary,
            clip_id,
            meta,
            duration=clip.duration if clip else 0.0,
            image_url=clip.image_large_url or clip.image_url if clip else None,
        )

    @staticmethod
    async def _save_to_cache(cache: SunoCache, clip_id: str, fmt: str, data: bytes, meta_hash: str = "") -> None:
        """Write bytes to cache, logging any failure."""
        try:
            await cache.async_put(clip_id, fmt, data, meta_hash=meta_hash)
        except Exception:
            _LOGGER.debug("Cache write failed for %s.%s", clip_id, fmt)
