"""Cover art reconciliation for the Downloaded Library engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from ..audio_stream import fetch_album_art
from ..models import image_url_hash
from .filesystem import _write_file, _write_track_sidecar


class CoverHashFile:
    """Owns parse / serialise / get / set / legacy-migration for ``.cover_hash``.

    Single source of truth for the on-disk hash sidecar that prevents
    cover-art "ping pong" between two clips sharing a folder. The
    legacy single-hash format (one bare hash on a line) is parsed but
    never written; the first ``set`` call migrates the file to the
    per-clip dict format.
    """

    _LEGACY_KEY = ""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._cache: dict[str, str] | None = None

    @property
    def path(self) -> Path:
        return self._path

    @staticmethod
    def _parse(raw: str) -> dict[str, str]:
        """Parse ``.cover_hash`` contents, including the legacy single-hash format."""
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if len(lines) == 1 and "=" not in lines[0]:
            return {CoverHashFile._LEGACY_KEY: lines[0]}

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

    @staticmethod
    def _serialise(hashes: dict[str, str]) -> str:
        """Serialise ``.cover_hash`` contents with deterministic ordering."""
        return "".join(
            f"{clip_id}={hashes[clip_id]}\n" for clip_id in sorted(hashes) if clip_id != CoverHashFile._LEGACY_KEY
        )

    def _read_sync(self) -> dict[str, str]:
        try:
            return self._parse(self._path.read_text())
        except OSError:
            return {}

    async def get(self, hass: HomeAssistant, clip_id: str) -> str | None:
        """Return the stored hash for a clip, or None if absent."""
        if self._cache is None:
            self._cache = await hass.async_add_executor_job(self._read_sync)
        return self._cache.get(clip_id)

    async def known_clip_ids(self, hass: HomeAssistant) -> set[str]:
        """Return the set of clip_ids stored in this ``.cover_hash`` file.

        Used by the sidecar-safety pass to decide whether the file is still
        being used by another clip (or another account that shares this
        folder) before deleting or rewriting it.
        """
        if self._cache is None:
            self._cache = await hass.async_add_executor_job(self._read_sync)
        return {cid for cid in self._cache if cid and cid != self._LEGACY_KEY}

    async def remove(self, hass: HomeAssistant, clip_id: str) -> bool:
        """Drop a clip_id from ``.cover_hash``; delete the file when emptied.

        Returns True when the file was unlinked because removing this clip
        emptied the dict, False otherwise.
        """
        if self._cache is None:
            self._cache = await hass.async_add_executor_job(self._read_sync)
        self._cache.pop(clip_id, None)
        self._cache.pop(self._LEGACY_KEY, None)
        if not self._cache:

            def _unlink() -> None:
                self._path.unlink(missing_ok=True)

            await hass.async_add_executor_job(_unlink)
            return True
        serialised = self._serialise(self._cache).encode()
        await _write_file(hass, self._path, serialised)
        return False

    async def set(self, hass: HomeAssistant, clip_id: str, hash_value: str) -> None:
        """Store the hash for a clip; migrates legacy format on first write."""
        if self._cache is None:
            self._cache = await hass.async_add_executor_job(self._read_sync)
        self._cache[clip_id] = hash_value
        self._cache.pop(self._LEGACY_KEY, None)
        serialised = self._serialise(self._cache).encode()
        await _write_file(hass, self._path, serialised)


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
    hash_file = CoverHashFile(hash_path)
    track_sidecar = track_path.with_suffix(".jpg") if track_path else None
    if await hash_file.get(hass, clip_id) == url_hash:
        if (
            track_sidecar is not None
            and not await hass.async_add_executor_job(track_sidecar.exists)
            and await hass.async_add_executor_job(cover_path.exists)
        ):
            await _write_track_sidecar(hass, cover_path, track_sidecar)
        return False
    image_data = await fetch_album_art(session, image_url)
    if image_data:
        await _write_file(hass, cover_path, image_data)
        await hash_file.set(hass, clip_id, url_hash)
        if track_sidecar is not None:
            await _write_track_sidecar(hass, cover_path, track_sidecar)
        return True
    return False
