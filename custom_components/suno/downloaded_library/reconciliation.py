"""Disk and manifest reconciliation passes for the Downloaded Library engine.

These free functions handle the two read-then-mutate passes that compare the
manifest state against what is actually on disk. Both run their I/O via a
Home Assistant executor job, so they take ``hass`` as their first argument.

Cross-account safety is enforced at the runtime layer: the integration
refuses to load two entries whose download paths overlap or nest, so a
single loaded account exclusively owns its entire download tree. The
reconcile pass therefore walks the whole tree, but keeps a per-folder
``.cover_hash`` foreign-id check as defense-in-depth against legacy state
or manual fiddling that leaves unknown clip_ids in a sidecar.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from homeassistant.core import HomeAssistant

from ..const import VIDEO_ART_BOTH, VIDEO_ART_CONVERT, VIDEO_ART_DOWNLOAD
from .contracts import ManifestEntry

_LOGGER = logging.getLogger(__name__)


async def _reconcile_disk(
    hass: HomeAssistant,
    base: Path,
    clips_state: dict[str, ManifestEntry],
    video_art_mode: str = VIDEO_ART_BOTH,
) -> int:
    """Remove orphaned audio and video files not tracked in download state."""
    known_paths = {entry.path for entry in clips_state.values() if entry.path}
    keep_mp4 = video_art_mode in (VIDEO_ART_DOWNLOAD, VIDEO_ART_BOTH)
    keep_webp = video_art_mode in (VIDEO_ART_CONVERT, VIDEO_ART_BOTH)
    for entry in clips_state.values():
        if not entry.path:
            continue
        clip_path = Path(entry.path)
        if keep_mp4:
            known_paths.add(str(clip_path.with_suffix(".mp4")))
        if keep_webp:
            known_paths.add(str(clip_path.parent / "cover.webp"))

    def _scan_and_remove(base_path: Path, known: set[str]) -> int:
        count = 0
        if not base_path.exists():
            return 0
        for f in base_path.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in (".flac", ".mp3", ".mp4", ".webp"):
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
            if has_audio:
                continue
            # Defense-in-depth: if ``.cover_hash`` records clip_ids this account
            # does not know about, leave every folder sidecar in place. Within
            # a single, exclusively-owned tree this only fires after legacy
            # migrations or manual edits, so the rare cost is acceptable.
            cover_hash_path = d / ".cover_hash"
            if cover_hash_path.exists() and _cover_hash_has_foreign_ids(cover_hash_path, clips_state):
                continue
            for sidecar in ("cover.jpg", "cover.webp", ".cover_hash"):
                sc = d / sidecar
                if sc.exists():
                    sc.unlink(missing_ok=True)
                    _LOGGER.info("Reconciliation: removed orphan sidecar %s", sc.relative_to(base_path))
                    count += 1
        for d in sorted(base_path.rglob("*"), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        return count

    return await hass.async_add_executor_job(_scan_and_remove, base, known_paths)


def _cover_hash_has_foreign_ids(
    cover_hash_path: Path,
    clips_state: Mapping[str, ManifestEntry],
) -> bool:
    """Return True when ``.cover_hash`` has clip_ids outside this manifest.

    Imported lazily to avoid a circular import with ``cover_art``.
    """
    from .cover_art import CoverHashFile  # noqa: PLC0415

    try:
        raw = cover_hash_path.read_text()
    except OSError:
        return False
    parsed = CoverHashFile._parse(raw)
    if not parsed:
        return False
    known_ids = set(clips_state.keys())
    for clip_id in parsed:
        if clip_id and clip_id not in known_ids:
            return True
    return False


async def _reconcile_manifest(hass: HomeAssistant, base: Path, clips_state: dict[str, ManifestEntry]) -> int:
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

    rel_paths: list[tuple[str, str]] = [(cid, entry.path) for cid, entry in clips_state.items() if entry.path]
    if not rel_paths:
        return 0
    missing = await hass.async_add_executor_job(_check_paths, rel_paths)
    for cid in missing:
        entry = clips_state.get(cid)
        if entry is None:
            continue
        entry.clear_for_redownload()
    return len(missing)
