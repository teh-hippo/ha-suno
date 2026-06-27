"""Media source for the Suno integration."""

from __future__ import annotations

from datetime import timedelta
from typing import cast

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
from .runtime import HomeAssistantRuntime, iter_entry_runtimes, runtime_from_entry

_CHUNK_SIZE = 50
_ACCOUNT_PREFIX = "account/"


async def async_get_media_source(hass: HomeAssistant) -> SunoMediaSource:
    """Set up Suno media source."""
    return SunoMediaSource(hass)


def _scoped_identifier(prefix: str, identifier: str) -> str:
    """Apply an account identifier prefix when browsing multiple accounts."""
    if not prefix:
        return identifier
    return f"{prefix}/{identifier}" if identifier else prefix


def _split_account_identifier(identifier: str) -> tuple[str, str] | None:
    """Split account-scoped media source identifiers."""
    if not identifier.startswith(_ACCOUNT_PREFIX):
        return None
    remainder = identifier.removeprefix(_ACCOUNT_PREFIX)
    entry_id, sep, child_identifier = remainder.partition("/")
    if not entry_id:
        return None
    return entry_id, child_identifier if sep else ""


def _clip_to_media(clip: SunoClip, content_type: str = "audio/mpeg", prefix: str = "") -> BrowseMediaSource:
    """Convert a SunoClip to a browsable media item."""
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=_scoped_identifier(prefix, f"clip/{clip.id}"),
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

    def _get_runtime_for_entry_id(self, entry_id: str) -> tuple[SunoConfigEntry, HomeAssistantRuntime] | None:
        """Find a specific loaded Suno config entry and runtime."""
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            return None
        runtime = runtime_from_entry(entry)
        if runtime is None:
            return None
        return cast(SunoConfigEntry, entry), runtime

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
        scoped = _split_account_identifier(identifier)
        if scoped is not None:
            return self._resolve_scoped_media(item.identifier or "", scoped)

        if not identifier.startswith("clip/"):
            raise BrowseError(f"Unknown media identifier: {identifier}")
        clip_id = identifier.removeprefix("clip/")
        result = self._find_clip_entry(clip_id)
        entry_id: str | None = None
        if result:
            entry, runtime, clip = result
            quality = self._get_clip_quality(clip, entry, runtime)
            entry_id = entry.entry_id if self._has_multiple_loaded_accounts() else None
        else:
            if not self._get_entry_and_runtime():
                raise BrowseError("Suno integration not configured")
            quality = QUALITY_STANDARD
        return self._play_media_for_clip(clip_id, quality, entry_id=entry_id)

    def _resolve_scoped_media(self, original_identifier: str, scoped: tuple[str, str]) -> PlayMedia:
        """Resolve an account-scoped media identifier."""
        entry_id, identifier = scoped
        result = self._get_runtime_for_entry_id(entry_id)
        if result is None:
            raise BrowseError("Suno account not configured")
        entry, runtime = result
        if not identifier.startswith("clip/"):
            raise BrowseError(f"Unknown media identifier: {original_identifier}")
        clip_id = identifier.removeprefix("clip/")
        clip = runtime.find_clip(clip_id)
        if clip is None:
            raise BrowseError(f"Unknown Suno clip for account: {clip_id}")
        quality = self._get_clip_quality(clip, entry, runtime)
        return self._play_media_for_clip(clip_id, quality, entry_id=entry_id)

    def _play_media_for_clip(self, clip_id: str, quality: str, *, entry_id: str | None = None) -> PlayMedia:
        """Build a signed proxy URL for a clip."""
        ext = "flac" if quality == QUALITY_HIGH else "mp3"
        mime = "audio/flac" if quality == QUALITY_HIGH else "audio/mpeg"
        path = f"/api/suno/media/{entry_id}/{clip_id}.{ext}" if entry_id else f"/api/suno/media/{clip_id}.{ext}"
        signed = async_sign_path(self.hass, path, timedelta(hours=1), use_content_user=True)
        return PlayMedia(url=signed, mime_type=mime)

    def _has_multiple_loaded_accounts(self) -> bool:
        """Return True if more than one Suno runtime is loaded."""
        count = 0
        for _entry, _runtime in iter_entry_runtimes(self.hass):
            count += 1
            if count > 1:
                return True
        return False

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse the Suno library."""
        runtimes = list(iter_entry_runtimes(self.hass))
        if not runtimes:
            return _folder("", "Suno", [])

        identifier = item.identifier or ""
        if not identifier:
            if len(runtimes) == 1:
                entry, runtime = runtimes[0]
                return await self._browse_root(entry, runtime)
            return _folder(
                "",
                "Suno",
                [
                    _folder(f"account/{entry.entry_id}", runtime.user.display_name or entry.title)
                    for entry, runtime in runtimes
                ],
            )

        scoped = _split_account_identifier(identifier)
        if scoped is not None:
            entry_id, child_identifier = scoped
            result = self._get_runtime_for_entry_id(entry_id)
            if result is None:
                raise BrowseError("Suno account not configured")
            entry, runtime = result
            return await self._browse_identifier(
                entry,
                runtime,
                child_identifier,
                prefix=f"account/{entry.entry_id}",
            )

        entry, runtime = runtimes[0]
        return await self._browse_identifier(entry, runtime, identifier)

    async def _browse_identifier(
        self,
        entry: SunoConfigEntry,
        runtime: HomeAssistantRuntime,
        identifier: str,
        *,
        prefix: str = "",
    ) -> BrowseMediaSource:
        """Browse one entry's media tree."""
        ct = "audio/mpeg"
        if not identifier:
            title = (runtime.user.display_name or entry.title) if prefix else "Suno"
            return await self._browse_root(entry, runtime, prefix=prefix, title=title)
        if identifier == "liked":
            return self._browse_liked(runtime, ct, prefix)
        if identifier == "my_songs":
            return await self._browse_my_songs(runtime, ct, prefix)
        if identifier == "playlists":
            return self._browse_playlists(runtime, prefix)
        if identifier.startswith("playlist/"):
            return await self._browse_playlist(runtime, identifier.removeprefix("playlist/"), ct, prefix)
        if identifier == "all":
            return self._browse_all(runtime, ct, prefix)
        if identifier.startswith("all/page/"):
            return self._browse_all_page(runtime, int(identifier.removeprefix("all/page/")), ct, prefix)
        return _folder(_scoped_identifier(prefix, ""), "Suno", [])

    async def _browse_root(
        self,
        entry: SunoConfigEntry,
        runtime: HomeAssistantRuntime,
        *,
        prefix: str = "",
        title: str = "Suno",
    ) -> BrowseMediaSource:
        """Build the root media browser view."""
        children: list[BrowseMediaSource] = []
        data = runtime.suno_library
        if entry.options.get(CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED):
            children.append(_folder(_scoped_identifier(prefix, "liked"), f"Liked Songs ({len(data.liked_clips)})"))
        if entry.options.get(CONF_SHOW_MY_SONGS, DEFAULT_SHOW_MY_SONGS):
            children.append(_folder(_scoped_identifier(prefix, "my_songs"), "My Songs"))
        if entry.options.get(CONF_SHOW_PLAYLISTS, DEFAULT_SHOW_PLAYLISTS) and data.playlists:
            children.append(_folder(_scoped_identifier(prefix, "playlists"), f"Playlists ({len(data.playlists)})"))
        children.append(_folder(_scoped_identifier(prefix, "all"), f"All Songs ({len(data.clips)})"))
        return _folder(_scoped_identifier(prefix, ""), title, children)

    def _browse_liked(self, runtime: HomeAssistantRuntime, ct: str, prefix: str = "") -> BrowseMediaSource:
        """Show liked songs."""
        liked = runtime.suno_library.liked_clips
        return _folder(
            _scoped_identifier(prefix, "liked"),
            f"Liked Songs ({len(liked)})",
            [_clip_to_media(c, ct, prefix) for c in liked],
        )

    async def _browse_my_songs(self, runtime: HomeAssistantRuntime, ct: str, prefix: str = "") -> BrowseMediaSource:
        """Show user's songs from the cached library, sorted by newest first."""
        clips = sorted(runtime.suno_library.clips, key=lambda c: c.created_at or "", reverse=True)[:20]
        children = [_clip_to_media(c, ct, prefix) for c in clips]
        return _folder(_scoped_identifier(prefix, "my_songs"), f"My Songs ({len(children)})", children)

    def _browse_playlists(self, runtime: HomeAssistantRuntime, prefix: str = "") -> BrowseMediaSource:
        """Show playlist folders."""
        data = runtime.suno_library
        return _folder(
            _scoped_identifier(prefix, "playlists"),
            f"Playlists ({len(data.playlists)})",
            [
                _folder(_scoped_identifier(prefix, f"playlist/{pl.id}"), f"{pl.name} ({pl.num_clips})")
                for pl in data.playlists
            ],
        )

    async def _browse_playlist(
        self, runtime: HomeAssistantRuntime, playlist_id: str, ct: str, prefix: str = ""
    ) -> BrowseMediaSource:
        """Show songs in a specific playlist."""
        clips = runtime.suno_library.playlist_clips.get(playlist_id, [])
        name = next((pl.name for pl in runtime.suno_library.playlists if pl.id == playlist_id), "Playlist")
        children = [_clip_to_media(c, ct, prefix) for c in clips]
        return _folder(_scoped_identifier(prefix, f"playlist/{playlist_id}"), f"{name} ({len(children)})", children)

    def _browse_all(self, runtime: HomeAssistantRuntime, ct: str, prefix: str = "") -> BrowseMediaSource:
        """Show all songs, chunked into virtual folders if large."""
        data, total = runtime.suno_library, len(runtime.suno_library.clips)
        if total <= _CHUNK_SIZE:
            return _folder(
                _scoped_identifier(prefix, "all"),
                f"All Songs ({total})",
                [_clip_to_media(c, ct, prefix) for c in data.clips],
            )
        folders = [
            _folder(
                _scoped_identifier(prefix, f"all/page/{i // _CHUNK_SIZE}"),
                f"Songs {i + 1}-{min(i + _CHUNK_SIZE, total)}",
            )
            for i in range(0, total, _CHUNK_SIZE)
        ]
        return _folder(_scoped_identifier(prefix, "all"), f"All Songs ({total})", folders)

    def _browse_all_page(
        self, runtime: HomeAssistantRuntime, page: int, ct: str, prefix: str = ""
    ) -> BrowseMediaSource:
        """Show a chunk of all songs."""
        start, end = page * _CHUNK_SIZE, min((page + 1) * _CHUNK_SIZE, len(runtime.suno_library.clips))
        return _folder(
            _scoped_identifier(prefix, f"all/page/{page}"),
            f"Songs {start + 1}-{end}",
            [_clip_to_media(c, ct, prefix) for c in runtime.suno_library.clips[start:end]],
        )
