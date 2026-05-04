"""M3U8 playlist serialisation for the Downloaded Library.

Materialises desired clips into media-library-compatible .m3u8 files inside the
download directory, alongside the audio files themselves.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .contracts import DownloadItem
from .paths import _safe_name

_LOGGER = logging.getLogger(__name__)


def _write_m3u8_playlists(
    base: Path,
    clips_state: dict[str, Any],
    desired: list[DownloadItem],
    source_to_name: dict[str, str] | None = None,
    playlist_order: dict[str, list[str]] | None = None,
) -> None:
    """Write M3U8 playlist files for media library compatibility."""
    if source_to_name is None:
        source_to_name = {}
    if playlist_order is None:
        playlist_order = {}

    track_info: dict[str, tuple[str, str, int]] = {}
    for item in desired:
        entry = clips_state.get(item.clip.id)
        if not entry or not entry.get("path"):
            continue
        abs_path = str(base / entry["path"])
        title = entry.get("title") or item.clip.title or "Untitled"
        title = title.replace("\n", " ").replace("\r", "")
        duration = int(item.clip.duration) if item.clip.duration else -1
        track_info[item.clip.id] = (abs_path, title, duration)

    playlists: dict[str, list[tuple[str, str, int]]] = {}
    seen_in_playlist: dict[str, set[str]] = {}
    for item in desired:
        if item.clip.id not in track_info:
            continue
        for source in item.sources:
            if source == "liked":
                name = "Liked Songs"
            elif source.startswith("playlist:"):
                name = source_to_name.get(source, source)
            else:
                continue
            if name not in playlists:
                order = playlist_order.get(source)
                if order:
                    playlists[name] = [track_info[cid] for cid in order if cid in track_info]
                    seen_in_playlist[name] = {cid for cid in order if cid in track_info}
                else:
                    playlists[name] = []
                    seen_in_playlist[name] = set()
            if not playlist_order.get(source) and item.clip.id not in seen_in_playlist.get(name, set()):
                playlists[name].append(track_info[item.clip.id])
                seen_in_playlist.setdefault(name, set()).add(item.clip.id)

    written: set[str] = set()
    for name, tracks in playlists.items():
        safe_name = name.replace("\n", " ").replace("\r", "")
        filename = f"{_safe_name(safe_name)}.m3u8"
        lines = [f"#EXTM3U\n#PLAYLIST:{safe_name}"]
        for abs_path, title, duration in tracks:
            lines.append(f"#EXTINF:{duration},{title}\n{abs_path}")
        try:
            (base / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")
            written.add(filename)
        except OSError:
            _LOGGER.warning("Failed to write playlist file: %s", filename)

    for existing in base.glob("*.m3u8"):
        if existing.name not in written:
            existing.unlink(missing_ok=True)


__all__ = ["_write_m3u8_playlists"]
