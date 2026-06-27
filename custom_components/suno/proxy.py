"""HTTP proxy that injects metadata into Suno audio streams."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

from aiohttp import ClientResponse, web
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.http import HomeAssistantView  # type: ignore[attr-defined]
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .audio_metadata import build_id3_header, skip_existing_id3
from .audio_stream import fetch_album_art
from .const import CDN_BASE_URL
from .models import SunoClip, TrackMetadata, clip_meta_hash, selected_image_url
from .runtime import HomeAssistantRuntime, iter_entry_runtimes, runtime_from_entry

_LOGGER = logging.getLogger(__name__)

_InflightKey = tuple[str, str, str]
_ClipGeneration = tuple[tuple[str, int, int], ...]


class SunoMediaProxyView(HomeAssistantView):
    """Proxy Suno CDN audio with injected metadata."""

    url = "/api/suno/media/{clip_id}.{ext}"
    extra_urls = ["/api/suno/media/{entry_id}/{clip_id}.{ext}"]
    name = "api:suno:media"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._inflight: dict[_InflightKey, asyncio.Future[bytes | None]] = {}
        self._clips_by_id: dict[str, tuple[SunoClip, HomeAssistantRuntime]] = {}
        self._clips_generation: _ClipGeneration = ()

    def _get_runtime_for_entry_id(self, entry_id: str) -> HomeAssistantRuntime | None:
        """Find a specific loaded Suno runtime."""
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            return None
        return runtime_from_entry(entry)

    @staticmethod
    def _runtime_entry_id(runtime: HomeAssistantRuntime | None) -> str:
        """Return the runtime's owning config entry id for request keying."""
        return runtime.entry.entry_id if runtime is not None else ""

    @staticmethod
    def _generation_for(
        runtimes: list[tuple[Any, HomeAssistantRuntime]],
    ) -> _ClipGeneration:
        """Build a non-colliding generation key for loaded runtimes."""
        return tuple((entry.entry_id, id(runtime), runtime.data_version) for entry, runtime in runtimes)

    def _find_clip(self, clip_id: str) -> tuple[SunoClip | None, HomeAssistantRuntime | None]:
        """Look up a clip and its owning runtime across all active runtimes."""
        runtimes = list(iter_entry_runtimes(self.hass))
        generation = self._generation_for(runtimes)
        first_runtime = runtimes[0][1] if runtimes else None
        if generation != self._clips_generation:
            lookup: dict[str, tuple[SunoClip, HomeAssistantRuntime]] = {}
            for _entry, runtime in runtimes:
                for clip in runtime.iter_clips():
                    lookup.setdefault(clip.id, (clip, runtime))
            self._clips_by_id = lookup
            self._clips_generation = generation
        result = self._clips_by_id.get(clip_id)
        if result is not None:
            return result
        return None, first_runtime

    async def get(
        self,
        request: web.Request,
        clip_id: str,
        ext: str,
        entry_id: str | None = None,
    ) -> web.StreamResponse:
        """Stream audio with injected metadata tags."""
        if entry_id is not None:
            runtime = self._get_runtime_for_entry_id(entry_id)
            if runtime is None:
                return web.Response(status=404, text="Suno account not loaded")
            clip = runtime.find_clip(clip_id)
            if clip is None:
                return web.Response(status=404, text="Suno clip not found for account")
            resolved_entry_id = entry_id
        else:
            clip, runtime = self._find_clip(clip_id)
            resolved_entry_id = self._runtime_entry_id(runtime)

        title = clip.title if clip else "Suno"
        artist = clip.display_name if clip and clip.display_name else "Suno"
        meta_hash = clip_meta_hash(clip) if clip else ""

        is_hq = ext == "flac"
        content_type = "audio/flac" if is_hq else "audio/mpeg"

        if runtime is not None:
            if (dl_path := runtime.get_downloaded_path(clip_id, meta_hash)) is not None:
                dl_ext = dl_path.suffix.lstrip(".")
                if (is_hq and dl_ext == "flac") or (not is_hq and dl_ext == "mp3"):
                    mime = "audio/flac" if dl_ext == "flac" else "audio/mpeg"
                    try:
                        return web.FileResponse(dl_path, headers={"Content-Type": mime})
                    except FileNotFoundError, OSError:
                        _LOGGER.debug("Downloaded file vanished for %s, falling through", clip_id)

        if runtime is not None:
            cache_fmt = "flac" if is_hq else "mp3"
            if (cached_path := await runtime.async_get_cached_audio(clip_id, cache_fmt, meta_hash)) is not None:
                return web.FileResponse(cached_path, headers={"Content-Type": content_type})

        if is_hq:
            return await self._handle_hq(
                clip_id,
                clip,
                title,
                artist,
                content_type,
                runtime,
                meta_hash,
                entry_id=resolved_entry_id,
            )

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

        return await self._handle_mp3(request, upstream, clip_id, clip, title, artist, content_type, runtime, meta_hash)

    async def _handle_mp3(
        self,
        request: web.Request,
        upstream: ClientResponse,
        clip_id: str,
        clip: SunoClip | None,
        title: str,
        artist: str,
        content_type: str,
        runtime: HomeAssistantRuntime | None,
        meta_hash: str = "",
    ) -> web.StreamResponse:
        """Stream MP3 with ID3 header injection and optional caching."""
        meta = clip.to_track_metadata(title, artist) if clip else TrackMetadata(title=title, artist=artist)
        if meta.image_data is None and clip and (image_url := selected_image_url(clip)):
            session = async_get_clientsession(self.hass)
            if image_data := await fetch_album_art(session, image_url):
                meta = replace(meta, image_data=image_data)
        id3_header = build_id3_header(meta)
        cache_buf = bytearray(id3_header) if runtime is not None else None
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
                    chunk = skip_existing_id3(chunk)
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
        if runtime is not None and cache_buf is not None:
            await self._save_to_cache(runtime, clip_id, "mp3", bytes(cache_buf), meta_hash)
        return response

    async def _handle_hq(
        self,
        clip_id: str,
        clip: SunoClip | None,
        title: str,
        artist: str,
        content_type: str,
        runtime: HomeAssistantRuntime | None,
        meta_hash: str,
        client: Any = None,
        entry_id: str | None = None,
    ) -> web.Response:
        """Transcode WAV to FLAC with metadata, using request coalescing."""
        if runtime is None and isinstance(client, HomeAssistantRuntime):
            runtime = client
        key = (entry_id if entry_id is not None else self._runtime_entry_id(runtime), clip_id, "flac")
        if key in self._inflight:
            try:
                result = await asyncio.wait_for(asyncio.shield(self._inflight[key]), timeout=150)
            except Exception:
                result = None
            if result is not None:
                return web.Response(body=result, content_type=content_type)

        fut: asyncio.Future[bytes | None] = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            flac_data = await self._run_hq_pipeline(clip_id, clip, title, artist, runtime)
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
        if runtime is not None:
            await self._save_to_cache(runtime, clip_id, "flac", flac_data, meta_hash)
        return web.Response(body=flac_data, content_type=content_type)

    async def _run_hq_pipeline(
        self, clip_id: str, clip: SunoClip | None, title: str, artist: str, runtime: HomeAssistantRuntime | None = None
    ) -> bytes | None:
        """Execute the full WAV-to-FLAC pipeline."""
        if not isinstance(runtime, HomeAssistantRuntime):
            return None
        meta = clip.to_track_metadata(title, artist) if clip else TrackMetadata(title=title, artist=artist)
        return await runtime.async_render_hq_audio(
            clip_id,
            meta,
            duration=clip.duration if clip else 0.0,
            image_url=(selected_image_url(clip) or None) if clip else None,
            session=async_get_clientsession(self.hass),
            ffmpeg_binary=get_ffmpeg_manager(self.hass).binary,
        )

    @staticmethod
    async def _save_to_cache(
        runtime: HomeAssistantRuntime, clip_id: str, fmt: str, data: bytes, meta_hash: str = ""
    ) -> None:
        """Write bytes to cache, logging any failure."""
        try:
            await runtime.async_put_cached_audio(clip_id, fmt, data, meta_hash)
        except Exception:
            _LOGGER.debug("Cache write failed for %s.%s", clip_id, fmt)
