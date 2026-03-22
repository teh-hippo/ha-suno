"""Background FLAC sync for the Suno integration.

Downloads FLAC files from Suno into a local directory, organised by date,
playlist, or flat.  Tracks sync state via HA Store and writes a co-located
manifest for external tools.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .audio import ensure_wav_url, fetch_album_art, wav_to_flac
from .const import (
    CONF_SYNC_ALL_PLAYLISTS,
    CONF_SYNC_ENABLED,
    CONF_SYNC_LIKED,
    CONF_SYNC_PATH,
    CONF_SYNC_PLAYLISTS,
    CONF_SYNC_RECENT_COUNT,
    CONF_SYNC_RECENT_DAYS,
    CONF_SYNC_TRASH_DAYS,
    DEFAULT_SYNC_ALL_PLAYLISTS,
    DEFAULT_SYNC_ENABLED,
    DEFAULT_SYNC_LIKED,
    DEFAULT_SYNC_RECENT_COUNT,
    DEFAULT_SYNC_RECENT_DAYS,
    DEFAULT_SYNC_TRASH_DAYS,
    DOMAIN,
    SYNC_DOWNLOAD_DELAY,
    SYNC_MAX_DOWNLOADS_BOOTSTRAP,
    SYNC_MAX_DOWNLOADS_PER_RUN,
)
from .models import SunoClip, clip_meta_hash

if TYPE_CHECKING:
    from .api import SunoClient
    from .cache import SunoCache
    from .coordinator import SunoCoordinator, SunoData

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
_SERVICE_SYNC = "sync_media"
_MANIFEST_FILENAME = ".suno_sync.json"
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitise_filename(name: str, max_len: int = 200) -> str:
    """Make a string safe for use as a filename."""
    safe = _UNSAFE_CHARS.sub("_", name).strip(". ")
    return safe[:max_len] if safe else "untitled"


def _clip_path(clip: SunoClip, index: int) -> str:
    """Build the relative file path for a clip (date organisation)."""
    title = _sanitise_filename(clip.title or "untitled")
    num = f"{index + 1:02d}"
    date_str = clip.created_at[:10] if clip.created_at else "unknown"
    return f"{date_str}/{num} - {title}.flac"


# ── Module-level file operations ────────────────────────────────────


def _cleanup_empty_dirs(base: Path, target: Path) -> None:
    """Remove empty parent directories between *target* and *base*."""
    parent = target.parent
    while parent != base:
        try:
            parent.rmdir()
            parent = parent.parent
        except OSError:
            break


async def _write_file(hass: HomeAssistant, target: Path, data: bytes) -> None:
    """Atomically write data to target path."""

    def _write(t: Path, d: bytes) -> None:
        t.parent.mkdir(parents=True, exist_ok=True)
        tmp = t.with_suffix(".tmp")
        try:
            tmp.write_bytes(d)
            os.replace(str(tmp), str(t))
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    await hass.async_add_executor_job(_write, target, data)


async def _delete_file(hass: HomeAssistant, base: Path, rel_path: str) -> None:
    """Delete a synced file and remove empty parent directories."""

    def _delete(b: Path, r: str) -> None:
        target = b / r
        try:
            if target.exists():
                target.unlink()
                _LOGGER.info("Removed: %s", r)
                _cleanup_empty_dirs(b, target)
        except OSError:
            _LOGGER.warning("Failed to delete: %s", r)

    await hass.async_add_executor_job(_delete, base, rel_path)


# ── Module-level trash management ───────────────────────────────────


async def _trash_file(
    hass: HomeAssistant,
    base: Path,
    clip_id: str,
    entry: dict[str, Any],
    trash_state: dict[str, Any],
) -> None:
    """Move a file to .trash/ instead of deleting. Modifies *trash_state* in place."""
    rel_path = entry.get("path", "")

    def _move_to_trash(b: Path, r: str) -> str | None:
        source = b / r
        if not source.exists():
            return None
        trash_dir = b / ".trash"
        trash_dir.mkdir(parents=True, exist_ok=True)
        dest = trash_dir / source.name
        try:
            os.replace(str(source), str(dest))
            _LOGGER.info("Trashed: %s", r)
            _cleanup_empty_dirs(b, source)
            return f".trash/{source.name}"
        except OSError:
            _LOGGER.warning("Failed to trash: %s", r)
            return None

    trash_path = await hass.async_add_executor_job(_move_to_trash, base, rel_path)
    if trash_path:
        trash_state[clip_id] = {
            "path": trash_path,
            "original_path": rel_path,
            "trashed_at": datetime.now(tz=UTC).isoformat(),
        }


async def _restore_from_trash(
    hass: HomeAssistant,
    base: Path,
    clip_id: str,
    trash_state: dict[str, Any],
) -> dict[str, Any] | None:
    """Restore a file from .trash/ if it exists. Returns clip state entry or None."""
    trash_entry = trash_state.get(clip_id)
    if not trash_entry:
        return None

    trash_path = trash_entry.get("path", "")
    original_path = trash_entry.get("original_path", "")

    def _restore(b: Path, tp: str, op: str) -> bool | None:
        source = b / tp
        if not source.exists():
            return None
        dest = b / op
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(str(source), str(dest))
            _LOGGER.info("Restored from trash: %s", op)
            return True
        except OSError:
            _LOGGER.warning("Failed to restore: %s", tp)
            return None

    restored = await hass.async_add_executor_job(_restore, base, trash_path, original_path)
    if restored:
        trash_state.pop(clip_id, None)
        return {
            "path": original_path,
            "title": trash_entry.get("title", ""),
            "created": trash_entry.get("created"),
            "sources": [],
        }
    return None


async def _purge_trash(
    hass: HomeAssistant,
    base: Path,
    trash_state: dict[str, Any],
    max_days: int,
) -> None:
    """Delete trash files older than *max_days*. Modifies *trash_state* in place."""
    now = datetime.now(tz=UTC)
    to_purge: list[str] = []

    for clip_id, entry in trash_state.items():
        trashed_at = entry.get("trashed_at", "")
        try:
            trashed = datetime.fromisoformat(trashed_at)
            age_days = (now - trashed).total_seconds() / 86400
            if age_days >= max_days:
                to_purge.append(clip_id)
        except ValueError, TypeError:
            to_purge.append(clip_id)

    for clip_id in to_purge:
        entry = trash_state.pop(clip_id)
        path = entry.get("path", "")
        if path:
            await _delete_file(hass, base, path)
        _LOGGER.debug("Purged from trash: %s", clip_id)


class SunoSync:
    """Manages background FLAC sync to a local directory."""

    def __init__(
        self,
        hass: HomeAssistant,
        store_key: str,
    ) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, store_key)
        self._state: dict[str, Any] = {"clips": {}, "last_sync": None}
        self._cache: SunoCache | None = None
        self._sync_path: str = ""
        self._running = False
        self._errors = 0
        self._pending = 0

    async def async_init(self) -> None:
        """Load persisted sync state."""
        data = await self._store.async_load()
        if data and isinstance(data, dict):
            self._state = data

    @classmethod
    async def async_setup(
        cls,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: SunoCoordinator,
        client: SunoClient,
    ) -> SunoSync:
        """Create, initialise, and wire up sync."""
        sync = cls(hass, f"suno_sync_{entry.entry_id}")
        sync._cache = coordinator.cache
        sync._sync_path = entry.options.get(CONF_SYNC_PATH, "")
        await sync.async_init()

        sync_path = entry.options.get(CONF_SYNC_PATH, "")
        if sync_path:
            await sync.cleanup_tmp_files(sync_path)

        # Trigger sync on coordinator refresh
        def _on_coordinator_update() -> None:
            if sync.is_running:
                return
            hass.async_create_task(
                sync.async_sync(dict(entry.options), client, coordinator_data=coordinator.data),
                f"suno_sync_refresh_{entry.entry_id}",
            )

        entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))

        # Register sync service
        async def _handle_sync_service(call: ServiceCall) -> None:
            force = call.data.get("force", False)
            await sync.async_sync(dict(entry.options), client, force=force)

        if not hass.services.has_service(DOMAIN, _SERVICE_SYNC):
            hass.services.async_register(DOMAIN, _SERVICE_SYNC, _handle_sync_service)
            entry.async_on_unload(lambda: hass.services.async_remove(DOMAIN, _SERVICE_SYNC))

        # Delayed initial sync (60s after startup)
        async def _initial_sync() -> None:
            await asyncio.sleep(60)
            await sync.async_sync(dict(entry.options), client)

        entry.async_create_background_task(hass, _initial_sync(), f"suno_sync_init_{entry.entry_id}")

        return sync

    # ── Public status ───────────────────────────────────────────────

    @property
    def last_sync(self) -> str | None:
        """ISO timestamp of last completed sync."""
        return self._state.get("last_sync")

    @property
    def total_files(self) -> int:
        """Number of synced files."""
        return len(self._state.get("clips", {}))

    @property
    def pending(self) -> int:
        """Number of clips pending download in current/last run."""
        return self._pending

    @property
    def errors(self) -> int:
        """Number of errors in current/last run."""
        return self._errors

    @property
    def library_size_mb(self) -> float:
        """Total size of synced files in MB."""
        clips: dict[str, Any] = self._state.get("clips", {})
        total: int = sum(int(entry.get("size", 0)) for entry in clips.values())
        return round(total / 1048576, 1)

    @property
    def is_running(self) -> bool:
        """Whether a sync is currently in progress."""
        return self._running

    def get_synced_path(self, clip_id: str, meta_hash: str = "") -> Path | None:
        """Return the absolute path to a synced FLAC if it exists and is fresh.

        Returns None if the clip isn't synced, the file is missing on disk,
        or the metadata hash indicates stale embedded tags.
        """
        if not self._sync_path:
            return None
        entry = self._state.get("clips", {}).get(clip_id)
        if entry is None:
            return None
        if meta_hash and entry.get("meta_hash") and entry["meta_hash"] != meta_hash:
            return None
        path = Path(self._sync_path) / str(entry["path"])
        if not path.is_file():
            return None
        return path

    # ── Main sync entry point ───────────────────────────────────────

    async def async_sync(
        self,
        options: dict[str, Any],
        client: Any,
        force: bool = False,
        coordinator_data: SunoData | None = None,
    ) -> None:
        """Run a full sync cycle."""
        if self._running:
            _LOGGER.debug("Sync already running, skipping")
            return

        if not options.get(CONF_SYNC_ENABLED, DEFAULT_SYNC_ENABLED):
            return

        sync_path = options.get(CONF_SYNC_PATH)
        if not sync_path:
            _LOGGER.warning("Sync enabled but no sync_path configured")
            return

        self._running = True
        self._errors = 0
        self._pending = 0

        try:
            await self._run_sync(options, client, sync_path, force, coordinator_data)
        except asyncio.CancelledError:
            _LOGGER.info("Sync cancelled")
            raise
        except Exception:
            _LOGGER.exception("Sync failed")
            self._errors += 1
        finally:
            self._running = False

    async def _run_sync(
        self,
        options: dict[str, Any],
        client: Any,
        sync_path: str,
        force: bool,
        coordinator_data: SunoData | None = None,
    ) -> None:
        """Core sync logic: build desired state, diff, download, cleanup."""
        trash_days = options.get(CONF_SYNC_TRASH_DAYS, DEFAULT_SYNC_TRASH_DAYS)
        base = Path(sync_path)

        # Work on a single copy of trash state throughout
        trash_state: dict[str, Any] = dict(self._state.get("trash", {}))
        if trash_days:
            await _purge_trash(self.hass, base, trash_state, int(trash_days))

        # Build desired clips with sources (preserved_ids protects against transient API failures)
        desired, preserved_ids = await self._build_desired(options, client, coordinator_data)

        clips_state: dict[str, Any] = dict(self._state.get("clips", {}))

        # Determine what to download and what to delete
        to_download: list[tuple[SunoClip, int]] = []
        to_delete: list[str] = []
        seen_ids: set[str] = set()

        for clip, index, _collection, sources in desired:
            seen_ids.add(clip.id)
            if clip.id not in clips_state or force:
                # Check if restorable from trash
                if clip.id in trash_state:
                    restored = await _restore_from_trash(self.hass, base, clip.id, trash_state)
                    if restored:
                        clips_state[clip.id] = restored
                        clips_state[clip.id]["sources"] = sources
                        continue
                to_download.append((clip, index))
            if clip.id in clips_state:
                clips_state[clip.id]["sources"] = sources

        for clip_id in list(clips_state.keys()):
            if clip_id not in seen_ids and clip_id not in preserved_ids:
                to_delete.append(clip_id)

        self._pending = len(to_download)
        _LOGGER.info(
            "Sync: %d to download, %d to remove, %d current",
            len(to_download),
            len(to_delete),
            len(seen_ids),
        )

        # Ensure sync directory exists
        try:
            await self.hass.async_add_executor_job(base.mkdir, 0o755, True, True)
        except OSError:
            _LOGGER.error("Cannot create sync directory: %s", sync_path)
            self._errors += 1
            return

        # Bootstrap mode: higher cap for initial sync
        is_bootstrap = self.total_files == 0 and len(to_download) > SYNC_MAX_DOWNLOADS_PER_RUN
        max_downloads = SYNC_MAX_DOWNLOADS_BOOTSTRAP if is_bootstrap else SYNC_MAX_DOWNLOADS_PER_RUN
        if is_bootstrap:
            _LOGGER.info("Bootstrap mode: downloading up to %d files", max_downloads)

        # Download new clips (rate limited)
        downloaded = 0
        for clip, index in to_download:
            if downloaded >= max_downloads:
                _LOGGER.info("Reached max downloads (%d), continuing next sync", max_downloads)
                break

            rel_path = _clip_path(clip, index)
            file_size = await self._download_clip(client, clip, base, rel_path)
            if file_size is not None:
                clips_state[clip.id] = {
                    "path": rel_path,
                    "title": clip.title,
                    "created": clip.created_at[:10] if clip.created_at else None,
                    "sources": next((srcs for c, _, _, srcs in desired if c.id == clip.id), []),
                    "size": file_size,
                    "meta_hash": clip_meta_hash(clip),
                }
                downloaded += 1
            else:
                self._errors += 1

            if downloaded < len(to_download):
                await asyncio.sleep(SYNC_DOWNLOAD_DELAY)

        # Remove orphaned files (trash or delete)
        for clip_id in to_delete:
            entry = clips_state.pop(clip_id, None)
            if entry and entry.get("path"):
                if trash_days:
                    await _trash_file(self.hass, base, clip_id, entry, trash_state)
                else:
                    await _delete_file(self.hass, base, entry["path"])

        # Update state
        self._state["clips"] = clips_state
        self._state["trash"] = trash_state
        self._state["last_sync"] = datetime.now(tz=UTC).isoformat()
        self._pending = max(0, len(to_download) - downloaded)
        await self._save_state(base)

    # ── Desired state builder ───────────────────────────────────────

    async def _build_desired(
        self,
        options: dict[str, Any],
        client: Any,
        coordinator_data: SunoData | None = None,
    ) -> tuple[list[tuple[SunoClip, int, str, list[str]]], set[str]]:
        """Build list of (clip, index, collection_name, sources).

        Returns (desired_list, preserved_ids) where preserved_ids contains clip
        IDs from the previous sync state whose source API call failed.  These
        IDs must not be deleted, since the failure may be transient.

        When coordinator_data is provided, uses it for liked/all_songs/playlists
        instead of making duplicate API calls.  Per-playlist clip fetches still
        hit the API (not available from coordinator).
        """
        clip_map: dict[str, tuple[SunoClip, str, list[str]]] = {}
        preserved: set[str] = set()
        prev_clips: dict[str, Any] = self._state.get("clips", {})

        # Liked songs
        if options.get(CONF_SYNC_LIKED, DEFAULT_SYNC_LIKED):
            try:
                liked = coordinator_data.liked_clips if coordinator_data else await client.get_liked_songs()
                for clip in liked:
                    if clip.id in clip_map:
                        clip_map[clip.id][2].append("liked")
                    else:
                        clip_map[clip.id] = (clip, "Liked Songs", ["liked"])
            except Exception:
                _LOGGER.warning("Failed to fetch liked songs for sync")
                for cid, entry in prev_clips.items():
                    if "liked" in entry.get("sources", []):
                        preserved.add(cid)

        # Playlists
        sync_all = options.get(CONF_SYNC_ALL_PLAYLISTS, DEFAULT_SYNC_ALL_PLAYLISTS)
        selected_ids = options.get(CONF_SYNC_PLAYLISTS, []) or []

        if sync_all or selected_ids:
            try:
                playlists = coordinator_data.playlists if coordinator_data else await client.get_playlists()
                for pl in playlists:
                    if not sync_all and pl.id not in selected_ids:
                        continue
                    try:
                        clips = await client.get_playlist_clips(pl.id)
                        for clip in clips:
                            source = f"playlist:{pl.id}"
                            if clip.id in clip_map:
                                clip_map[clip.id][2].append(source)
                            else:
                                clip_map[clip.id] = (clip, pl.name, [source])
                    except Exception:
                        _LOGGER.warning("Failed to fetch clips for playlist %s", pl.name)
                        source_tag = f"playlist:{pl.id}"
                        for cid, entry in prev_clips.items():
                            if source_tag in entry.get("sources", []):
                                preserved.add(cid)
            except Exception:
                _LOGGER.warning("Failed to fetch playlists for sync")
                for cid, entry in prev_clips.items():
                    if any(s.startswith("playlist:") for s in entry.get("sources", [])):
                        preserved.add(cid)

        # Recent songs
        recent_count = options.get(CONF_SYNC_RECENT_COUNT, DEFAULT_SYNC_RECENT_COUNT)
        recent_days = options.get(CONF_SYNC_RECENT_DAYS, DEFAULT_SYNC_RECENT_DAYS)

        if recent_count or recent_days:
            try:
                all_clips = coordinator_data.clips if coordinator_data else await client.get_all_songs()
                recent_set: set[str] = set()

                if recent_count:
                    for clip in all_clips[: int(recent_count)]:
                        recent_set.add(clip.id)

                if recent_days:
                    cutoff = datetime.now(tz=UTC).timestamp() - (int(recent_days) * 86400)
                    for clip in all_clips:
                        if clip.created_at:
                            try:
                                created = datetime.fromisoformat(clip.created_at.replace("Z", "+00:00"))
                                if created.timestamp() >= cutoff:
                                    recent_set.add(clip.id)
                            except ValueError:
                                pass

                for clip in all_clips:
                    if clip.id in recent_set:
                        if clip.id in clip_map:
                            clip_map[clip.id][2].append("recent")
                        else:
                            clip_map[clip.id] = (clip, "Recent", ["recent"])
            except Exception:
                _LOGGER.warning("Failed to fetch recent songs for sync")
                for cid, entry in prev_clips.items():
                    if "recent" in entry.get("sources", []):
                        preserved.add(cid)

        # Don't preserve IDs that were successfully fetched via another source
        preserved -= clip_map.keys()

        # Convert to indexed list
        result: list[tuple[SunoClip, int, str, list[str]]] = []
        for i, (_clip_id, (clip, collection, sources)) in enumerate(clip_map.items()):
            result.append((clip, i, collection, sources))
        return result, preserved

    # ── Download ────────────────────────────────────────────────────

    async def _download_clip(self, client: Any, clip: SunoClip, base: Path, rel_path: str) -> int | None:
        """Download a single clip as FLAC. Returns file size on success, None on failure."""
        target = base / rel_path
        if await self.hass.async_add_executor_job(target.exists):
            size = await self.hass.async_add_executor_job(lambda: target.stat().st_size)
            return size

        _LOGGER.info("Downloading: %s", clip.title)

        try:
            wav_url = await ensure_wav_url(client, clip.id)
            if not wav_url:
                _LOGGER.warning("WAV generation timed out for %s", clip.id)
                return None

            session = async_get_clientsession(self.hass)
            async with session.get(wav_url) as resp:
                if resp.status != 200:
                    _LOGGER.warning("WAV download failed for %s: %d", clip.id, resp.status)
                    return None
                wav_data = await resp.read()

            # Download album art
            image_url = clip.image_large_url or clip.image_url
            image_data = await fetch_album_art(session, image_url) if image_url else None

            # Transcode to FLAC with metadata and album art
            flac_data = await wav_to_flac(
                get_ffmpeg_manager(self.hass).binary,
                wav_data,
                clip.title or "Suno",
                genre=clip.tags or "",
                image_data=image_data,
            )
            if flac_data is None:
                return None

            # Atomic write
            await _write_file(self.hass, target, flac_data)
            _LOGGER.info("Synced: %s (%d bytes)", rel_path, len(flac_data))

            return len(flac_data)

        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Failed to download %s", clip.id)
            return None

    async def cleanup_tmp_files(self, sync_path: str) -> None:
        """Remove stale .tmp files from the sync directory."""

        def _cleanup(p: str) -> None:
            base = Path(p)
            if not base.exists():
                return
            for tmp in base.rglob("*.tmp"):
                tmp.unlink(missing_ok=True)
                _LOGGER.debug("Cleaned up: %s", tmp)

        await self.hass.async_add_executor_job(_cleanup, sync_path)

    # ── State persistence ───────────────────────────────────────────

    async def _save_state(self, base: Path) -> None:
        """Save sync state to HA Store and write co-located manifest."""
        await self._store.async_save(self._state)

        # Write manifest for external tools (not read back)
        def _write_manifest(b: Path, state: dict[str, Any]) -> None:
            manifest = b / _MANIFEST_FILENAME
            try:
                manifest.write_text(json.dumps(state, indent=2))
            except OSError:
                pass  # Non-critical

        await self.hass.async_add_executor_job(_write_manifest, base, self._state)
