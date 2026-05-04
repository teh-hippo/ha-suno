"""Filesystem primitives for the Downloaded Library.

Atomic writes, deletes that prune empty parent directories, and hardlink-with-
copy-fallback for sidecar duplication.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


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


def _link_or_copy_sync(src: Path, dst: Path) -> None:
    """Hardlink ``src`` to ``dst``, falling back to copy if linking fails."""
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


__all__ = [
    "_cleanup_empty_dirs",
    "_delete_file",
    "_link_or_copy_sync",
    "_write_file",
    "_write_track_sidecar",
]
