"""Per-source download-mode resolution for the Downloaded Library.

Maps source tags ("liked", "my_songs", "playlist:<id>") onto the configured
download mode (mirror / archive / cache) drawn from the integration options.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..const import (
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_MY_SONGS,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    DEFAULT_DOWNLOAD_MODE,
    DOWNLOAD_MODE_ARCHIVE,
    DOWNLOAD_MODE_MIRROR,
)

_SOURCE_MODE_KEYS: dict[str, str] = {
    "liked": CONF_DOWNLOAD_MODE_LIKED,
    "my_songs": CONF_DOWNLOAD_MODE_MY_SONGS,
}


def _get_source_mode(source: str, options: Mapping[str, Any]) -> str:
    """Return the configured download mode for a source tag."""
    if source.startswith("playlist:"):
        key: str | None = CONF_DOWNLOAD_MODE_PLAYLISTS
    else:
        key = _SOURCE_MODE_KEYS.get(source)
    if key is None:
        return DOWNLOAD_MODE_MIRROR
    return str(options.get(key, DEFAULT_DOWNLOAD_MODE))


def _source_preserves_files(source: str, options: Mapping[str, Any]) -> bool:
    """Return True if the source mode keeps files permanently."""
    return _get_source_mode(source, options) == DOWNLOAD_MODE_ARCHIVE


def _source_modes_for(sources: list[str], options: Mapping[str, Any]) -> dict[str, str]:
    """Return persisted source mode metadata for a stored clip record."""
    return {source: _get_source_mode(source, options) for source in sources}


def _entry_source_modes(
    entry: Mapping[str, Any],
    sources: list[str],
    fallback_options: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Return source mode metadata, inferring it from fallback options when absent."""
    raw_modes = entry.get("source_modes")
    if isinstance(raw_modes, dict):
        modes = {str(source): str(mode) for source, mode in raw_modes.items()}
    else:
        modes = {}
    if fallback_options is not None:
        for source in sources:
            modes.setdefault(source, _get_source_mode(source, fallback_options))
    return modes


__all__ = [
    "_entry_source_modes",
    "_get_source_mode",
    "_source_modes_for",
    "_source_preserves_files",
]
