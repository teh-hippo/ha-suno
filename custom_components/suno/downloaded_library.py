"""Downloaded Library reconciliation for the Suno integration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from pathvalidate import sanitize_filename

from .audio import download_and_transcode_to_flac, download_as_mp3, fetch_album_art, retag_flac, retag_mp3
from .const import (
    CDN_BASE_URL,
    CONF_ALL_PLAYLISTS,
    CONF_CREATE_PLAYLISTS,
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_MY_SONGS,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
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
    DOWNLOAD_MODE_ARCHIVE,
    DOWNLOAD_MODE_CACHE,
    DOWNLOAD_MODE_MIRROR,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from .library_refresh import SunoData
from .models import SunoClip, TrackMetadata, clip_meta_hash

if TYPE_CHECKING:
    from .api import SunoClient

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
_MANIFEST_FILENAME = ".suno_download.json"
_MAX_FILENAME_LEN = 200


class RetagResult(Enum):
    """Outcome of attempting to re-tag an existing audio file on disk."""

    OK = "ok"
    MISSING = "missing"
    FAILED = "failed"


@dataclass
class DownloadItem:
    """A clip selected for the Downloaded Library."""

    clip: SunoClip
    sources: list[str]
    quality: str


@dataclass(frozen=True, slots=True)
class DownloadedLibraryStatus:
    """Published Downloaded Library status for Home Assistant consumers."""

    running: bool = False
    pending: int = 0
    errors: int = 0
    last_result: str = ""
    last_download: str | None = None
    file_count: int = 0
    size_mb: float = 0.0
    source_breakdown: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class DesiredDownloadPlan:
    """Desired Downloaded Library records and safety metadata."""

    items: list[DownloadItem]
    preserved_ids: set[str]
    source_to_name: dict[str, str]
    playlist_order: dict[str, list[str]]

    def as_legacy_tuple(self) -> tuple[list[DownloadItem], set[str], dict[str, str], dict[str, list[str]]]:
        """Return the tuple shape used by the legacy download manager tests."""
        return self.items, self.preserved_ids, self.source_to_name, self.playlist_order

    @classmethod
    def from_legacy_tuple(
        cls,
        value: tuple[list[DownloadItem], set[str], dict[str, str], dict[str, list[str]]],
    ) -> DesiredDownloadPlan:
        """Build a plan from the legacy tuple shape."""
        items, preserved_ids, source_to_name, playlist_order = value
        return cls(items, preserved_ids, source_to_name, playlist_order)


@dataclass(frozen=True, slots=True)
class RenderedAudio:
    """Rendered audio bytes and file format."""

    data: bytes
    fmt: str


class DownloadedLibraryStorage(Protocol):
    """Persistence adapter for Downloaded Library state."""

    async def async_load(self) -> dict[str, Any] | None: ...

    async def async_save(self, state: dict[str, Any]) -> None: ...


class HomeAssistantDownloadedLibraryStorage:
    """Downloaded Library storage backed by Home Assistant Store."""

    def __init__(self, hass: HomeAssistant, store_key: str) -> None:
        self.store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, store_key)

    async def async_load(self) -> dict[str, Any] | None:
        data = await self.store.async_load()
        return data if isinstance(data, dict) else None

    async def async_save(self, state: dict[str, Any]) -> None:
        await self.store.async_save(state)


class InMemoryDownloadedLibraryStorage:
    """In-memory Downloaded Library storage for tests."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state = state

    async def async_load(self) -> dict[str, Any] | None:
        return self.state

    async def async_save(self, state: dict[str, Any]) -> None:
        self.state = state


class DownloadedLibraryCache(Protocol):
    """Audio cache adapter used by the Downloaded Library."""

    async def async_get(self, clip_id: str, fmt: str, meta_hash: str) -> Path | None: ...

    async def async_put(self, clip_id: str, fmt: str, data: bytes, meta_hash: str) -> None: ...


class NullDownloadedLibraryCache:
    """No-op audio cache adapter."""

    async def async_get(self, _clip_id: str, _fmt: str, _meta_hash: str) -> Path | None:
        return None

    async def async_put(self, _clip_id: str, _fmt: str, _data: bytes, meta_hash: str) -> None:
        return


class SunoCacheDownloadedLibraryAdapter:
    """Adapter from the playback audio cache to Downloaded Library cache operations."""

    def __init__(self, cache: Any) -> None:
        self._cache = cache

    async def async_get(self, clip_id: str, fmt: str, meta_hash: str) -> Path | None:
        if not hasattr(self._cache, "async_get"):
            return None
        result = await self._cache.async_get(clip_id, fmt, meta_hash=meta_hash)
        return result if isinstance(result, Path) and result.is_file() else None

    async def async_put(self, clip_id: str, fmt: str, data: bytes, meta_hash: str) -> None:
        if not hasattr(self._cache, "async_put"):
            return
        await self._cache.async_put(clip_id, fmt, data, meta_hash=meta_hash)


class DownloadedLibraryAudio(Protocol):
    """Audio rendering adapter for Downloaded Library files."""

    async def fetch_image(self, image_url: str) -> bytes | None: ...

    async def render(
        self,
        clip: SunoClip,
        quality: str,
        meta: TrackMetadata,
        image_url: str | None,
    ) -> RenderedAudio | None: ...

    async def retag(self, target: Path, meta: TrackMetadata) -> bool: ...

    async def download_video(self, video_url: str, target: Path) -> None: ...


class HomeAssistantDownloadedLibraryAudio:
    """Production audio adapter backed by Suno transport and Home Assistant helpers."""

    def __init__(self, hass: HomeAssistant, client: SunoClient) -> None:
        self._hass = hass
        self._client = client

    async def fetch_image(self, image_url: str) -> bytes | None:
        session = async_get_clientsession(self._hass)
        return await fetch_album_art(session, image_url)

    async def render(
        self,
        clip: SunoClip,
        quality: str,
        meta: TrackMetadata,
        image_url: str | None,
    ) -> RenderedAudio | None:
        session = async_get_clientsession(self._hass)
        if quality == QUALITY_HIGH:
            data = await download_and_transcode_to_flac(
                self._client,
                session,
                get_ffmpeg_manager(self._hass).binary,
                clip.id,
                meta,
                duration=clip.duration,
                image_url=image_url,
            )
            return RenderedAudio(data, "flac") if data is not None else None

        audio_url = clip.audio_url or f"{CDN_BASE_URL}/{clip.id}.mp3"
        data = await download_as_mp3(session, audio_url, meta)
        return RenderedAudio(data, "mp3") if data is not None else None

    async def retag(self, target: Path, meta: TrackMetadata) -> bool:
        if target.suffix == ".flac":
            return await retag_flac(get_ffmpeg_manager(self._hass).binary, target, meta)
        return await self._hass.async_add_executor_job(retag_mp3, target, meta)

    async def download_video(self, video_url: str, target: Path) -> None:
        if await self._hass.async_add_executor_job(target.exists):
            return
        session = async_get_clientsession(self._hass)
        try:
            async with session.get(video_url) as resp:
                if resp.status != 200:
                    _LOGGER.debug("Video download failed for %s: %d", video_url, resp.status)
                    return
                tmp_path = target.with_suffix(".mp4.tmp")
                try:
                    total = 0

                    def _open_tmp() -> Any:
                        tmp_path.parent.mkdir(parents=True, exist_ok=True)
                        return open(tmp_path, "wb")  # noqa: SIM115

                    fh = await self._hass.async_add_executor_job(_open_tmp)
                    try:
                        async for chunk in resp.content.iter_chunked(256 * 1024):
                            await self._hass.async_add_executor_job(fh.write, chunk)
                            total += len(chunk)
                    finally:
                        await self._hass.async_add_executor_job(fh.close)
                    await self._hass.async_add_executor_job(os.replace, str(tmp_path), str(target))
                    _LOGGER.info("Downloaded video: %s (%d bytes)", target.name, total)
                except BaseException:
                    await self._hass.async_add_executor_job(tmp_path.unlink, True)
                    raise
        except Exception:
            _LOGGER.debug("Failed to download video from %s", video_url)


def _safe_name(name: str) -> str:
    """Sanitise a string for use as a file or directory name."""
    safe = sanitize_filename(name, replacement_text="_")
    return safe[:_MAX_FILENAME_LEN] if safe else "untitled"


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


def _write_m3u8_playlists(
    base: Path,
    clips_state: dict[str, Any],
    desired: list[DownloadItem],
    source_to_name: dict[str, str] | None = None,
    playlist_order: dict[str, list[str]] | None = None,
) -> None:
    """Write M3U8 playlist files for media library compatibility."""
    if source_to_name is None:
        source_to_name = {}
    if playlist_order is None:
        playlist_order = {}

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
            if not playlist_order.get(source) and item.clip.id not in seen_in_playlist.get(name, set()):
                playlists[name].append(track_info[item.clip.id])
                seen_in_playlist.setdefault(name, set()).add(item.clip.id)

    written: set[str] = set()
    for name, tracks in playlists.items():
        safe_name = name.replace("\n", " ").replace("\r", "")
        filename = f"{_safe_name(safe_name)}.m3u8"
        lines = [f"#EXTM3U\n#PLAYLIST:{safe_name}"]
        for abs_path, title, duration in tracks:
            lines.append(f"#EXTINF:{duration},{title}\n{abs_path}")
        try:
            (base / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")
            written.add(filename)
        except OSError:
            _LOGGER.warning("Failed to write playlist file: %s", filename)

    for existing in base.glob("*.m3u8"):
        if existing.name not in written:
            existing.unlink(missing_ok=True)


def _clip_path(clip: SunoClip, quality: str) -> str:
    """Build the relative audio file path for a clip."""
    artist = _safe_name(clip.display_name or "Suno")
    title = _safe_name(clip.title or "untitled")
    clip_short = clip.id[:8]
    ext = "flac" if quality == QUALITY_HIGH else "mp3"
    return f"{artist}/{title}/{artist}-{title} [{clip_short}].{ext}"


def _video_clip_path(clip: SunoClip) -> str:
    """Build the relative music-video sidecar path for a clip."""
    artist = _safe_name(clip.display_name or "Suno")
    title = _safe_name(clip.title or "untitled")
    clip_short = clip.id[:8]
    return f"{artist}/{title}/{artist}-{title} [{clip_short}].mp4"


def _add_clip(clip_map: dict[str, DownloadItem], clip: SunoClip, source: str, quality: str) -> None:
    """Add or update a desired clip, with high quality winning over standard."""
    if clip.id in clip_map:
        item = clip_map[clip.id]
        if source not in item.sources:
            item.sources.append(source)
        if quality == QUALITY_HIGH:
            item.quality = QUALITY_HIGH
    else:
        clip_map[clip.id] = DownloadItem(clip=clip, sources=[source], quality=quality)


def _preserve_by(preserved: set[str], prev_clips: dict[str, Any], pred: Any) -> None:
    preserved.update(cid for cid, e in prev_clips.items() if pred(e.get("sources", [])))


def _preserve_source(
    preserved: set[str],
    clip_map: dict[str, DownloadItem],
    prev_clips: dict[str, Any],
    source: str,
) -> None:
    """Preserve a stale source without deleting or removing it from records."""
    for clip_id, entry in prev_clips.items():
        if source not in entry.get("sources", []):
            continue
        if clip_id in clip_map:
            if source not in clip_map[clip_id].sources:
                clip_map[clip_id].sources.append(source)
        else:
            preserved.add(clip_id)


def _cleanup_empty_dirs(base: Path, target: Path) -> None:
    parent = target.parent
    while parent != base:
        try:
            parent.rmdir()
            parent = parent.parent
        except OSError:
            break


async def _write_file(hass: HomeAssistant, target: Path, data: bytes) -> None:
    """Atomically write bytes to a file."""

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
    """Delete a file relative to the Downloaded Library base path."""

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


def _get_source_mode(source: str, options: Mapping[str, Any]) -> str:
    """Return the configured download mode for a source tag."""
    if source.startswith("playlist:"):
        key: str | None = CONF_DOWNLOAD_MODE_PLAYLISTS
    else:
        key = _SOURCE_MODE_KEYS.get(source)
    if key is None:
        return DOWNLOAD_MODE_MIRROR
    return str(options.get(key, DEFAULT_DOWNLOAD_MODE))


def _source_preserves_files(source: str, options: Mapping[str, Any]) -> bool:
    """Return True if the source mode keeps files permanently."""
    return _get_source_mode(source, options) == DOWNLOAD_MODE_ARCHIVE


def _clip_entry(item: DownloadItem, rel_path: str, file_size: int) -> dict[str, Any]:
    """Build a stored Downloaded Library record for one clip."""
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
    hass: HomeAssistant,
    session: Any,
    image_url: str,
    cover_path: Path,
    hash_path: Path,
    track_path: Path | None = None,
) -> bool:
    """Write album art sidecars if the source image changed."""
    url_hash = hashlib.md5(image_url.encode()).hexdigest()[:12]  # noqa: S324
    existing_hash = ""
    if await hass.async_add_executor_job(hash_path.exists):
        existing_hash = (await hass.async_add_executor_job(hash_path.read_text)).strip()
    track_sidecar = track_path.with_suffix(".jpg") if track_path else None
    if url_hash == existing_hash:
        if (
            track_sidecar is not None
            and not await hass.async_add_executor_job(track_sidecar.exists)
            and await hass.async_add_executor_job(cover_path.exists)
        ):
            await _write_track_sidecar(hass, cover_path, track_sidecar)
        return False
    image_data = await fetch_album_art(session, image_url)
    if image_data:
        await hass.async_add_executor_job(cover_path.parent.mkdir, 0o755, True, True)
        await _write_file(hass, cover_path, image_data)
        await hass.async_add_executor_job(hash_path.write_text, url_hash)
        if track_sidecar is not None:
            await _write_track_sidecar(hass, cover_path, track_sidecar)
        return True
    return False


def _link_or_copy_sync(src: Path, dst: Path) -> None:
    """Hardlink ``src`` to ``dst``, falling back to copy if linking fails."""
    import shutil  # noqa: PLC0415

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        try:
            dst.unlink()
        except OSError:
            return
    try:
        os.link(src, dst)
    except OSError:
        try:
            shutil.copyfile(src, dst)
        except OSError:
            pass


async def _write_track_sidecar(hass: HomeAssistant, cover_path: Path, sidecar_path: Path) -> None:
    """Write a per-track JPG sidecar, preferring a hardlink."""
    await hass.async_add_executor_job(_link_or_copy_sync, cover_path, sidecar_path)


def _album_for_clip(clip: SunoClip, clip_index: dict[str, SunoClip]) -> str | None:
    """Resolve the album title for a clip."""
    if clip.album_title:
        return clip.album_title
    if not clip.is_remix:
        return None
    root_id = clip.root_ancestor_id
    if not root_id or root_id == clip.id:
        return None
    if (root_clip := clip_index.get(root_id)) is None:
        return f"Remixes of {root_id[:8]}"
    return root_clip.title


def _with_image(meta: TrackMetadata, image_data: bytes | None) -> TrackMetadata:
    """Return metadata with image data attached."""
    return TrackMetadata(
        title=meta.title,
        artist=meta.artist,
        album=meta.album,
        album_artist=meta.album_artist,
        date=meta.date,
        lyrics=meta.lyrics,
        comment=meta.comment,
        image_data=image_data,
        suno_style=meta.suno_style,
        suno_style_summary=meta.suno_style_summary,
        suno_model=meta.suno_model,
        suno_handle=meta.suno_handle,
        suno_parent=meta.suno_parent,
        suno_lineage=meta.suno_lineage,
    )


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
        desired = plan.items
        preserved_ids = plan.preserved_ids
        source_to_name = plan.source_to_name
        playlist_order = plan.playlist_order
        self._clip_index = {item.clip.id: item.clip for item in desired}
        clips_state = dict(self._state.get("clips", {}))
        to_download: list[DownloadItem] = []
        to_retag: list[DownloadItem] = []
        old_paths_after_download: dict[str, str] = {}

        missing_on_disk = await self._reconcile_manifest(base, clips_state)
        if missing_on_disk:
            _LOGGER.info("Manifest reconciliation: %d files missing on disk", missing_on_disk)

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
        if migrated:
            _LOGGER.info("Renamed %d files", migrated)
            self._state["clips"] = clips_state
            await self._save_state(base)

        seen_ids: set[str] = set()
        for item in desired:
            seen_ids.add(item.clip.id)
            existing = clips_state.get(item.clip.id)
            if item.clip.id not in clips_state or force or not (existing and existing.get("path")):
                to_download.append(item)
            else:
                existing = clips_state[item.clip.id]
                existing_quality = existing.get("quality", QUALITY_HIGH)
                if existing_quality != item.quality:
                    old_path = existing.get("path")
                    if old_path:
                        old_paths_after_download[item.clip.id] = str(old_path)
                    to_download.append(item)
                else:
                    existing["sources"] = item.sources
                    old_hash = existing.get("meta_hash", "")
                    new_hash = clip_meta_hash(item.clip)
                    if old_hash and new_hash != old_hash:
                        to_retag.append(item)

        to_delete: list[str] = []
        if allow_destructive:
            for cid in clips_state:
                if cid in seen_ids or cid in preserved_ids:
                    continue
                entry = clips_state[cid]
                sources = entry.get("sources", [])
                if all(not _source_preserves_files(src, options) for src in sources):
                    to_delete.append(cid)

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
                    clips_state[item.clip.id] = _clip_entry(item, rel_path, stat.st_size)
                    await self._delete_replaced_quality(base, old_paths_after_download, item, rel_path)
                    reconciled += 1
                    continue
            if (file_size := await self._download_clip(item, base, rel_path, force=force)) is not None:
                clips_state[item.clip.id] = _clip_entry(item, rel_path, file_size)
                await self._delete_replaced_quality(base, old_paths_after_download, item, rel_path)
                downloaded += 1
            else:
                self._errors += 1
            self._pending = max(0, len(to_download) - downloaded - reconciled)
            self._last_result = f"{label} ({self._pending} remaining)" if self._pending > 0 else label
            self._publish_status()
        if reconciled:
            _LOGGER.info("Reconciled %d files already on disk", reconciled)

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

        for clip_id in to_delete:
            if (entry := clips_state.pop(clip_id, None)) and entry.get("path"):
                await _delete_file(self.hass, base, entry["path"])
                await self._delete_sidecars(base, str(entry["path"]))

        self._state["clips"] = clips_state
        self._state["last_download"] = datetime.now(tz=UTC).isoformat()
        self._pending = max(0, len(to_download) - downloaded - reconciled)
        if self._pending > 0:
            self._last_result = f"Syncing ({self._pending} remaining)"
        else:
            self._last_result = _build_download_summary(downloaded, len(to_delete), 0, migrated, retagged)
        self._state["last_result"] = self._last_result
        await self._save_state(base)

        if options.get(CONF_CREATE_PLAYLISTS):
            await self.hass.async_add_executor_job(
                _write_m3u8_playlists, base, clips_state, desired, source_to_name, playlist_order
            )

        if allow_destructive and (downloaded or to_delete or migrated or force):
            orphans = await self._reconcile_disk(base, clips_state)
            if orphans:
                _LOGGER.info("Reconciliation removed %d orphaned files", orphans)

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
        clip_map: dict[str, DownloadItem] = {}
        preserved: set[str] = set()
        source_to_name: dict[str, str] = {"liked": "Liked Songs"}
        playlist_order: dict[str, list[str]] = {}
        prev_clips = self._state.get("clips", {})
        stale_sections = set(suno_library.stale_sections)
        stale_sources: set[str] = set()

        if (
            options.get(CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED)
            and _get_source_mode("liked", options) != DOWNLOAD_MODE_CACHE
        ):
            liked_quality = options.get(CONF_QUALITY_LIKED, QUALITY_HIGH)
            for clip in suno_library.liked_clips:
                _add_clip(clip_map, clip, "liked", liked_quality)
            playlist_order["liked"] = [c.id for c in suno_library.liked_clips]
            if "liked_clips" in stale_sections:
                stale_sources.add("liked")

        sync_all = options.get(CONF_ALL_PLAYLISTS, DEFAULT_ALL_PLAYLISTS)
        selected_ids = options.get(CONF_PLAYLISTS, []) or []
        playlists_enabled = options.get(CONF_SHOW_PLAYLISTS, DEFAULT_SHOW_PLAYLISTS)
        playlists_mode = _get_source_mode("playlist:", options)
        if playlists_enabled and playlists_mode != DOWNLOAD_MODE_CACHE and (sync_all or selected_ids):
            playlist_quality = options.get(CONF_QUALITY_PLAYLISTS, QUALITY_HIGH)
            if "playlists" in stale_sections or "playlist_clips" in stale_sections:
                stale_sources.update(
                    source
                    for entry in prev_clips.values()
                    for source in entry.get("sources", [])
                    if source.startswith("playlist:")
                )
            for playlist in suno_library.playlists:
                if not sync_all and playlist.id not in selected_ids:
                    continue
                tag = f"playlist:{playlist.id}"
                source_to_name[tag] = playlist.name
                playlist_stale = f"playlist_clips:{playlist.id}" in stale_sections
                clips = suno_library.playlist_clips.get(playlist.id, [])
                playlist_order[tag] = [c.id for c in clips]
                for clip in clips:
                    _add_clip(clip_map, clip, tag, playlist_quality)
                if playlist_stale:
                    stale_sources.add(tag)

        if (
            options.get(CONF_SHOW_MY_SONGS, DEFAULT_SHOW_MY_SONGS)
            and _get_source_mode("my_songs", options) != DOWNLOAD_MODE_CACHE
        ):
            my_songs_count = options.get(CONF_MY_SONGS_COUNT, DEFAULT_MY_SONGS_COUNT)
            my_songs_days = options.get(CONF_MY_SONGS_DAYS, DEFAULT_MY_SONGS_DAYS)
            minimum = int(options.get(CONF_MY_SONGS_MINIMUM, DEFAULT_MY_SONGS_MINIMUM))
            if my_songs_count or my_songs_days or minimum:
                my_songs_quality = options.get(CONF_QUALITY_MY_SONGS, QUALITY_STANDARD)
                for clip in self._filter_my_songs(suno_library.clips, my_songs_count, my_songs_days, minimum):
                    _add_clip(clip_map, clip, "my_songs", my_songs_quality)
                if "clips" in stale_sections:
                    stale_sources.add("my_songs")

        for source in sorted(stale_sources):
            _preserve_source(preserved, clip_map, prev_clips, source)
        preserved -= clip_map.keys()
        return DesiredDownloadPlan(list(clip_map.values()), preserved, source_to_name, playlist_order)

    @staticmethod
    def _filter_my_songs(
        all_clips: list[SunoClip],
        my_songs_count: Any,
        my_songs_days: Any,
        minimum: int,
    ) -> list[SunoClip]:
        by_count: set[str] | None = None
        if my_songs_count:
            by_count = {c.id for c in all_clips[: int(my_songs_count)]}

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

        if by_count is not None and by_days is not None:
            my_songs_set = by_count & by_days
        elif by_count is not None:
            my_songs_set = by_count
        elif by_days is not None:
            my_songs_set = by_days
        else:
            my_songs_set = set()

        if minimum and len(my_songs_set) < minimum:
            my_songs_set |= {c.id for c in all_clips[:minimum]}

        return [clip for clip in all_clips if clip.id in my_songs_set]

    async def _reconcile_disk(self, base: Path, clips_state: dict[str, Any]) -> int:
        """Remove orphaned audio and video files not tracked in download state."""
        known_paths = {entry["path"] for entry in clips_state.values() if entry.get("path")}
        for entry in clips_state.values():
            if entry.get("path"):
                known_paths.add(str(Path(entry["path"]).with_suffix(".mp4")))

        def _scan_and_remove(base_path: Path, known: set[str]) -> int:
            count = 0
            if not base_path.exists():
                return 0
            for f in base_path.rglob("*"):
                if not f.is_file() or f.suffix.lower() not in (".flac", ".mp3", ".mp4"):
                    continue
                rel = str(f.relative_to(base_path))
                if rel not in known:
                    f.unlink(missing_ok=True)
                    _LOGGER.info("Reconciliation: removed orphan %s", rel)
                    count += 1
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
            for d in sorted(base_path.rglob("*"), reverse=True):
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
            return count

        return await self.hass.async_add_executor_job(_scan_and_remove, base, known_paths)

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

        def _check_paths(rel_paths: list[tuple[str, str]]) -> set[str]:
            missing: set[str] = set()
            for clip_id, rel_path in rel_paths:
                target = base / rel_path
                try:
                    if not target.is_file() or target.stat().st_size == 0:
                        missing.add(clip_id)
                except OSError:
                    missing.add(clip_id)
            return missing

        rel_paths: list[tuple[str, str]] = [
            (cid, entry["path"]) for cid, entry in clips_state.items() if isinstance(entry, dict) and entry.get("path")
        ]
        if not rel_paths:
            return 0
        missing = await self.hass.async_add_executor_job(_check_paths, rel_paths)
        for cid in missing:
            entry = clips_state.get(cid)
            if not entry:
                continue
            entry["path"] = ""
            entry.pop("meta_hash", None)
        return len(missing)

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
    "_build_download_summary",
    "_clip_path",
    "_get_source_mode",
    "_safe_name",
    "_source_preserves_files",
    "_video_clip_path",
    "_write_file",
    "_write_m3u8_playlists",
]
