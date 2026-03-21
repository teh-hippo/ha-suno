"""Data coordinator for the Suno integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SunoClient
from .const import CONF_CACHE_TTL, DEFAULT_CACHE_TTL, DOMAIN
from .exceptions import SunoAuthError
from .models import SunoClip, SunoCredits, SunoPlaylist

_LOGGER = logging.getLogger(__name__)


@dataclass
class SunoData:
    """Holds all cached Suno data."""

    clips: list[SunoClip] = field(default_factory=list)
    liked_clips: list[SunoClip] = field(default_factory=list)
    playlists: list[SunoPlaylist] = field(default_factory=list)
    credits: SunoCredits | None = None


class SunoCoordinator(DataUpdateCoordinator[SunoData]):
    """Fetches and caches Suno library data."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, client: SunoClient, entry: ConfigEntry) -> None:
        ttl = entry.options.get(CONF_CACHE_TTL, DEFAULT_CACHE_TTL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=ttl),
            config_entry=entry,
        )
        self.client = client
        self.cache: Any | None = None
        self.sync: Any | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return shared device info for all Suno entities."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.unique_id or self.config_entry.entry_id)},
            name=self.client.display_name,
            manufacturer="Suno",
            model="Music Library",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://suno.com",
        )

    async def _async_update_data(self) -> SunoData:
        """Fetch library, liked songs, playlists, and credits from Suno."""
        try:
            await self.client._auth.ensure_jwt()
        except SunoAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from err

        results = await asyncio.gather(
            self.client.get_all_songs(),
            self.client.get_liked_songs(),
            self.client.get_playlists(),
            self.client.get_credits(),
            return_exceptions=True,
        )

        # Songs must succeed
        if isinstance(results[0], BaseException):
            if isinstance(results[0], SunoAuthError):
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="auth_failed",
                ) from results[0]
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(results[0])},
            ) from results[0]
        clips = results[0]
        _LOGGER.debug("Fetched %d clips from Suno library", len(clips))

        # Liked songs - fallback to empty on failure
        if isinstance(results[1], BaseException):
            _LOGGER.warning("Could not fetch liked songs, skipping", exc_info=results[1])
            liked_clips: list[SunoClip] = []
        else:
            liked_clips = results[1]
            _LOGGER.debug("Fetched %d liked clips", len(liked_clips))

        # Playlists - fallback to empty on failure
        if isinstance(results[2], BaseException):
            _LOGGER.warning("Could not fetch playlists, skipping", exc_info=results[2])
            playlists: list[SunoPlaylist] = []
        else:
            playlists = results[2]
            _LOGGER.debug("Fetched %d playlists", len(playlists))

        # Credits - fallback to None on failure
        if isinstance(results[3], BaseException):
            _LOGGER.warning("Could not fetch credits, skipping", exc_info=results[3])
            credits: SunoCredits | None = None
        else:
            credits = results[3]

        return SunoData(
            clips=clips,
            liked_clips=liked_clips,
            playlists=playlists,
            credits=credits,
        )
