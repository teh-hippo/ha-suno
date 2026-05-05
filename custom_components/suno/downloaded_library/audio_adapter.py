"""Production audio adapter for the Downloaded Library engine."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..audio import download_and_transcode_to_flac, download_as_mp3, fetch_album_art, retag_flac, retag_mp3
from ..const import CDN_BASE_URL, QUALITY_HIGH
from ..models import SunoClip, TrackMetadata
from .contracts import RenderedAudio

if TYPE_CHECKING:
    from ..api import SunoClient

_LOGGER = logging.getLogger(__name__)


class HomeAssistantDownloadedLibraryAudio:
    """Production audio adapter backed by Suno transport and Home Assistant helpers."""

    def __init__(self, hass: HomeAssistant, client: SunoClient) -> None:
        self._hass = hass
        self._client = client

    async def fetch_image(self, image_url: str) -> bytes | None:
        session = async_get_clientsession(self._hass)
        return await fetch_album_art(session, image_url)

    async def render(
        self,
        clip: SunoClip,
        quality: str,
        meta: TrackMetadata,
        image_url: str | None,
    ) -> RenderedAudio | None:
        session = async_get_clientsession(self._hass)
        if quality == QUALITY_HIGH:
            data = await download_and_transcode_to_flac(
                self._client,
                session,
                get_ffmpeg_manager(self._hass).binary,
                clip.id,
                meta,
                duration=clip.duration,
                image_url=image_url,
            )
            return RenderedAudio(data, "flac") if data is not None else None

        audio_url = clip.audio_url or f"{CDN_BASE_URL}/{clip.id}.mp3"
        data = await download_as_mp3(session, audio_url, meta)
        return RenderedAudio(data, "mp3") if data is not None else None

    async def retag(self, target: Path, meta: TrackMetadata) -> bool:
        if target.suffix == ".flac":
            return await retag_flac(get_ffmpeg_manager(self._hass).binary, target, meta)
        return await self._hass.async_add_executor_job(retag_mp3, target, meta)

    async def download_video(self, video_url: str, target: Path) -> None:
        if await self._hass.async_add_executor_job(target.exists):
            return
        session = async_get_clientsession(self._hass)
        try:
            async with session.get(video_url) as resp:
                if resp.status != 200:
                    _LOGGER.debug("Video download failed for %s: %d", video_url, resp.status)
                    return
                tmp_path = target.with_suffix(".mp4.tmp")
                try:
                    total = 0

                    def _open_tmp() -> Any:
                        tmp_path.parent.mkdir(parents=True, exist_ok=True)
                        return open(tmp_path, "wb")  # noqa: SIM115

                    fh = await self._hass.async_add_executor_job(_open_tmp)
                    try:
                        async for chunk in resp.content.iter_chunked(256 * 1024):
                            await self._hass.async_add_executor_job(fh.write, chunk)
                            total += len(chunk)
                    finally:
                        await self._hass.async_add_executor_job(fh.close)
                    await self._hass.async_add_executor_job(os.replace, str(tmp_path), str(target))
                    _LOGGER.info("Downloaded video: %s (%d bytes)", target.name, total)
                except BaseException:
                    await self._hass.async_add_executor_job(tmp_path.unlink, True)
                    raise
        except Exception:
            _LOGGER.debug("Failed to download video from %s", video_url)
