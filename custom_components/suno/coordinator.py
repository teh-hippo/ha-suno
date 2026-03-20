"""Data coordinator for the Suno integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SunoClient, SunoClip, SunoCredits, SunoPlaylist
from .const import CONF_CACHE_TTL, DEFAULT_CACHE_TTL, DOMAIN
from .exceptions import SunoAuthError

_LOGGER = logging.getLogger(__name__)


@dataclass
class SunoData:
    """Holds all cached Suno data."""

    clips: list[SunoClip] = field(default_factory=list)
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

    async def _async_update_data(self) -> SunoData:
        """Fetch library, playlists, and credits from Suno."""
        try:
            clips = await self.client.get_all_songs()
            _LOGGER.debug("Fetched %d clips from Suno library", len(clips))

            playlists: list[SunoPlaylist] = []
            try:
                playlists = await self.client.get_playlists()
                _LOGGER.debug("Fetched %d playlists", len(playlists))
            except Exception:
                _LOGGER.warning("Could not fetch playlists, skipping")

            credits: SunoCredits | None = None
            try:
                credits = await self.client.get_credits()
            except Exception:
                _LOGGER.warning("Could not fetch credits, skipping")

            return SunoData(clips=clips, playlists=playlists, credits=credits)

        except SunoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Exception as err:
            raise UpdateFailed(f"Error fetching Suno data: {err}") from err
