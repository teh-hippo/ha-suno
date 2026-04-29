"""Library refresh policy for the Suno integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .api import SunoClient
from .exceptions import SunoAuthError, SunoConnectionError
from .models import SunoClip, SunoCredits, SunoPlaylist, SunoUser, _safe_clips, _safe_playlists

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1
_MAX_PARENT_LOOKUPS_PER_CYCLE = 10
_UNAVAILABLE_LINEAGE_RETRY_INTERVAL = 6

LINEAGE_RESOLVED = "resolved"
LINEAGE_EXTERNAL = "external"
LINEAGE_UNAVAILABLE = "unavailable"
LINEAGE_PENDING = "pending"

type TaskFactory = Callable[[Coroutine[Any, Any, None], str], asyncio.Task[None]]
type SnapshotCallback = Callable[["LibrarySnapshot"], None]
type ErrorCallback = Callable[[BaseException], None]


@dataclass
class SunoData:
    clips: list[SunoClip] = field(default_factory=list)
    liked_clips: list[SunoClip] = field(default_factory=list)
    playlists: list[SunoPlaylist] = field(default_factory=list)
    playlist_clips: dict[str, list[SunoClip]] = field(default_factory=dict)
    credits: SunoCredits | None = None
    stale_sections: tuple[str, ...] = ()
    hidden_pending_remix_count: int = 0
    unavailable_lineage_count: int = 0


@dataclass(frozen=True, slots=True)
class ParentLookup:
    """Parent lookup result for one clip lineage hop."""

    parent_id: str | None


@dataclass(frozen=True, slots=True)
class LibrarySnapshot:
    """Published Library Refresh state."""

    data: SunoData
    identity: SunoUser
    version: int


class SunoLibraryAdapter(Protocol):
    """Narrow Suno adapter needed by Library Refresh."""

    @property
    def user_id(self) -> str | None: ...

    @property
    def display_name(self) -> str: ...

    @property
    def suno_identity(self) -> str | None: ...

    async def ensure_authenticated(self) -> None: ...

    async def get_all_songs(self) -> list[SunoClip]: ...

    async def get_liked_songs(self) -> list[SunoClip]: ...

    async def get_playlists(self) -> list[SunoPlaylist]: ...

    async def get_playlist_clips(self, playlist_id: str) -> list[SunoClip]: ...

    async def get_credits(self) -> SunoCredits: ...

    async def get_clip_parent(self, clip_id: str) -> ParentLookup: ...


class StoredLibraryAdapter(Protocol):
    """Persistence adapter for Stored Library snapshots."""

    async def async_load(self) -> SunoData | None: ...

    async def async_save(self, data: SunoData) -> None: ...


class SunoClientLibraryAdapter:
    """Production Suno library adapter backed by the existing Suno client."""

    def __init__(self, client: SunoClient) -> None:
        self._client = client

    @property
    def user_id(self) -> str | None:
        return self._client.user_id

    @property
    def display_name(self) -> str:
        return self._client.display_name

    @property
    def suno_identity(self) -> str | None:
        return self._client.suno_display_name

    async def ensure_authenticated(self) -> None:
        await self._client.ensure_authenticated()

    async def get_all_songs(self) -> list[SunoClip]:
        return await self._client.get_all_songs()

    async def get_liked_songs(self) -> list[SunoClip]:
        return await self._client.get_liked_songs()

    async def get_playlists(self) -> list[SunoPlaylist]:
        return await self._client.get_playlists()

    async def get_playlist_clips(self, playlist_id: str) -> list[SunoClip]:
        return await self._client.get_playlist_clips(playlist_id)

    async def get_credits(self) -> SunoCredits:
        return await self._client.get_credits()

    async def get_clip_parent(self, clip_id: str) -> ParentLookup:
        data = await self._client.get_clip_parent_raw(clip_id)
        parent_id = data.get("id") if data else None
        return ParentLookup(str(parent_id) if parent_id else None)


class HomeAssistantStoredLibrary:
    """Stored Library adapter backed by Home Assistant Store."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.store: Store[dict[str, Any]] = Store(hass, _STORE_VERSION, f"suno_library_{entry_id}")

    async def async_load(self) -> SunoData | None:
        if not (saved := await self.store.async_load()) or not isinstance(saved, dict):
            return None
        try:
            return _data_from_payload(saved)
        except Exception:
            _LOGGER.warning("Stored library corrupt, ignoring")
            return None

    async def async_save(self, data: SunoData) -> None:
        await self.store.async_save(_payload_from_data(data))


class InMemoryStoredLibrary:
    """In-memory Stored Library adapter for tests."""

    def __init__(self, data: SunoData | None = None) -> None:
        self.data = data

    async def async_load(self) -> SunoData | None:
        return self.data

    async def async_save(self, data: SunoData) -> None:
        self.data = data


class LibraryRefresh:
    """Deep module that owns Suno Library refresh and reconciliation policy."""

    def __init__(
        self,
        source: SunoLibraryAdapter,
        storage: StoredLibraryAdapter,
        *,
        task_factory: TaskFactory | None = None,
        on_update: SnapshotCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        self._source = source
        self._storage = storage
        self._task_factory = task_factory or _default_task_factory
        self._on_update = on_update
        self._on_error = on_error
        self._current = SunoData()
        self._identity = SunoUser(id=source.user_id or "", display_name=source.display_name)
        self._data_version = 0
        self._refresh_task: asyncio.Task[None] | None = None
        self._lineage_cycle = 0

    @property
    def current_data(self) -> SunoData:
        return self._current

    @current_data.setter
    def current_data(self, data: SunoData) -> None:
        self._current = data

    @property
    def identity(self) -> SunoUser:
        return self._identity

    @property
    def data_version(self) -> int:
        return self._data_version

    @data_version.setter
    def data_version(self, version: int) -> None:
        self._data_version = version

    @property
    def refresh_task(self) -> asyncio.Task[None] | None:
        return self._refresh_task

    async def async_load_stored_library(self) -> SunoData | None:
        """Load the Stored Library and publish it as the current snapshot."""
        data = await self._storage.async_load()
        if data is None:
            return None
        self._current = data
        self._data_version += 1
        _LOGGER.info("Loaded stored library: %d clips", len(data.clips))
        return data

    async def async_update(self) -> SunoData:
        """Return current data immediately and schedule remote reconciliation."""
        if self._data_version == 0:
            self._data_version = 1
        self._schedule_remote_refresh()
        return self._current

    async def async_refresh_once(self) -> LibrarySnapshot:
        """Fetch and reconcile one remote snapshot."""
        data = await self._async_fetch_remote_data()
        self._current = data
        if identity := self._source.suno_identity:
            if identity != self._identity.display_name:
                self._identity = SunoUser(id=self._identity.id, display_name=identity)
        self._data_version += 1
        await self._async_store_save(data)
        return self.snapshot

    @property
    def snapshot(self) -> LibrarySnapshot:
        return LibrarySnapshot(data=self._current, identity=self._identity, version=self._data_version)

    def _schedule_remote_refresh(self) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_task = self._task_factory(self._async_background_refresh(), "suno_library_refresh")

    async def _async_background_refresh(self) -> None:
        try:
            snapshot = await self.async_refresh_once()
        except SunoAuthError as err:
            if self._on_error is not None:
                self._on_error(err)
            return
        except Exception as err:
            _LOGGER.exception("Unexpected error in background refresh")
            if self._on_error is not None:
                self._on_error(err)
            return
        if self._on_update is not None:
            self._on_update(snapshot)

    async def _async_fetch_remote_data(self) -> SunoData:
        previous = self._current
        stale_sections: set[str] = set()

        try:
            await self._source.ensure_authenticated()
        except SunoAuthError:
            raise
        except SunoConnectionError as err:
            _LOGGER.warning("Cannot reach Suno during Library Refresh, preserving previous library: %s", err)
            stale_sections.update(("clips", "liked_clips", "playlists", "playlist_clips", "credits"))
            return _with_stale_sections(previous, stale_sections)

        clips_result, liked_result, playlists_result, credits_result = await asyncio.gather(
            self._source.get_all_songs(),
            self._source.get_liked_songs(),
            self._source.get_playlists(),
            self._source.get_credits(),
            return_exceptions=True,
        )

        clips = self._section_or_previous("clips", clips_result, previous.clips, stale_sections)
        liked_clips = self._section_or_previous("liked_clips", liked_result, previous.liked_clips, stale_sections)
        playlists = self._section_or_previous("playlists", playlists_result, previous.playlists, stale_sections)
        credits = self._section_or_previous("credits", credits_result, previous.credits, stale_sections)

        playlist_clips = await self._fetch_playlist_clips(playlists, previous, stale_sections)
        data = SunoData(
            clips=clips,
            liked_clips=liked_clips,
            playlists=playlists,
            playlist_clips=playlist_clips,
            credits=credits,
            stale_sections=tuple(sorted(stale_sections)),
        )
        return await self._apply_lineage(data, previous)

    def _section_or_previous[T](
        self,
        section: str,
        result: T | BaseException,
        previous: T,
        stale_sections: set[str],
    ) -> T:
        if isinstance(result, SunoAuthError):
            raise result
        if isinstance(result, BaseException):
            _LOGGER.warning("Could not fetch %s during Library Refresh", section, exc_info=result)
            stale_sections.add(section)
            return previous
        return result

    async def _fetch_playlist_clips(
        self,
        playlists: list[SunoPlaylist],
        previous: SunoData,
        stale_sections: set[str],
    ) -> dict[str, list[SunoClip]]:
        if not playlists:
            return {}

        sem = asyncio.Semaphore(3)

        async def _fetch_playlist(pl: SunoPlaylist) -> tuple[str, list[SunoClip]]:
            async with sem:
                return pl.id, await self._source.get_playlist_clips(pl.id)

        results = await asyncio.gather(*[_fetch_playlist(pl) for pl in playlists], return_exceptions=True)
        playlist_clips: dict[str, list[SunoClip]] = {}
        for playlist, result in zip(playlists, results, strict=True):
            if isinstance(result, SunoAuthError):
                raise result
            if isinstance(result, BaseException):
                stale_sections.add(f"playlist_clips:{playlist.id}")
                if playlist.id in previous.playlist_clips:
                    playlist_clips[playlist.id] = previous.playlist_clips[playlist.id]
                else:
                    _LOGGER.warning("Failed to fetch playlist clips for %s: %s", playlist.id, result)
                continue
            pl_id, clips = result
            playlist_clips[pl_id] = clips
        return playlist_clips

    async def _apply_lineage(self, data: SunoData, previous: SunoData) -> SunoData:
        self._lineage_cycle += 1
        self._resolve_root_ancestors_in_memory(data)
        pending_ids = await self._resolve_root_ancestors_api(data, previous)
        self._apply_album_details(data)
        return _filter_pending_remixes(data, previous, pending_ids)

    def _resolve_root_ancestors_in_memory(self, data: SunoData) -> None:
        all_clips = _build_clip_index(data)
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
                    if all_clips[current].is_remix:
                        break
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

    async def _resolve_root_ancestors_api(self, data: SunoData, previous: SunoData) -> set[str]:
        all_clips = _build_clip_index(data)
        previous_clips = _build_clip_index(previous)
        pending_ids: set[str] = set()
        unresolved = [clip for clip in all_clips.values() if clip.is_remix and not clip.root_ancestor_id]
        api_calls = 0
        parent_cache: dict[str, ParentLookup] = {}

        for clip in unresolved:
            previous_clip = previous_clips.get(clip.id)
            if (
                previous_clip is not None
                and previous_clip.lineage_status == LINEAGE_UNAVAILABLE
                and self._lineage_cycle % _UNAVAILABLE_LINEAGE_RETRY_INTERVAL != 0
            ):
                _copy_lineage(previous_clip, clip)
                continue
            if api_calls >= _MAX_PARENT_LOOKUPS_PER_CYCLE:
                clip.lineage_status = LINEAGE_PENDING
                pending_ids.add(clip.id)
                continue

            current_id = clip.id
            chain = [clip.id]
            visited = {clip.id}
            resolved = False

            while api_calls < _MAX_PARENT_LOOKUPS_PER_CYCLE:
                if current_id in parent_cache:
                    lookup = parent_cache[current_id]
                else:
                    try:
                        lookup = await self._source.get_clip_parent(current_id)
                    except SunoAuthError:
                        raise
                    except Exception as err:
                        _LOGGER.debug("Clip Lineage lookup pending for %s: %s", current_id, err)
                        clip.lineage_status = LINEAGE_PENDING
                        pending_ids.add(clip.id)
                        break
                    parent_cache[current_id] = lookup
                    api_calls += 1

                parent_id = lookup.parent_id
                if not parent_id:
                    if current_id == clip.id:
                        _mark_chain_unavailable(chain, all_clips)
                    else:
                        _mark_chain_external(chain, current_id, all_clips)
                    resolved = True
                    break

                if parent_id in visited:
                    _mark_chain_unavailable(chain, all_clips)
                    resolved = True
                    break

                visited.add(parent_id)
                chain.append(parent_id)

                if parent_id in all_clips:
                    parent_clip = all_clips[parent_id]
                    if parent_clip.root_ancestor_id:
                        _mark_chain_resolved(chain, parent_clip.root_ancestor_id, all_clips)
                        resolved = True
                        break
                    if not parent_clip.edited_clip_id:
                        _mark_chain_resolved(chain, parent_id, all_clips)
                        resolved = True
                        break
                    current_id = parent_clip.edited_clip_id
                else:
                    current_id = parent_id

            if not resolved and clip.lineage_status != LINEAGE_PENDING:
                clip.lineage_status = LINEAGE_PENDING
                pending_ids.add(clip.id)

        return pending_ids

    def _apply_album_details(self, data: SunoData) -> None:
        all_clips = _build_clip_index(data)
        unavailable_count = 0
        for clip in all_clips.values():
            if not clip.is_remix:
                continue
            if clip.lineage_status == LINEAGE_UNAVAILABLE:
                unavailable_count += 1
                clip.album_title = "Remixes of unknown root"
                continue
            root_id = clip.root_ancestor_id
            if not root_id or root_id == clip.id:
                clip.lineage_status = LINEAGE_PENDING
                continue
            if root_clip := all_clips.get(root_id):
                clip.lineage_status = LINEAGE_RESOLVED
                clip.album_title = root_clip.title
            else:
                clip.lineage_status = LINEAGE_EXTERNAL
                clip.album_title = f"Remixes of {root_id[:8]}"
        data.unavailable_lineage_count = unavailable_count

    async def _async_store_save(self, data: SunoData) -> None:
        try:
            await self._storage.async_save(data)
        except Exception:
            _LOGGER.warning("Failed to persist Suno library to Store", exc_info=True)


def _default_task_factory(coro: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
    return asyncio.create_task(coro, name=name)


def _data_from_payload(saved: dict[str, Any]) -> SunoData:
    return SunoData(
        clips=_safe_clips(saved.get("clips", [])),
        liked_clips=_safe_clips(saved.get("liked_clips", [])),
        playlists=_safe_playlists(saved.get("playlists", [])),
        playlist_clips={pid: _safe_clips(pclips) for pid, pclips in saved.get("playlist_clips", {}).items()},
    )


def _payload_from_data(data: SunoData) -> dict[str, Any]:
    return {
        "clips": [asdict(c) for c in data.clips],
        "liked_clips": [asdict(c) for c in data.liked_clips],
        "playlists": [asdict(p) for p in data.playlists],
        "playlist_clips": {pid: [asdict(c) for c in pclips] for pid, pclips in data.playlist_clips.items()},
    }


def _with_stale_sections(data: SunoData, stale_sections: set[str]) -> SunoData:
    return SunoData(
        clips=data.clips,
        liked_clips=data.liked_clips,
        playlists=data.playlists,
        playlist_clips=data.playlist_clips,
        credits=data.credits,
        stale_sections=tuple(sorted(stale_sections)),
        hidden_pending_remix_count=data.hidden_pending_remix_count,
        unavailable_lineage_count=data.unavailable_lineage_count,
    )


def _build_clip_index(data: SunoData) -> dict[str, SunoClip]:
    all_clips: dict[str, SunoClip] = {}
    for clip in data.clips:
        all_clips[clip.id] = clip
    for clip in data.liked_clips:
        all_clips[clip.id] = clip
    for clips in data.playlist_clips.values():
        for clip in clips:
            all_clips[clip.id] = clip
    return all_clips


def _mark_chain_resolved(chain: list[str], root_id: str, all_clips: dict[str, SunoClip]) -> None:
    for cid in chain:
        if clip := all_clips.get(cid):
            clip.root_ancestor_id = root_id
            clip.lineage_status = LINEAGE_RESOLVED


def _mark_chain_external(chain: list[str], root_id: str, all_clips: dict[str, SunoClip]) -> None:
    for cid in chain:
        if clip := all_clips.get(cid):
            clip.root_ancestor_id = root_id
            clip.lineage_status = LINEAGE_EXTERNAL
            clip.album_title = f"Remixes of {root_id[:8]}"


def _mark_chain_unavailable(chain: list[str], all_clips: dict[str, SunoClip]) -> None:
    for cid in chain:
        if clip := all_clips.get(cid):
            clip.root_ancestor_id = ""
            clip.lineage_status = LINEAGE_UNAVAILABLE
            clip.album_title = "Remixes of unknown root"


def _copy_lineage(source: SunoClip, target: SunoClip) -> None:
    target.root_ancestor_id = source.root_ancestor_id
    target.lineage_status = source.lineage_status
    target.album_title = source.album_title


def _filter_pending_remixes(data: SunoData, previous: SunoData, pending_ids: set[str]) -> SunoData:
    if not pending_ids:
        return data
    previous_clips = _build_clip_index(previous)
    hidden_ids: set[str] = set()

    def _clip_or_previous(clip: SunoClip) -> SunoClip | None:
        if clip.id not in pending_ids:
            return clip
        previous_clip = previous_clips.get(clip.id)
        if previous_clip is not None and previous_clip.lineage_status != LINEAGE_PENDING:
            return previous_clip
        hidden_ids.add(clip.id)
        return None

    def _filter(clips: list[SunoClip]) -> list[SunoClip]:
        return [resolved for clip in clips if (resolved := _clip_or_previous(clip)) is not None]

    return SunoData(
        clips=_filter(data.clips),
        liked_clips=_filter(data.liked_clips),
        playlists=data.playlists,
        playlist_clips={pid: _filter(clips) for pid, clips in data.playlist_clips.items()},
        credits=data.credits,
        stale_sections=data.stale_sections,
        hidden_pending_remix_count=len(hidden_ids),
        unavailable_lineage_count=data.unavailable_lineage_count,
    )


__all__ = [
    "HomeAssistantStoredLibrary",
    "InMemoryStoredLibrary",
    "LibraryRefresh",
    "LibrarySnapshot",
    "ParentLookup",
    "StoredLibraryAdapter",
    "SunoClientLibraryAdapter",
    "SunoData",
    "SunoLibraryAdapter",
    "_MAX_PARENT_LOOKUPS_PER_CYCLE",
]
