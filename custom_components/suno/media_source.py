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
from homeassistant.core import HomeAssistant

from . import SunoConfigEntry
from .const import (
    CONF_AUDIO_QUALITY,
    CONF_RECENT_COUNT,
    CONF_SHOW_LIKED,
    CONF_SHOW_PLAYLISTS,
    CONF_SHOW_RECENT,
    DEFAULT_AUDIO_QUALITY,
    DEFAULT_RECENT_COUNT,
    DEFAULT_SHOW_LIKED,
    DEFAULT_SHOW_PLAYLISTS,
    DEFAULT_SHOW_RECENT,
    DOMAIN,
    QUALITY_HIGH,
)
from .coordinator import SunoCoordinator, SunoData
from .models import SunoClip

_LOGGER = logging.getLogger(__name__)

# Virtual folder chunk size for large libraries
_CHUNK_SIZE = 50


async def async_get_media_source(hass: HomeAssistant) -> SunoMediaSource:
    """Set up Suno media source."""
    return SunoMediaSource(hass)


def _clip_to_media(clip: SunoClip, content_type: str = "audio/mpeg") -> BrowseMediaSource:
    """Convert a SunoClip to a browsable media item."""
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=f"clip/{clip.id}",
        media_class=MediaClass.MUSIC,
        media_content_type=content_type,
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

    def _get_entry_and_coordinator(self) -> tuple[SunoConfigEntry, SunoCoordinator] | None:
        """Find the active Suno config entry and its coordinator."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            if hasattr(entry, "runtime_data") and entry.runtime_data is not None:
                return entry, entry.runtime_data
        return None

    def _get_mime_type(self, entry: SunoConfigEntry) -> str:
        """Return the MIME type based on the audio quality setting."""
        quality = entry.options.get(CONF_AUDIO_QUALITY, DEFAULT_AUDIO_QUALITY)
        return "audio/flac" if quality == QUALITY_HIGH else "audio/mpeg"

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a media item to a playable URL."""
        identifier = item.identifier or ""

        if not identifier.startswith("clip/"):
            raise BrowseError(f"Unknown media identifier: {identifier}")

        clip_id = identifier.removeprefix("clip/")
        result = self._get_entry_and_coordinator()
        if not result:
            raise BrowseError("Suno integration not configured")

        entry, _ = result

        # Always resolve via the proxy.  Playlist clips may not be in the
        # coordinator cache, but the proxy can still stream them from CDN.
        quality = entry.options.get(CONF_AUDIO_QUALITY, DEFAULT_AUDIO_QUALITY)
        ext = "flac" if quality == QUALITY_HIGH else "mp3"
        return PlayMedia(
            url=f"/api/suno/media/{clip_id}.{ext}",
            mime_type=self._get_mime_type(entry),
        )

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse the Suno library."""
        result = self._get_entry_and_coordinator()
        if not result:
            return _folder("", "Suno", [])

        entry, coordinator = result
        identifier = item.identifier or ""
        ct = self._get_mime_type(entry)

        if not identifier:
            return await self._browse_root(entry, coordinator, ct)
        if identifier == "liked":
            return self._browse_liked(coordinator, ct)
        if identifier == "recent":
            return await self._browse_recent(entry, coordinator, ct)
        if identifier == "playlists":
            return self._browse_playlists(coordinator)
        if identifier.startswith("playlist/"):
            playlist_id = identifier.removeprefix("playlist/")
            return await self._browse_playlist(coordinator, playlist_id, ct)
        if identifier == "all":
            return self._browse_all(coordinator, ct)
        if identifier.startswith("all/page/"):
            page_str = identifier.removeprefix("all/page/")
            return self._browse_all_page(coordinator, int(page_str), ct)

        return _folder("", "Suno", [])

    async def _browse_root(self, entry: SunoConfigEntry, coordinator: SunoCoordinator, ct: str) -> BrowseMediaSource:
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

    def _browse_liked(self, coordinator: SunoCoordinator, ct: str) -> BrowseMediaSource:
        """Show liked songs."""
        data: SunoData = coordinator.data
        liked = data.liked_clips
        children = [_clip_to_media(clip, ct) for clip in liked]
        return _folder("liked", f"Liked Songs ({len(liked)})", children)

    async def _browse_recent(self, entry: SunoConfigEntry, coordinator: SunoCoordinator, ct: str) -> BrowseMediaSource:
        """Show recent songs, fetched live from page 0."""
        count = entry.options.get(CONF_RECENT_COUNT, DEFAULT_RECENT_COUNT)
        try:
            clips, _ = await coordinator.client.get_feed(0)
            clips = clips[:count]
        except Exception:
            _LOGGER.warning("Could not fetch recent songs live, falling back to cache")
            clips = coordinator.data.clips[:count]

        children = [_clip_to_media(clip, ct) for clip in clips]
        return _folder("recent", f"Recent ({len(children)})", children)

    def _browse_playlists(self, coordinator: SunoCoordinator) -> BrowseMediaSource:
        """Show playlist folders."""
        data: SunoData = coordinator.data
        children = [_folder(f"playlist/{pl.id}", f"{pl.name} ({pl.num_clips})") for pl in data.playlists]
        return _folder("playlists", f"Playlists ({len(data.playlists)})", children)

    async def _browse_playlist(self, coordinator: SunoCoordinator, playlist_id: str, ct: str) -> BrowseMediaSource:
        """Show songs in a specific playlist."""
        try:
            clips = await coordinator.client.get_playlist_clips(playlist_id)
        except Exception:
            _LOGGER.warning("Could not fetch playlist %s", playlist_id)
            clips = []

        name = "Playlist"
        for pl in coordinator.data.playlists:
            if pl.id == playlist_id:
                name = pl.name
                break

        children = [_clip_to_media(clip, ct) for clip in clips]
        return _folder(f"playlist/{playlist_id}", f"{name} ({len(children)})", children)

    def _browse_all(self, coordinator: SunoCoordinator, ct: str) -> BrowseMediaSource:
        """Show all songs, chunked into virtual folders if large."""
        data: SunoData = coordinator.data
        total = len(data.clips)

        if total <= _CHUNK_SIZE:
            items = [_clip_to_media(clip, ct) for clip in data.clips]
            return _folder("all", f"All Songs ({total})", items)

        folders: list[BrowseMediaSource] = []
        for i in range(0, total, _CHUNK_SIZE):
            end = min(i + _CHUNK_SIZE, total)
            folders.append(_folder(f"all/page/{i // _CHUNK_SIZE}", f"Songs {i + 1}-{end}"))
        return _folder("all", f"All Songs ({total})", folders)

    def _browse_all_page(self, coordinator: SunoCoordinator, page: int, ct: str) -> BrowseMediaSource:
        """Show a chunk of all songs."""
        data: SunoData = coordinator.data
        start = page * _CHUNK_SIZE
        end = min(start + _CHUNK_SIZE, len(data.clips))
        chunk = data.clips[start:end]

        children = [_clip_to_media(clip, ct) for clip in chunk]
        return _folder(f"all/page/{page}", f"Songs {start + 1}-{end}", children)
