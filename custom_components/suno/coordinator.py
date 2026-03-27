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
from .models import SunoClip, SunoCredits, SunoPlaylist, SunoUser, _safe_clips, _safe_playlists

if TYPE_CHECKING:
    from .cache import SunoCache
    from .download import SunoDownloadManager

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1
_MAX_PARENT_LOOKUPS_PER_CYCLE = 10
_PARENT_LOOKUP_DELAY = 2.0


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
                clips=_safe_clips(saved.get("clips", [])),
                liked_clips=_safe_clips(saved.get("liked_clips", [])),
                playlists=_safe_playlists(saved.get("playlists", [])),
                playlist_clips={pid: _safe_clips(pclips) for pid, pclips in saved.get("playlist_clips", {}).items()},
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

    async def _resolve_root_ancestors(self, data: SunoData) -> None:
        """Resolve root ancestor IDs for clips via lineage chains."""
        # Build unified clip index
        all_clips: dict[str, SunoClip] = {}
        for clip in data.clips:
            all_clips[clip.id] = clip
        for clip in data.liked_clips:
            all_clips[clip.id] = clip
        for clips in data.playlist_clips.values():
            for clip in clips:
                all_clips[clip.id] = clip

        # Phase 1: In-memory chain resolution
        resolved: dict[str, str] = {}  # clip_id -> root_ancestor_id
        for clip in all_clips.values():
            if clip.root_ancestor_id:
                resolved[clip.id] = clip.root_ancestor_id
                continue
            # Trace chain
            visited: set[str] = set()
            chain: list[str] = []
            current = clip.id
            while current in all_clips and current not in visited:
                visited.add(current)
                chain.append(current)
                parent_id = all_clips[current].edited_clip_id
                if not parent_id:
                    # Found root
                    for cid in chain:
                        resolved[cid] = current
                    break
                if parent_id in resolved:
                    # Connect to already-resolved chain
                    root = resolved[parent_id]
                    for cid in chain:
                        resolved[cid] = root
                    break
                current = parent_id
            # If chain broke (current not in index), leave unresolved

        # Apply Phase 1 results
        for clip in all_clips.values():
            if clip.id in resolved:
                clip.root_ancestor_id = resolved[clip.id]

        # Phase 2: API resolution for orphan remixes
        unresolved = [c for c in all_clips.values() if not c.root_ancestor_id and c.is_remix]
        if not unresolved:
            return

        api_calls = 0
        parent_cache: dict[str, str] = {}
        for clip in unresolved[:_MAX_PARENT_LOOKUPS_PER_CYCLE]:
            if api_calls > 0:
                await asyncio.sleep(_PARENT_LOOKUP_DELAY)

            # Trace via API
            current_id = clip.id
            chain = [current_id]
            visited = {current_id}

            while api_calls < _MAX_PARENT_LOOKUPS_PER_CYCLE:
                if current_id in resolved:
                    root = resolved[current_id]
                    for cid in chain:
                        resolved[cid] = root
                    break
                if current_id in parent_cache:
                    parent_id = parent_cache[current_id]
                else:
                    parent_data = await self.client.get_clip_parent(current_id)
                    api_calls += 1
                    parent_id = parent_data.get("id", "") if parent_data else ""
                    parent_cache[current_id] = parent_id

                if not parent_id or parent_id in visited:
                    # Root found or circular
                    for cid in chain:
                        resolved[cid] = current_id
                    break

                visited.add(parent_id)
                chain.append(parent_id)

                # Check if parent is in our index
                if parent_id in all_clips:
                    parent_clip = all_clips[parent_id]
                    if parent_clip.root_ancestor_id:
                        root = parent_clip.root_ancestor_id
                        for cid in chain:
                            resolved[cid] = root
                        break
                    if not parent_clip.edited_clip_id:
                        for cid in chain:
                            resolved[cid] = parent_id
                        break
                    current_id = parent_clip.edited_clip_id
                else:
                    current_id = parent_id

            # Apply results for this clip
            if clip.id in resolved:
                clip.root_ancestor_id = resolved[clip.id]

        remaining = sum(1 for c in all_clips.values() if not c.root_ancestor_id and c.is_remix)
        if api_calls or remaining:
            _LOGGER.info(
                "Root ancestor resolution: %d resolved, %d API calls, %d remaining",
                sum(1 for c in all_clips.values() if c.root_ancestor_id),
                api_calls,
                remaining,
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
        await self._resolve_root_ancestors(data)

        # Update user identity.
        # Clerk auth is authoritative (updates immediately on profile rename).
        # Feed clip display_name is stale (baked at clip creation time).
        auth_name = self.client.display_name
        api_name = self.client.suno_display_name

        if auth_name and auth_name != "Suno":
            authoritative_name = auth_name
        elif api_name:
            authoritative_name = api_name
        else:
            authoritative_name = None

        if authoritative_name and authoritative_name != self.user.display_name:
            _LOGGER.info("Display name changed: '%s' -> '%s'", self.user.display_name, authoritative_name)
            self.user = SunoUser(id=self.user.id, display_name=authoritative_name)
            if authoritative_name != self.config_entry.title:
                self.hass.config_entries.async_update_entry(self.config_entry, title=authoritative_name)

        # Correct stale display_name on clips from the API.
        # After a profile rename, the feed still returns the old name on clips.
        # Override so the download manager sees the current name and triggers renames.
        if api_name and api_name != self.user.display_name:
            count = 0
            for clip in data.clips:
                if clip.display_name == api_name:
                    clip.display_name = self.user.display_name
                    count += 1
            for clip in data.liked_clips:
                if clip.display_name == api_name:
                    clip.display_name = self.user.display_name
                    count += 1
            if count:
                _LOGGER.info(
                    "Corrected stale display_name on %d clips: '%s' -> '%s'",
                    count,
                    api_name,
                    self.user.display_name,
                )

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
