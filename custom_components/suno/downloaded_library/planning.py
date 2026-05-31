"""Planning helpers for the Downloaded Library engine.

These functions translate options + a Suno library snapshot into a
``DesiredDownloadPlan``. ``_filter_my_songs`` reads ``datetime.now`` directly;
injecting a clock for full purity is left for a future cleanup.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ..const import (
    CONF_ALL_PLAYLISTS,
    CONF_MY_SONGS_COUNT,
    CONF_MY_SONGS_DAYS,
    CONF_MY_SONGS_MINIMUM,
    CONF_PLAYLISTS,
    CONF_QUALITY_LIKED,
    CONF_QUALITY_MY_SONGS,
    CONF_QUALITY_PLAYLISTS,
    CONF_SHOW_LIKED,
    CONF_SHOW_MY_SONGS,
    CONF_SHOW_PLAYLISTS,
    DEFAULT_ALL_PLAYLISTS,
    DEFAULT_MY_SONGS_COUNT,
    DEFAULT_MY_SONGS_DAYS,
    DEFAULT_MY_SONGS_MINIMUM,
    DEFAULT_SHOW_LIKED,
    DEFAULT_SHOW_MY_SONGS,
    DEFAULT_SHOW_PLAYLISTS,
    DOWNLOAD_MODE_CACHE,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from ..models import SunoClip, SunoData
from .contracts import DesiredDownloadPlan, DownloadItem, ManifestEntry
from .source_modes import _get_source_mode, _source_modes_for


def _add_clip(clip_map: dict[str, DownloadItem], clip: SunoClip, source: str, quality: str) -> None:
    """Add or update a desired clip, with high quality winning over standard."""
    if clip.id in clip_map:
        item = clip_map[clip.id]
        if source not in item.sources:
            item.sources.append(source)
        if quality == QUALITY_HIGH:
            item.quality = QUALITY_HIGH
    else:
        clip_map[clip.id] = DownloadItem(clip=clip, sources=[source], quality=quality)


def _preserve_source(
    preserved: set[str],
    clip_map: dict[str, DownloadItem],
    prev_clips: dict[str, Any],
    source: str,
) -> None:
    """Preserve a stale source without deleting or removing it from records."""
    for clip_id, entry in prev_clips.items():
        if source not in entry.get("sources", []):
            continue
        if clip_id in clip_map:
            if source not in clip_map[clip_id].sources:
                clip_map[clip_id].sources.append(source)
        else:
            preserved.add(clip_id)


def _set_manifest_album(entry: dict[str, Any], album: str | None) -> None:
    """Store the inherited album marker, or remove it when not needed.

    Thin forwarder kept for tests and legacy call sites; new code should
    set ``ManifestEntry.album`` directly (None = absent).
    """
    if album is None:
        entry.pop("album", None)
    else:
        entry["album"] = album


def _apply_clip_metadata(
    entry: dict[str, Any],
    clip: SunoClip,
    *,
    album: str | None = None,
) -> None:
    """Refresh clip-mirror manifest fields on a dict-shaped entry.

    Thin forwarder around :meth:`ManifestEntry.apply_clip_metadata` so
    test fixtures and any remaining dict-style call sites keep working
    while engine internals use ``ManifestEntry`` directly.
    """
    me = ManifestEntry.from_dict(entry)
    me.apply_clip_metadata(clip, album=album)
    _replace_entry_dict(entry, me.to_dict())
    if album is None:
        entry.pop("album", None)


def _apply_file_state(
    entry: dict[str, Any],
    clip: SunoClip,
    file_size: int,
) -> None:
    """Refresh file-mirror manifest fields on a dict-shaped entry.

    Thin forwarder around :meth:`ManifestEntry.apply_file_state` for the
    same reason as :func:`_apply_clip_metadata`.
    """
    me = ManifestEntry.from_dict(entry)
    me.apply_file_state(clip, file_size)
    _replace_entry_dict(entry, me.to_dict())


def _clip_entry(
    item: DownloadItem,
    rel_path: str,
    file_size: int,
    options: Mapping[str, Any],
    *,
    album: str | None = None,
) -> dict[str, Any]:
    """Build a stored Downloaded Library record for one clip (dict shape)."""
    me = ManifestEntry(
        path=rel_path,
        sources=list(item.sources),
        source_modes=_source_modes_for(item.sources, options),
        quality=item.quality,
    )
    me.apply_clip_metadata(item.clip, album=album)
    me.apply_file_state(item.clip, file_size)
    return me.to_dict()


def _replace_entry_dict(entry: dict[str, Any], new_dict: dict[str, Any]) -> None:
    """Replace the contents of an entry dict in place."""
    entry.clear()
    entry.update(new_dict)


def _clear_for_redownload(entry: dict[str, Any]) -> None:
    """Reset every field whose meaning is "describes the file on disk".

    Wraps :meth:`ManifestEntry.clear_for_redownload`. Closes the
    v6.3.1–v6.3.4 leak where call sites manually cleared ``path`` +
    ``meta_hash`` (+ sometimes ``album``) but forgot ``embedded_art_hash``,
    leaving a stale art-hash sentinel that suppressed the next retag.
    """
    me = ManifestEntry.from_dict(entry)
    me.clear_for_redownload()
    _replace_entry_dict(entry, me.to_dict())


def _needs_retag(
    entry: dict[str, Any],
    clip: SunoClip,
    resolved_album: str | None,
) -> str | None:
    """Return the first reason an entry needs retagging, or None.

    Wraps :meth:`ManifestEntry.needs_retag` for dict-shaped engine state.
    Returns "meta" / "art" / "album" so reconcile logs can explain
    themselves.
    """
    return ManifestEntry.from_dict(entry).needs_retag(clip, resolved_album)


def _filter_my_songs(
    all_clips: list[SunoClip],
    my_songs_count: Any,
    my_songs_days: Any,
    minimum: int,
) -> list[SunoClip]:
    """Select clips matching configured My Songs count/age window."""
    by_count: set[str] | None = None
    if my_songs_count:
        by_count = {c.id for c in all_clips[: int(my_songs_count)]}

    by_days: set[str] | None = None
    if my_songs_days:
        cutoff = datetime.now(tz=UTC).timestamp() - int(my_songs_days) * 86400
        by_days = set()
        for clip in all_clips:
            if clip.created_at:
                try:
                    created = datetime.fromisoformat(clip.created_at.replace("Z", "+00:00"))
                    if created.timestamp() >= cutoff:
                        by_days.add(clip.id)
                except ValueError:
                    pass

    if by_count is not None and by_days is not None:
        my_songs_set = by_count & by_days
    elif by_count is not None:
        my_songs_set = by_count
    elif by_days is not None:
        my_songs_set = by_days
    else:
        my_songs_set = set()

    if minimum and len(my_songs_set) < minimum:
        my_songs_set |= {c.id for c in all_clips[:minimum]}

    return [clip for clip in all_clips if clip.id in my_songs_set]


def build_desired(
    options: Mapping[str, Any],
    suno_library: SunoData,
    prev_clips: Mapping[str, Any],
) -> DesiredDownloadPlan:
    """Build the desired Downloaded Library records from a Suno Library."""
    clip_map: dict[str, DownloadItem] = {}
    preserved: set[str] = set()
    source_to_name: dict[str, str] = {"liked": "Liked Songs"}
    playlist_order: dict[str, list[str]] = {}
    stale_sections = set(suno_library.stale_sections)
    stale_sources: set[str] = set()

    if options.get(CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED) and _get_source_mode("liked", options) != DOWNLOAD_MODE_CACHE:
        liked_quality = options.get(CONF_QUALITY_LIKED, QUALITY_HIGH)
        for clip in suno_library.liked_clips:
            _add_clip(clip_map, clip, "liked", liked_quality)
        playlist_order["liked"] = [c.id for c in suno_library.liked_clips]
        if "liked_clips" in stale_sections:
            stale_sources.add("liked")

    sync_all = options.get(CONF_ALL_PLAYLISTS, DEFAULT_ALL_PLAYLISTS)
    selected_ids = options.get(CONF_PLAYLISTS, []) or []
    playlists_enabled = options.get(CONF_SHOW_PLAYLISTS, DEFAULT_SHOW_PLAYLISTS)
    playlists_mode = _get_source_mode("playlist:", options)
    if playlists_enabled and playlists_mode != DOWNLOAD_MODE_CACHE and (sync_all or selected_ids):
        playlist_quality = options.get(CONF_QUALITY_PLAYLISTS, QUALITY_HIGH)
        if "playlists" in stale_sections or "playlist_clips" in stale_sections:
            stale_sources.update(
                source
                for entry in prev_clips.values()
                for source in entry.get("sources", [])
                if source.startswith("playlist:")
            )
        for playlist in suno_library.playlists:
            if not sync_all and playlist.id not in selected_ids:
                continue
            tag = f"playlist:{playlist.id}"
            source_to_name[tag] = playlist.name
            playlist_stale = f"playlist_clips:{playlist.id}" in stale_sections
            clips = suno_library.playlist_clips.get(playlist.id, [])
            playlist_order[tag] = [c.id for c in clips]
            for clip in clips:
                _add_clip(clip_map, clip, tag, playlist_quality)
            if playlist_stale:
                stale_sources.add(tag)

    if (
        options.get(CONF_SHOW_MY_SONGS, DEFAULT_SHOW_MY_SONGS)
        and _get_source_mode("my_songs", options) != DOWNLOAD_MODE_CACHE
    ):
        my_songs_count = options.get(CONF_MY_SONGS_COUNT, DEFAULT_MY_SONGS_COUNT)
        my_songs_days = options.get(CONF_MY_SONGS_DAYS, DEFAULT_MY_SONGS_DAYS)
        minimum = int(options.get(CONF_MY_SONGS_MINIMUM, DEFAULT_MY_SONGS_MINIMUM))
        if my_songs_count or my_songs_days or minimum:
            my_songs_quality = options.get(CONF_QUALITY_MY_SONGS, QUALITY_STANDARD)
            for clip in _filter_my_songs(suno_library.clips, my_songs_count, my_songs_days, minimum):
                _add_clip(clip_map, clip, "my_songs", my_songs_quality)
            if "clips" in stale_sections:
                stale_sources.add("my_songs")

    for source in sorted(stale_sources):
        _preserve_source(preserved, clip_map, dict(prev_clips), source)
    preserved -= clip_map.keys()
    return DesiredDownloadPlan(list(clip_map.values()), preserved, source_to_name, playlist_order)
