"""Background download manager for the Suno integration."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .audio import download_and_transcode_to_flac, download_as_mp3
from .const import (
    CDN_BASE_URL,
    CONF_ALL_PLAYLISTS,
    CONF_CREATE_PLAYLISTS,
    CONF_DOWNLOAD_MODE_LATEST,
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_LATEST_COUNT,
    CONF_LATEST_DAYS,
    CONF_LATEST_MINIMUM,
    CONF_PLAYLISTS,
    CONF_QUALITY_LATEST,
    CONF_QUALITY_LIKED,
    CONF_QUALITY_PLAYLISTS,
    CONF_SHOW_LIKED,
    DEFAULT_ALL_PLAYLISTS,
    DEFAULT_DOWNLOAD_MODE,
    DEFAULT_LATEST_COUNT,
    DEFAULT_LATEST_DAYS,
    DEFAULT_LATEST_MINIMUM,
    DEFAULT_SHOW_LIKED,
    DOMAIN,
    DOWNLOAD_DELAY,
    DOWNLOAD_MAX_BOOTSTRAP,
    DOWNLOAD_MAX_PER_RUN,
    DOWNLOAD_MODE_MIRROR,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from .models import SunoClip, clip_meta_hash

if TYPE_CHECKING:
    from .api import SunoClient
    from .cache import SunoCache
    from .coordinator import SunoCoordinator, SunoData

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
_SERVICE_DOWNLOAD = "download_library"
_MANIFEST_FILENAME = ".suno_download.json"
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class DownloadItem:
    """A clip scheduled for download with its resolved metadata."""

    clip: SunoClip
    collection: str
    sources: list[str]
    quality: str  # "high" | "standard"


def _sanitise_filename(name: str, max_len: int = 200) -> str:
    safe = _UNSAFE_CHARS.sub("_", name).strip(". ")
    return safe[:max_len] if safe else "untitled"


def _build_download_summary(downloaded: int, removed: int, meta_updates: int) -> str:
    """Build a human-readable summary of download results."""
    parts: list[str] = []
    if downloaded:
        parts.append(f"{downloaded} new song{'s' if downloaded != 1 else ''}")
    if meta_updates:
        parts.append(f"{meta_updates} metadata update{'s' if meta_updates != 1 else ''}")
    if removed:
        parts.append(f"{removed} removal{'s' if removed != 1 else ''}")
    return ", ".join(parts) if parts else "No change"


def _write_m3u8_playlists(
    base: Path,
    clips_state: dict[str, Any],
    desired: list[DownloadItem],
    source_to_name: dict[str, str] | None = None,
) -> None:
    """Write M3U8 playlist files for Jellyfin/media player compatibility.

    Each source tag (e.g. "liked", "playlist:abc") is resolved to a playlist
    name via *source_to_name*.  The "liked" source always maps to
    "Liked Songs"; "latest" sources are intentionally excluded from playlists.
    """
    if source_to_name is None:
        source_to_name = {}
    # Build playlist_name → [(abs_path, title, duration)] from sources
    playlists: dict[str, list[tuple[str, str, int]]] = {}
    for item in desired:
        entry = clips_state.get(item.clip.id)
        if not entry or not entry.get("path"):
            continue
        abs_path = str(base / entry["path"])
        title = entry.get("title") or item.clip.title or "Untitled"
        # Strip newlines to prevent M3U8 directive injection
        title = title.replace("\n", " ").replace("\r", "")
        duration = int(item.clip.duration) if item.clip.duration else -1
        for source in item.sources:
            if source == "liked":
                playlists.setdefault("Liked Songs", []).append((abs_path, title, duration))
            elif source.startswith("playlist:"):
                playlist_name = source_to_name.get(source, source)
                playlists.setdefault(playlist_name, []).append((abs_path, title, duration))

    # Write M3U8 files
    written: set[str] = set()
    for name, tracks in playlists.items():
        safe_name = name.replace("\n", " ").replace("\r", "")
        filename = f"{_sanitise_filename(safe_name)}.m3u8"
        lines = [f"#EXTM3U\n#PLAYLIST:{safe_name}"]
        for abs_path, title, duration in tracks:
            lines.append(f"#EXTINF:{duration},{title}\n{abs_path}")
        try:
            (base / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")
            written.add(filename)
        except OSError:
            _LOGGER.warning("Failed to write playlist file: %s", filename)

    # Clean up stale M3U8 files
    for existing in base.glob("*.m3u8"):
        if existing.name not in written:
            existing.unlink(missing_ok=True)


def _clip_path(clip: SunoClip, quality: str) -> str:
    """Build the relative file path for a clip.

    Uses clip ID prefix for uniqueness (not position index).
    Extension determined by quality setting.
    """
    title = _sanitise_filename(clip.title or "untitled")
    date_str = clip.created_at[:10] if clip.created_at else "unknown"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        date_str = "unknown"
    clip_short = clip.id[:8]
    ext = "flac" if quality == QUALITY_HIGH else "mp3"
    return f"{date_str}/{title} [{clip_short}].{ext}"


def _add_clip(
    clip_map: dict[str, DownloadItem],
    clip: SunoClip,
    collection: str,
    source: str,
    quality: str,
) -> None:
    if clip.id in clip_map:
        item = clip_map[clip.id]
        item.sources.append(source)
        if quality == QUALITY_HIGH:
            item.quality = QUALITY_HIGH
    else:
        clip_map[clip.id] = DownloadItem(clip=clip, collection=collection, sources=[source], quality=quality)


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



def _source_uses_mirror_mode(source: str, options: dict[str, Any]) -> bool:
    """Check if a source tag's mode is 'mirror' (managed, delete removed)."""
    if source == "liked":
        return bool(options.get(CONF_DOWNLOAD_MODE_LIKED, DEFAULT_DOWNLOAD_MODE) == DOWNLOAD_MODE_MIRROR)
    if source.startswith("playlist:"):
        return bool(options.get(CONF_DOWNLOAD_MODE_PLAYLISTS, DEFAULT_DOWNLOAD_MODE) == DOWNLOAD_MODE_MIRROR)
    if source == "latest":
        return bool(options.get(CONF_DOWNLOAD_MODE_LATEST, DEFAULT_DOWNLOAD_MODE) == DOWNLOAD_MODE_MIRROR)
    return True  # Unknown sources default to mirror (delete-safe)


class SunoDownloadManager:
    """Manages background file downloads to a local directory."""

    def __init__(self, hass: HomeAssistant, store_key: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, store_key)
        self._state: dict[str, Any] = {"clips": {}, "last_download": None}
        self._cache: SunoCache | None = None
        self._coordinator: SunoCoordinator | None = None
        self._client: SunoClient | None = None
        self._entry: ConfigEntry | None = None
        self._download_path = ""
        self._running = False
        self._errors = self._pending = 0
        self._last_result = ""
        self._updating_sensors = False

    async def async_init(self) -> None:
        """Load persisted download state."""
        if (data := await self._store.async_load()) and isinstance(data, dict):
            self._state = data
            self._last_result = data.get("last_result", "")

    @classmethod
    async def async_setup(
        cls, hass: HomeAssistant, entry: ConfigEntry, coordinator: SunoCoordinator, client: SunoClient
    ) -> SunoDownloadManager:
        """Create, initialise, and wire up download manager."""
        mgr = cls(hass, f"suno_sync_{entry.entry_id}")
        mgr._cache = coordinator.cache
        mgr._coordinator = coordinator
        mgr._client = client
        mgr._entry = entry
        mgr._download_path = entry.options.get(CONF_DOWNLOAD_PATH, "")
        await mgr.async_init()
        if download_path := entry.options.get(CONF_DOWNLOAD_PATH, ""):
            await mgr.cleanup_tmp_files(download_path)

        def _on_coordinator_update() -> None:
            if not mgr.is_running and not mgr._updating_sensors:
                hass.async_create_task(
                    mgr.async_download(dict(entry.options), client, coordinator_data=coordinator.data),
                    f"suno_download_refresh_{entry.entry_id}",
                )

        entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))

        async def _handle_download_service(call: ServiceCall) -> None:
            await mgr.async_download(dict(entry.options), client, force=call.data.get("force", False))

        if not hass.services.has_service(DOMAIN, _SERVICE_DOWNLOAD):
            hass.services.async_register(DOMAIN, _SERVICE_DOWNLOAD, _handle_download_service)
            entry.async_on_unload(lambda: hass.services.async_remove(DOMAIN, _SERVICE_DOWNLOAD))

        async def _initial_download() -> None:
            await asyncio.sleep(60)
            await mgr.async_download(dict(entry.options), client)

        entry.async_create_background_task(hass, _initial_download(), f"suno_download_init_{entry.entry_id}")
        return mgr

    # fmt: off
    @property
    def last_download(self) -> str | None: return self._state.get("last_download") or self._state.get("last_sync")
    @property
    def last_result(self) -> str: return self._last_result
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

    @property
    def source_breakdown(self) -> dict[str, int]:
        """Count synced clips per source tag."""
        from collections import Counter  # noqa: PLC0415

        counts: Counter[str] = Counter()
        for entry in self._state.get("clips", {}).values():
            for src in entry.get("sources", []):
                counts[src] += 1
        return dict(counts)

    def get_downloaded_path(self, clip_id: str, meta_hash: str = "") -> Path | None:
        """Return absolute path to a downloaded file if it exists and is fresh."""
        if not self._download_path:
            return None
        if not (entry := self._state.get("clips", {}).get(clip_id)):
            return None
        if meta_hash and entry.get("meta_hash") and entry["meta_hash"] != meta_hash:
            return None
        path = Path(self._download_path) / str(entry["path"])
        return path if path.is_file() else None

    async def async_download(
        self,
        options: dict[str, Any],
        client: Any,
        force: bool = False,
        coordinator_data: SunoData | None = None,
    ) -> None:
        """Run a full download cycle."""
        if self._running:
            _LOGGER.debug("Download already running, skipping")
            return
        if not (download_path := options.get(CONF_DOWNLOAD_PATH)):
            _LOGGER.warning("No download_path configured")
            return
        self._running = True
        self._errors = self._pending = 0
        self._notify_coordinator()
        try:
            await self._run_download(options, client, download_path, force, coordinator_data)
        except asyncio.CancelledError:
            _LOGGER.info("Download cancelled")
            raise
        except Exception:
            _LOGGER.exception("Download failed")
            self._errors += 1
        finally:
            self._running = False
            self._notify_coordinator()
        self._maybe_continue(force=force)

    def _notify_coordinator(self) -> None:
        """Push sensor updates via the coordinator without re-triggering sync."""
        if self._coordinator and self._coordinator.data:
            self._updating_sensors = True
            self._coordinator.async_set_updated_data(self._coordinator.data)
            self._updating_sensors = False

    def _maybe_continue(self, *, force: bool = False) -> None:
        """Schedule the next batch immediately if items remain and no errors."""
        if self._pending <= 0 or self._errors > 0 or self._entry is None or self._client is None:
            return
        _LOGGER.info("Continuing download: %d remaining", self._pending)
        self._entry.async_create_background_task(
            self.hass,
            self.async_download(dict(self._entry.options), self._client, force=force),
            f"suno_download_continue_{self._entry.entry_id}",
        )

    async def _run_download(
        self,
        options: dict[str, Any],
        client: Any,
        download_path: str,
        force: bool,
        coordinator_data: SunoData | None = None,
    ) -> None:
        base = Path(download_path)
        # Clean up legacy .trash directory if it exists
        trash_dir = base / ".trash"
        if await self.hass.async_add_executor_job(trash_dir.is_dir):
            import shutil  # noqa: PLC0415

            await self.hass.async_add_executor_job(shutil.rmtree, str(trash_dir), True)
            _LOGGER.info("Removed legacy .trash directory")
        self._state.pop("trash", None)
        desired, preserved_ids, source_to_name = await self._build_desired(options, client, coordinator_data)
        clips_state = dict(self._state.get("clips", {}))
        to_download: list[DownloadItem] = []
        meta_updates = 0
        seen_ids: set[str] = set()
        for item in desired:
            seen_ids.add(item.clip.id)
            if item.clip.id not in clips_state or force:
                to_download.append(item)
            else:
                existing = clips_state[item.clip.id]
                # Quality change detection
                existing_quality = existing.get("quality", QUALITY_HIGH)
                if existing_quality != item.quality:
                    old_path = existing.get("path")
                    if old_path:
                        await _delete_file(self.hass, base, old_path)
                    to_download.append(item)
                else:
                    existing["sources"] = item.sources
                    old_hash = existing.get("meta_hash", "")
                    new_hash = clip_meta_hash(item.clip)
                    if old_hash and new_hash != old_hash:
                        existing["meta_hash"] = new_hash
                        existing["title"] = item.clip.title
                        meta_updates += 1
        to_delete = []
        for cid in clips_state:
            if cid in seen_ids or cid in preserved_ids:
                continue
            entry = clips_state[cid]
            sources = entry.get("sources", [])
            # Only delete if ALL sources use "mirror" mode
            # Empty sources → all() returns True → delete (orphan cleanup)
            if all(_source_uses_mirror_mode(src, options) for src in sources):
                to_delete.append(cid)
        self._pending = len(to_download)
        self._notify_coordinator()
        _LOGGER.info(
            "Download: %d to download, %d to remove, %d current",
            len(to_download),
            len(to_delete),
            len(seen_ids),
        )
        try:
            await self.hass.async_add_executor_job(base.mkdir, 0o755, True, True)
        except OSError:
            _LOGGER.error("Cannot create download directory: %s", download_path)
            self._errors += 1
            return
        is_bootstrap = len(to_download) > DOWNLOAD_MAX_PER_RUN
        max_dl = DOWNLOAD_MAX_BOOTSTRAP if is_bootstrap else DOWNLOAD_MAX_PER_RUN
        if is_bootstrap:
            _LOGGER.info("Bootstrap mode: downloading up to %d files", max_dl)
        downloaded = 0
        reconciled = 0
        for item in to_download:
            rel_path = _clip_path(item.clip, item.quality)
            target = base / rel_path
            if not force and await self.hass.async_add_executor_job(target.exists):
                # File already on disk -- register in state without counting
                # against the download cap or adding a delay.
                stat = await self.hass.async_add_executor_job(target.stat)
                clips_state[item.clip.id] = {
                    "path": rel_path,
                    "title": item.clip.title,
                    "created": item.clip.created_at[:10] if item.clip.created_at else None,
                    "sources": item.sources,
                    "size": stat.st_size,
                    "meta_hash": clip_meta_hash(item.clip),
                    "quality": item.quality,
                }
                reconciled += 1
                continue
            if downloaded >= max_dl:
                _LOGGER.info("Reached max downloads (%d), continuing next sync", max_dl)
                break
            if (file_size := await self._download_clip(client, item, base, rel_path)) is not None:
                clips_state[item.clip.id] = {
                    "path": rel_path,
                    "title": item.clip.title,
                    "created": item.clip.created_at[:10] if item.clip.created_at else None,
                    "sources": item.sources,
                    "size": file_size,
                    "meta_hash": clip_meta_hash(item.clip),
                    "quality": item.quality,
                }
                downloaded += 1
            else:
                self._errors += 1
            self._pending = max(0, len(to_download) - downloaded - reconciled)
            self._notify_coordinator()
            await asyncio.sleep(DOWNLOAD_DELAY)
        if reconciled:
            _LOGGER.info("Reconciled %d files already on disk", reconciled)
        for clip_id in to_delete:
            if (entry := clips_state.pop(clip_id, None)) and entry.get("path"):
                await _delete_file(self.hass, base, entry["path"])
        self._state["clips"] = clips_state
        self._state["last_download"] = datetime.now(tz=UTC).isoformat()
        self._pending = max(0, len(to_download) - downloaded - reconciled)
        if self._pending > 0:
            self._last_result = f"Downloading ({self._pending} remaining)"
        else:
            self._last_result = _build_download_summary(downloaded, len(to_delete), meta_updates)
        self._state["last_result"] = self._last_result
        await self._save_state(base)
        if options.get(CONF_CREATE_PLAYLISTS):
            await self.hass.async_add_executor_job(
                _write_m3u8_playlists, base, clips_state, desired, source_to_name
            )

        orphans = await self._reconcile_disk(base, clips_state)
        if orphans:
            _LOGGER.info("Reconciliation removed %d orphaned files", orphans)

    async def _reconcile_disk(self, base: Path, clips_state: dict[str, Any]) -> int:
        """Remove orphaned audio files not tracked in download state."""
        known_paths = {entry["path"] for entry in clips_state.values() if entry.get("path")}

        def _scan_and_remove(base_path: Path, known: set[str]) -> int:
            count = 0
            if not base_path.exists():
                return 0
            for f in base_path.rglob("*"):
                if not f.is_file():
                    continue
                # Skip non-audio files (manifest, playlists, tmp, hidden)
                if f.suffix.lower() not in (".flac", ".mp3"):
                    continue
                rel = str(f.relative_to(base_path))
                if rel not in known:
                    f.unlink(missing_ok=True)
                    _LOGGER.info("Reconciliation: removed orphan %s", rel)
                    count += 1
            # Clean up empty directories
            for d in sorted(base_path.rglob("*"), reverse=True):
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
            return count

        return await self.hass.async_add_executor_job(_scan_and_remove, base, known_paths)

    async def _build_desired(
        self,
        options: dict[str, Any],
        client: Any,
        coordinator_data: SunoData | None = None,
    ) -> tuple[list[DownloadItem], set[str], dict[str, str]]:
        clip_map: dict[str, DownloadItem] = {}
        preserved: set[str] = set()
        source_to_name: dict[str, str] = {"liked": "Liked Songs"}
        prev_clips = self._state.get("clips", {})
        if options.get(CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED):
            liked_quality = options.get(CONF_QUALITY_LIKED, QUALITY_HIGH)
            try:
                liked = coordinator_data.liked_clips if coordinator_data else await client.get_liked_songs()
                for clip in liked:
                    _add_clip(clip_map, clip, "Liked Songs", "liked", liked_quality)
            except Exception:
                _LOGGER.warning("Failed to fetch liked songs for sync")
                _preserve_by(preserved, prev_clips, lambda s: "liked" in s)
        sync_all = options.get(CONF_ALL_PLAYLISTS, DEFAULT_ALL_PLAYLISTS)
        selected_ids = options.get(CONF_PLAYLISTS, []) or []
        if sync_all or selected_ids:
            playlist_quality = options.get(CONF_QUALITY_PLAYLISTS, QUALITY_HIGH)
            try:
                playlists = coordinator_data.playlists if coordinator_data else await client.get_playlists()
                for pl in playlists:
                    if not sync_all and pl.id not in selected_ids:
                        continue
                    tag = f"playlist:{pl.id}"
                    source_to_name[tag] = pl.name
                    try:
                        for clip in await client.get_playlist_clips(pl.id):
                            _add_clip(clip_map, clip, pl.name, tag, playlist_quality)
                    except Exception:
                        _LOGGER.warning("Failed to fetch clips for playlist %s", pl.name)
                        _preserve_by(preserved, prev_clips, lambda s, t=tag: t in s)
            except Exception:
                _LOGGER.warning("Failed to fetch playlists for sync")
                _preserve_by(preserved, prev_clips, lambda s: any(x.startswith("playlist:") for x in s))
        latest_count = options.get(CONF_LATEST_COUNT, DEFAULT_LATEST_COUNT)
        latest_days = options.get(CONF_LATEST_DAYS, DEFAULT_LATEST_DAYS)
        minimum = int(options.get(CONF_LATEST_MINIMUM, DEFAULT_LATEST_MINIMUM))
        if latest_count or latest_days or minimum:
            latest_quality = options.get(CONF_QUALITY_LATEST, QUALITY_STANDARD)
            try:
                all_clips = coordinator_data.clips if coordinator_data else await client.get_all_songs()

                # Start with all clips, then narrow
                by_count: set[str] | None = set(c.id for c in all_clips[: int(latest_count)]) if latest_count else None
                by_days: set[str] | None = None
                if latest_days:
                    cutoff = datetime.now(tz=UTC).timestamp() - int(latest_days) * 86400
                    by_days = set()
                    for clip in all_clips:
                        if clip.created_at:
                            try:
                                created = datetime.fromisoformat(clip.created_at.replace("Z", "+00:00"))
                                if created.timestamp() >= cutoff:
                                    by_days.add(clip.id)
                            except ValueError:
                                pass

                # AND logic: intersect the filters that are active
                if by_count is not None and by_days is not None:
                    latest_set = by_count & by_days  # intersection
                elif by_count is not None:
                    latest_set = by_count
                elif by_days is not None:
                    latest_set = by_days
                else:
                    latest_set = set()

                # Minimum floor: pad with most recent songs if below threshold
                if minimum and len(latest_set) < minimum:
                    latest_set |= {c.id for c in all_clips[:minimum]}

                for clip in all_clips:
                    if clip.id in latest_set:
                        _add_clip(clip_map, clip, "Latest", "latest", latest_quality)
            except Exception:
                _LOGGER.warning("Failed to fetch latest songs for sync")
                _preserve_by(preserved, prev_clips, lambda s: "latest" in s)
        preserved -= clip_map.keys()
        return list(clip_map.values()), preserved, source_to_name

    async def _download_clip(self, client: Any, item: DownloadItem, base: Path, rel_path: str) -> int | None:
        target = base / rel_path
        _LOGGER.info("Downloading: %s (%s)", item.clip.title, item.quality)
        clip = item.clip
        date = clip.created_at[:10] if clip.created_at else ""
        common_meta = {
            "album": clip.title or "Suno",
            "album_artist": "Suno",
            "date": date,
            "lyrics": clip.lyrics,
            "comment": clip.gpt_description_prompt or clip.prompt,
        }
        try:
            session = async_get_clientsession(self.hass)

            if item.quality == QUALITY_HIGH:
                data = await download_and_transcode_to_flac(
                    client,
                    session,
                    get_ffmpeg_manager(self.hass).binary,
                    clip.id,
                    clip.title or "Suno",
                    genre=clip.tags or "",
                    image_url=clip.image_large_url or clip.image_url or None,
                    **common_meta,
                )
                fmt = "flac"
            else:
                audio_url = clip.audio_url or f"{CDN_BASE_URL}/{clip.id}.mp3"
                data = await download_as_mp3(
                    session,
                    audio_url,
                    title=clip.title or "Suno",
                    genre=clip.tags or "",
                    **common_meta,
                )
                fmt = "mp3"

            if data is None:
                return None

            await _write_file(self.hass, target, data)
            _LOGGER.info("Downloaded: %s (%d bytes)", rel_path, len(data))

            # Write-through to cache
            if self._cache is not None:
                meta_hash = clip_meta_hash(item.clip)
                try:
                    await self._cache.async_put(item.clip.id, fmt, data, meta_hash=meta_hash)
                except Exception:
                    _LOGGER.debug("Cache write-through failed for %s", item.clip.id)

            return len(data)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Failed to download %s", item.clip.id)
            return None

    # fmt: off
    async def cleanup_tmp_files(self, download_path: str) -> None:
        """Remove stale .tmp files from the download directory."""
        def _cleanup(p: str) -> None:
            base = Path(p)
            if not base.exists():
                return
            for tmp in base.rglob("*.tmp"):
                tmp.unlink(missing_ok=True)
                _LOGGER.debug("Cleaned up: %s", tmp)
        await self.hass.async_add_executor_job(_cleanup, download_path)

    async def _save_state(self, base: Path) -> None:
        await self._store.async_save(self._state)
        def _write_manifest(b: Path, state: dict[str, Any]) -> None:
            try:
                (b / _MANIFEST_FILENAME).write_text(json.dumps(state, indent=2))
            except OSError:
                pass
        await self.hass.async_add_executor_job(_write_manifest, base, self._state)
    # fmt: on
