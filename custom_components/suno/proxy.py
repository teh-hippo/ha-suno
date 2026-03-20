"""HTTP proxy that injects ID3 metadata into Suno MP3 streams.

Suno's CDN serves MP3 files with no ID3 tags, so media players (e.g. Sonos)
display the filename instead of the song title.  This view sits in front of
the CDN and prepends a minimal ID3v2.4 header with title and artist before
streaming the audio data through to the client.
"""

from __future__ import annotations

import logging

from aiohttp import web
from homeassistant.components.http import HomeAssistantView  # type: ignore[attr-defined]
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CDN_BASE_URL, DOMAIN
from .coordinator import SunoCoordinator
from .models import SunoClip

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


class SunoMediaProxyView(HomeAssistantView):
    """Proxy Suno CDN MP3s with injected ID3 metadata."""

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

    async def get(self, request: web.Request, clip_id: str) -> web.StreamResponse:
        """Stream the MP3 with injected ID3 tags."""
        clip = self._find_clip(clip_id)

        # Build ID3 header from cached metadata, or use defaults
        title = clip.title if clip else "Suno"
        artist = (clip.tags if clip else None) or "Suno"
        audio_url = clip.audio_url if clip else f"{CDN_BASE_URL}/{clip_id}.mp3"

        id3_header = _build_id3_header(title=title, artist=artist)

        session = async_get_clientsession(self.hass)
        try:
            upstream = await session.get(audio_url)
        except Exception:
            _LOGGER.exception("Failed to fetch upstream MP3 for %s", clip_id)
            return web.Response(status=502, text="Upstream fetch failed")

        if upstream.status != 200:
            upstream.close()
            return web.Response(
                status=502,
                text=f"Upstream returned {upstream.status}",
            )

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "audio/mpeg",
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
        except ConnectionResetError:
            _LOGGER.debug("Client disconnected while streaming %s", clip_id)
        finally:
            upstream.close()

        return response
