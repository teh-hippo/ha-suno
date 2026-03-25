"""Background download manager for the Suno integration."""

from __future__ import annotations

import asyncio
import hashlib
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

from .audio import download_and_transcode_to_flac, download_as_mp3, fetch_album_art
from .const import (
    CDN_BASE_URL,
    CONF_ALL_PLAYLISTS,
    CONF_CREATE_PLAYLISTS,
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_MY_SONGS,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_DOWNLOAD_VIDEOS,
    CONF_MY_SONGS_COUNT,
    CONF_MY_SONGS_DAYS,
    CONF_MY_SONGS_MINIMUM,
    CONF_PLAYLISTS,
    CONF_QUALITY_LIKED,
    CONF_QUALITY_MY_SONGS,
    CONF_QUALITY_PLAYLISTS,
    CONF_SHOW_LIKED,
    CONF_SHOW_MY_SONGS,
    CONF_SHOW_PLAYLISTS,
    DEFAULT_ALL_PLAYLISTS,
    DEFAULT_DOWNLOAD_MODE,
    DEFAULT_MY_SONGS_COUNT,
    DEFAULT_MY_SONGS_DAYS,
    DEFAULT_MY_SONGS_MINIMUM,
    DEFAULT_SHOW_LIKED,
    DEFAULT_SHOW_MY_SONGS,
    DEFAULT_SHOW_PLAYLISTS,
    DOMAIN,
    DOWNLOAD_MODE_ARCHIVE,
    DOWNLOAD_MODE_CACHE,
    DOWNLOAD_MODE_MIRROR,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from .models import SunoClip, TrackMetadata, clip_meta_hash

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
    playlist_order: dict[str, list[str]] | None = None,
) -> None:
    """Write M3U8 playlist files for Jellyfin/media player compatibility.

    Each source tag (e.g. "liked", "playlist:abc") is resolved to a playlist
    name via *source_to_name*.  The "liked" source always maps to
    "Liked Songs"; "my_songs" sources are intentionally excluded from playlists.

    *playlist_order* maps source tags to ordered clip ID lists as returned
    by the Suno API, ensuring playlists respect the user's chosen order.
    """
    if source_to_name is None:
        source_to_name = {}
    if playlist_order is None:
        playlist_order = {}
    # Build clip_id → track info lookup
    track_info: dict[str, tuple[str, str, int]] = {}
    for item in desired:
        entry = clips_state.get(item.clip.id)
        if not entry or not entry.get("path"):
            continue
        abs_path = str(base / entry["path"])
        title = entry.get("title") or item.clip.title or "Untitled"
        title = title.replace("\n", " ").replace("\r", "")
        duration = int(item.clip.duration) if item.clip.duration else -1
        track_info[item.clip.id] = (abs_path, title, duration)
    # Build playlist_name → ordered [(abs_path, title, duration)]
    playlists: dict[str, list[tuple[str, str, int]]] = {}
    seen_in_playlist: dict[str, set[str]] = {}
    for item in desired:
        if item.clip.id not in track_info:
            continue
        for source in item.sources:
            if source == "liked":
                name = "Liked Songs"
            elif source.startswith("playlist:"):
                name = source_to_name.get(source, source)
            else:
                continue
            if name not in playlists:
                order = playlist_order.get(source)
                if order:
                    playlists[name] = [track_info[cid] for cid in order if cid in track_info]
                    seen_in_playlist[name] = {cid for cid in order if cid in track_info}
                else:
                    playlists[name] = []
                    seen_in_playlist[name] = set()
            if not playlist_order.get(source):
                if item.clip.id not in seen_in_playlist.get(name, set()):
                    playlists[name].append(track_info[item.clip.id])
                    seen_in_playlist.setdefault(name, set()).add(item.clip.id)

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

    Structure: <display_name>/<title>/<display_name>-<title> [<clip_short>].<ext>
    Uses clip ID prefix for uniqueness when multiple clips share the same title.
    """
    artist = _sanitise_filename(clip.display_name or "Suno")
    title = _sanitise_filename(clip.title or "untitled")
    clip_short = clip.id[:8]
    ext = "flac" if quality == QUALITY_HIGH else "mp3"
    return f"{artist}/{title}/{artist}-{title} [{clip_short}].{ext}"


def _add_clip(
    clip_map: dict[str, DownloadItem],
    clip: SunoClip,
    source: str,
    quality: str,
) -> None:
    if clip.id in clip_map:
        item = clip_map[clip.id]
        item.sources.append(source)
        if quality == QUALITY_HIGH:
            item.quality = QUALITY_HIGH
    else:
        clip_map[clip.id] = DownloadItem(clip=clip, sources=[source], quality=quality)


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



_SOURCE_MODE_KEYS: dict[str, str] = {
    "liked": CONF_DOWNLOAD_MODE_LIKED,
    "my_songs": CONF_DOWNLOAD_MODE_MY_SONGS,
}


def _get_source_mode(source: str, options: dict[str, Any]) -> str:
    """Return the configured download mode for a source tag."""
    if source.startswith("playlist:"):
        key: str | None = CONF_DOWNLOAD_MODE_PLAYLISTS
    else:
        key = _SOURCE_MODE_KEYS.get(source)
    if key is None:
        return DOWNLOAD_MODE_MIRROR
    return str(options.get(key, DEFAULT_DOWNLOAD_MODE))


def _source_preserves_files(source: str, options: dict[str, Any]) -> bool:
    """Return True if the source mode keeps files permanently (archive only)."""
    return _get_source_mode(source, options) == DOWNLOAD_MODE_ARCHIVE


def _clip_entry(item: DownloadItem, rel_path: str, file_size: int) -> dict[str, Any]:
    """Build a clips_state dict entry for a download item."""
    return {
        "path": rel_path,
        "title": item.clip.title,
        "created": item.clip.created_at[:10] if item.clip.created_at else None,
        "sources": item.sources,
        "size": file_size,
        "meta_hash": clip_meta_hash(item.clip),
        "quality": item.quality,
    }


async def _update_cover_art(
    hass: HomeAssistant, session: Any, image_url: str, cover_path: Path, hash_path: Path
) -> bool:
    """Check image URL hash and write cover.jpg if changed. Returns True if updated."""
    url_hash = hashlib.md5(image_url.encode()).hexdigest()[:12]  # noqa: S324
    existing_hash = ""
    if await hass.async_add_executor_job(hash_path.exists):
        existing_hash = (await hass.async_add_executor_job(hash_path.read_text)).strip()
    if url_hash == existing_hash:
        return False
    image_data = await fetch_album_art(session, image_url)
    if image_data:
        await hass.async_add_executor_job(cover_path.parent.mkdir, 0o755, True, True)
        await _write_file(hass, cover_path, image_data)
        await hass.async_add_executor_job(hash_path.write_text, url_hash)
        return True
    return False


class SunoDownloadManager:
    """Manages background file downloads to a local directory."""

    def __init__(self, hass: HomeAssistant, store_key: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, store_key)
        self._state: dict[str, Any] = {"clips": {}, "last_download": None}
        self._cache: SunoCache | None = None
        self._coordinator: SunoCoordinator | None = None
        self._download_path = ""
        self._download_videos = True
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
        mgr._download_path = entry.options.get(CONF_DOWNLOAD_PATH, "")
        mgr._download_videos = entry.options.get(CONF_DOWNLOAD_VIDEOS, True)
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

            def _maybe_remove_service() -> None:
                remaining = [
                    e for e in hass.config_entries.async_entries(DOMAIN)
                    if e.entry_id != entry.entry_id
                ]
                if not remaining:
                    hass.services.async_remove(DOMAIN, _SERVICE_DOWNLOAD)

            entry.async_on_unload(_maybe_remove_service)

        async def _on_ha_started(_event: Any) -> None:
            """Run initial sync once Home Assistant is fully started."""
            _LOGGER.info("Home Assistant started — beginning initial sync")
            await mgr.async_download(dict(entry.options), client, initial=True)

        from homeassistant.helpers.start import async_at_started  # noqa: PLC0415

        async_at_started(hass, _on_ha_started)
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
        initial: bool = False,
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
            await self._run_download(options, client, download_path, force, coordinator_data, initial=initial)
        except asyncio.CancelledError:
            _LOGGER.info("Download cancelled")
            raise
        except Exception:
            _LOGGER.exception("Download failed")
            self._errors += 1
        finally:
            self._running = False
            self._notify_coordinator()

    def _notify_coordinator(self) -> None:
        """Push sensor updates via the coordinator without re-triggering sync."""
        if self._coordinator and self._coordinator.data:
            self._updating_sensors = True
            try:
                self._coordinator.async_set_updated_data(self._coordinator.data)
            finally:
                self._updating_sensors = False

    async def _run_download(
        self,
        options: dict[str, Any],
        client: Any,
        download_path: str,
        force: bool,
        coordinator_data: SunoData | None = None,
        initial: bool = False,
    ) -> None:
        base = Path(download_path)
        # Clean up legacy .trash directory if it exists
        trash_dir = base / ".trash"
        if await self.hass.async_add_executor_job(trash_dir.is_dir):
            import shutil  # noqa: PLC0415

            await self.hass.async_add_executor_job(shutil.rmtree, str(trash_dir), True)
            _LOGGER.info("Removed legacy .trash directory")
        self._state.pop("trash", None)
        desired, preserved_ids, source_to_name, playlist_order = await self._build_desired(
            options, client, coordinator_data
        )
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
        # Migrate files to new paths if the path format changed
        migrated = 0
        for item in desired:
            if item.clip.id not in clips_state:
                continue
            existing = clips_state[item.clip.id]
            old_path = existing.get("path", "")
            new_path = _clip_path(item.clip, existing.get("quality", item.quality))
            if old_path and old_path != new_path:
                old_file = base / old_path
                new_file = base / new_path
                if await self.hass.async_add_executor_job(old_file.exists):
                    await self.hass.async_add_executor_job(new_file.parent.mkdir, 0o755, True, True)
                    await self.hass.async_add_executor_job(old_file.rename, new_file)
                    # Also move video if it exists
                    old_video = old_file.with_suffix(".mp4")
                    if await self.hass.async_add_executor_job(old_video.exists):
                        await self.hass.async_add_executor_job(old_video.rename, new_file.with_suffix(".mp4"))
                    existing["path"] = new_path
                    migrated += 1
                    _cleanup_empty_dirs(base, old_file)
        if migrated:
            _LOGGER.info("Migrated %d files to new path structure", migrated)

        to_delete = []
        for cid in clips_state:
            if cid in seen_ids or cid in preserved_ids:
                continue
            entry = clips_state[cid]
            sources = entry.get("sources", [])
            # Only delete if NO source preserves files (archive mode)
            # Empty sources → all() returns True → delete (orphan cleanup)
            if all(not _source_preserves_files(src, options) for src in sources):
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
            self._pending = 0
            return
        label = "Initial sync" if initial else "Syncing"
        if initial:
            _LOGGER.info("Initial sync: %d files to download", len(to_download))
        downloaded = 0
        reconciled = 0
        cover_dirs: set[str] = set()
        for item in to_download:
            rel_path = _clip_path(item.clip, item.quality)
            target = base / rel_path
            if not force and await self.hass.async_add_executor_job(target.exists):
                stat = await self.hass.async_add_executor_job(target.stat)
                if stat.st_size == 0:
                    _LOGGER.warning("Empty file on disk, re-downloading: %s", rel_path)
                else:
                    clips_state[item.clip.id] = _clip_entry(item, rel_path, stat.st_size)
                    reconciled += 1
                    continue
            if (file_size := await self._download_clip(client, item, base, rel_path)) is not None:
                clips_state[item.clip.id] = _clip_entry(item, rel_path, file_size)
                downloaded += 1
                # Track directory for cover.jpg writing
                cover_dirs.add(str(target.parent))
            else:
                self._errors += 1
            self._pending = max(0, len(to_download) - downloaded - reconciled)
            self._last_result = f"{label} ({self._pending} remaining)" if self._pending > 0 else label
            self._notify_coordinator()
        if reconciled:
            _LOGGER.info("Reconciled %d files already on disk", reconciled)

        # Reconcile cover art for all clips (fixes stale cover.jpg files)
        session = async_get_clientsession(self.hass)
        covers_fixed = 0
        for item in desired:
            entry = clips_state.get(item.clip.id)
            if not entry or not entry.get("path"):
                continue
            image_url = item.clip.image_large_url or item.clip.image_url or None
            if not image_url:
                continue
            target = base / entry["path"]
            if await _update_cover_art(
                self.hass, session, image_url, target.parent / "cover.jpg", target.parent / ".cover_hash"
            ):
                covers_fixed += 1
        if covers_fixed:
            _LOGGER.info("Updated %d cover.jpg files", covers_fixed)
        for clip_id in to_delete:
            if (entry := clips_state.pop(clip_id, None)) and entry.get("path"):
                await _delete_file(self.hass, base, entry["path"])
                # Clean up sidecar files
                clip_file = base / entry["path"]
                sidecars = (
                    clip_file.with_suffix(".mp4"),
                    clip_file.parent / "cover.jpg",
                    clip_file.parent / ".cover_hash",
                )
                for sidecar in sidecars:
                    if await self.hass.async_add_executor_job(sidecar.exists):
                        try:
                            await self.hass.async_add_executor_job(sidecar.unlink)
                        except OSError:
                            pass
        self._state["clips"] = clips_state
        self._state["last_download"] = datetime.now(tz=UTC).isoformat()
        self._pending = max(0, len(to_download) - downloaded - reconciled)
        if self._pending > 0:
            self._last_result = f"Syncing ({self._pending} remaining)"
        else:
            self._last_result = _build_download_summary(downloaded, len(to_delete), meta_updates)
        self._state["last_result"] = self._last_result
        await self._save_state(base)
        if options.get(CONF_CREATE_PLAYLISTS):
            await self.hass.async_add_executor_job(
                _write_m3u8_playlists, base, clips_state, desired, source_to_name, playlist_order
            )

        if downloaded or to_delete or migrated or force:
            orphans = await self._reconcile_disk(base, clips_state)
            if orphans:
                _LOGGER.info("Reconciliation removed %d orphaned files", orphans)

    async def _reconcile_disk(self, base: Path, clips_state: dict[str, Any]) -> int:
        """Remove orphaned audio files not tracked in download state."""
        known_paths = {entry["path"] for entry in clips_state.values() if entry.get("path")}
        # Also track known video paths (audio path with .mp4 extension)
        for entry in clips_state.values():
            if entry.get("path"):
                video_rel = str(Path(entry["path"]).with_suffix(".mp4"))
                known_paths.add(video_rel)

        def _scan_and_remove(base_path: Path, known: set[str]) -> int:
            count = 0
            if not base_path.exists():
                return 0
            for f in base_path.rglob("*"):
                if not f.is_file():
                    continue
                # Skip non-audio files (manifest, playlists, tmp, hidden)
                if f.suffix.lower() not in (".flac", ".mp3", ".mp4"):
                    continue
                rel = str(f.relative_to(base_path))
                if rel not in known:
                    f.unlink(missing_ok=True)
                    _LOGGER.info("Reconciliation: removed orphan %s", rel)
                    count += 1
            # Clean orphaned sidecars in dirs with no audio files
            for d in base_path.rglob("*"):
                if not d.is_dir():
                    continue
                has_audio = any(f.suffix.lower() in (".flac", ".mp3") for f in d.iterdir() if f.is_file())
                if not has_audio:
                    for sidecar in ("cover.jpg", ".cover_hash"):
                        sc = d / sidecar
                        if sc.exists():
                            sc.unlink(missing_ok=True)
                            _LOGGER.info("Reconciliation: removed orphan sidecar %s", sc.relative_to(base_path))
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
    ) -> tuple[list[DownloadItem], set[str], dict[str, str], dict[str, list[str]]]:
        clip_map: dict[str, DownloadItem] = {}
        preserved: set[str] = set()
        source_to_name: dict[str, str] = {"liked": "Liked Songs"}
        playlist_order: dict[str, list[str]] = {}
        prev_clips = self._state.get("clips", {})
        if options.get(CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED):
            if _get_source_mode("liked", options) == DOWNLOAD_MODE_CACHE:
                pass  # cache only — skip download
            else:
                liked_quality = options.get(CONF_QUALITY_LIKED, QUALITY_HIGH)
                try:
                    liked = coordinator_data.liked_clips if coordinator_data else await client.get_liked_songs()
                    playlist_order["liked"] = [c.id for c in liked]
                    for clip in liked:
                        _add_clip(clip_map, clip, "liked", liked_quality)
                except Exception:
                    _LOGGER.warning("Failed to fetch liked songs for sync")
                    _preserve_by(preserved, prev_clips, lambda s: "liked" in s)
        sync_all = options.get(CONF_ALL_PLAYLISTS, DEFAULT_ALL_PLAYLISTS)
        selected_ids = options.get(CONF_PLAYLISTS, []) or []
        if not options.get(CONF_SHOW_PLAYLISTS, DEFAULT_SHOW_PLAYLISTS):
            pass  # playlists disabled
        elif _get_source_mode("playlist:", options) == DOWNLOAD_MODE_CACHE:
            pass  # cache only — skip download
        elif sync_all or selected_ids:
            playlist_quality = options.get(CONF_QUALITY_PLAYLISTS, QUALITY_HIGH)
            try:
                playlists = coordinator_data.playlists if coordinator_data else await client.get_playlists()
                for pl in playlists:
                    if not sync_all and pl.id not in selected_ids:
                        continue
                    tag = f"playlist:{pl.id}"
                    source_to_name[tag] = pl.name
                    try:
                        pl_clips = (
                            coordinator_data.playlist_clips.get(pl.id, [])
                            if coordinator_data
                            else await client.get_playlist_clips(pl.id)
                        )
                        playlist_order[tag] = [c.id for c in pl_clips]
                        for clip in pl_clips:
                            _add_clip(clip_map, clip, tag, playlist_quality)
                    except Exception:
                        _LOGGER.warning("Failed to fetch clips for playlist %s", pl.name)
                        _preserve_by(preserved, prev_clips, lambda s, t=tag: t in s)
            except Exception:
                _LOGGER.warning("Failed to fetch playlists for sync")
                _preserve_by(preserved, prev_clips, lambda s: any(x.startswith("playlist:") for x in s))
        if not options.get(CONF_SHOW_MY_SONGS, DEFAULT_SHOW_MY_SONGS):
            pass  # my songs disabled
        elif _get_source_mode("my_songs", options) == DOWNLOAD_MODE_CACHE:
            pass  # cache only — skip download
        else:
            my_songs_count = options.get(CONF_MY_SONGS_COUNT, DEFAULT_MY_SONGS_COUNT)
            my_songs_days = options.get(CONF_MY_SONGS_DAYS, DEFAULT_MY_SONGS_DAYS)
            minimum = int(options.get(CONF_MY_SONGS_MINIMUM, DEFAULT_MY_SONGS_MINIMUM))
            if my_songs_count or my_songs_days or minimum:
                my_songs_quality = options.get(CONF_QUALITY_MY_SONGS, QUALITY_STANDARD)
                try:
                    all_clips = coordinator_data.clips if coordinator_data else await client.get_all_songs()

                    # Start with all clips, then narrow
                    by_count: set[str] | None = None
                    if my_songs_count:
                        by_count = set(c.id for c in all_clips[: int(my_songs_count)])
                    by_days: set[str] | None = None
                    if my_songs_days:
                        cutoff = datetime.now(tz=UTC).timestamp() - int(my_songs_days) * 86400
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
                        my_songs_set = by_count & by_days  # intersection
                    elif by_count is not None:
                        my_songs_set = by_count
                    elif by_days is not None:
                        my_songs_set = by_days
                    else:
                        my_songs_set = set()

                    # Minimum floor: pad with most recent songs if below threshold
                    if minimum and len(my_songs_set) < minimum:
                        my_songs_set |= {c.id for c in all_clips[:minimum]}

                    for clip in all_clips:
                        if clip.id in my_songs_set:
                            _add_clip(clip_map, clip, "my_songs", my_songs_quality)
                except Exception:
                    _LOGGER.warning("Failed to fetch my songs for sync")
                    _preserve_by(preserved, prev_clips, lambda s: "my_songs" in s)
        preserved -= clip_map.keys()
        return list(clip_map.values()), preserved, source_to_name, playlist_order

    async def _download_clip(self, client: Any, item: DownloadItem, base: Path, rel_path: str) -> int | None:
        target = base / rel_path
        _LOGGER.info("Downloading: %s (%s)", item.clip.title, item.quality)
        clip = item.clip
        meta = clip.to_track_metadata()
        try:
            session = async_get_clientsession(self.hass)
            image_url = clip.image_large_url or clip.image_url or None
            image_data = await fetch_album_art(session, image_url) if image_url else None
            meta = TrackMetadata(
                title=meta.title, artist=meta.artist, album=meta.album,
                album_artist=meta.album_artist, date=meta.date, lyrics=meta.lyrics,
                comment=meta.comment, image_data=image_data,
                suno_style=meta.suno_style, suno_style_summary=meta.suno_style_summary,
                suno_model=meta.suno_model, suno_handle=meta.suno_handle,
                suno_parent=meta.suno_parent, suno_lineage=meta.suno_lineage,
            )

            if item.quality == QUALITY_HIGH:
                data = await download_and_transcode_to_flac(
                    client, session, get_ffmpeg_manager(self.hass).binary,
                    clip.id, meta, duration=clip.duration, image_url=image_url,
                )
                fmt = "flac"
            else:
                audio_url = clip.audio_url or f"{CDN_BASE_URL}/{clip.id}.mp3"
                data = await download_as_mp3(session, audio_url, meta)
                fmt = "mp3"

            if data is None:
                return None

            await _write_file(self.hass, target, data)
            _LOGGER.info("Downloaded: %s (%d bytes)", rel_path, len(data))

            # Write cover.jpg for Jellyfin album art discovery
            if image_data and image_url:
                await _update_cover_art(
                    self.hass, session, image_url, target.parent / "cover.jpg", target.parent / ".cover_hash"
                )

            # Download video if enabled and available
            if self._download_videos and clip.video_url:
                await self._download_video(session, clip.video_url, target)

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

    async def _download_video(self, session: Any, video_url: str, audio_target: Path) -> None:
        """Download the video file alongside the audio file."""
        video_path = audio_target.with_suffix(".mp4")
        if await self.hass.async_add_executor_job(video_path.exists):
            return
        try:
            async with session.get(video_url) as resp:
                if resp.status != 200:
                    _LOGGER.debug("Video download failed for %s: %d", video_url, resp.status)
                    return
                tmp_path = video_path.with_suffix(".mp4.tmp")
                try:
                    total = 0

                    def _open_tmp() -> Any:
                        tmp_path.parent.mkdir(parents=True, exist_ok=True)
                        return open(tmp_path, "wb")  # noqa: SIM115

                    fh = await self.hass.async_add_executor_job(_open_tmp)
                    try:
                        async for chunk in resp.content.iter_chunked(256 * 1024):
                            await self.hass.async_add_executor_job(fh.write, chunk)
                            total += len(chunk)
                    finally:
                        await self.hass.async_add_executor_job(fh.close)
                    await self.hass.async_add_executor_job(os.replace, str(tmp_path), str(video_path))
                    _LOGGER.info("Downloaded video: %s (%d bytes)", video_path.name, total)
                except BaseException:
                    await self.hass.async_add_executor_job(tmp_path.unlink, True)
                    raise
        except Exception:
            _LOGGER.debug("Failed to download video from %s", video_url)

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
                _LOGGER.warning("Failed to write manifest file", exc_info=True)
        await self.hass.async_add_executor_job(_write_manifest, base, self._state)
    # fmt: on
