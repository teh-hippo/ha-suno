"""Tests for the Suno audio cache."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.suno.cache import SunoCache


def _mp3_bytes(size: int = 256) -> bytes:
    """Return fake MP3 data with valid ID3 magic."""
    return b"ID3" + b"\x00" * (size - 3)


def _wav_bytes(size: int = 256) -> bytes:
    """Return fake WAV data with valid RIFF magic."""
    data_size = size - 12
    return b"RIFF" + (data_size + 4).to_bytes(4, "little") + b"WAVE" + b"\x00" * data_size


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


async def test_cache_wav_validation(hass: HomeAssistant, tmp_path: Path) -> None:
    """WAV files should be validated against RIFF magic."""
    cache_dir = str(tmp_path / "suno_cache")
    with patch.object(hass.config, "path", return_value=cache_dir):
        cache = SunoCache(hass, max_size_mb=10)
        await cache.async_init()

    data = _wav_bytes(512)
    await cache.async_put("clip-wav", "wav", data)
    got = await cache.async_get("clip-wav", "wav")
    assert got is not None

    # Corrupt it
    (cache.cache_dir / "clip-wav.wav").write_bytes(b"NOT_RIFF")
    got = await cache.async_get("clip-wav", "wav")
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
