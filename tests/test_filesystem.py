"""Tests for filesystem primitives used by the Downloaded Library."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.suno.downloaded_library.filesystem import (
    _cleanup_empty_dirs,
    _delete_file,
    _link_or_copy_sync,
    _write_file,
    _write_track_sidecar,
)


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):  # noqa: PT004
    """Required for HA fixture."""


async def test_write_file_creates_parents_and_writes(hass: HomeAssistant, tmp_path: Path) -> None:
    """File is written atomically; parent directories are created on demand."""
    target = tmp_path / "deep" / "nested" / "song.flac"
    await _write_file(hass, target, b"fLaC" + b"\x00" * 10)

    assert target.exists()
    assert target.read_bytes() == b"fLaC" + b"\x00" * 10
    # No stale tmp left behind
    assert not (target.parent / "song.tmp").exists()


async def test_write_file_cleans_tmp_on_error(hass: HomeAssistant, tmp_path: Path) -> None:
    """An os.replace failure unlinks the .tmp file before re-raising."""
    target = tmp_path / "song.flac"
    with (
        patch("custom_components.suno.downloaded_library.filesystem.os.replace", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        await _write_file(hass, target, b"fLaC")
    assert not (target.with_suffix(".tmp")).exists()


async def test_delete_file_removes_and_prunes_parents(hass: HomeAssistant, tmp_path: Path) -> None:
    """Deletion of the only file in a directory removes the dir too."""
    nested_dir = tmp_path / "album"
    nested_dir.mkdir()
    target = nested_dir / "song.flac"
    target.write_bytes(b"x")

    await _delete_file(hass, tmp_path, "album/song.flac")

    assert not target.exists()
    assert not nested_dir.exists()


async def test_delete_file_no_op_when_missing(hass: HomeAssistant, tmp_path: Path) -> None:
    """Deleting a missing path is a no-op (no exception)."""
    await _delete_file(hass, tmp_path, "ghost.flac")
    # No assertion needed - just verifying it doesn't raise


async def test_delete_file_logs_warning_on_oserror(hass: HomeAssistant, tmp_path: Path, caplog) -> None:
    """Unlinking failures are logged as warnings, not raised."""
    target = tmp_path / "locked.flac"
    target.write_bytes(b"x")
    with (
        patch.object(Path, "unlink", side_effect=OSError("permission")),
        caplog.at_level("WARNING"),
    ):
        await _delete_file(hass, tmp_path, "locked.flac")
    assert any("Failed to delete" in rec.message for rec in caplog.records)


def test_cleanup_empty_dirs_stops_at_base(tmp_path: Path) -> None:
    """Pruning stops once it hits the base directory."""
    base = tmp_path
    nested = base / "a" / "b" / "c"
    nested.mkdir(parents=True)
    target = nested / "file"

    _cleanup_empty_dirs(base, target)
    # All empty intermediate dirs gone, base preserved
    assert not (base / "a").exists()
    assert base.exists()


def test_link_or_copy_sync_uses_hardlink_when_possible(tmp_path: Path) -> None:
    """A successful os.link creates a hardlink (same inode)."""
    src = tmp_path / "src.jpg"
    src.write_bytes(b"image-bytes")
    dst = tmp_path / "subdir" / "dst.jpg"

    _link_or_copy_sync(src, dst)

    assert dst.exists()
    assert dst.read_bytes() == b"image-bytes"
    # Hardlinked → same inode
    assert src.stat().st_ino == dst.stat().st_ino


def test_link_or_copy_sync_falls_back_to_copy_on_link_failure(tmp_path: Path) -> None:
    """A failing os.link falls back to shutil.copyfile."""
    src = tmp_path / "src.jpg"
    src.write_bytes(b"copied-bytes")
    dst = tmp_path / "dst.jpg"

    with patch("custom_components.suno.downloaded_library.filesystem.os.link", side_effect=OSError("xdev")):
        _link_or_copy_sync(src, dst)

    assert dst.read_bytes() == b"copied-bytes"
    # Copy → different inode
    assert src.stat().st_ino != dst.stat().st_ino


def test_link_or_copy_sync_replaces_existing_dst(tmp_path: Path) -> None:
    """An existing destination is unlinked before linking the new file."""
    src = tmp_path / "src.jpg"
    src.write_bytes(b"new-image")
    dst = tmp_path / "dst.jpg"
    dst.write_bytes(b"old-image")

    _link_or_copy_sync(src, dst)
    assert dst.read_bytes() == b"new-image"


def test_link_or_copy_sync_silent_when_unlink_fails(tmp_path: Path) -> None:
    """An unremovable existing dst causes the link to abort cleanly (no raise)."""
    src = tmp_path / "src.jpg"
    src.write_bytes(b"new-image")
    dst = tmp_path / "dst.jpg"
    dst.write_bytes(b"locked")

    with patch.object(Path, "unlink", side_effect=OSError("locked")):
        _link_or_copy_sync(src, dst)
    # dst still holds the old bytes - link was aborted
    assert dst.read_bytes() == b"locked"


def test_link_or_copy_sync_silent_when_both_link_and_copy_fail(tmp_path: Path) -> None:
    """When both os.link and shutil.copyfile fail, nothing is raised."""
    src = tmp_path / "src.jpg"
    src.write_bytes(b"image")
    dst = tmp_path / "dst.jpg"

    with (
        patch("custom_components.suno.downloaded_library.filesystem.os.link", side_effect=OSError("xdev")),
        patch("custom_components.suno.downloaded_library.filesystem.shutil.copyfile", side_effect=OSError("disk full")),
    ):
        _link_or_copy_sync(src, dst)
    assert not dst.exists()


async def test_write_track_sidecar_writes_via_link(hass: HomeAssistant, tmp_path: Path) -> None:
    """Sidecar write goes through link-or-copy via the executor."""
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"jpeg-bytes")
    sidecar = tmp_path / "track-sidecar.jpg"

    await _write_track_sidecar(hass, cover, sidecar)

    assert sidecar.read_bytes() == b"jpeg-bytes"
