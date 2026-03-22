"""Background FLAC sync for the Suno integration."""

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
    CONF_SYNC_PLAYLISTS_M3U,
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
    safe = _UNSAFE_CHARS.sub("_", name).strip(". ")
    return safe[:max_len] if safe else "untitled"


def _write_m3u8_playlists(
    base: Path, clips_state: dict[str, Any], desired: list[tuple[Any, int, str, list[str]]]
) -> None:
    """Write M3U8 playlist files for Jellyfin/media player compatibility."""
    # Build playlist_name → [(abs_path, title, duration)] from sources
    playlists: dict[str, list[tuple[str, str, int]]] = {}
    for clip, _idx, collection, sources in desired:
        entry = clips_state.get(clip.id)
        if not entry or not entry.get("path"):
            continue
        abs_path = str(base / entry["path"])
        title = entry.get("title") or clip.title or "Untitled"
        duration = int(clip.duration) if clip.duration else -1
        for source in sources:
            if source == "liked":
                playlists.setdefault("Liked Songs", []).append((abs_path, title, duration))
            elif source.startswith("playlist:"):
                playlists.setdefault(collection, []).append((abs_path, title, duration))

    # Write M3U8 files
    written: set[str] = set()
    for name, tracks in playlists.items():
        filename = f"{_sanitise_filename(name)}.m3u8"
        written.add(filename)
        lines = [f"#EXTM3U\n#PLAYLIST:{name}"]
        for abs_path, title, duration in tracks:
            lines.append(f"#EXTINF:{duration},{title}\n{abs_path}")
        try:
            (base / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    # Clean up stale M3U8 files
    for existing in base.glob("*.m3u8"):
        if existing.name not in written:
            existing.unlink(missing_ok=True)


def _clip_path(clip: SunoClip, index: int) -> str:
    """Build the relative file path for a clip (date organisation)."""
    title = _sanitise_filename(clip.title or "untitled")
    date_str = clip.created_at[:10] if clip.created_at else "unknown"
    return f"{date_str}/{index + 1:02d} - {title}.flac"


def _add_clip(
    clip_map: dict[str, tuple[SunoClip, str, list[str]]], clip: SunoClip, collection: str, source: str
) -> None:
    if clip.id in clip_map:
        clip_map[clip.id][2].append(source)
    else:
        clip_map[clip.id] = (clip, collection, [source])


def _preserve_by(preserved: set[str], prev_clips: dict[str, Any], pred: Any) -> None:
    preserved.update(cid for cid, e in prev_clips.items() if pred(e.get("sources", [])))


def _cleanup_empty_dirs(base: Path, target: Path) -> None:
    parent = target.parent
    while parent != base:
        try:
            parent.rmdir()
            parent = parent.parent
        except OSError:
            break


# fmt: off
async def _write_file(hass: HomeAssistant, target: Path, data: bytes) -> None:
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


async def _trash_file(
    hass: HomeAssistant, base: Path, clip_id: str, entry: dict[str, Any], trash_state: dict[str, Any],
) -> None:
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
    if trash_path := await hass.async_add_executor_job(_move_to_trash, base, rel_path):
        trash_state[clip_id] = {
            "path": trash_path, "original_path": rel_path, "trashed_at": datetime.now(tz=UTC).isoformat(),
        }


async def _restore_from_trash(
    hass: HomeAssistant, base: Path, clip_id: str, trash_state: dict[str, Any],
) -> dict[str, Any] | None:
    if not (trash_entry := trash_state.get(clip_id)):
        return None
    trash_path, original_path = trash_entry.get("path", ""), trash_entry.get("original_path", "")
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
    if await hass.async_add_executor_job(_restore, base, trash_path, original_path):
        trash_state.pop(clip_id, None)
        return {
            "path": original_path, "title": trash_entry.get("title", ""),
            "created": trash_entry.get("created"), "sources": [],
        }
    return None
# fmt: on


async def _purge_trash(hass: HomeAssistant, base: Path, trash_state: dict[str, Any], max_days: int) -> None:
    now = datetime.now(tz=UTC)
    to_purge: list[str] = []
    for clip_id, entry in trash_state.items():
        try:
            if (now - datetime.fromisoformat(entry.get("trashed_at", ""))).total_seconds() / 86400 >= max_days:
                to_purge.append(clip_id)
        except ValueError, TypeError:
            to_purge.append(clip_id)
    for clip_id in to_purge:
        entry = trash_state.pop(clip_id)
        if path := entry.get("path", ""):
            await _delete_file(hass, base, path)
        _LOGGER.debug("Purged from trash: %s", clip_id)


class SunoSync:
    """Manages background FLAC sync to a local directory."""

    def __init__(self, hass: HomeAssistant, store_key: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, store_key)
        self._state: dict[str, Any] = {"clips": {}, "last_sync": None}
        self._cache: SunoCache | None = None
        self._sync_path = ""
        self._running = False
        self._errors = self._pending = 0

    async def async_init(self) -> None:
        """Load persisted sync state."""
        if (data := await self._store.async_load()) and isinstance(data, dict):
            self._state = data

    @classmethod
    async def async_setup(
        cls, hass: HomeAssistant, entry: ConfigEntry, coordinator: SunoCoordinator, client: SunoClient
    ) -> SunoSync:
        """Create, initialise, and wire up sync."""
        sync = cls(hass, f"suno_sync_{entry.entry_id}")
        sync._cache = coordinator.cache
        sync._sync_path = entry.options.get(CONF_SYNC_PATH, "")
        await sync.async_init()
        if sync_path := entry.options.get(CONF_SYNC_PATH, ""):
            await sync.cleanup_tmp_files(sync_path)

        def _on_coordinator_update() -> None:
            if not sync.is_running:
                hass.async_create_task(
                    sync.async_sync(dict(entry.options), client, coordinator_data=coordinator.data),
                    f"suno_sync_refresh_{entry.entry_id}",
                )

        entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))

        async def _handle_sync_service(call: ServiceCall) -> None:
            await sync.async_sync(dict(entry.options), client, force=call.data.get("force", False))

        if not hass.services.has_service(DOMAIN, _SERVICE_SYNC):
            hass.services.async_register(DOMAIN, _SERVICE_SYNC, _handle_sync_service)
            entry.async_on_unload(lambda: hass.services.async_remove(DOMAIN, _SERVICE_SYNC))

        async def _initial_sync() -> None:
            await asyncio.sleep(60)
            await sync.async_sync(dict(entry.options), client)

        entry.async_create_background_task(hass, _initial_sync(), f"suno_sync_init_{entry.entry_id}")
        return sync

    # fmt: off
    @property
    def last_sync(self) -> str | None: return self._state.get("last_sync")
    @property
    def total_files(self) -> int: return len(self._state.get("clips", {}))
    @property
    def pending(self) -> int: return self._pending
    @property
    def errors(self) -> int: return self._errors
    @property
    def is_running(self) -> bool: return self._running
    # fmt: on

    @property
    def library_size_mb(self) -> float:
        """Total size of synced files in MB."""
        return round(sum(int(e.get("size", 0)) for e in self._state.get("clips", {}).values()) / 1048576, 1)

    def get_synced_path(self, clip_id: str, meta_hash: str = "") -> Path | None:
        """Return absolute path to a synced FLAC if it exists and is fresh."""
        if not self._sync_path:
            return None
        if not (entry := self._state.get("clips", {}).get(clip_id)):
            return None
        if meta_hash and entry.get("meta_hash") and entry["meta_hash"] != meta_hash:
            return None
        path = Path(self._sync_path) / str(entry["path"])
        return path if path.is_file() else None

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
        if not (sync_path := options.get(CONF_SYNC_PATH)):
            _LOGGER.warning("Sync enabled but no sync_path configured")
            return
        self._running = True
        self._errors = self._pending = 0
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
        trash_days = options.get(CONF_SYNC_TRASH_DAYS, DEFAULT_SYNC_TRASH_DAYS)
        base = Path(sync_path)
        trash_state = dict(self._state.get("trash", {}))
        if trash_days:
            await _purge_trash(self.hass, base, trash_state, int(trash_days))
        desired, preserved_ids = await self._build_desired(options, client, coordinator_data)
        clips_state = dict(self._state.get("clips", {}))
        to_download: list[tuple[SunoClip, int]] = []
        seen_ids: set[str] = set()
        for clip, index, _collection, sources in desired:
            seen_ids.add(clip.id)
            if clip.id not in clips_state or force:
                if clip.id in trash_state:
                    if restored := await _restore_from_trash(self.hass, base, clip.id, trash_state):
                        clips_state[clip.id] = restored
                        clips_state[clip.id]["sources"] = sources
                        continue
                to_download.append((clip, index))
            if clip.id in clips_state:
                clips_state[clip.id]["sources"] = sources
        to_delete = [cid for cid in clips_state if cid not in seen_ids and cid not in preserved_ids]
        self._pending = len(to_download)
        _LOGGER.info("Sync: %d to download, %d to remove, %d current", len(to_download), len(to_delete), len(seen_ids))
        try:
            await self.hass.async_add_executor_job(base.mkdir, 0o755, True, True)
        except OSError:
            _LOGGER.error("Cannot create sync directory: %s", sync_path)
            self._errors += 1
            return
        is_bootstrap = self.total_files == 0 and len(to_download) > SYNC_MAX_DOWNLOADS_PER_RUN
        max_dl = SYNC_MAX_DOWNLOADS_BOOTSTRAP if is_bootstrap else SYNC_MAX_DOWNLOADS_PER_RUN
        if is_bootstrap:
            _LOGGER.info("Bootstrap mode: downloading up to %d files", max_dl)
        downloaded = 0
        for clip, index in to_download:
            if downloaded >= max_dl:
                _LOGGER.info("Reached max downloads (%d), continuing next sync", max_dl)
                break
            rel_path = _clip_path(clip, index)
            if (file_size := await self._download_clip(client, clip, base, rel_path)) is not None:
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
        for clip_id in to_delete:
            if (entry := clips_state.pop(clip_id, None)) and entry.get("path"):
                if trash_days:
                    await _trash_file(self.hass, base, clip_id, entry, trash_state)
                else:
                    await _delete_file(self.hass, base, entry["path"])
        self._state["clips"] = clips_state
        self._state["trash"] = trash_state
        self._state["last_sync"] = datetime.now(tz=UTC).isoformat()
        self._pending = max(0, len(to_download) - downloaded)
        await self._save_state(base)
        if options.get(CONF_SYNC_PLAYLISTS_M3U):
            await self.hass.async_add_executor_job(_write_m3u8_playlists, base, clips_state, desired)

    async def _build_desired(
        self,
        options: dict[str, Any],
        client: Any,
        coordinator_data: SunoData | None = None,
    ) -> tuple[list[tuple[SunoClip, int, str, list[str]]], set[str]]:
        clip_map: dict[str, tuple[SunoClip, str, list[str]]] = {}
        preserved: set[str] = set()
        prev_clips = self._state.get("clips", {})
        if options.get(CONF_SYNC_LIKED, DEFAULT_SYNC_LIKED):
            try:
                liked = coordinator_data.liked_clips if coordinator_data else await client.get_liked_songs()
                for clip in liked:
                    _add_clip(clip_map, clip, "Liked Songs", "liked")
            except Exception:
                _LOGGER.warning("Failed to fetch liked songs for sync")
                _preserve_by(preserved, prev_clips, lambda s: "liked" in s)
        sync_all = options.get(CONF_SYNC_ALL_PLAYLISTS, DEFAULT_SYNC_ALL_PLAYLISTS)
        selected_ids = options.get(CONF_SYNC_PLAYLISTS, []) or []
        if sync_all or selected_ids:
            try:
                playlists = coordinator_data.playlists if coordinator_data else await client.get_playlists()
                for pl in playlists:
                    if not sync_all and pl.id not in selected_ids:
                        continue
                    try:
                        for clip in await client.get_playlist_clips(pl.id):
                            _add_clip(clip_map, clip, pl.name, f"playlist:{pl.id}")
                    except Exception:
                        _LOGGER.warning("Failed to fetch clips for playlist %s", pl.name)
                        tag = f"playlist:{pl.id}"
                        _preserve_by(preserved, prev_clips, lambda s, t=tag: t in s)
            except Exception:
                _LOGGER.warning("Failed to fetch playlists for sync")
                _preserve_by(preserved, prev_clips, lambda s: any(x.startswith("playlist:") for x in s))
        recent_count = options.get(CONF_SYNC_RECENT_COUNT, DEFAULT_SYNC_RECENT_COUNT)
        recent_days = options.get(CONF_SYNC_RECENT_DAYS, DEFAULT_SYNC_RECENT_DAYS)
        if recent_count or recent_days:
            try:
                all_clips = coordinator_data.clips if coordinator_data else await client.get_all_songs()
                recent_set: set[str] = set()
                if recent_count:
                    recent_set.update(c.id for c in all_clips[: int(recent_count)])
                if recent_days:
                    cutoff = datetime.now(tz=UTC).timestamp() - int(recent_days) * 86400
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
                        _add_clip(clip_map, clip, "Recent", "recent")
            except Exception:
                _LOGGER.warning("Failed to fetch recent songs for sync")
                _preserve_by(preserved, prev_clips, lambda s: "recent" in s)
        preserved -= clip_map.keys()
        return [
            (clip, i, collection, sources) for i, (clip, collection, sources) in enumerate(clip_map.values())
        ], preserved

    async def _download_clip(self, client: Any, clip: SunoClip, base: Path, rel_path: str) -> int | None:
        target = base / rel_path
        if await self.hass.async_add_executor_job(target.exists):
            return await self.hass.async_add_executor_job(lambda: target.stat().st_size)
        _LOGGER.info("Downloading: %s", clip.title)
        try:
            if not (wav_url := await ensure_wav_url(client, clip.id)):
                _LOGGER.warning("WAV generation timed out for %s", clip.id)
                return None
            session = async_get_clientsession(self.hass)
            async with session.get(wav_url) as resp:
                if resp.status != 200:
                    _LOGGER.warning("WAV download failed for %s: %d", clip.id, resp.status)
                    return None
                wav_data = await resp.read()
            image_url = clip.image_large_url or clip.image_url
            image_data = await fetch_album_art(session, image_url) if image_url else None
            flac_data = await wav_to_flac(
                get_ffmpeg_manager(self.hass).binary,
                wav_data,
                clip.title or "Suno",
                genre=clip.tags or "",
                image_data=image_data,
            )
            if flac_data is None:
                return None
            await _write_file(self.hass, target, flac_data)
            _LOGGER.info("Synced: %s (%d bytes)", rel_path, len(flac_data))
            return len(flac_data)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Failed to download %s", clip.id)
            return None

    # fmt: off
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

    async def _save_state(self, base: Path) -> None:
        await self._store.async_save(self._state)
        def _write_manifest(b: Path, state: dict[str, Any]) -> None:
            try:
                (b / _MANIFEST_FILENAME).write_text(json.dumps(state, indent=2))
            except OSError:
                pass
        await self.hass.async_add_executor_job(_write_manifest, base, self._state)
    # fmt: on
