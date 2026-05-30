"""Cover art reconciliation for the Downloaded Library engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from ..audio_stream import fetch_album_art
from ..models import image_url_hash
from .filesystem import _write_file, _write_track_sidecar

_LEGACY_COVER_HASH_KEY = ""


def _parse_cover_hashes(raw: str) -> dict[str, str]:
    """Parse .cover_hash contents, including the legacy single-hash format."""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) == 1 and "=" not in lines[0]:
        return {_LEGACY_COVER_HASH_KEY: lines[0]}

    hashes: dict[str, str] = {}
    for line in lines:
        if "=" not in line:
            continue
        clip_id, hash_value = line.split("=", 1)
        clip_id = clip_id.strip()
        hash_value = hash_value.strip()
        if clip_id and hash_value:
            hashes[clip_id] = hash_value
    return hashes


def _serialise_cover_hashes(hashes: dict[str, str]) -> str:
    """Serialise .cover_hash contents with deterministic ordering."""
    return "".join(f"{clip_id}={hashes[clip_id]}\n" for clip_id in sorted(hashes) if clip_id != _LEGACY_COVER_HASH_KEY)


async def _update_cover_art(
    hass: HomeAssistant,
    session: Any,
    image_url: str,
    cover_path: Path,
    hash_path: Path,
    *,
    clip_id: str,
    track_path: Path | None = None,
) -> bool:
    """Write album art sidecars if the source image changed."""
    url_hash = image_url_hash(image_url)
    cover_hashes: dict[str, str] = {}
    if await hass.async_add_executor_job(hash_path.exists):
        raw_hashes = await hass.async_add_executor_job(hash_path.read_text)
        cover_hashes = _parse_cover_hashes(raw_hashes)
    track_sidecar = track_path.with_suffix(".jpg") if track_path else None
    if cover_hashes.get(clip_id) == url_hash:
        if (
            track_sidecar is not None
            and not await hass.async_add_executor_job(track_sidecar.exists)
            and await hass.async_add_executor_job(cover_path.exists)
        ):
            await _write_track_sidecar(hass, cover_path, track_sidecar)
        return False
    image_data = await fetch_album_art(session, image_url)
    if image_data:
        cover_hashes[clip_id] = url_hash
        cover_hashes.pop(_LEGACY_COVER_HASH_KEY, None)
        await _write_file(hass, cover_path, image_data)
        await _write_file(hass, hash_path, _serialise_cover_hashes(cover_hashes).encode())
        if track_sidecar is not None:
            await _write_track_sidecar(hass, cover_path, track_sidecar)
        return True
    return False
