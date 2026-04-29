"""Media source for the Suno integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.media_player import BrowseError, MediaClass  # type: ignore[attr-defined]
from homeassistant.components.media_source import BrowseMediaSource, MediaSource, MediaSourceItem, PlayMedia
from homeassistant.core import HomeAssistant

from . import SunoConfigEntry
from .const import (
    CONF_SHOW_LIKED,
    CONF_SHOW_MY_SONGS,
    CONF_SHOW_PLAYLISTS,
    DEFAULT_SHOW_LIKED,
    DEFAULT_SHOW_MY_SONGS,
    DEFAULT_SHOW_PLAYLISTS,
    DOMAIN,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from .models import SunoClip
from .runtime import HomeAssistantRuntime, iter_entry_runtimes

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

    def _get_entry_and_runtime(self) -> tuple[SunoConfigEntry, HomeAssistantRuntime] | None:
        """Find the active Suno config entry and its runtime."""
        for entry, runtime in iter_entry_runtimes(self.hass):
            return entry, runtime
        return None

    def _get_clip_quality(self, clip: SunoClip, _entry: SunoConfigEntry, runtime: HomeAssistantRuntime) -> str:
        """Determine quality for a clip based on source membership."""
        return runtime.quality_for_clip(clip)

    def _find_clip_entry(self, clip_id: str) -> tuple[SunoConfigEntry, HomeAssistantRuntime, SunoClip] | None:
        """Find which entry owns a specific clip by searching all loaded entries."""
        for entry, runtime in iter_entry_runtimes(self.hass):
            clip = runtime.find_clip(clip_id)
            if clip:
                return entry, runtime, clip
        return None

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a media item to a playable URL."""
        identifier = item.identifier or ""
        if not identifier.startswith("clip/"):
            raise BrowseError(f"Unknown media identifier: {identifier}")
        clip_id = identifier.removeprefix("clip/")
        result = self._find_clip_entry(clip_id)
        if result:
            entry, runtime, clip = result
            quality = self._get_clip_quality(clip, entry, runtime)
        else:
            if not self._get_entry_and_runtime():
                raise BrowseError("Suno integration not configured")
            quality = QUALITY_STANDARD
        ext = "flac" if quality == QUALITY_HIGH else "mp3"
        mime = "audio/flac" if quality == QUALITY_HIGH else "audio/mpeg"
        path = f"/api/suno/media/{clip_id}.{ext}"
        signed = async_sign_path(self.hass, path, timedelta(hours=1), use_content_user=True)
        return PlayMedia(url=signed, mime_type=mime)

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse the Suno library."""
        if not (result := self._get_entry_and_runtime()):
            return _folder("", "Suno", [])
        entry, runtime = result
        identifier = item.identifier or ""
        ct = "audio/mpeg"
        if not identifier:
            return await self._browse_root(entry, runtime)
        if identifier == "liked":
            return self._browse_liked(runtime, ct)
        if identifier == "my_songs":
            return await self._browse_my_songs(runtime, ct)
        if identifier == "playlists":
            return self._browse_playlists(runtime)
        if identifier.startswith("playlist/"):
            return await self._browse_playlist(runtime, identifier.removeprefix("playlist/"), ct)
        if identifier == "all":
            return self._browse_all(runtime, ct)
        if identifier.startswith("all/page/"):
            return self._browse_all_page(runtime, int(identifier.removeprefix("all/page/")), ct)
        return _folder("", "Suno", [])

    async def _browse_root(self, entry: SunoConfigEntry, runtime: HomeAssistantRuntime) -> BrowseMediaSource:
        """Build the root media browser view."""
        children: list[BrowseMediaSource] = []
        data = runtime.suno_library
        if entry.options.get(CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED):
            children.append(_folder("liked", f"Liked Songs ({len(data.liked_clips)})"))
        if entry.options.get(CONF_SHOW_MY_SONGS, DEFAULT_SHOW_MY_SONGS):
            children.append(_folder("my_songs", "My Songs"))
        if entry.options.get(CONF_SHOW_PLAYLISTS, DEFAULT_SHOW_PLAYLISTS) and data.playlists:
            children.append(_folder("playlists", f"Playlists ({len(data.playlists)})"))
        children.append(_folder("all", f"All Songs ({len(data.clips)})"))
        return _folder("", "Suno", children)

    def _browse_liked(self, runtime: HomeAssistantRuntime, ct: str) -> BrowseMediaSource:
        """Show liked songs."""
        liked = runtime.suno_library.liked_clips
        return _folder("liked", f"Liked Songs ({len(liked)})", [_clip_to_media(c, ct) for c in liked])

    async def _browse_my_songs(self, runtime: HomeAssistantRuntime, ct: str) -> BrowseMediaSource:
        """Show user's songs from the cached library, sorted by newest first."""
        clips = sorted(runtime.suno_library.clips, key=lambda c: c.created_at or "", reverse=True)[:20]
        children = [_clip_to_media(c, ct) for c in clips]
        return _folder("my_songs", f"My Songs ({len(children)})", children)

    def _browse_playlists(self, runtime: HomeAssistantRuntime) -> BrowseMediaSource:
        """Show playlist folders."""
        data = runtime.suno_library
        return _folder(
            "playlists",
            f"Playlists ({len(data.playlists)})",
            [_folder(f"playlist/{pl.id}", f"{pl.name} ({pl.num_clips})") for pl in data.playlists],
        )

    async def _browse_playlist(self, runtime: HomeAssistantRuntime, playlist_id: str, ct: str) -> BrowseMediaSource:
        """Show songs in a specific playlist."""
        clips = runtime.suno_library.playlist_clips.get(playlist_id, [])
        name = next((pl.name for pl in runtime.suno_library.playlists if pl.id == playlist_id), "Playlist")
        children = [_clip_to_media(c, ct) for c in clips]
        return _folder(f"playlist/{playlist_id}", f"{name} ({len(children)})", children)

    def _browse_all(self, runtime: HomeAssistantRuntime, ct: str) -> BrowseMediaSource:
        """Show all songs, chunked into virtual folders if large."""
        data, total = runtime.suno_library, len(runtime.suno_library.clips)
        if total <= _CHUNK_SIZE:
            return _folder("all", f"All Songs ({total})", [_clip_to_media(c, ct) for c in data.clips])
        folders = [
            _folder(f"all/page/{i // _CHUNK_SIZE}", f"Songs {i + 1}-{min(i + _CHUNK_SIZE, total)}")
            for i in range(0, total, _CHUNK_SIZE)
        ]
        return _folder("all", f"All Songs ({total})", folders)

    def _browse_all_page(self, runtime: HomeAssistantRuntime, page: int, ct: str) -> BrowseMediaSource:
        """Show a chunk of all songs."""
        start, end = page * _CHUNK_SIZE, min((page + 1) * _CHUNK_SIZE, len(runtime.suno_library.clips))
        return _folder(
            f"all/page/{page}",
            f"Songs {start + 1}-{end}",
            [_clip_to_media(c, ct) for c in runtime.suno_library.clips[start:end]],
        )
