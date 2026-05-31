"""Tests for the CoverHashFile helper that owns ``.cover_hash`` files."""

from __future__ import annotations

from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant

from custom_components.suno.downloaded_library.cover_art import CoverHashFile


@pytest.mark.asyncio
async def test_get_returns_none_when_file_missing(hass: HomeAssistant, tmp_path: Path) -> None:
    hash_file = CoverHashFile(tmp_path / ".cover_hash")

    assert await hash_file.get(hass, "clip-a") is None


@pytest.mark.asyncio
async def test_get_returns_stored_hash(hass: HomeAssistant, tmp_path: Path) -> None:
    hash_path = tmp_path / ".cover_hash"
    hash_path.write_text("clip-a=hash1\nclip-b=hash2\n")
    hash_file = CoverHashFile(hash_path)

    assert await hash_file.get(hass, "clip-a") == "hash1"
    assert await hash_file.get(hass, "clip-b") == "hash2"
    assert await hash_file.get(hass, "missing") is None


@pytest.mark.asyncio
async def test_set_creates_file_with_single_entry(hass: HomeAssistant, tmp_path: Path) -> None:
    hash_path = tmp_path / ".cover_hash"
    hash_file = CoverHashFile(hash_path)

    await hash_file.set(hass, "clip-a", "hash1")

    assert hash_path.read_text() == "clip-a=hash1\n"


@pytest.mark.asyncio
async def test_set_preserves_other_entries(hass: HomeAssistant, tmp_path: Path) -> None:
    hash_path = tmp_path / ".cover_hash"
    hash_path.write_text("clip-a=hash1\nclip-b=hash2\n")
    hash_file = CoverHashFile(hash_path)

    await hash_file.set(hass, "clip-c", "hash3")

    contents = hash_path.read_text()
    assert "clip-a=hash1" in contents
    assert "clip-b=hash2" in contents
    assert "clip-c=hash3" in contents


@pytest.mark.asyncio
async def test_set_uses_sorted_output(hass: HomeAssistant, tmp_path: Path) -> None:
    hash_path = tmp_path / ".cover_hash"
    hash_file = CoverHashFile(hash_path)

    await hash_file.set(hass, "clip-zebra", "hz")
    await hash_file.set(hass, "clip-alpha", "ha")
    await hash_file.set(hass, "clip-mike", "hm")

    assert hash_path.read_text() == "clip-alpha=ha\nclip-mike=hm\nclip-zebra=hz\n"


@pytest.mark.asyncio
async def test_legacy_single_hash_format_migrates_on_first_set(hass: HomeAssistant, tmp_path: Path) -> None:
    """Legacy bare-line format must convert to per-clip dict the first time
    a new clip hash is recorded. Closes the upgrade path from before v6.3.3."""
    hash_path = tmp_path / ".cover_hash"
    hash_path.write_text("legacy-bare-hash\n")
    hash_file = CoverHashFile(hash_path)

    await hash_file.set(hass, "clip-a", "new-hash")

    contents = hash_path.read_text()
    assert "legacy-bare-hash" not in contents
    assert contents == "clip-a=new-hash\n"


@pytest.mark.asyncio
async def test_cache_avoids_repeat_reads(hass: HomeAssistant, tmp_path: Path) -> None:
    hash_path = tmp_path / ".cover_hash"
    hash_path.write_text("clip-a=hash1\n")
    hash_file = CoverHashFile(hash_path)

    # First read populates cache.
    assert await hash_file.get(hass, "clip-a") == "hash1"

    # External mutation should not be observed by a CoverHashFile that has cached.
    hash_path.write_text("clip-a=hash2\n")
    assert await hash_file.get(hass, "clip-a") == "hash1"
