"""Tests for the Downloaded Library cache adapters.

Covers the two thin adapters in ``cache_adapter.py``:
``NullDownloadedLibraryCache`` (no-op) and
``SunoCacheDownloadedLibraryAdapter`` (translation shim onto the
playback audio cache).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.suno.downloaded_library.cache_adapter import (
    NullDownloadedLibraryCache,
    SunoCacheDownloadedLibraryAdapter,
)


async def test_null_cache_get_returns_none() -> None:
    """The no-op cache never returns a path."""
    cache = NullDownloadedLibraryCache()
    assert await cache.async_get("clip-aaa-111", "flac", "meta-hash") is None


async def test_null_cache_put_is_no_op() -> None:
    """The no-op cache silently accepts puts."""
    cache = NullDownloadedLibraryCache()
    # Returns None without raising
    assert await cache.async_put("clip-aaa-111", "flac", b"data", meta_hash="h") is None


async def test_adapter_get_returns_path_when_underlying_returns_file(tmp_path: Path) -> None:
    """When the playback cache yields an existing file Path, the adapter forwards it."""
    cached_file = tmp_path / "clip-aaa.flac"
    cached_file.write_bytes(b"fLaC")

    underlying = MagicMock()
    underlying.async_get = AsyncMock(return_value=cached_file)

    adapter = SunoCacheDownloadedLibraryAdapter(underlying)
    result = await adapter.async_get("clip-aaa-111", "flac", "meta-hash")

    assert result == cached_file
    underlying.async_get.assert_awaited_once_with("clip-aaa-111", "flac", meta_hash="meta-hash")


async def test_adapter_get_returns_none_when_path_does_not_exist(tmp_path: Path) -> None:
    """A Path returned by the underlying cache is filtered when the file is gone."""
    missing = tmp_path / "vanished.flac"  # never created
    underlying = MagicMock()
    underlying.async_get = AsyncMock(return_value=missing)

    adapter = SunoCacheDownloadedLibraryAdapter(underlying)
    assert await adapter.async_get("clip-aaa-111", "flac", "meta-hash") is None


async def test_adapter_get_returns_none_when_underlying_returns_non_path() -> None:
    """Non-Path return values from the underlying cache are coerced to None."""
    underlying = MagicMock()
    underlying.async_get = AsyncMock(return_value="not a path")

    adapter = SunoCacheDownloadedLibraryAdapter(underlying)
    assert await adapter.async_get("clip-aaa-111", "flac", "meta-hash") is None


async def test_adapter_get_returns_none_when_underlying_has_no_get_method() -> None:
    """Underlying cache without async_get returns None defensively."""

    class _NoGet:
        async def async_put(self, *_args, **_kwargs) -> None:
            return

    adapter = SunoCacheDownloadedLibraryAdapter(_NoGet())
    assert await adapter.async_get("clip-aaa-111", "flac", "meta-hash") is None


async def test_adapter_put_forwards_to_underlying() -> None:
    """The adapter forwards put calls with the meta_hash keyword."""
    underlying = MagicMock()
    underlying.async_put = AsyncMock(return_value=None)

    adapter = SunoCacheDownloadedLibraryAdapter(underlying)
    await adapter.async_put("clip-aaa-111", "flac", b"audio-bytes", "meta-hash")

    underlying.async_put.assert_awaited_once_with("clip-aaa-111", "flac", b"audio-bytes", meta_hash="meta-hash")


async def test_adapter_put_no_ops_when_underlying_has_no_put_method() -> None:
    """Underlying cache without async_put silently does nothing."""

    class _NoPut:
        async def async_get(self, *_args, **_kwargs) -> None:
            return None

    adapter = SunoCacheDownloadedLibraryAdapter(_NoPut())
    # Returns without raising
    assert await adapter.async_put("clip-aaa-111", "flac", b"data", "h") is None


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):  # noqa: PT004
    """Required for any tests that touch HA infrastructure."""
