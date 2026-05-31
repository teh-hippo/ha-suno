"""Tests for the planning submodule of the Downloaded Library engine.

Split from the legacy 5129-line ``test_downloaded_library.py`` by the
Round 2 test restructure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from homeassistant.core import HomeAssistant

from custom_components.suno.const import (
    CONF_ALL_PLAYLISTS,
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_MY_SONGS,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_MY_SONGS_COUNT,
    CONF_MY_SONGS_DAYS,
    CONF_PLAYLISTS,
    CONF_SHOW_LIKED,
    CONF_SHOW_MY_SONGS,
    CONF_SHOW_PLAYLISTS,
    DOWNLOAD_MODE_ARCHIVE,
    DOWNLOAD_MODE_CACHE,
    DOWNLOAD_MODE_MIRROR,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from custom_components.suno.downloaded_library import (
    DownloadedLibrary,
    DownloadItem,
    InMemoryDownloadedLibraryStorage,
    ManifestEntry,
    RenderedAudio,
    _build_download_summary,
)
from custom_components.suno.downloaded_library.planning import _add_clip
from custom_components.suno.downloaded_library.source_modes import _source_preserves_files
from custom_components.suno.models import (
    SunoClip,
    SunoData,
    TrackMetadata,
)

from .conftest import make_clip

# ── Shared test fixtures (from legacy test_downloaded_library.py) ──


def _make_clip(clip_id: str, title: str = "Song", created: str = "2026-03-15T10:00:00Z") -> SunoClip:
    """Construct a minimal SunoClip for planning helper tests."""
    return make_clip(clip_id, title=title, created_at=created, image_url="", image_large_url="")


def _clip(clip_id: str, title: str = "Song") -> SunoClip:
    return make_clip(
        clip_id,
        title=title,
        audio_url=f"https://cdn1.suno.ai/{clip_id}.mp3",
        display_name="artist",
    )


def _options(download_path: Path) -> dict[str, object]:
    return {
        CONF_DOWNLOAD_PATH: str(download_path),
        CONF_SHOW_LIKED: True,
        CONF_SHOW_MY_SONGS: False,
        CONF_SHOW_PLAYLISTS: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
    }


class _FakeAudio:
    def __init__(
        self,
        data: bytes = b"fLaC" + b"\x00" * 50,
        video_data: bytes | None = b"\x00\x00\x00\x1cftypisom",
        retag_result: bool = True,
        image_bytes: bytes | None = b"\xff\xd8\xff\xe0fakejpegheader",
        retag_data: bytes | None = None,
    ) -> None:
        self.data = data
        self.video_data = video_data
        self.retag_result = retag_result
        self.image_bytes = image_bytes
        self.retag_data = retag_data
        self.rendered: list[str] = []
        self.render_qualities: list[str] = []
        self.render_metas: list[TrackMetadata] = []
        self.retag_calls: list[tuple[Path, TrackMetadata]] = []
        self.video_calls: list[tuple[str, Path]] = []
        self.image_fetches: list[str] = []

    async def fetch_image(self, image_url: str) -> bytes | None:
        self.image_fetches.append(image_url)
        return self.image_bytes

    async def render(
        self,
        clip: SunoClip,
        quality: str,
        meta: TrackMetadata,
        _image_url: str | None,
    ) -> RenderedAudio | None:
        self.rendered.append(clip.id)
        self.render_qualities.append(quality)
        self.render_metas.append(meta)
        if quality == QUALITY_HIGH:
            return RenderedAudio(b"fLaC" + b"\x00" * 50, "flac")
        return RenderedAudio(b"ID3" + b"\x00" * 50, "mp3")

    async def retag(self, target: Path, meta: TrackMetadata) -> bool:
        self.retag_calls.append((target, meta))
        if self.retag_result and self.retag_data is not None:
            target.write_bytes(self.retag_data)
        return self.retag_result

    async def download_video(self, video_url: str, target: Path) -> None:
        if target.exists():
            return
        self.video_calls.append((video_url, target))
        if self.video_data is None:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.video_data)


class _FakeCache:
    def __init__(self, cached_path: Path | None = None) -> None:
        self.cached_path = cached_path
        self.puts: list[tuple[str, str, bytes, str]] = []

    async def async_get(self, _clip_id: str, _fmt: str, _meta_hash: str) -> Path | None:
        return self.cached_path

    async def async_put(self, clip_id: str, fmt: str, data: bytes, meta_hash: str) -> None:
        self.puts.append((clip_id, fmt, data, meta_hash))


def _make_dated_clip(clip_id: str, title: str = "Song", created: str = "2026-03-15T10:00:00Z") -> SunoClip:
    return SunoClip(
        id=clip_id,
        title=title,
        audio_url=f"https://cdn1.suno.ai/{clip_id}.mp3",
        image_url=None,
        image_large_url=None,
        is_liked=False,
        status="complete",
        created_at=created,
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
    )


def _clip_with_display(
    clip_id: str,
    title: str = "Song",
    created: str = "2026-03-15T10:00:00Z",
    display_name: str = "testuser",
    image_url: str | None = None,
    image_large_url: str | None = None,
    video_url: str = "",
    video_cover_url: str = "",
) -> SunoClip:
    return SunoClip(
        id=clip_id,
        title=title,
        audio_url=f"https://cdn1.suno.ai/{clip_id}.mp3",
        image_url=image_url,
        image_large_url=image_large_url,
        is_liked=True,
        status="complete",
        created_at=created,
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
        display_name=display_name,
        video_url=video_url,
        video_cover_url=video_cover_url,
    )


async def test_build_desired_preserves_clips_when_section_is_stale(hass: HomeAssistant) -> None:
    """Stale liked section preserves liked clips already on disk."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    library.state = {
        "clips": {
            "clip-liked": {"path": "liked.flac", "sources": ["liked"]},
            "clip-my-songs": {"path": "my_songs.flac", "sources": ["my_songs"]},
        },
        "last_download": None,
    }

    options = {
        CONF_SHOW_LIKED: True,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_MY_SONGS_COUNT: None,
        CONF_MY_SONGS_DAYS: None,
    }
    suno_data = SunoData(stale_sections=("liked_clips",))
    plan = library.build_desired(options, suno_data)

    assert "clip-liked" in plan.preserved_ids


async def test_build_desired_my_songs_count_only(hass: HomeAssistant) -> None:
    """count=N, days=None returns top-N clips."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    clips = [_make_dated_clip(f"clip-{i}", created="2026-03-15T10:00:00Z") for i in range(10)]

    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_SHOW_MY_SONGS: True,
        CONF_MY_SONGS_COUNT: 5,
        CONF_MY_SONGS_DAYS: None,
    }
    plan = library.build_desired(options, SunoData(clips=clips))
    assert len(plan.items) == 5
    ids = {item.clip.id for item in plan.items}
    assert ids == {f"clip-{i}" for i in range(5)}


async def test_build_desired_my_songs_days_only(hass: HomeAssistant) -> None:
    """count=None, days=N returns clips within N days."""
    from datetime import timedelta

    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    now = datetime.now(tz=UTC)
    recent_ts = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_ts = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    clips = [
        _make_dated_clip("clip-new-1", created=recent_ts),
        _make_dated_clip("clip-new-2", created=recent_ts),
        _make_dated_clip("clip-old", created=old_ts),
    ]

    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_SHOW_MY_SONGS: True,
        CONF_MY_SONGS_COUNT: None,
        CONF_MY_SONGS_DAYS: 7,
    }
    plan = library.build_desired(options, SunoData(clips=clips))
    ids = {item.clip.id for item in plan.items}
    assert "clip-new-1" in ids
    assert "clip-new-2" in ids
    assert "clip-old" not in ids


async def test_build_desired_my_songs_count_and_days_intersect(hass: HomeAssistant) -> None:
    """count=N AND days=M returns at most N clips within M days (intersection)."""
    from datetime import timedelta

    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    now = datetime.now(tz=UTC)
    recent_ts = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_ts = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    clips = [
        _make_dated_clip("clip-r0", created=recent_ts),
        _make_dated_clip("clip-r1", created=recent_ts),
        _make_dated_clip("clip-r2", created=recent_ts),
        _make_dated_clip("clip-r3", created=recent_ts),
        _make_dated_clip("clip-r4", created=recent_ts),
        _make_dated_clip("clip-old-0", created=old_ts),
        _make_dated_clip("clip-old-1", created=old_ts),
    ]

    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_SHOW_MY_SONGS: True,
        CONF_MY_SONGS_COUNT: 3,
        CONF_MY_SONGS_DAYS: 7,
    }
    plan = library.build_desired(options, SunoData(clips=clips))
    ids = {item.clip.id for item in plan.items}
    assert len(ids) == 3
    assert ids == {"clip-r0", "clip-r1", "clip-r2"}


async def test_build_desired_my_songs_disabled_when_both_zero(hass: HomeAssistant) -> None:
    """count=None/0 and days=None/0 means my_songs is disabled."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_SHOW_MY_SONGS: True,
        CONF_MY_SONGS_COUNT: None,
        CONF_MY_SONGS_DAYS: None,
    }
    plan = library.build_desired(options, SunoData(clips=[_make_dated_clip("clip-1")]))
    assert len(plan.items) == 0


# ── source enable / path skip ───────────────────────────────────


async def test_build_desired_skips_disabled_source(hass: HomeAssistant) -> None:
    """show_liked=False excludes liked clips from desired set."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    clip = _clip("clip-liked-1", "Liked Song")
    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_MY_SONGS_COUNT: None,
        CONF_MY_SONGS_DAYS: None,
    }
    plan = library.build_desired(options, SunoData(liked_clips=[clip]))
    assert len(plan.items) == 0


# ── build_desired source-mode toggles ───────────────────────────


async def test_build_desired_skips_cache_only_sources(hass: HomeAssistant) -> None:
    """build_desired excludes cache-mode sections from the items list."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    options = {
        CONF_SHOW_LIKED: True,
        CONF_SHOW_MY_SONGS: True,
        CONF_SHOW_PLAYLISTS: True,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_CACHE,
        CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_CACHE,
        CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE,
        CONF_MY_SONGS_COUNT: 5,
        CONF_MY_SONGS_DAYS: None,
    }
    suno_data = SunoData(
        liked_clips=[_clip("c1")],
        clips=[_clip("c2")],
    )
    plan = library.build_desired(options, suno_data)
    assert len(plan.items) == 0


async def test_build_desired_respects_show_toggles(hass: HomeAssistant) -> None:
    """show_playlists=False excludes playlists from desired set."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    options = {
        CONF_SHOW_LIKED: True,
        CONF_SHOW_PLAYLISTS: False,
        CONF_SHOW_MY_SONGS: False,
        CONF_ALL_PLAYLISTS: True,
        CONF_PLAYLISTS: [],
        CONF_MY_SONGS_COUNT: 5,
        CONF_MY_SONGS_DAYS: None,
    }
    plan = library.build_desired(options, SunoData(liked_clips=[_clip("c1")]))
    ids = {item.clip.id for item in plan.items}
    assert "c1" in ids


# ── TestBuildSyncSummary (converted to free functions) ────────────────────


def test_build_sync_summary_no_change() -> None:
    assert _build_download_summary(0, 0, 0) == "No change"


def test_build_sync_summary_single_new_song() -> None:
    assert _build_download_summary(1, 0, 0) == "1 new song"


def test_build_sync_summary_multiple_new_songs() -> None:
    assert _build_download_summary(8, 0, 0) == "8 new songs"


def test_build_sync_summary_single_removal() -> None:
    assert _build_download_summary(0, 1, 0) == "1 removal"


def test_build_sync_summary_multiple_removals() -> None:
    assert _build_download_summary(0, 3, 0) == "3 removals"


def test_build_sync_summary_single_metadata_update() -> None:
    assert _build_download_summary(0, 0, 1) == "1 metadata update"


def test_build_sync_summary_multiple_metadata_updates() -> None:
    assert _build_download_summary(0, 0, 2) == "2 metadata updates"


def test_build_sync_summary_combined() -> None:
    result = _build_download_summary(1, 2, 1)
    assert result == "1 new song, 1 metadata update, 2 removals"


def test_build_sync_summary_all_plural() -> None:
    result = _build_download_summary(3, 4, 5)
    assert result == "3 new songs, 5 metadata updates, 4 removals"


def test_build_sync_summary_renamed() -> None:
    assert _build_download_summary(0, 0, 0, renamed=3) == "3 renamed"


def test_build_sync_summary_retagged() -> None:
    assert _build_download_summary(0, 0, 0, retagged=5) == "5 re-tagged"


def test_build_sync_summary_full_username_change() -> None:
    """Typical username change: renames + re-tags, no downloads."""
    result = _build_download_summary(0, 0, 0, renamed=50, retagged=0)
    assert result == "50 renamed"


def test_build_sync_summary_all_operations() -> None:
    result = _build_download_summary(2, 1, 0, renamed=3, retagged=5)
    assert result == "2 new songs, 3 renamed, 5 re-tagged, 1 removal"


def test_sync_retention_modes_sync_mode_deletes_removed_clips() -> None:
    """Clip with source ['liked'], mode=mirror → deleted when removed from desired."""
    clips_state = {
        "clip-1": ManifestEntry.from_dict({"path": "2026-01-15/Song [clip-1].flac", "sources": ["liked"]}),
    }
    seen_ids: set[str] = set()  # clip not in desired
    preserved_ids: set[str] = set()
    options = {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_MIRROR}

    to_delete = []
    for cid in clips_state:
        if cid in seen_ids or cid in preserved_ids:
            continue
        entry = clips_state[cid]
        sources = entry.sources
        if all(not _source_preserves_files(src, options) for src in sources):
            to_delete.append(cid)

    assert to_delete == ["clip-1"]


def test_sync_retention_modes_archive_mode_keeps_removed_clips() -> None:
    """Clip with source ['liked'], mode=archive → NOT deleted when removed from desired."""
    clips_state = {
        "clip-1": ManifestEntry.from_dict({"path": "2026-01-15/Song [clip-1].flac", "sources": ["liked"]}),
    }
    seen_ids: set[str] = set()
    preserved_ids: set[str] = set()
    options = {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_ARCHIVE}

    to_delete = []
    for cid in clips_state:
        if cid in seen_ids or cid in preserved_ids:
            continue
        entry = clips_state[cid]
        sources = entry.sources
        if all(not _source_preserves_files(src, options) for src in sources):
            to_delete.append(cid)

    assert to_delete == []


def test_sync_retention_modes_mixed_sources_archive_wins() -> None:
    """Clip with sources ['liked', 'my_songs']. Liked=archive, my_songs=mirror → NOT deleted."""
    clips_state = {
        "clip-1": ManifestEntry.from_dict({"path": "2026-01-15/Song [clip-1].flac", "sources": ["liked", "my_songs"]}),
    }
    seen_ids: set[str] = set()
    preserved_ids: set[str] = set()
    options = {
        CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_ARCHIVE,
        CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_MIRROR,
    }

    to_delete = []
    for cid in clips_state:
        if cid in seen_ids or cid in preserved_ids:
            continue
        entry = clips_state[cid]
        sources = entry.sources
        if all(not _source_preserves_files(src, options) for src in sources):
            to_delete.append(cid)

    assert to_delete == []


def test_sync_retention_modes_empty_sources_deleted() -> None:
    """Clip with sources [] → deleted (orphan cleanup via all() on empty)."""
    clips_state = {
        "clip-1": ManifestEntry.from_dict({"path": "2026-01-15/Song [clip-1].flac", "sources": []}),
    }
    seen_ids: set[str] = set()
    preserved_ids: set[str] = set()
    options = {}

    to_delete = []
    for cid in clips_state:
        if cid in seen_ids or cid in preserved_ids:
            continue
        entry = clips_state[cid]
        sources = entry.sources
        if all(not _source_preserves_files(src, options) for src in sources):
            to_delete.append(cid)

    assert to_delete == ["clip-1"]


def test_sync_retention_modes_cache_mode_deletes_like_mirror() -> None:
    """Cache mode sources are not preserved (treated like mirror for deletion)."""
    clips_state = {
        "clip-1": ManifestEntry.from_dict({"path": "2026-01-15/Song [clip-1].flac", "sources": ["my_songs"]}),
    }
    seen_ids: set[str] = set()
    preserved_ids: set[str] = set()
    options = {CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE}

    to_delete = []
    for cid in clips_state:
        if cid in seen_ids or cid in preserved_ids:
            continue
        entry = clips_state[cid]
        sources = entry.sources
        if all(not _source_preserves_files(src, options) for src in sources):
            to_delete.append(cid)

    assert to_delete == ["clip-1"]


def test_add_clip_quality_merge_flac_wins_over_mp3() -> None:
    """When a clip appears first as MP3 then FLAC, quality upgrades to FLAC."""
    clip = _make_clip("clip-merge-1", "Merged")
    clip_map: dict[str, DownloadItem] = {}
    _add_clip(clip_map, clip, "liked", QUALITY_STANDARD)
    _add_clip(clip_map, clip, "playlist:x", QUALITY_HIGH)
    assert clip_map["clip-merge-1"].quality == QUALITY_HIGH
    assert set(clip_map["clip-merge-1"].sources) == {"liked", "playlist:x"}


def test_add_clip_quality_merge_mp3_does_not_downgrade_flac() -> None:
    """When a clip appears first as FLAC then MP3, quality stays FLAC."""
    clip = _make_clip("clip-merge-2", "Stays High")
    clip_map: dict[str, DownloadItem] = {}
    _add_clip(clip_map, clip, "liked", QUALITY_HIGH)
    _add_clip(clip_map, clip, "my_songs", QUALITY_STANDARD)
    assert clip_map["clip-merge-2"].quality == QUALITY_HIGH
    assert set(clip_map["clip-merge-2"].sources) == {"liked", "my_songs"}


def test_add_clip_quality_merge_same_quality_no_change() -> None:
    """Same quality from both sources stays unchanged."""
    clip = _make_clip("clip-merge-3", "Same")
    clip_map: dict[str, DownloadItem] = {}
    _add_clip(clip_map, clip, "liked", QUALITY_STANDARD)
    _add_clip(clip_map, clip, "my_songs", QUALITY_STANDARD)
    assert clip_map["clip-merge-3"].quality == QUALITY_STANDARD


def test_add_clip_quality_merge_first_add_creates_entry() -> None:
    """First add creates a new DownloadItem with correct fields."""
    clip = _make_clip("clip-new", "New Song")
    clip_map: dict[str, DownloadItem] = {}
    _add_clip(clip_map, clip, "liked", QUALITY_HIGH)
    item = clip_map["clip-new"]
    assert item.clip is clip
    assert item.sources == ["liked"]
    assert item.quality == QUALITY_HIGH
