"""Data coordinator for Suno integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SunoClient
from .const import DEFAULT_CACHE_TTL, DOMAIN
from .exceptions import SunoAuthError, SunoConnectionError
from .models import SunoClip, SunoCredits, SunoPlaylist, SunoUser

if TYPE_CHECKING:
    from .cache import SunoCache
    from .download import SunoDownloadManager

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1


@dataclass
class SunoData:
    clips: list[SunoClip] = field(default_factory=list)
    liked_clips: list[SunoClip] = field(default_factory=list)
    playlists: list[SunoPlaylist] = field(default_factory=list)
    playlist_clips: dict[str, list[SunoClip]] = field(default_factory=dict)
    credits: SunoCredits | None = None


class SunoCoordinator(DataUpdateCoordinator[SunoData]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, client: SunoClient, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=DEFAULT_CACHE_TTL),
            config_entry=entry,
        )
        self.client = client
        self.cache: SunoCache | None = None
        self.download_manager: SunoDownloadManager | None = None
        self._store: Store[dict[str, Any]] = Store(hass, _STORE_VERSION, f"suno_library_{entry.entry_id}")
        self.user = SunoUser(
            id=client.user_id or "",
            display_name=client.display_name,
        )

    async def async_load_stored_data(self) -> SunoData | None:
        """Load persisted library from HA Store."""
        if not (saved := await self._store.async_load()) or not isinstance(saved, dict):
            return None
        try:
            data = SunoData(
                clips=[SunoClip(**c) for c in saved.get("clips", [])],
                liked_clips=[SunoClip(**c) for c in saved.get("liked_clips", [])],
                playlists=[SunoPlaylist(**p) for p in saved.get("playlists", [])],
                playlist_clips={
                    pid: [SunoClip(**c) for c in clips] for pid, clips in saved.get("playlist_clips", {}).items()
                },
            )
        except Exception:
            _LOGGER.warning("Stored library corrupt, ignoring")
            return None
        self.data = data
        _LOGGER.info("Loaded stored library: %d clips", len(data.clips))
        return data

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.unique_id or self.config_entry.entry_id)},
            name=self.user.display_name,
            manufacturer="Suno",
            model="Music Library",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://suno.com",
        )

    async def _async_update_data(self) -> SunoData:
        try:
            await self.client.ensure_authenticated()
        except SunoConnectionError as err:
            raise UpdateFailed(f"Cannot reach Suno: {err}") from err
        except SunoAuthError as err:
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="auth_failed") from err

        results = await asyncio.gather(
            self.client.get_all_songs(),
            self.client.get_liked_songs(),
            self.client.get_playlists(),
            self.client.get_credits(),
            return_exceptions=True,
        )

        if isinstance(results[0], BaseException):
            if isinstance(results[0], SunoAuthError):
                raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="auth_failed") from results[0]
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(results[0])},
            ) from results[0]
        clips = results[0]
        _LOGGER.debug("Fetched %d clips", len(clips))

        liked_clips = [] if isinstance(results[1], BaseException) else results[1]
        playlists = [] if isinstance(results[2], BaseException) else results[2]
        credits = None if isinstance(results[3], BaseException) else results[3]

        if isinstance(results[1], BaseException):
            _LOGGER.warning("Could not fetch liked songs", exc_info=results[1])
        if isinstance(results[2], BaseException):
            _LOGGER.warning("Could not fetch playlists", exc_info=results[2])
        if isinstance(results[3], BaseException):
            _LOGGER.warning("Could not fetch credits", exc_info=results[3])

        playlist_clips: dict[str, list[SunoClip]] = {}
        if playlists:
            sem = asyncio.Semaphore(3)

            async def _fetch_playlist(pl: SunoPlaylist) -> tuple[str, list[SunoClip]]:
                async with sem:
                    return pl.id, await self.client.get_playlist_clips(pl.id)

            results_pl = await asyncio.gather(
                *[_fetch_playlist(pl) for pl in playlists],
                return_exceptions=True,
            )
            for result in results_pl:
                if isinstance(result, tuple):
                    pl_id, clips_list = result
                    playlist_clips[pl_id] = clips_list
                elif isinstance(result, Exception):
                    _LOGGER.warning("Failed to fetch playlist clips: %s", result)

        data = SunoData(
            clips=clips, liked_clips=liked_clips, playlists=playlists, playlist_clips=playlist_clips, credits=credits
        )
        # Update user identity from Suno API data
        api_display_name = self.client.suno_display_name
        if api_display_name and api_display_name != self.user.display_name:
            self.user = SunoUser(id=self.user.id, display_name=api_display_name)
            if api_display_name != self.config_entry.title:
                self.hass.config_entries.async_update_entry(self.config_entry, title=api_display_name)

        self.hass.async_create_task(
            self._store.async_save(
                {
                    "clips": [asdict(c) for c in data.clips],
                    "liked_clips": [asdict(c) for c in data.liked_clips],
                    "playlists": [asdict(p) for p in data.playlists],
                    "playlist_clips": {pid: [asdict(c) for c in pclips] for pid, pclips in data.playlist_clips.items()},
                }
            ),
            f"suno_store_save_{self.config_entry.entry_id}",
        )
        return data
