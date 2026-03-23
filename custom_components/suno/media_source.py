"""Media source for the Suno integration."""

from __future__ import annotations

import logging

from homeassistant.components.media_player import BrowseError, MediaClass  # type: ignore[attr-defined]
from homeassistant.components.media_source import BrowseMediaSource, MediaSource, MediaSourceItem, PlayMedia
from homeassistant.core import HomeAssistant

from . import SunoConfigEntry
from .const import (
    CONF_QUALITY_LIKED,
    CONF_QUALITY_PLAYLISTS,
    CONF_SHOW_LATEST,
    CONF_SHOW_LIKED,
    CONF_SHOW_PLAYLISTS,
    DEFAULT_SHOW_LATEST,
    DEFAULT_SHOW_LIKED,
    DEFAULT_SHOW_PLAYLISTS,
    DOMAIN,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from .coordinator import SunoCoordinator, SunoData
from .models import SunoClip

_LOGGER = logging.getLogger(__name__)
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
    """Create a folder media item."""
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
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if hasattr(entry, "runtime_data") and entry.runtime_data is not None:
                return entry, entry.runtime_data
        return None

    def _get_clip_quality(self, clip: SunoClip, entry: SunoConfigEntry, coordinator: SunoCoordinator) -> str:
        """Determine quality for a clip based on source membership."""
        opts = entry.options
        if opts.get(CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED) and clip.is_liked:
            if opts.get(CONF_QUALITY_LIKED, QUALITY_HIGH) == QUALITY_HIGH:
                return QUALITY_HIGH
        if opts.get(CONF_SHOW_PLAYLISTS, DEFAULT_SHOW_PLAYLISTS):
            if opts.get(CONF_QUALITY_PLAYLISTS, QUALITY_HIGH) == QUALITY_HIGH:
                for pl_clips in coordinator.data.playlist_clips.values():
                    if any(c.id == clip.id for c in pl_clips):
                        return QUALITY_HIGH
        # Clips not in a FLAC source default to standard (MP3).
        # "Latest" membership is expensive to check and not worth computing per-resolve.
        return QUALITY_STANDARD

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a media item to a playable URL."""
        identifier = item.identifier or ""
        if not identifier.startswith("clip/"):
            raise BrowseError(f"Unknown media identifier: {identifier}")
        if not (result := self._get_entry_and_coordinator()):
            raise BrowseError("Suno integration not configured")
        entry, coordinator = result
        clip_id = identifier.removeprefix("clip/")
        clip = next((c for c in coordinator.data.clips if c.id == clip_id), None)
        if not clip:
            clip = next((c for c in coordinator.data.liked_clips if c.id == clip_id), None)
        quality = self._get_clip_quality(clip, entry, coordinator) if clip else QUALITY_STANDARD
        ext = "flac" if quality == QUALITY_HIGH else "mp3"
        mime = "audio/flac" if quality == QUALITY_HIGH else "audio/mpeg"
        return PlayMedia(url=f"/api/suno/media/{clip_id}.{ext}", mime_type=mime)

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse the Suno library."""
        if not (result := self._get_entry_and_coordinator()):
            return _folder("", "Suno", [])
        entry, coordinator = result
        identifier = item.identifier or ""
        ct = "audio/mpeg"
        if not identifier:
            return await self._browse_root(entry, coordinator, ct)
        if identifier == "liked":
            return self._browse_liked(coordinator, ct)
        if identifier == "latest":
            return await self._browse_latest(entry, coordinator, ct)
        if identifier == "playlists":
            return self._browse_playlists(coordinator)
        if identifier.startswith("playlist/"):
            return await self._browse_playlist(coordinator, identifier.removeprefix("playlist/"), ct)
        if identifier == "all":
            return self._browse_all(coordinator, ct)
        if identifier.startswith("all/page/"):
            return self._browse_all_page(coordinator, int(identifier.removeprefix("all/page/")), ct)
        return _folder("", "Suno", [])

    async def _browse_root(self, entry: SunoConfigEntry, coordinator: SunoCoordinator, ct: str) -> BrowseMediaSource:
        """Build the root media browser view."""
        children: list[BrowseMediaSource] = []
        data: SunoData = coordinator.data
        if entry.options.get(CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED):
            children.append(_folder("liked", f"Liked Songs ({len(data.liked_clips)})"))
        if entry.options.get(CONF_SHOW_LATEST, DEFAULT_SHOW_LATEST):
            children.append(_folder("latest", "Your Latest"))
        if entry.options.get(CONF_SHOW_PLAYLISTS, DEFAULT_SHOW_PLAYLISTS) and data.playlists:
            children.append(_folder("playlists", f"Playlists ({len(data.playlists)})"))
        children.append(_folder("all", f"All Songs ({len(data.clips)})"))
        return _folder("", "Suno", children)

    def _browse_liked(self, coordinator: SunoCoordinator, ct: str) -> BrowseMediaSource:
        """Show liked songs."""
        liked = coordinator.data.liked_clips
        return _folder("liked", f"Liked Songs ({len(liked)})", [_clip_to_media(c, ct) for c in liked])

    async def _browse_latest(self, entry: SunoConfigEntry, coordinator: SunoCoordinator, ct: str) -> BrowseMediaSource:
        """Show latest songs from the feed."""
        try:
            clips, _ = await coordinator.client.get_feed(0)
        except Exception:
            _LOGGER.warning("Could not fetch latest songs live, falling back to cache")
            clips = coordinator.data.clips[:20]
        children = [_clip_to_media(c, ct) for c in clips]
        return _folder("latest", f"Your Latest ({len(children)})", children)

    def _browse_playlists(self, coordinator: SunoCoordinator) -> BrowseMediaSource:
        """Show playlist folders."""
        data = coordinator.data
        return _folder(
            "playlists",
            f"Playlists ({len(data.playlists)})",
            [_folder(f"playlist/{pl.id}", f"{pl.name} ({pl.num_clips})") for pl in data.playlists],
        )

    async def _browse_playlist(self, coordinator: SunoCoordinator, playlist_id: str, ct: str) -> BrowseMediaSource:
        """Show songs in a specific playlist."""
        clips = coordinator.data.playlist_clips.get(playlist_id, [])
        name = next((pl.name for pl in coordinator.data.playlists if pl.id == playlist_id), "Playlist")
        children = [_clip_to_media(c, ct) for c in clips]
        return _folder(f"playlist/{playlist_id}", f"{name} ({len(children)})", children)

    def _browse_all(self, coordinator: SunoCoordinator, ct: str) -> BrowseMediaSource:
        """Show all songs, chunked into virtual folders if large."""
        data, total = coordinator.data, len(coordinator.data.clips)
        if total <= _CHUNK_SIZE:
            return _folder("all", f"All Songs ({total})", [_clip_to_media(c, ct) for c in data.clips])
        folders = [
            _folder(f"all/page/{i // _CHUNK_SIZE}", f"Songs {i + 1}-{min(i + _CHUNK_SIZE, total)}")
            for i in range(0, total, _CHUNK_SIZE)
        ]
        return _folder("all", f"All Songs ({total})", folders)

    def _browse_all_page(self, coordinator: SunoCoordinator, page: int, ct: str) -> BrowseMediaSource:
        """Show a chunk of all songs."""
        start, end = page * _CHUNK_SIZE, min((page + 1) * _CHUNK_SIZE, len(coordinator.data.clips))
        return _folder(
            f"all/page/{page}",
            f"Songs {start + 1}-{end}",
            [_clip_to_media(c, ct) for c in coordinator.data.clips[start:end]],
        )
