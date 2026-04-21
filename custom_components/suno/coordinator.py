"""Data coordinator for Suno integration."""

from __future__ import annotations

import asyncio
import hashlib
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
        self._data_version: int = 0
        self._last_remix_hash: str | None = None
        self._ancestor_task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self.user = SunoUser(
            id=client.user_id or "",
            display_name=client.display_name,
        )

    @property
    def data_version(self) -> int:
        """Monotonic counter incremented on every successful coordinator update.

        Consumers (e.g. the proxy clip cache) use this to invalidate derived
        state without comparing object identity, which is fragile when the
        coordinator returns the same SunoData instance after an in-place
        mutation.
        """
        return self._data_version

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
        # Mark that we have *some* data so _async_update_data takes the
        # fast path on the first refresh instead of blocking on the API.
        self._data_version += 1
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
        """Run the in-memory + API ancestor passes back to back. Test entry-point."""
        self._resolve_root_ancestors_in_memory(data)
        await self._resolve_root_ancestors_api(data)

    def _build_clip_index(self, data: SunoData) -> dict[str, SunoClip]:
        """Build a unified clip_id → clip map across all data buckets."""
        all_clips: dict[str, SunoClip] = {}
        for clip in data.clips:
            all_clips[clip.id] = clip
        for clip in data.liked_clips:
            all_clips[clip.id] = clip
        for clips in data.playlist_clips.values():
            for clip in clips:
                all_clips[clip.id] = clip
        return all_clips

    def _resolve_root_ancestors_in_memory(self, data: SunoData) -> None:
        """Fast pass: resolve every chain we can answer without API calls."""
        all_clips = self._build_clip_index(data)
        resolved: dict[str, str] = {}
        for clip in all_clips.values():
            if clip.root_ancestor_id:
                resolved[clip.id] = clip.root_ancestor_id
                continue
            visited: set[str] = set()
            chain: list[str] = []
            current = clip.id
            while current in all_clips and current not in visited:
                visited.add(current)
                chain.append(current)
                parent_id = all_clips[current].edited_clip_id
                if not parent_id:
                    for cid in chain:
                        resolved[cid] = current
                    break
                if parent_id in resolved:
                    root = resolved[parent_id]
                    for cid in chain:
                        resolved[cid] = root
                    break
                current = parent_id
        for clip in all_clips.values():
            if clip.id in resolved:
                clip.root_ancestor_id = resolved[clip.id]

    def _schedule_ancestor_api_resolution(self, data: SunoData) -> None:
        """Kick off the slow per-clip parent API lookups in the background.

        Returns immediately. If a previous resolution task is still running we
        leave it alone — when it finishes the next coordinator tick will see
        the up-to-date ancestors.
        """
        if self._ancestor_task is not None and not self._ancestor_task.done():
            return
        self._ancestor_task = self.hass.async_create_background_task(
            self._resolve_root_ancestors_api(data),
            f"suno_ancestor_resolution_{self.config_entry.entry_id}",
        )

    async def _resolve_root_ancestors_api(self, data: SunoData) -> None:
        """Slow pass: per-clip parent lookups for orphan remixes (with sleeps)."""
        all_clips = self._build_clip_index(data)
        resolved: dict[str, str] = {
            cid: clip.root_ancestor_id for cid, clip in all_clips.items() if clip.root_ancestor_id
        }
        unresolved = [c for c in all_clips.values() if not c.root_ancestor_id and c.is_remix]
        if not unresolved:
            return

        # Skip the (slow) ancestor API calls if the unresolved remix set is
        # identical to the previous cycle — nothing has changed, the answer
        # would be the same, and we'd just spend ~6s sleeping between calls.
        remix_hash = hashlib.sha1(  # noqa: S324 - non-cryptographic identity hash
            ",".join(sorted(c.id for c in unresolved)).encode()
        ).hexdigest()
        if remix_hash == self._last_remix_hash:
            _LOGGER.debug("Remix set unchanged, skipping ancestor resolution")
            return
        self._last_remix_hash = remix_hash

        api_calls = 0
        parent_cache: dict[str, str] = {}
        for clip in unresolved[:_MAX_PARENT_LOOKUPS_PER_CYCLE]:
            if api_calls > 0:
                await asyncio.sleep(_PARENT_LOOKUP_DELAY)

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
                    for cid in chain:
                        resolved[cid] = current_id
                    break

                visited.add(parent_id)
                chain.append(parent_id)

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
        """Return cached data immediately and refresh in the background.

        The Suno API can take 10–30s to respond on slow networks (4 endpoints
        plus per-playlist fetches). Doing that synchronously inside the
        coordinator update contract trips HA's 10s "update is taking over 10
        seconds" watchdog warning every time a sensor refreshes.

        Strategy: serve the previous tick's data (or empty SunoData on
        first run) and kick off the slow API gather + ancestor resolution as
        a background task. When that task finishes it calls
        ``async_set_updated_data`` so listeners (sensors, the download
        manager) pick up the fresh data.

        First-run behaviour: if there's no cached data yet, we still need
        to block — otherwise sensors would come up "unknown". We detect
        this via ``self.data is None`` and fall back to the synchronous
        path for that one call. Stored library (loaded by
        ``async_load_stored_data``) counts as data, so a normal restart
        never blocks.
        """
        try:
            await self.client.ensure_authenticated()
        except SunoConnectionError as err:
            raise UpdateFailed(f"Cannot reach Suno: {err}") from err
        except SunoAuthError as err:
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="auth_failed") from err

        if self._data_version == 0:
            # Cold start: no successful refresh yet (stored data, if any,
            # is loaded via async_load_stored_data which also bumps the
            # version path is _async_fetch_remote_data only). Block so the
            # first tick produces real data before sensors are added.
            return await self._async_fetch_remote_data()

        # Hot path: schedule a background refresh and return current data.
        self._schedule_remote_refresh()
        return self.data

    def _schedule_remote_refresh(self) -> None:
        """Kick off the slow API gather + reconciliation in the background.

        Idempotent: if a refresh is already in flight we leave it alone.
        Returns immediately so ``_async_update_data`` can complete in <1s.
        """
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_task = self.hass.async_create_background_task(
            self._async_background_refresh(),
            f"suno_refresh_{self.config_entry.entry_id}",
        )

    async def _async_background_refresh(self) -> None:
        """Fetch fresh data from Suno API and publish via async_set_updated_data."""
        try:
            data = await self._async_fetch_remote_data()
        except ConfigEntryAuthFailed as err:
            # Surface auth failures via the standard coordinator path so
            # HA puts the entry into reauth.
            self.async_set_update_error(err)
            return
        except UpdateFailed as err:
            self.async_set_update_error(err)
            return
        except Exception as err:  # noqa: BLE001 - last-resort catch for background task
            _LOGGER.exception("Unexpected error in background refresh")
            self.async_set_update_error(UpdateFailed(str(err)))
            return
        self.async_set_updated_data(data)

    async def _async_fetch_remote_data(self) -> SunoData:
        """Synchronously fetch all Suno data and return a populated SunoData.

        This is the slow path: 4 parallel API calls plus per-playlist clip
        fetches plus in-memory ancestor resolution. Used directly on cold
        start (when there's no cached data yet); otherwise scheduled via
        ``_schedule_remote_refresh``.
        """
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

        # Resolve cheap in-memory lineage chains synchronously so the data we
        # return reflects what we already know. The slow path (per-clip parent
        # API calls with 2s sleeps between them) runs as a background task so
        # individual sensor refreshes don't trip HA's 10s watchdog warning.
        self._resolve_root_ancestors_in_memory(data)
        self._schedule_ancestor_api_resolution(data)

        # Update user identity from Suno API data.
        # suno_display_name (from feed clips) is the authoritative source.
        # Clerk auth username is the login handle, NOT the Suno display name.
        api_display_name = self.client.suno_display_name
        if api_display_name and api_display_name != self.user.display_name:
            _LOGGER.info("Display name changed: '%s' -> '%s'", self.user.display_name, api_display_name)
            self.user = SunoUser(id=self.user.id, display_name=api_display_name)

        # Keep config entry title in sync (may be stale from a previous version)
        if self.user.display_name != self.config_entry.title:
            self.hass.config_entries.async_update_entry(self.config_entry, title=self.user.display_name)

        self.hass.async_create_task(
            self._async_store_save(
                {
                    "clips": [asdict(c) for c in data.clips],
                    "liked_clips": [asdict(c) for c in data.liked_clips],
                    "playlists": [asdict(p) for p in data.playlists],
                    "playlist_clips": {pid: [asdict(c) for c in pclips] for pid, pclips in data.playlist_clips.items()},
                }
            ),
            f"suno_store_save_{self.config_entry.entry_id}",
        )
        self._data_version += 1
        return data

    async def _async_store_save(self, payload: dict[str, Any]) -> None:
        """Persist library to Store, logging any failure instead of raising.

        Wrapped because the call is fire-and-forget via ``async_create_task``;
        without this wrapper an exception would surface as a task-not-awaited
        warning with no useful context.
        """
        try:
            await self._store.async_save(payload)
        except Exception:  # noqa: BLE001 - intentionally broad: best-effort write
            _LOGGER.warning("Failed to persist Suno library to Store", exc_info=True)
