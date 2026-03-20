"""Media source for the Suno integration.

Exposes the user's Suno library in the HA media browser.
"""

from __future__ import annotations

import logging

from homeassistant.components.media_player import BrowseError, MediaClass  # type: ignore[attr-defined]
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import SunoClip
from .const import (
    CONF_RECENT_COUNT,
    CONF_SHOW_LIKED,
    CONF_SHOW_PLAYLISTS,
    CONF_SHOW_RECENT,
    DEFAULT_RECENT_COUNT,
    DEFAULT_SHOW_LIKED,
    DEFAULT_SHOW_PLAYLISTS,
    DEFAULT_SHOW_RECENT,
    DOMAIN,
)
from .coordinator import SunoCoordinator, SunoData

_LOGGER = logging.getLogger(__name__)

# Virtual folder chunk size for large libraries
_CHUNK_SIZE = 50


async def async_get_media_source(hass: HomeAssistant) -> SunoMediaSource:
    """Set up Suno media source."""
    return SunoMediaSource(hass)


def _clip_to_media(clip: SunoClip) -> BrowseMediaSource:
    """Convert a SunoClip to a browsable media item."""
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=f"clip/{clip.id}",
        media_class=MediaClass.MUSIC,
        media_content_type="audio/mpeg",
        title=clip.title,
        can_play=bool(clip.audio_url),
        can_expand=False,
        thumbnail=clip.image_url or None,
    )


def _folder(identifier: str, title: str, children: list[BrowseMediaSource] | None = None) -> BrowseMediaSource:
    """Create a folder (directory) media item."""
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=identifier,
        media_class=MediaClass.DIRECTORY,
        media_content_type="",
        title=title,
        can_play=False,
        can_expand=True,
        children=children or [],
        children_media_class=MediaClass.MUSIC,
    )


class SunoMediaSource(MediaSource):
    """Provide Suno library as a media source."""

    name = "Suno"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    def _get_entry_and_coordinator(self) -> tuple[ConfigEntry, SunoCoordinator] | None:
        """Find the active Suno config entry and its coordinator."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            if hasattr(entry, "runtime_data") and entry.runtime_data is not None:
                return entry, entry.runtime_data
        return None

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a media item to a playable URL."""
        identifier = item.identifier or ""

        if not identifier.startswith("clip/"):
            raise BrowseError(f"Unknown media identifier: {identifier}")

        clip_id = identifier.removeprefix("clip/")
        result = self._get_entry_and_coordinator()
        if not result:
            raise BrowseError("Suno integration not configured")

        _, coordinator = result
        data: SunoData = coordinator.data

        # Search all clips and liked clips
        for clip in data.clips:
            if clip.id == clip_id:
                return PlayMedia(
                    url=f"/api/suno/media/{clip_id}",
                    mime_type="audio/mpeg",
                )
        for clip in data.liked_clips:
            if clip.id == clip_id:
                return PlayMedia(
                    url=f"/api/suno/media/{clip_id}",
                    mime_type="audio/mpeg",
                )

        raise BrowseError(f"Clip {clip_id} not found in library")

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse the Suno library."""
        result = self._get_entry_and_coordinator()
        if not result:
            return _folder("", "Suno", [])

        entry, coordinator = result
        identifier = item.identifier or ""

        if not identifier:
            return await self._browse_root(entry, coordinator)
        if identifier == "liked":
            return self._browse_liked(coordinator)
        if identifier == "recent":
            return await self._browse_recent(entry, coordinator)
        if identifier == "playlists":
            return self._browse_playlists(coordinator)
        if identifier.startswith("playlist/"):
            playlist_id = identifier.removeprefix("playlist/")
            return await self._browse_playlist(coordinator, playlist_id)
        if identifier == "all":
            return self._browse_all(coordinator)
        if identifier.startswith("all/page/"):
            page_str = identifier.removeprefix("all/page/")
            return self._browse_all_page(coordinator, int(page_str))

        return _folder("", "Suno", [])

    async def _browse_root(self, entry: ConfigEntry, coordinator: SunoCoordinator) -> BrowseMediaSource:
        """Build the root media browser view."""
        children: list[BrowseMediaSource] = []
        data: SunoData = coordinator.data

        if entry.options.get(CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED):
            liked_count = len(data.liked_clips)
            children.append(_folder("liked", f"Liked Songs ({liked_count})"))

        if entry.options.get(CONF_SHOW_RECENT, DEFAULT_SHOW_RECENT):
            children.append(_folder("recent", "Recent"))

        if entry.options.get(CONF_SHOW_PLAYLISTS, DEFAULT_SHOW_PLAYLISTS) and data.playlists:
            children.append(_folder("playlists", f"Playlists ({len(data.playlists)})"))

        children.append(_folder("all", f"All Songs ({len(data.clips)})"))

        return _folder("", "Suno", children)

    def _browse_liked(self, coordinator: SunoCoordinator) -> BrowseMediaSource:
        """Show liked songs."""
        data: SunoData = coordinator.data
        liked = data.liked_clips
        children = [_clip_to_media(clip) for clip in liked]
        return _folder("liked", f"Liked Songs ({len(liked)})", children)

    async def _browse_recent(self, entry: ConfigEntry, coordinator: SunoCoordinator) -> BrowseMediaSource:
        """Show recent songs, fetched live from page 0."""
        count = entry.options.get(CONF_RECENT_COUNT, DEFAULT_RECENT_COUNT)
        try:
            clips, _ = await coordinator.client.get_feed(0)
            clips = clips[:count]
        except Exception:
            _LOGGER.warning("Could not fetch recent songs live, falling back to cache")
            clips = coordinator.data.clips[:count]

        children = [_clip_to_media(clip) for clip in clips]
        return _folder("recent", f"Recent ({len(children)})", children)

    def _browse_playlists(self, coordinator: SunoCoordinator) -> BrowseMediaSource:
        """Show playlist folders."""
        data: SunoData = coordinator.data
        children = [_folder(f"playlist/{pl.id}", f"{pl.name} ({pl.num_clips})") for pl in data.playlists]
        return _folder("playlists", f"Playlists ({len(data.playlists)})", children)

    async def _browse_playlist(self, coordinator: SunoCoordinator, playlist_id: str) -> BrowseMediaSource:
        """Show songs in a specific playlist."""
        try:
            clips = await coordinator.client.get_playlist_clips(playlist_id)
        except Exception:
            _LOGGER.warning("Could not fetch playlist %s", playlist_id)
            clips = []

        # Find playlist name
        name = "Playlist"
        for pl in coordinator.data.playlists:
            if pl.id == playlist_id:
                name = pl.name
                break

        children = [_clip_to_media(clip) for clip in clips]
        return _folder(f"playlist/{playlist_id}", f"{name} ({len(children)})", children)

    def _browse_all(self, coordinator: SunoCoordinator) -> BrowseMediaSource:
        """Show all songs, chunked into virtual folders if large."""
        data: SunoData = coordinator.data
        total = len(data.clips)

        if total <= _CHUNK_SIZE:
            items = [_clip_to_media(clip) for clip in data.clips]
            return _folder("all", f"All Songs ({total})", items)

        # Chunk into virtual folders
        folders: list[BrowseMediaSource] = []
        for i in range(0, total, _CHUNK_SIZE):
            end = min(i + _CHUNK_SIZE, total)
            folders.append(_folder(f"all/page/{i // _CHUNK_SIZE}", f"Songs {i + 1}-{end}"))
        return _folder("all", f"All Songs ({total})", folders)

    def _browse_all_page(self, coordinator: SunoCoordinator, page: int) -> BrowseMediaSource:
        """Show a chunk of all songs."""
        data: SunoData = coordinator.data
        start = page * _CHUNK_SIZE
        end = min(start + _CHUNK_SIZE, len(data.clips))
        chunk = data.clips[start:end]

        children = [_clip_to_media(clip) for clip in chunk]
        return _folder(f"all/page/{page}", f"Songs {start + 1}-{end}", children)
