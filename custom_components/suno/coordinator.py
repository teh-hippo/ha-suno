"""Data coordinator for the Suno integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from functools import cached_property

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SunoClient, SunoClip, SunoCredits, SunoPlaylist
from .const import CONF_CACHE_TTL, DEFAULT_CACHE_TTL, DOMAIN
from .exceptions import SunoAuthError

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

    @cached_property
    def device_info(self) -> DeviceInfo:
        """Return shared device info for all Suno entities."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.unique_id or self.config_entry.entry_id)},
            name="Suno",
            manufacturer="Suno",
            model="Music Library",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://suno.com",
        )

    async def _async_update_data(self) -> SunoData:
        """Fetch library, liked songs, playlists, and credits from Suno."""
        try:
            clips = await self.client.get_all_songs()
            _LOGGER.debug("Fetched %d clips from Suno library", len(clips))

            liked_clips: list[SunoClip] = []
            try:
                liked_clips = await self.client.get_liked_songs()
                _LOGGER.debug("Fetched %d liked clips", len(liked_clips))
            except Exception:
                _LOGGER.warning("Could not fetch liked songs, skipping", exc_info=True)

            playlists: list[SunoPlaylist] = []
            try:
                playlists = await self.client.get_playlists()
                _LOGGER.debug("Fetched %d playlists", len(playlists))
            except Exception:
                _LOGGER.warning("Could not fetch playlists, skipping", exc_info=True)

            credits: SunoCredits | None = None
            try:
                credits = await self.client.get_credits()
            except Exception:
                _LOGGER.warning("Could not fetch credits, skipping", exc_info=True)

            return SunoData(
                clips=clips,
                liked_clips=liked_clips,
                playlists=playlists,
                credits=credits,
            )

        except SunoAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from err
        except Exception as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
