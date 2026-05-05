"""Disk and manifest reconciliation passes for the Downloaded Library engine.

These free functions handle the two read-then-mutate passes that compare the
manifest state against what is actually on disk. Both run their I/O via a
Home Assistant executor job, so they take ``hass`` as their first argument.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def _reconcile_disk(hass: HomeAssistant, base: Path, clips_state: dict[str, Any]) -> int:
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

    return await hass.async_add_executor_job(_scan_and_remove, base, known_paths)


async def _reconcile_manifest(hass: HomeAssistant, base: Path, clips_state: dict[str, dict[str, Any]]) -> int:
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
    missing = await hass.async_add_executor_job(_check_paths, rel_paths)
    for cid in missing:
        entry = clips_state.get(cid)
        if not entry:
            continue
        entry["path"] = ""
        entry.pop("meta_hash", None)
    return len(missing)
