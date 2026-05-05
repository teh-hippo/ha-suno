"""Downloaded Library reconciliation for the Suno integration."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import (
    CONF_CREATE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    DOWNLOAD_MODE_ARCHIVE,
    QUALITY_HIGH,
)
from ..models import SunoClip, SunoData, clip_meta_hash
from .audio_adapter import HomeAssistantDownloadedLibraryAudio
from .cache_adapter import NullDownloadedLibraryCache, SunoCacheDownloadedLibraryAdapter
from .contracts import (
    DesiredDownloadPlan,
    DownloadedLibraryAudio,
    DownloadedLibraryCache,
    DownloadedLibraryStatus,
    DownloadedLibraryStorage,
    DownloadItem,
    RenderedAudio,
    RetagResult,
)
from .cover_art import _update_cover_art
from .filesystem import (
    _cleanup_empty_dirs,
    _delete_file,
    _link_or_copy_sync,
    _write_file,
)
from .m3u8 import _write_m3u8_playlists
from .metadata import _album_for_clip, _with_image
from .paths import _clip_path, _safe_name, _video_clip_path
from .planning import _add_clip, _clip_entry, build_desired
from .reconciliation import _reconcile_disk as _reconcile_disk_fn
from .reconciliation import _reconcile_manifest as _reconcile_manifest_fn
from .source_modes import (
    _entry_source_modes,
    _get_source_mode,
    _source_modes_for,
    _source_preserves_files,
)
from .storage import HomeAssistantDownloadedLibraryStorage, InMemoryDownloadedLibraryStorage

_LOGGER = logging.getLogger(__name__)

_MANIFEST_FILENAME = ".suno_download.json"


def _build_download_summary(
    downloaded: int, removed: int, meta_updates: int, renamed: int = 0, retagged: int = 0
) -> str:
    """Build a human-readable summary of download results."""
    parts: list[str] = []
    if downloaded:
        parts.append(f"{downloaded} new song{'s' if downloaded != 1 else ''}")
    if renamed:
        parts.append(f"{renamed} renamed")
    if retagged:
        parts.append(f"{retagged} re-tagged")
    if meta_updates:
        parts.append(f"{meta_updates} metadata update{'s' if meta_updates != 1 else ''}")
    if removed:
        parts.append(f"{removed} removal{'s' if removed != 1 else ''}")
    return ", ".join(parts) if parts else "No change"


def _is_empty_suno_library(data: SunoData) -> bool:
    return not data.clips and not data.liked_clips and not data.playlists and not data.playlist_clips


class DownloadedLibrary:
    """Deep module that owns Downloaded Library reconciliation."""

    def __init__(
        self,
        hass: HomeAssistant,
        storage: DownloadedLibraryStorage,
        *,
        audio: DownloadedLibraryAudio | None = None,
        cache: DownloadedLibraryCache | None = None,
        status_callback: Any | None = None,
        download_path: str = "",
        download_videos: bool = True,
    ) -> None:
        self.hass = hass
        self._storage = storage
        self._audio = audio
        self._cache = cache or NullDownloadedLibraryCache()
        self._status_callback = status_callback
        self._state: dict[str, Any] = {"clips": {}, "last_download": None}
        self._download_path = download_path
        self._download_videos = download_videos
        self._running = False
        self._errors = self._pending = 0
        self._last_result = ""
        self._clip_index: dict[str, SunoClip] = {}

    @property
    def storage(self) -> DownloadedLibraryStorage:
        return self._storage

    @property
    def state(self) -> dict[str, Any]:
        return self._state

    @state.setter
    def state(self, value: dict[str, Any]) -> None:
        self._state = value
        self._last_result = value.get("last_result", self._last_result)

    @property
    def download_path(self) -> str:
        return self._download_path

    @download_path.setter
    def download_path(self, value: str) -> None:
        self._download_path = value

    @property
    def download_videos(self) -> bool:
        return self._download_videos

    @download_videos.setter
    def download_videos(self, value: bool) -> None:
        self._download_videos = value

    @property
    def audio(self) -> DownloadedLibraryAudio | None:
        return self._audio

    @audio.setter
    def audio(self, value: DownloadedLibraryAudio | None) -> None:
        self._audio = value

    @property
    def cache(self) -> DownloadedLibraryCache:
        return self._cache

    @cache.setter
    def cache(self, value: DownloadedLibraryCache | None) -> None:
        self._cache = value or NullDownloadedLibraryCache()

    @property
    def running(self) -> bool:
        return self._running

    @running.setter
    def running(self, value: bool) -> None:
        self._running = value

    @property
    def errors(self) -> int:
        return self._errors

    @errors.setter
    def errors(self, value: int) -> None:
        self._errors = value

    @property
    def pending(self) -> int:
        return self._pending

    @pending.setter
    def pending(self, value: int) -> None:
        self._pending = value

    @property
    def last_result(self) -> str:
        return self._last_result

    @last_result.setter
    def last_result(self, value: str) -> None:
        self._last_result = value

    @property
    def clip_index(self) -> dict[str, SunoClip]:
        return self._clip_index

    @clip_index.setter
    def clip_index(self, value: dict[str, SunoClip]) -> None:
        self._clip_index = value

    @property
    def last_download(self) -> str | None:
        return self._state.get("last_download") or self._state.get("last_sync")

    @property
    def total_files(self) -> int:
        return len(self._state.get("clips", {}))

    @property
    def library_size_mb(self) -> float:
        return round(sum(int(e.get("size", 0)) for e in self._state.get("clips", {}).values()) / 1048576, 1)

    @property
    def source_breakdown(self) -> dict[str, int]:
        from collections import Counter  # noqa: PLC0415

        counts: Counter[str] = Counter()
        for entry in self._state.get("clips", {}).values():
            for src in entry.get("sources", []):
                counts[src] += 1
        return dict(counts)

    @property
    def status(self) -> DownloadedLibraryStatus:
        return DownloadedLibraryStatus(
            running=self._running,
            pending=self._pending,
            errors=self._errors,
            last_result=self._last_result,
            last_download=self.last_download,
            file_count=self.total_files,
            size_mb=self.library_size_mb,
            source_breakdown=self.source_breakdown,
        )

    async def async_load(self) -> None:
        """Load persisted Downloaded Library state."""
        if (data := await self._storage.async_load()) and isinstance(data, dict):
            self.state = data

    def get_downloaded_path(self, clip_id: str, meta_hash: str = "") -> Path | None:
        """Return a downloaded file path if it exists and matches metadata."""
        if not self._download_path:
            return None
        if not (entry := self._state.get("clips", {}).get(clip_id)):
            return None
        if meta_hash and entry.get("meta_hash") and entry["meta_hash"] != meta_hash:
            return None
        path = Path(self._download_path) / str(entry["path"])
        return path if path.is_file() else None

    async def async_reconcile(
        self,
        options: Mapping[str, Any],
        suno_library: SunoData,
        *,
        force: bool = False,
        initial: bool = False,
        allow_destructive: bool = True,
        desired_plan: DesiredDownloadPlan | None = None,
    ) -> None:
        """Run a Downloaded Library reconciliation cycle."""
        if self._running:
            _LOGGER.debug("Downloaded Library reconciliation already running, skipping")
            return
        if not (download_path := options.get(CONF_DOWNLOAD_PATH) or self._download_path):
            _LOGGER.warning("No download_path configured")
            return
        self._download_path = str(download_path)
        self._running = True
        self._errors = self._pending = 0
        self._publish_status()
        try:
            await self._run_download(
                options,
                suno_library,
                str(download_path),
                force,
                initial=initial,
                allow_destructive=allow_destructive,
                desired_plan=desired_plan,
            )
        except asyncio.CancelledError:
            _LOGGER.info("Download cancelled")
            raise
        except Exception:
            _LOGGER.exception("Download failed")
            self._errors += 1
        finally:
            self._running = False
            self._publish_status()

    def _publish_status(self) -> None:
        if self._status_callback is not None:
            self._status_callback(self.status)

    async def _run_download(
        self,
        options: Mapping[str, Any],
        suno_library: SunoData,
        download_path: str,
        force: bool,
        *,
        initial: bool = False,
        allow_destructive: bool = True,
        desired_plan: DesiredDownloadPlan | None = None,
    ) -> None:
        base = Path(download_path)
        if not allow_destructive and _is_empty_suno_library(suno_library):
            self._last_result = "Waiting for Library Refresh"
            self._pending = 0
            _LOGGER.info("Skipping destructive Downloaded Library reconciliation until Library Refresh completes")
            return

        self._state.pop("trash", None)
        plan = desired_plan or self.build_desired(options, suno_library)
        self._clip_index = {item.clip.id: item.clip for item in plan.items}
        clips_state = dict(self._state.get("clips", {}))

        missing_on_disk = await self._reconcile_manifest(base, clips_state)
        if missing_on_disk:
            _LOGGER.info("Manifest reconciliation: %d files missing on disk", missing_on_disk)

        to_retag: list[DownloadItem] = []
        migrated = await self._migrate_renamed_paths(base, plan.items, clips_state, to_retag)
        if migrated:
            _LOGGER.info("Renamed %d files", migrated)
            self._state["clips"] = clips_state
            await self._save_state(base)

        to_download, old_paths_after_download, to_delete, seen_ids = self._plan_actions(
            options,
            plan,
            clips_state,
            to_retag,
            force=force,
            allow_destructive=allow_destructive,
        )

        self._pending = len(to_download)
        self._publish_status()
        _LOGGER.info(
            "Sync: %d to download, %d to re-tag, %d to remove, %d current",
            len(to_download),
            len(to_retag),
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

        retagged = await self._run_retags(base, to_retag, clips_state, to_download)

        downloaded, reconciled = await self._run_downloads(
            options, base, to_download, clips_state, old_paths_after_download, force=force, label=label
        )

        await self._sync_cover_art(base, plan.items, clips_state)

        await self._prune_removed_entries(base, to_delete, clips_state)

        await self._finalize_state(
            base,
            clips_state,
            plan,
            options,
            downloaded=downloaded,
            reconciled=reconciled,
            removed=len(to_delete),
            migrated=migrated,
            retagged=retagged,
            to_download_count=len(to_download),
        )

        if allow_destructive and (downloaded or to_delete or migrated or force):
            orphans = await self._reconcile_disk(base, clips_state)
            if orphans:
                _LOGGER.info("Reconciliation removed %d orphaned files", orphans)

    async def _migrate_renamed_paths(
        self,
        base: Path,
        desired: list[DownloadItem],
        clips_state: dict[str, Any],
        to_retag: list[DownloadItem],
    ) -> int:
        """Rename existing files whose target path changed (e.g. retitled clips).

        Mutates ``clips_state`` and ``to_retag`` in-place. Returns the number of
        successfully renamed files. Failures clear the entry's ``path`` and
        ``meta_hash`` so the file is re-downloaded on the download pass.
        """
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
                try:
                    if await self.hass.async_add_executor_job(old_file.exists):
                        await self.hass.async_add_executor_job(new_file.parent.mkdir, 0o755, True, True)
                        await self.hass.async_add_executor_job(old_file.rename, new_file)
                        existing["path"] = new_path
                        migrated += 1
                        to_retag.append(item)
                        _LOGGER.debug("Renamed: %s -> %s", old_path, new_path)
                        await self._move_sidecars(base, item.clip, old_file, new_file)
                        _cleanup_empty_dirs(base, old_file)
                except OSError:
                    _LOGGER.warning("Failed to rename: %s -> %s", old_path, new_path)
                    existing["path"] = ""
                    existing.pop("meta_hash", None)
        return migrated

    def _plan_actions(
        self,
        options: Mapping[str, Any],
        plan: DesiredDownloadPlan,
        clips_state: dict[str, Any],
        to_retag: list[DownloadItem],
        *,
        force: bool,
        allow_destructive: bool,
    ) -> tuple[list[DownloadItem], dict[str, str], list[str], set[str]]:
        """Classify desired clips into download/retag/unchanged + compute deletions.

        Mutates ``clips_state`` for unchanged entries (sources/source_modes refresh)
        and appends to ``to_retag`` for metadata-only changes. Returns the
        download list, old-paths bookkeeping for quality migrations, the deletion
        list, and the set of clip IDs seen in the desired plan.
        """
        to_download: list[DownloadItem] = []
        old_paths_after_download: dict[str, str] = {}
        seen_ids: set[str] = set()

        for item in plan.items:
            seen_ids.add(item.clip.id)
            existing = clips_state.get(item.clip.id)
            if item.clip.id not in clips_state or force or not (existing and existing.get("path")):
                to_download.append(item)
                continue
            existing = clips_state[item.clip.id]
            existing_quality = existing.get("quality", QUALITY_HIGH)
            if existing_quality != item.quality:
                if old_path := existing.get("path"):
                    old_paths_after_download[item.clip.id] = str(old_path)
                to_download.append(item)
            else:
                existing["sources"] = item.sources
                existing["source_modes"] = _source_modes_for(item.sources, options)
                old_hash = existing.get("meta_hash", "")
                new_hash = clip_meta_hash(item.clip)
                if old_hash and new_hash != old_hash:
                    to_retag.append(item)

        to_delete: list[str] = []
        if allow_destructive:
            for cid in clips_state:
                if cid in seen_ids or cid in plan.preserved_ids:
                    continue
                entry = clips_state[cid]
                sources = entry.get("sources", [])
                if all(not _source_preserves_files(src, options) for src in sources):
                    to_delete.append(cid)

        return to_download, old_paths_after_download, to_delete, seen_ids

    async def _run_retags(
        self,
        base: Path,
        to_retag: list[DownloadItem],
        clips_state: dict[str, Any],
        to_download: list[DownloadItem],
    ) -> int:
        """Re-tag existing files; queue missing/failed targets for re-download.

        Mutates ``clips_state`` (refreshing ``meta_hash`` on success or clearing
        ``path``/``meta_hash`` when the target is missing) and appends to
        ``to_download`` when a target needs to be re-fetched. Increments
        ``self._errors`` for retag failures other than missing targets.
        Returns the number of successfully retagged files.
        """
        retagged = 0
        retag_missing = 0
        for item in to_retag:
            existing = clips_state.get(item.clip.id)
            if not existing or not existing.get("path"):
                continue
            target = base / existing["path"]
            result = await self._retag_clip(item, target)
            if result is RetagResult.OK:
                existing["meta_hash"] = clip_meta_hash(item.clip)
                retagged += 1
                _LOGGER.debug("Re-tagged: %s", existing["path"])
            elif result is RetagResult.MISSING:
                _LOGGER.info("Re-tag target missing, re-downloading: %s", existing["path"])
                existing["path"] = ""
                existing.pop("meta_hash", None)
                to_download.append(item)
                retag_missing += 1
            else:
                self._errors += 1
        if retagged:
            _LOGGER.info("Re-tagged %d files", retagged)
        if retag_missing:
            _LOGGER.info("Queued %d missing files for re-download", retag_missing)
        return retagged

    async def _run_downloads(
        self,
        options: Mapping[str, Any],
        base: Path,
        to_download: list[DownloadItem],
        clips_state: dict[str, Any],
        old_paths_after_download: dict[str, str],
        *,
        force: bool,
        label: str,
    ) -> tuple[int, int]:
        """Execute the per-clip download/promote loop and publish progress."""
        downloaded = 0
        reconciled = 0
        for item in to_download:
            rel_path = _clip_path(item.clip, item.quality)
            target = base / rel_path
            if not force and await self.hass.async_add_executor_job(target.exists):
                stat = await self.hass.async_add_executor_job(target.stat)
                if stat.st_size == 0:
                    _LOGGER.warning("Empty file on disk, re-downloading: %s", rel_path)
                else:
                    clips_state[item.clip.id] = _clip_entry(item, rel_path, stat.st_size, options)
                    await self._delete_replaced_quality(base, old_paths_after_download, item, rel_path)
                    reconciled += 1
                    continue
            if (file_size := await self._download_clip(item, base, rel_path, force=force)) is not None:
                clips_state[item.clip.id] = _clip_entry(item, rel_path, file_size, options)
                await self._delete_replaced_quality(base, old_paths_after_download, item, rel_path)
                downloaded += 1
            else:
                self._errors += 1
            self._pending = max(0, len(to_download) - downloaded - reconciled)
            self._last_result = f"{label} ({self._pending} remaining)" if self._pending > 0 else label
            self._publish_status()
        if reconciled:
            _LOGGER.info("Reconciled %d files already on disk", reconciled)
        return downloaded, reconciled

    async def _sync_cover_art(self, base: Path, desired: list[DownloadItem], clips_state: dict[str, Any]) -> int:
        """Refresh cover.jpg + .cover_hash for every desired clip already on disk."""
        session = async_get_clientsession(self.hass)
        covers_fixed = 0
        for item in desired:
            entry = clips_state.get(item.clip.id)
            if not entry or not entry.get("path"):
                continue
            image_url = item.clip.image_large_url or item.clip.image_url or item.clip.video_cover_url or None
            if not image_url:
                continue
            target = base / entry["path"]
            if await _update_cover_art(
                self.hass,
                session,
                image_url,
                target.parent / "cover.jpg",
                target.parent / ".cover_hash",
                track_path=target,
            ):
                covers_fixed += 1
        if covers_fixed:
            _LOGGER.info("Updated %d cover.jpg files", covers_fixed)
        return covers_fixed

    async def _prune_removed_entries(self, base: Path, to_delete: list[str], clips_state: dict[str, Any]) -> None:
        """Drop deleted entries from manifest and clean their files + sidecars."""
        for clip_id in to_delete:
            if (entry := clips_state.pop(clip_id, None)) and entry.get("path"):
                await _delete_file(self.hass, base, entry["path"])
                await self._delete_sidecars(base, str(entry["path"]))

    async def _finalize_state(
        self,
        base: Path,
        clips_state: dict[str, Any],
        plan: DesiredDownloadPlan,
        options: Mapping[str, Any],
        *,
        downloaded: int,
        reconciled: int,
        removed: int,
        migrated: int,
        retagged: int,
        to_download_count: int,
    ) -> None:
        """Commit manifest, last_result, and timestamp; write m3u8 playlists."""
        self._state["clips"] = clips_state
        self._state["last_download"] = datetime.now(tz=UTC).isoformat()
        self._pending = max(0, to_download_count - downloaded - reconciled)
        if self._pending > 0:
            self._last_result = f"Syncing ({self._pending} remaining)"
        else:
            self._last_result = _build_download_summary(downloaded, removed, 0, migrated, retagged)
        self._state["last_result"] = self._last_result
        await self._save_state(base)

        if options.get(CONF_CREATE_PLAYLISTS):
            await self.hass.async_add_executor_job(
                _write_m3u8_playlists, base, clips_state, plan.items, plan.source_to_name, plan.playlist_order
            )

    async def _move_sidecars(self, base: Path, clip: SunoClip, old_file: Path, new_file: Path) -> None:
        old_video = old_file.with_suffix(".mp4")
        if await self.hass.async_add_executor_job(old_video.exists):
            new_video = base / _video_clip_path(clip)
            await self.hass.async_add_executor_job(new_video.parent.mkdir, 0o755, True, True)
            await self.hass.async_add_executor_job(old_video.rename, new_video)
        if old_file.parent != new_file.parent:
            for sidecar_name in ("cover.jpg", ".cover_hash"):
                old_sc = old_file.parent / sidecar_name
                if await self.hass.async_add_executor_job(old_sc.exists):
                    new_sc = new_file.parent / sidecar_name
                    await self.hass.async_add_executor_job(new_sc.parent.mkdir, 0o755, True, True)
                    await self.hass.async_add_executor_job(old_sc.rename, new_sc)
        old_track_jpg = old_file.with_suffix(".jpg")
        if await self.hass.async_add_executor_job(old_track_jpg.exists):
            new_track_jpg = new_file.with_suffix(".jpg")
            await self.hass.async_add_executor_job(new_track_jpg.parent.mkdir, 0o755, True, True)
            try:
                await self.hass.async_add_executor_job(old_track_jpg.rename, new_track_jpg)
            except OSError:
                _LOGGER.debug("Could not move track sidecar JPG for %s", old_file)

    async def _delete_replaced_quality(
        self,
        base: Path,
        old_paths_after_download: dict[str, str],
        item: DownloadItem,
        rel_path: str,
    ) -> None:
        if (old_path := old_paths_after_download.pop(item.clip.id, "")) and old_path != rel_path:
            await _delete_file(self.hass, base, old_path)

    async def _delete_sidecars(self, base: Path, rel_path: str) -> None:
        clip_file = base / rel_path
        sidecars = (
            clip_file.with_suffix(".mp4"),
            clip_file.with_suffix(".jpg"),
            clip_file.parent / "cover.jpg",
            clip_file.parent / ".cover_hash",
        )
        for sidecar in sidecars:
            if await self.hass.async_add_executor_job(sidecar.exists):
                try:
                    await self.hass.async_add_executor_job(sidecar.unlink)
                except OSError:
                    pass

    def build_desired(self, options: Mapping[str, Any], suno_library: SunoData) -> DesiredDownloadPlan:
        """Build the desired Downloaded Library records from a Suno Library."""
        return build_desired(options, suno_library, self._state.get("clips", {}))

    async def _reconcile_disk(self, base: Path, clips_state: dict[str, Any]) -> int:
        """Remove orphaned audio and video files not tracked in download state."""
        return await _reconcile_disk_fn(self.hass, base, clips_state)

    async def _download_clip(self, item: DownloadItem, base: Path, rel_path: str, *, force: bool = False) -> int | None:
        """Ensure a clip exists at the target path by promoting cache or rendering audio."""
        if self._audio is None:
            _LOGGER.warning("No audio adapter configured for Downloaded Library")
            return None

        target = base / rel_path
        clip = item.clip
        fmt = "flac" if item.quality == QUALITY_HIGH else "mp3"
        meta_hash = clip_meta_hash(clip)

        if not force and (cached_path := await self._cache.async_get(clip.id, fmt, meta_hash)) is not None:
            await self.hass.async_add_executor_job(_link_or_copy_sync, cached_path, target)
            if await self.hass.async_add_executor_job(target.exists):
                stat = await self.hass.async_add_executor_job(target.stat)
                _LOGGER.info("Promoted cached audio: %s (%d bytes)", rel_path, stat.st_size)
                return int(stat.st_size)

        _LOGGER.info("Downloading: %s (%s)", clip.title, item.quality)
        album_title = _album_for_clip(clip, self._clip_index)
        image_url = clip.image_large_url or clip.image_url or clip.video_cover_url or None
        image_data = await self._audio.fetch_image(image_url) if image_url else None
        meta = _with_image(clip.to_track_metadata(album=album_title), image_data)

        try:
            rendered = await self._audio.render(clip, item.quality, meta, image_url)
            if rendered is None:
                return None

            await _write_file(self.hass, target, rendered.data)
            _LOGGER.info("Downloaded: %s (%d bytes)", rel_path, len(rendered.data))

            if image_data and image_url:
                session = async_get_clientsession(self.hass)
                await _update_cover_art(
                    self.hass,
                    session,
                    image_url,
                    target.parent / "cover.jpg",
                    target.parent / ".cover_hash",
                    track_path=target,
                )

            if self._download_videos and clip.video_url:
                await self._audio.download_video(clip.video_url, base / _video_clip_path(clip))

            try:
                await self._cache.async_put(clip.id, rendered.fmt, rendered.data, meta_hash=meta_hash)
            except Exception:
                _LOGGER.debug("Cache write-through failed for %s", clip.id)

            return len(rendered.data)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Failed to download %s", clip.id)
            return None

    async def _reconcile_manifest(self, base: Path, clips_state: dict[str, dict[str, Any]]) -> int:
        """Clear manifest paths whose files are missing or empty on disk."""
        return await _reconcile_manifest_fn(self.hass, base, clips_state)

    async def _retag_clip(self, item: DownloadItem, target: Path) -> RetagResult:
        """Re-tag an existing downloaded file."""
        try:
            stat = await self.hass.async_add_executor_job(target.stat)
        except FileNotFoundError:
            return RetagResult.MISSING
        except OSError:
            _LOGGER.exception("Failed to stat re-tag target %s", target)
            return RetagResult.FAILED
        if stat.st_size == 0:
            return RetagResult.MISSING
        if self._audio is None:
            return RetagResult.FAILED

        meta = item.clip.to_track_metadata(album=_album_for_clip(item.clip, self._clip_index))
        try:
            ok = await self._audio.retag(target, meta)
        except Exception:
            _LOGGER.exception("Failed to re-tag %s", target)
            return RetagResult.FAILED
        return RetagResult.OK if ok else RetagResult.FAILED

    async def async_cleanup_disabled_downloads(
        self,
        options: Mapping[str, Any],
        previous_options: Mapping[str, Any] | None = None,
    ) -> None:
        """Remove Mirror-managed downloads when local downloads are disabled."""
        download_path = (
            options.get(CONF_DOWNLOAD_PATH) or (previous_options or {}).get(CONF_DOWNLOAD_PATH) or self._download_path
        )
        if not download_path:
            return

        self._download_path = str(download_path)
        base = Path(str(download_path))
        clips_state = dict(self._state.get("clips", {}))
        removed = 0
        preserved = 0

        for clip_id, entry in list(clips_state.items()):
            if not isinstance(entry, dict):
                clips_state.pop(clip_id, None)
                continue
            sources = [str(source) for source in entry.get("sources", [])]
            source_modes = _entry_source_modes(entry, sources, previous_options)
            archive_sources = [source for source in sources if source_modes.get(source) == DOWNLOAD_MODE_ARCHIVE]
            if archive_sources or (sources and not source_modes):
                preserved_sources = archive_sources or sources
                entry["sources"] = preserved_sources
                entry["source_modes"] = {
                    source: source_modes.get(source, DOWNLOAD_MODE_ARCHIVE) for source in preserved_sources
                }
                preserved += 1
                continue

            clips_state.pop(clip_id, None)
            if entry.get("path"):
                await _delete_file(self.hass, base, str(entry["path"]))
                await self._delete_sidecars(base, str(entry["path"]))
            removed += 1

        await self._delete_generated_playlists(base)
        self._state["clips"] = clips_state
        self._state["last_download"] = datetime.now(tz=UTC).isoformat()
        if removed and preserved:
            self._last_result = f"Downloads disabled: {removed} removed, {preserved} archived"
        elif removed:
            self._last_result = f"Downloads disabled: {removed} removed"
        elif preserved:
            self._last_result = f"Downloads disabled: {preserved} archived"
        else:
            self._last_result = "Downloads disabled"
        self._state["last_result"] = self._last_result
        await self._save_state(base)
        self._publish_status()

    async def _delete_generated_playlists(self, base: Path) -> None:
        """Remove generated playlist files from the Downloaded Library root."""

        def _delete_playlists(base_path: Path) -> None:
            if not base_path.exists():
                return
            for playlist in base_path.glob("*.m3u8"):
                try:
                    playlist.unlink(missing_ok=True)
                except OSError:
                    _LOGGER.warning("Failed to delete playlist file: %s", playlist)

        await self.hass.async_add_executor_job(_delete_playlists, base)

    async def cleanup_tmp_files(self, download_path: str) -> None:
        """Remove stale temporary files from the Downloaded Library directory."""

        def _cleanup(p: str) -> None:
            base = Path(p)
            if not base.exists():
                return
            for tmp in base.rglob("*.tmp"):
                tmp.unlink(missing_ok=True)
                _LOGGER.debug("Cleaned up: %s", tmp)

        await self.hass.async_add_executor_job(_cleanup, download_path)

    async def _save_state(self, base: Path) -> None:
        await self._storage.async_save(self._state)

        def _write_manifest(b: Path, state: dict[str, Any]) -> None:
            try:
                (b / _MANIFEST_FILENAME).write_text(json.dumps(state, indent=2))
            except OSError:
                _LOGGER.warning("Failed to write manifest file", exc_info=True)

        await self.hass.async_add_executor_job(_write_manifest, base, self._state)


__all__ = [
    "DesiredDownloadPlan",
    "DownloadItem",
    "DownloadedLibraryAudio",
    "DownloadedLibraryCache",
    "DownloadedLibrary",
    "DownloadedLibraryStatus",
    "DownloadedLibraryStorage",
    "HomeAssistantDownloadedLibraryAudio",
    "HomeAssistantDownloadedLibraryStorage",
    "InMemoryDownloadedLibraryStorage",
    "NullDownloadedLibraryCache",
    "RenderedAudio",
    "RetagResult",
    "SunoCacheDownloadedLibraryAdapter",
    "_add_clip",
    "_album_for_clip",
    "_build_download_summary",
    "_clip_path",
    "_get_source_mode",
    "_safe_name",
    "_source_preserves_files",
    "_video_clip_path",
    "_write_file",
    "_write_m3u8_playlists",
]
