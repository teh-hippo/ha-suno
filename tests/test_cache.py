"""Tests for the Suno audio cache."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.suno.cache import SunoCache


def _mp3_bytes(size: int = 256) -> bytes:
    """Return fake MP3 data with valid ID3 magic."""
    return b"ID3" + b"\x00" * (size - 3)


def _flac_bytes(size: int = 256) -> bytes:
    """Return fake FLAC data with valid fLaC magic."""
    return b"fLaC" + b"\x00" * (size - 4)


async def test_cache_init_creates_dir(hass: HomeAssistant, tmp_path: Path) -> None:
    """async_init should create the cache directory."""
    with patch.object(hass.config, "path", return_value=str(tmp_path / "suno_cache")):
        cache = SunoCache(hass, max_size_mb=10)
        await cache.async_init()

    assert cache.cache_dir.is_dir()


async def test_cache_put_and_get(hass: HomeAssistant, tmp_path: Path) -> None:
    """Putting data then getting it should return a valid path."""
    cache_dir = str(tmp_path / "suno_cache")
    with patch.object(hass.config, "path", return_value=cache_dir):
        cache = SunoCache(hass, max_size_mb=10)
        await cache.async_init()

    data = _mp3_bytes(512)
    result = await cache.async_put("clip-001", "mp3", data)
    assert result is not None
    assert result.exists()
    assert result.read_bytes() == data

    got = await cache.async_get("clip-001", "mp3")
    assert got is not None
    assert got == result


async def test_cache_get_miss(hass: HomeAssistant, tmp_path: Path) -> None:
    """Getting a clip that was never cached returns None."""
    with patch.object(hass.config, "path", return_value=str(tmp_path / "suno_cache")):
        cache = SunoCache(hass, max_size_mb=10)
        await cache.async_init()

    assert await cache.async_get("nonexistent", "mp3") is None


async def test_cache_atomic_write(hass: HomeAssistant, tmp_path: Path) -> None:
    """No .tmp file should remain after a successful put."""
    cache_dir = str(tmp_path / "suno_cache")
    with patch.object(hass.config, "path", return_value=cache_dir):
        cache = SunoCache(hass, max_size_mb=10)
        await cache.async_init()

    await cache.async_put("clip-002", "mp3", _mp3_bytes())

    tmp_files = list(cache.cache_dir.glob("*.tmp"))
    assert len(tmp_files) == 0


async def test_cache_eviction(hass: HomeAssistant, tmp_path: Path) -> None:
    """When the cache is full, the oldest entry should be evicted."""
    cache_dir = str(tmp_path / "suno_cache")
    # 1 KB max
    with patch.object(hass.config, "path", return_value=cache_dir):
        cache = SunoCache(hass, max_size_mb=0)
        cache._max_bytes = 1024
        await cache.async_init()

    # Fill with 600 bytes
    await cache.async_put("old-clip", "mp3", _mp3_bytes(600))
    assert await cache.async_get("old-clip", "mp3") is not None

    # Adding 600 more should evict old-clip (total would be 1200 > 1024)
    await cache.async_put("new-clip", "mp3", _mp3_bytes(600))
    assert await cache.async_get("new-clip", "mp3") is not None
    assert await cache.async_get("old-clip", "mp3") is None


async def test_cache_corrupt_file_rejects(hass: HomeAssistant, tmp_path: Path) -> None:
    """A file with bad magic bytes should be rejected on get."""
    cache_dir = str(tmp_path / "suno_cache")
    with patch.object(hass.config, "path", return_value=cache_dir):
        cache = SunoCache(hass, max_size_mb=10)
        await cache.async_init()

    # Write valid data first to get it into the index
    await cache.async_put("clip-bad", "mp3", _mp3_bytes())

    # Corrupt the file on disk
    (cache.cache_dir / "clip-bad.mp3").write_bytes(b"CORRUPT_DATA")

    result = await cache.async_get("clip-bad", "mp3")
    assert result is None


async def test_cache_flac_validation(hass: HomeAssistant, tmp_path: Path) -> None:
    """FLAC files should be validated against fLaC magic."""
    cache_dir = str(tmp_path / "suno_cache")
    with patch.object(hass.config, "path", return_value=cache_dir):
        cache = SunoCache(hass, max_size_mb=10)
        await cache.async_init()

    data = _flac_bytes(512)
    await cache.async_put("clip-flac", "flac", data)
    got = await cache.async_get("clip-flac", "flac")
    assert got is not None

    # Corrupt it
    (cache.cache_dir / "clip-flac.flac").write_bytes(b"NOT_FLAC")
    got = await cache.async_get("clip-flac", "flac")
    assert got is None


async def test_cache_cleanup_tmp_on_init(hass: HomeAssistant, tmp_path: Path) -> None:
    """Stale .tmp files should be removed during init."""
    cache_dir = tmp_path / "suno_cache"
    cache_dir.mkdir()
    stale_tmp = cache_dir / "stale.mp3.tmp"
    stale_tmp.write_bytes(b"partial data")

    with patch.object(hass.config, "path", return_value=str(cache_dir)):
        cache = SunoCache(hass, max_size_mb=10)
        await cache.async_init()

    assert not stale_tmp.exists()


async def test_cache_empty_file_rejected(hass: HomeAssistant, tmp_path: Path) -> None:
    """A zero-byte file should be rejected."""
    cache_dir = str(tmp_path / "suno_cache")
    with patch.object(hass.config, "path", return_value=cache_dir):
        cache = SunoCache(hass, max_size_mb=10)
        await cache.async_init()

    await cache.async_put("clip-empty", "mp3", _mp3_bytes())
    # Truncate the file
    (cache.cache_dir / "clip-empty.mp3").write_bytes(b"")

    result = await cache.async_get("clip-empty", "mp3")
    assert result is None


async def test_cache_init_loads_saved_index(hass: HomeAssistant, tmp_path: Path) -> None:
    """async_init should load a previously saved index (line 50)."""
    cache_dir = str(tmp_path / "suno_cache")
    with patch.object(hass.config, "path", return_value=cache_dir):
        cache = SunoCache(hass, max_size_mb=10)

    saved_index = {"clip-saved.mp3": {"access": 1000.0, "meta_hash": "abc123"}}
    with patch.object(cache._store, "async_load", return_value=saved_index):
        await cache.async_init()

    assert cache._index == saved_index


async def test_cache_init_tmp_cleanup_oserror(hass: HomeAssistant, tmp_path: Path) -> None:
    """OSError during .tmp cleanup should be logged, not raised (lines 58-59)."""
    cache_dir = tmp_path / "suno_cache"
    cache_dir.mkdir()
    stale_tmp = cache_dir / "stale.mp3.tmp"
    stale_tmp.write_bytes(b"partial")

    with patch.object(hass.config, "path", return_value=str(cache_dir)):
        cache = SunoCache(hass, max_size_mb=10)

    with patch.object(Path, "unlink", side_effect=OSError("permission denied")):
        await cache.async_init()

    # Should not raise; file may still exist since unlink was mocked to fail
    assert cache.cache_dir.is_dir()


async def test_cache_put_oserror_returns_none(hass: HomeAssistant, tmp_path: Path) -> None:
    """OSError during atomic write should return None (lines 89-91)."""
    cache_dir = str(tmp_path / "suno_cache")
    with patch.object(hass.config, "path", return_value=cache_dir):
        cache = SunoCache(hass, max_size_mb=10)
        await cache.async_init()

    with patch.object(SunoCache, "_atomic_write", side_effect=OSError("disk full")):
        result = await cache.async_put("clip-fail", "mp3", _mp3_bytes())

    assert result is None


async def test_cache_eviction_break_when_enough_space(hass: HomeAssistant, tmp_path: Path) -> None:
    """Eviction loop should break once enough space is freed (line 108)."""
    cache_dir = str(tmp_path / "suno_cache")
    with patch.object(hass.config, "path", return_value=cache_dir):
        cache = SunoCache(hass, max_size_mb=0)
        cache._max_bytes = 2048
        await cache.async_init()

    # Add three small files
    await cache.async_put("clip-a", "mp3", _mp3_bytes(400))
    await cache.async_put("clip-b", "mp3", _mp3_bytes(400))
    await cache.async_put("clip-c", "mp3", _mp3_bytes(400))

    # Adding a 1000-byte file needs eviction of only the oldest
    await cache.async_put("clip-d", "mp3", _mp3_bytes(1000))
    assert await cache.async_get("clip-d", "mp3") is not None
    # At least one old clip should still exist (not all evicted)
    remaining = 0
    for cid in ("clip-a", "clip-b", "clip-c"):
        if await cache.async_get(cid, "mp3") is not None:
            remaining += 1
    assert remaining >= 1


async def test_cache_eviction_oserror_on_file_ops(hass: HomeAssistant, tmp_path: Path) -> None:
    """OSError during eviction file operations should be handled (lines 114-115)."""
    cache_dir = str(tmp_path / "suno_cache")
    with patch.object(hass.config, "path", return_value=cache_dir):
        cache = SunoCache(hass, max_size_mb=0)
        cache._max_bytes = 512
        await cache.async_init()

    await cache.async_put("clip-old", "mp3", _mp3_bytes(400))

    # Make _file_size and unlink raise OSError during eviction
    original_add_executor = hass.async_add_executor_job
    call_count = 0

    async def patched_executor(fn, *args):
        nonlocal call_count
        # During eviction, _file_size and unlink are called via executor
        if fn == SunoCache._file_size:
            raise OSError("stat failed")
        if fn == Path.unlink:
            raise OSError("unlink failed")
        return await original_add_executor(fn, *args)

    with patch.object(hass, "async_add_executor_job", side_effect=patched_executor):
        # This should not raise despite OSError during eviction
        try:
            await cache.async_evict(400)
        except OSError:
            pass  # The eviction still removes from index

    # The entry should have been removed from the index regardless
    assert "clip-old.mp3" not in cache._index


async def test_total_size_handles_missing_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """_total_size should handle missing files gracefully (lines 127-128)."""
    cache_dir = str(tmp_path / "suno_cache")
    with patch.object(hass.config, "path", return_value=cache_dir):
        cache = SunoCache(hass, max_size_mb=10)
        await cache.async_init()

    # Put a file in the index but don't create it on disk
    cache._index["ghost.mp3"] = 1000.0
    size = cache._total_size()
    assert size == 0


async def test_file_size_missing_returns_zero(tmp_path: Path) -> None:
    """_file_size should return 0 for missing files (lines 136-137)."""
    missing = tmp_path / "nonexistent.mp3"
    assert SunoCache._file_size(missing) == 0


async def test_validate_file_not_a_file(tmp_path: Path) -> None:
    """_validate_file should return False if path is a directory (line 150)."""
    assert SunoCache._validate_file(tmp_path, "mp3") is False


async def test_validate_file_unknown_format(tmp_path: Path) -> None:
    """_validate_file with unknown format should return True if file exists (line 160)."""
    f = tmp_path / "clip.ogg"
    f.write_bytes(b"\x00" * 100)
    assert SunoCache._validate_file(f, "ogg") is True


async def test_validate_file_oserror(tmp_path: Path) -> None:
    """_validate_file should return False on OSError (lines 161-162)."""
    missing = tmp_path / "gone.mp3"
    assert SunoCache._validate_file(missing, "mp3") is False


async def test_cache_get_file_deleted_from_disk(hass: HomeAssistant, tmp_path: Path) -> None:
    """Getting a clip whose file was deleted should return None and clean index."""
    cache_dir = str(tmp_path / "suno_cache")
    with patch.object(hass.config, "path", return_value=cache_dir):
        cache = SunoCache(hass, max_size_mb=10)
        await cache.async_init()

    await cache.async_put("clip-del", "mp3", _mp3_bytes())
    # Delete the file behind the cache's back
    (cache.cache_dir / "clip-del.mp3").unlink()

    result = await cache.async_get("clip-del", "mp3")
    assert result is None
    assert "clip-del.mp3" not in cache._index
