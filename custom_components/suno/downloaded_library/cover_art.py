"""Cover art reconciliation for the Downloaded Library engine."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from ..audio import fetch_album_art
from .filesystem import _write_file, _write_track_sidecar


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
