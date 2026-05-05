"""Tests for the Downloaded Library seam."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.suno.const import (
    CONF_ALL_PLAYLISTS,
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_MY_SONGS,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_MY_SONGS_COUNT,
    CONF_MY_SONGS_DAYS,
    CONF_MY_SONGS_MINIMUM,
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
    DesiredDownloadPlan,
    DownloadedLibrary,
    DownloadItem,
    InMemoryDownloadedLibraryStorage,
    RenderedAudio,
    _add_clip,
    _build_download_summary,
    _clip_path,
    _get_source_mode,
    _safe_name,
    _source_preserves_files,
    _video_clip_path,
    _write_file,
    _write_m3u8_playlists,
)
from custom_components.suno.library_refresh import SunoData
from custom_components.suno.models import SunoClip, TrackMetadata


def _clip(clip_id: str, title: str = "Song") -> SunoClip:
    return SunoClip(
        id=clip_id,
        title=title,
        audio_url=f"https://cdn1.suno.ai/{clip_id}.mp3",
        image_url="",
        image_large_url="",
        is_liked=True,
        status="complete",
        created_at="2026-03-15T10:00:00Z",
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
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
    ) -> None:
        self.data = data
        self.video_data = video_data
        self.rendered: list[str] = []
        self.render_qualities: list[str] = []
        self.render_metas: list[TrackMetadata] = []
        self.video_calls: list[tuple[str, Path]] = []

    async def fetch_image(self, _image_url: str) -> bytes | None:
        return None

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

    async def retag(self, _target: Path, _meta: TrackMetadata) -> bool:
        return True

    async def download_video(self, video_url: str, target: Path) -> None:
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


async def test_stale_liked_section_preserves_downloaded_file(hass: HomeAssistant, tmp_path: Path) -> None:
    """A stale liked section is not enough authority to delete a local file."""
    clip = _clip("clip-stale-0000-0000-0000-000000000000")
    rel_path = _clip_path(clip, QUALITY_HIGH)
    target = tmp_path / "downloads" / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fLaC" + b"\x00" * 50)
    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip.id: {
                    "path": rel_path,
                    "sources": ["liked"],
                    "size": target.stat().st_size,
                    "quality": QUALITY_HIGH,
                }
            },
            "last_download": None,
        }
    )
    library = DownloadedLibrary(hass, storage, audio=_FakeAudio())
    await library.async_load()

    await library.async_reconcile(
        _options(tmp_path / "downloads"),
        SunoData(stale_sections=("liked_clips",)),
    )

    assert target.exists()
    assert clip.id in library.state["clips"]


async def test_fresh_liked_section_removes_unliked_file(hass: HomeAssistant, tmp_path: Path) -> None:
    """A fresh liked section can prove a local liked file should be removed."""
    clip = _clip("clip-fresh-0000-0000-0000-000000000000")
    rel_path = _clip_path(clip, QUALITY_HIGH)
    target = tmp_path / "downloads" / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fLaC" + b"\x00" * 50)
    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip.id: {
                    "path": rel_path,
                    "sources": ["liked"],
                    "size": target.stat().st_size,
                    "quality": QUALITY_HIGH,
                }
            },
            "last_download": None,
        }
    )
    library = DownloadedLibrary(hass, storage, audio=_FakeAudio())
    await library.async_load()

    await library.async_reconcile(_options(tmp_path / "downloads"), SunoData())

    assert not target.exists()
    assert clip.id not in library.state["clips"]


async def test_empty_cold_start_library_is_not_destructive(hass: HomeAssistant, tmp_path: Path) -> None:
    """An empty cold-start Suno Library must not remove local files."""
    clip = _clip("clip-cold-0000-0000-0000-000000000000")
    rel_path = _clip_path(clip, QUALITY_HIGH)
    target = tmp_path / "downloads" / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fLaC" + b"\x00" * 50)
    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip.id: {
                    "path": rel_path,
                    "sources": ["liked"],
                    "size": target.stat().st_size,
                    "quality": QUALITY_HIGH,
                }
            },
            "last_download": None,
        }
    )
    library = DownloadedLibrary(hass, storage, audio=_FakeAudio())
    await library.async_load()

    await library.async_reconcile(
        _options(tmp_path / "downloads"),
        SunoData(),
        allow_destructive=False,
    )

    assert target.exists()
    assert library.last_result == "Waiting for Library Refresh"


async def test_downloaded_library_promotes_fresh_audio_cache(hass: HomeAssistant, tmp_path: Path) -> None:
    """A fresh matching audio cache file can be promoted before rendering audio."""
    clip = _clip("clip-cache-0000-0000-0000-000000000000")
    cached = tmp_path / "cache" / "clip-cache.flac"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"fLaCcached")
    audio = _FakeAudio()
    library = DownloadedLibrary(
        hass,
        InMemoryDownloadedLibraryStorage(),
        audio=audio,
        cache=_FakeCache(cached),
    )
    await library.async_load()

    await library.async_reconcile(
        _options(tmp_path / "downloads"),
        SunoData(liked_clips=[clip]),
    )

    target = tmp_path / "downloads" / _clip_path(clip, QUALITY_HIGH)
    assert target.read_bytes() == b"fLaCcached"
    assert audio.rendered == []
    assert clip.id in library.state["clips"]


def test_stale_source_membership_is_preserved_when_clip_has_fresh_source(hass: HomeAssistant, tmp_path: Path) -> None:
    """A stale source is not removed from a record just because another source is fresh."""
    clip = _clip("clip-source-0000-0000-0000-000000000000")
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    library.state = {
        "clips": {
            clip.id: {
                "path": _clip_path(clip, QUALITY_HIGH),
                "sources": ["liked", "my_songs"],
                "quality": QUALITY_HIGH,
            }
        }
    }

    plan = library.build_desired(
        {
            **_options(tmp_path / "downloads"),
            CONF_SHOW_MY_SONGS: True,
            CONF_MY_SONGS_COUNT: 1,
            CONF_MY_SONGS_DAYS: None,
        },
        SunoData(clips=[clip], stale_sections=("liked_clips",)),
    )

    assert plan.items[0].sources == ["my_songs", "liked"]


async def test_disabled_download_cleanup_removes_mirror_and_preserves_archive(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Disabling downloads removes Mirror files but preserves Archive files."""
    mirror_clip = _clip("clip-mirror-0000-0000-0000-000000000000", "Mirror")
    archive_clip = _clip("clip-archive-0000-0000-0000-000000000000", "Archive")
    base = tmp_path / "downloads"
    mirror_rel = _clip_path(mirror_clip, QUALITY_HIGH)
    archive_rel = _clip_path(archive_clip, QUALITY_HIGH)
    mirror_target = base / mirror_rel
    archive_target = base / archive_rel
    mirror_target.parent.mkdir(parents=True)
    archive_target.parent.mkdir(parents=True)
    mirror_target.write_bytes(b"fLaCmirror")
    archive_target.write_bytes(b"fLaCarchive")
    (base / "Liked Songs.m3u8").write_text("#EXTM3U\n")
    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                mirror_clip.id: {
                    "path": mirror_rel,
                    "sources": ["liked"],
                    "source_modes": {"liked": DOWNLOAD_MODE_MIRROR},
                    "size": mirror_target.stat().st_size,
                    "quality": QUALITY_HIGH,
                },
                archive_clip.id: {
                    "path": archive_rel,
                    "sources": ["playlist:archived"],
                    "source_modes": {"playlist:archived": DOWNLOAD_MODE_ARCHIVE},
                    "size": archive_target.stat().st_size,
                    "quality": QUALITY_HIGH,
                },
            },
            "last_download": None,
        }
    )
    library = DownloadedLibrary(hass, storage, audio=_FakeAudio())
    await library.async_load()

    await library.async_cleanup_disabled_downloads(
        {
            **_options(base),
            CONF_SHOW_LIKED: True,
            CONF_SHOW_PLAYLISTS: True,
        }
    )

    assert not mirror_target.exists()
    assert archive_target.exists()
    assert mirror_clip.id not in library.state["clips"]
    assert archive_clip.id in library.state["clips"]
    assert library.state["clips"][archive_clip.id]["sources"] == ["playlist:archived"]
    assert not (base / "Liked Songs.m3u8").exists()


async def test_disabled_download_cleanup_uses_previous_options_for_legacy_state(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Legacy state without source modes can still clean Mirror files during a live transition."""
    clip = _clip("clip-legacy-0000-0000-0000-000000000000")
    base = tmp_path / "downloads"
    rel_path = _clip_path(clip, QUALITY_HIGH)
    target = base / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fLaClegacy")
    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip.id: {
                    "path": rel_path,
                    "sources": ["liked"],
                    "size": target.stat().st_size,
                    "quality": QUALITY_HIGH,
                }
            },
            "last_download": None,
        }
    )
    library = DownloadedLibrary(hass, storage, audio=_FakeAudio())
    await library.async_load()

    await library.async_cleanup_disabled_downloads(
        {
            **_options(base),
            CONF_SHOW_LIKED: True,
        },
        previous_options={
            **_options(base),
            CONF_SHOW_LIKED: True,
            CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_MIRROR,
        },
    )

    assert not target.exists()
    assert clip.id not in library.state["clips"]


async def test_disabled_download_cleanup_preserves_unknown_legacy_state(hass: HomeAssistant, tmp_path: Path) -> None:
    """Legacy state without previous modes is preserved instead of risking Archive deletion."""
    clip = _clip("clip-unknown-0000-0000-0000-000000000000")
    base = tmp_path / "downloads"
    rel_path = _clip_path(clip, QUALITY_HIGH)
    target = base / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fLaCunknown")
    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip.id: {
                    "path": rel_path,
                    "sources": ["liked"],
                    "size": target.stat().st_size,
                    "quality": QUALITY_HIGH,
                }
            },
            "last_download": None,
        }
    )
    library = DownloadedLibrary(hass, storage, audio=_FakeAudio())
    await library.async_load()

    await library.async_cleanup_disabled_downloads(
        {
            **_options(base),
            CONF_SHOW_LIKED: True,
            CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_CACHE,
        }
    )

    assert target.exists()
    assert clip.id in library.state["clips"]


# ── async_load / reconcile guards ───────────────────────────────


async def test_async_load_loads_persisted_state(hass: HomeAssistant) -> None:
    """async_load should load persisted state from storage."""
    storage = InMemoryDownloadedLibraryStorage({"clips": {"abc": {}}, "last_download": "2026-01-01"})
    library = DownloadedLibrary(hass, storage)
    await library.async_load()
    assert library.total_files == 1
    assert library.last_download == "2026-01-01"


async def test_async_load_handles_empty_storage(hass: HomeAssistant) -> None:
    """async_load with empty storage should keep defaults."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    await library.async_load()
    assert library.total_files == 0
    assert library.last_download is None


async def test_async_reconcile_skips_when_path_empty(hass: HomeAssistant) -> None:
    """async_reconcile should do nothing when download_path option is empty."""
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=audio)
    await library.async_reconcile({CONF_DOWNLOAD_PATH: ""}, SunoData())
    assert library.running is False
    assert library.last_result == ""
    assert audio.rendered == []


async def test_async_reconcile_skips_when_no_path_option(hass: HomeAssistant) -> None:
    """async_reconcile should do nothing when download_path key is missing."""
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=audio)
    await library.async_reconcile({}, SunoData())
    assert library.running is False
    assert library.last_result == ""
    assert audio.rendered == []


async def test_async_reconcile_skips_when_already_running(hass: HomeAssistant) -> None:
    """async_reconcile should not run concurrently."""
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=audio)
    library.running = True
    await library.async_reconcile({CONF_DOWNLOAD_PATH: "/safe/path"}, SunoData())  # noqa: S108
    assert library.running is True
    assert library.last_result == ""
    assert audio.rendered == []


# ── Properties / source_breakdown ───────────────────────────────


async def test_default_engine_properties(hass: HomeAssistant) -> None:
    """A fresh DownloadedLibrary has zeroed counters and no last_download."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    assert library.running is False
    assert library.total_files == 0
    assert library.pending == 0
    assert library.errors == 0
    assert library.last_download is None


async def test_source_breakdown_empty_state(hass: HomeAssistant) -> None:
    """Empty state returns empty source breakdown."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    assert library.source_breakdown == {}


async def test_source_breakdown_counts_per_source(hass: HomeAssistant) -> None:
    """source_breakdown counts clips per source tag."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    library.state = {
        "clips": {
            "c1": {"sources": ["liked"]},
            "c2": {"sources": ["liked", "playlist:abc"]},
            "c3": {"sources": ["my_songs"]},
            "c4": {"sources": ["playlist:abc"]},
        },
        "last_download": None,
    }
    breakdown = library.source_breakdown
    assert breakdown["liked"] == 2
    assert breakdown["playlist:abc"] == 2
    assert breakdown["my_songs"] == 1


# ── cleanup_tmp_files ───────────────────────────────────────────


async def test_cleanup_tmp_files_removes_only_tmp(hass: HomeAssistant, tmp_path: Path) -> None:
    """cleanup_tmp_files removes .tmp files but preserves real audio files."""
    download_dir = tmp_path / "mirror"
    download_dir.mkdir()
    (download_dir / "song.flac.tmp").write_bytes(b"partial")
    (download_dir / "real.flac").write_bytes(b"fLaC")

    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    await library.cleanup_tmp_files(str(download_dir))

    assert not (download_dir / "song.flac.tmp").exists()
    assert (download_dir / "real.flac").exists()


# ── _write_file (filesystem helper) ─────────────────────────────


async def test_write_file_creates_file(hass: HomeAssistant, tmp_path: Path) -> None:
    """_write_file performs an atomic write and leaves no .tmp behind."""
    target = tmp_path / "subdir" / "output.flac"
    data = b"fLaC" + b"\x00" * 50

    await _write_file(hass, target, data)

    assert target.exists()
    assert target.read_bytes() == data
    assert not target.with_suffix(".tmp").exists()


async def test_write_file_failure_cleans_tmp(hass: HomeAssistant, tmp_path: Path) -> None:
    """_write_file removes the .tmp file when write fails."""
    target = tmp_path / "output.flac"

    with patch.object(Path, "write_bytes", side_effect=OSError("disk full")):
        try:
            await _write_file(hass, target, b"data")
        except OSError:
            pass

    assert not target.with_suffix(".tmp").exists()
    assert not target.exists()


# ── Reconcile happy path / orphan / manifest ────────────────────


async def test_async_reconcile_downloads_new_clips(hass: HomeAssistant, tmp_path: Path) -> None:
    """async_reconcile downloads clips not yet in state."""
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=audio)
    await library.async_load()

    suno_data = SunoData(liked_clips=[_clip("clip-new-0000-0000-0000-000000000000", "Test Song")])
    opts = {
        CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
        CONF_SHOW_LIKED: True,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
    }
    await library.async_reconcile(opts, suno_data)

    assert library.total_files == 1
    assert library.errors == 0


async def test_async_reconcile_deletes_orphaned_clips(hass: HomeAssistant, tmp_path: Path) -> None:
    """async_reconcile deletes files for clips no longer in desired set."""
    sync_dir = tmp_path / "mirror"
    sync_dir.mkdir()
    orphan = sync_dir / "old-file.flac"
    orphan.write_bytes(b"fLaC" + b"\x00" * 50)

    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                "orphan-id": {
                    "path": "old-file.flac",
                    "title": "Old Song",
                    "created": "2026-01-01",
                    "sources": ["liked"],
                }
            },
            "last_download": None,
        }
    )
    library = DownloadedLibrary(hass, storage, audio=_FakeAudio())
    await library.async_load()
    assert library.total_files == 1

    opts = {
        CONF_DOWNLOAD_PATH: str(sync_dir),
        CONF_SHOW_LIKED: True,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
    }
    await library.async_reconcile(opts, SunoData())

    assert library.total_files == 0
    assert not orphan.exists()


async def test_async_reconcile_writes_manifest(hass: HomeAssistant, tmp_path: Path) -> None:
    """async_reconcile writes the .suno_download.json manifest into the download dir."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=_FakeAudio())
    await library.async_load()

    sync_dir = tmp_path / "mirror"
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(),
    )

    manifest = sync_dir / ".suno_download.json"
    assert manifest.exists()
    data = json.loads(manifest.read_text())
    assert "last_download" in data
    assert "clips" in data


# ── build_desired API failure / my_songs filtering ──────────────


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


# ── Quality tracking ────────────────────────────────────────────


def _clip_with_display(
    clip_id: str,
    title: str = "Song",
    created: str = "2026-03-15T10:00:00Z",
    display_name: str = "testuser",
    image_url: str | None = None,
    image_large_url: str | None = None,
    video_url: str = "",
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
    )


async def test_quality_change_deletes_old_and_redownloads(hass: HomeAssistant, tmp_path: Path) -> None:
    """A change from high → standard removes the old FLAC and writes a new MP3."""
    clip_id = "clip0001-0000-0000-0000-000000000000"
    sync_dir = tmp_path / "mirror"
    sync_dir.mkdir()
    old_file = sync_dir / "2026-03-15" / "Song [clip0001].flac"
    old_file.parent.mkdir(parents=True)
    old_file.write_bytes(b"fLaC" + b"\x00" * 50)

    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip_id: {
                    "path": "2026-03-15/Song [clip0001].flac",
                    "title": "Song",
                    "created": "2026-03-15",
                    "sources": ["liked"],
                    "size": 54,
                    "meta_hash": "abc",
                    "quality": "high",
                }
            },
            "last_download": None,
        }
    )
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, storage, audio=audio)
    await library.async_load()

    clip = _clip(clip_id, "Song")
    plan = DesiredDownloadPlan(
        items=[DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_STANDARD)],
        preserved_ids=set(),
        source_to_name={"liked": "Liked Songs"},
        playlist_order={},
    )
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
        desired_plan=plan,
    )

    assert audio.render_qualities == [QUALITY_STANDARD]
    assert not old_file.exists()
    entry = library.state["clips"][clip_id]
    assert entry["quality"] == QUALITY_STANDARD
    assert entry["path"].endswith(".mp3")


async def test_quality_match_skips_redownload(hass: HomeAssistant, tmp_path: Path) -> None:
    """The same quality on the desired plan does not trigger a redownload."""
    clip_id = "clip0002-0000-0000-0000-000000000000"
    target = tmp_path / "mirror" / "2026-03-15" / "Song [clip0002].flac"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"fLaC" + b"\x00" * 50)

    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip_id: {
                    "path": "2026-03-15/Song [clip0002].flac",
                    "title": "Song",
                    "created": "2026-03-15",
                    "sources": ["liked"],
                    "size": 54,
                    "meta_hash": "9ad1d8ab369d",
                    "quality": "high",
                }
            },
            "last_download": None,
        }
    )
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, storage, audio=audio)
    await library.async_load()

    clip = _clip(clip_id, "Song")
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
    )

    assert audio.rendered == []


async def test_quality_stored_in_state_after_download(hass: HomeAssistant, tmp_path: Path) -> None:
    """After a high-quality download, quality is recorded in state."""
    clip_id = "clip0003-0000-0000-0000-000000000000"
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=audio)
    await library.async_load()

    clip = _clip(clip_id, "Song")
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
    )

    entry = library.state["clips"][clip_id]
    assert entry["quality"] == QUALITY_HIGH


async def test_quality_downgrade_on_source_removal(hass: HomeAssistant, tmp_path: Path) -> None:
    """Removing the high-quality source downgrades FLAC → MP3 end-to-end."""
    clip_id = "downgrde-0000-0000-0000-000000000000"
    sync_dir = tmp_path / "mirror"
    sync_dir.mkdir()

    clip = _clip_with_display(clip_id, "Downgrader", display_name="testuser")

    flac_rel = _clip_path(clip, QUALITY_HIGH)
    flac_path = sync_dir / flac_rel
    flac_path.parent.mkdir(parents=True)
    flac_path.write_bytes(b"fLaC" + b"\x00" * 50)

    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip_id: {
                    "path": flac_rel,
                    "title": "Downgrader",
                    "created": "2026-03-15",
                    "sources": ["liked", "my_songs"],
                    "size": 54,
                    "meta_hash": "deadbeef0000",
                    "quality": QUALITY_HIGH,
                }
            },
            "last_download": None,
        }
    )
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, storage, audio=audio)
    await library.async_load()

    plan = DesiredDownloadPlan(
        items=[DownloadItem(clip=clip, sources=["my_songs"], quality=QUALITY_STANDARD)],
        preserved_ids=set(),
        source_to_name={"liked": "Liked Songs"},
        playlist_order={},
    )
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_SHOW_MY_SONGS: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(clips=[clip]),
        desired_plan=plan,
    )

    assert audio.render_qualities == [QUALITY_STANDARD]
    assert not flac_path.exists()
    mp3_rel = _clip_path(clip, QUALITY_STANDARD)
    mp3_path = sync_dir / mp3_rel
    assert mp3_path.exists()

    entry = library.state["clips"][clip_id]
    assert entry["quality"] == QUALITY_STANDARD
    assert entry["path"] == mp3_rel
    assert entry["sources"] == ["my_songs"]

    audio_files = [p for p in sync_dir.rglob("*") if p.is_file() and p.suffix.lower() in (".flac", ".mp3")]
    assert len(audio_files) == 1, f"expected one audio file, got: {audio_files}"


# ── Download clip rendering branch ──────────────────────────────


async def test_download_clip_uses_flac_for_high_quality(hass: HomeAssistant, tmp_path: Path) -> None:
    """A high-quality plan renders flac via the audio adapter."""
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=audio)
    await library.async_load()

    clip = _clip("clip-flac-0000-0000-0000-000000000000", "FLAC Song")
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
    )

    assert audio.render_qualities == [QUALITY_HIGH]
    assert library.total_files == 1
    assert library.errors == 0


async def test_download_clip_uses_mp3_for_standard_quality(hass: HomeAssistant, tmp_path: Path) -> None:
    """A standard-quality plan renders mp3 via the audio adapter."""
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=audio)
    await library.async_load()

    clip = _clip("clip-mp3-00000-0000-0000-000000000000", "MP3 Song")
    plan = DesiredDownloadPlan(
        items=[DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_STANDARD)],
        preserved_ids=set(),
        source_to_name={"liked": "Liked Songs"},
        playlist_order={},
    )
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
        desired_plan=plan,
    )

    assert audio.render_qualities == [QUALITY_STANDARD]
    entry = library.state["clips"]["clip-mp3-00000-0000-0000-000000000000"]
    assert entry["quality"] == QUALITY_STANDARD
    assert entry["path"].endswith(".mp3")


async def test_download_writes_through_cache(hass: HomeAssistant, tmp_path: Path) -> None:
    """A successful download writes the rendered bytes through the audio cache."""
    audio = _FakeAudio()
    cache = AsyncMock()
    cache.async_get = AsyncMock(return_value=None)
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=audio, cache=cache)
    await library.async_load()

    clip = _clip("clip-cache-0000-0000-0000-000000000000", "Cached Song")
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
    )

    cache.async_put.assert_awaited_once()
    args = cache.async_put.await_args.args
    assert args[0] == "clip-cache-0000-0000-0000-000000000000"
    assert args[1] == "flac"
    assert args[2] == b"fLaC" + b"\x00" * 50


# ── Disk reconciliation ─────────────────────────────────────────


async def test_reconcile_disk_removes_orphan_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """Orphan .flac files not in clips_state are deleted."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    orphan = tmp_path / "2026-01-01" / "Orphan [deadbeef].flac"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"fake")

    removed = await library._reconcile_disk(tmp_path, {})
    assert removed == 1
    assert not orphan.exists()


async def test_reconcile_disk_keeps_tracked_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """Files referenced in clips_state are not deleted."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    rel = "2026-01-01/Tracked [abcd1234].flac"
    tracked = tmp_path / rel
    tracked.parent.mkdir(parents=True)
    tracked.write_bytes(b"real")

    clips_state = {"clip-id": {"path": rel}}
    removed = await library._reconcile_disk(tmp_path, clips_state)
    assert removed == 0
    assert tracked.exists()


async def test_reconcile_disk_skips_non_audio(hass: HomeAssistant, tmp_path: Path) -> None:
    """Non-audio files (.json, .m3u8, .tmp) are left alone."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    for name in (".suno_download.json", "Liked Songs.m3u8", "partial.tmp"):
        (tmp_path / name).write_text("x")

    removed = await library._reconcile_disk(tmp_path, {})
    assert removed == 0
    assert all((tmp_path / n).exists() for n in (".suno_download.json", "Liked Songs.m3u8", "partial.tmp"))


async def test_reconcile_disk_cleans_empty_dirs(hass: HomeAssistant, tmp_path: Path) -> None:
    """Empty parent directories are removed after orphan deletion."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    orphan = tmp_path / "2026-01-01" / "Gone [deadbeef].flac"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"bye")

    removed = await library._reconcile_disk(tmp_path, {})
    assert removed == 1
    assert not orphan.parent.exists()


async def test_reconcile_disk_keeps_mp4_sidecar_next_to_audio(hass: HomeAssistant, tmp_path: Path) -> None:
    """mp4 sidecars sharing an audio file's basename are not treated as orphans."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    rel = "artist/Song/artist-Song [abcd1234].flac"
    audio_path = tmp_path / rel
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"fLaC" + b"\x00" * 50)
    video = audio_path.with_suffix(".mp4")
    video.write_bytes(b"\x00\x00\x00\x1cftypisom")

    clips_state = {"abcd1234": {"path": rel}}
    removed = await library._reconcile_disk(tmp_path, clips_state)
    assert removed == 0
    assert audio_path.exists()
    assert video.exists()


async def test_reconcile_disk_removes_orphan_mp4_in_legacy_music_videos(hass: HomeAssistant, tmp_path: Path) -> None:
    """An orphan mp4 left behind in the legacy music-videos/ tree is cleaned up."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    legacy_video = tmp_path / "music-videos" / "artist" / "artist-Song [abcd1234].mp4"
    legacy_video.parent.mkdir(parents=True)
    legacy_video.write_bytes(b"\x00\x00\x00\x1cftypisom")

    removed = await library._reconcile_disk(tmp_path, {})
    assert removed == 1
    assert not legacy_video.exists()


# ── get_downloaded_path edge cases ──────────────────────────────


async def test_get_downloaded_path_meta_hash_mismatch(hass: HomeAssistant, tmp_path: Path) -> None:
    """meta_hash mismatch returns None to trigger re-download."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    synced_file = tmp_path / "2026-01-15" / "Song [abcd1234].flac"
    synced_file.parent.mkdir(parents=True)
    synced_file.write_bytes(b"fLaC")
    library.download_path = str(tmp_path)
    library.state = {
        "clips": {
            "abcd1234": {
                "path": "2026-01-15/Song [abcd1234].flac",
                "meta_hash": "old_hash_abc",
            }
        },
    }
    assert library.get_downloaded_path("abcd1234", meta_hash="new_hash_xyz") is None


async def test_get_downloaded_path_matching_hash(hass: HomeAssistant, tmp_path: Path) -> None:
    """Matching meta_hash returns the file path."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    synced_file = tmp_path / "2026-01-15" / "Song [abcd1234].flac"
    synced_file.parent.mkdir(parents=True)
    synced_file.write_bytes(b"fLaC")
    library.download_path = str(tmp_path)
    library.state = {
        "clips": {
            "abcd1234": {
                "path": "2026-01-15/Song [abcd1234].flac",
                "meta_hash": "same_hash",
            }
        },
    }
    result = library.get_downloaded_path("abcd1234", meta_hash="same_hash")
    assert result is not None
    assert result.name == "Song [abcd1234].flac"


async def test_get_downloaded_path_no_download_path(hass: HomeAssistant) -> None:
    """Returns None when download_path is empty."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    library.download_path = ""
    assert library.get_downloaded_path("any-id") is None


async def test_get_downloaded_path_clip_not_in_state(hass: HomeAssistant) -> None:
    """Returns None when clip ID is not in state."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    library.download_path = "/some/path"
    library.state = {"clips": {}}
    assert library.get_downloaded_path("missing-id") is None


# ── library_size_mb ─────────────────────────────────────────────


async def test_library_size_mb_calculation(hass: HomeAssistant) -> None:
    """library_size_mb sums file sizes and converts to MB."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    library.state = {
        "clips": {
            "c1": {"size": 1048576},  # 1 MB
            "c2": {"size": 2097152},  # 2 MB
            "c3": {"size": 524288},  # 0.5 MB
        },
    }
    assert library.library_size_mb == 3.5


async def test_library_size_mb_empty(hass: HomeAssistant) -> None:
    """library_size_mb is 0.0 when no clips."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    assert library.library_size_mb == 0.0


async def test_library_size_mb_missing_size(hass: HomeAssistant) -> None:
    """Clips without 'size' key contribute 0."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    library.state = {
        "clips": {
            "c1": {"path": "song.flac"},  # no size key
            "c2": {"size": 1048576},
        },
    }
    assert library.library_size_mb == 1.0


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


async def test_async_reconcile_empty_path_skips_work(hass: HomeAssistant) -> None:
    """download_path='' means no downloads happen."""
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=audio)
    await library.async_load()

    clip = _clip("clip-empty-1", "Liked Song")
    await library.async_reconcile({CONF_DOWNLOAD_PATH: ""}, SunoData(liked_clips=[clip]))

    assert audio.rendered == []
    assert library.total_files == 0


# ── My songs minimum padding ────────────────────────────────────


async def test_my_songs_minimum_pads_when_below_floor(hass: HomeAssistant) -> None:
    """Minimum pads with most recent clips when intersection is below threshold."""
    from datetime import timedelta

    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    now = datetime.now(tz=UTC)
    recent_ts = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_ts = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    clips = [_make_dated_clip(f"clip-new-{i}", created=recent_ts) for i in range(3)] + [
        _make_dated_clip(f"clip-old-{i}", created=old_ts) for i in range(7)
    ]
    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_SHOW_MY_SONGS: True,
        CONF_MY_SONGS_COUNT: 5,
        CONF_MY_SONGS_DAYS: 7,
        CONF_MY_SONGS_MINIMUM: 7,
    }
    plan = library.build_desired(options, SunoData(clips=clips))
    assert len(plan.items) == 7


async def test_my_songs_minimum_disabled_when_zero(hass: HomeAssistant) -> None:
    """Minimum=0 has no effect."""
    from datetime import timedelta

    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    now = datetime.now(tz=UTC)
    old_ts = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    clips = [_make_dated_clip(f"clip-{i}", created=old_ts) for i in range(5)]
    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_SHOW_MY_SONGS: True,
        CONF_MY_SONGS_COUNT: 3,
        CONF_MY_SONGS_DAYS: 7,
        CONF_MY_SONGS_MINIMUM: 0,
    }
    plan = library.build_desired(options, SunoData(clips=clips))
    assert len(plan.items) == 0


async def test_my_songs_minimum_alone_triggers_my_songs(hass: HomeAssistant) -> None:
    """Minimum works when count=None and days=None (both filters disabled)."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    clips = [_make_dated_clip(f"clip-{i}") for i in range(10)]
    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_SHOW_MY_SONGS: True,
        CONF_MY_SONGS_COUNT: None,
        CONF_MY_SONGS_DAYS: None,
        CONF_MY_SONGS_MINIMUM: 5,
    }
    plan = library.build_desired(options, SunoData(clips=clips))
    assert len(plan.items) == 5


async def test_my_songs_minimum_capped_by_library_size(hass: HomeAssistant) -> None:
    """Minimum can't exceed available clips."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    clips = [_make_dated_clip(f"clip-{i}") for i in range(3)]
    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_SHOW_MY_SONGS: True,
        CONF_MY_SONGS_COUNT: None,
        CONF_MY_SONGS_DAYS: None,
        CONF_MY_SONGS_MINIMUM: 100,
    }
    plan = library.build_desired(options, SunoData(clips=clips))
    assert len(plan.items) == 3


async def test_my_songs_minimum_overrides_expired_days(hass: HomeAssistant) -> None:
    """Minimum pads even when all clips are outside lookback period."""
    from datetime import timedelta

    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    now = datetime.now(tz=UTC)
    old_ts = (now - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    clips = [_make_dated_clip(f"clip-{i}", created=old_ts) for i in range(8)]
    options = {
        CONF_SHOW_LIKED: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_SHOW_MY_SONGS: True,
        CONF_MY_SONGS_COUNT: None,
        CONF_MY_SONGS_DAYS: 7,
        CONF_MY_SONGS_MINIMUM: 5,
    }
    plan = library.build_desired(options, SunoData(clips=clips))
    assert len(plan.items) == 5


# ── last_result persistence + bootstrap display ─────────────────


async def test_last_result_persisted_and_restored(hass: HomeAssistant) -> None:
    """last_result is saved to state and restored on async_load."""
    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {},
            "last_download": "2026-03-22T08:00:00+00:00",
            "last_result": "3 new songs, 1 removal",
        }
    )
    library = DownloadedLibrary(hass, storage)
    await library.async_load()

    assert library.last_result == "3 new songs, 1 removal"


async def test_async_reconcile_downloads_all_clips_without_batch_cap(hass: HomeAssistant, tmp_path: Path) -> None:
    """All clips download in one reconcile — no batch caps or continuation."""
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=audio)
    await library.async_load()

    num_clips = 30
    clips = [_clip(f"clip-{i:04d}-0000-0000-0000-000000000000", f"Song {i}") for i in range(num_clips)]
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=clips),
    )

    assert library.pending == 0
    assert library.total_files == num_clips


async def test_async_reconcile_initial_sync_label(hass: HomeAssistant, tmp_path: Path) -> None:
    """Initial reconcile records 'Initial sync' in status_callback updates."""
    results_during: list[str] = []

    def capture(status: object) -> None:
        results_during.append(status.last_result)  # type: ignore[attr-defined]

    audio = _FakeAudio()
    library = DownloadedLibrary(
        hass,
        InMemoryDownloadedLibraryStorage(),
        audio=audio,
        status_callback=capture,
    )
    await library.async_load()

    clips = [_clip(f"clip-{i:04d}-0000-0000-0000-000000000000", f"Song {i}") for i in range(3)]
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=clips),
        initial=True,
    )

    assert any("Initial sync" in r for r in results_during)


# ── Path migration (rename instead of redownload) ───────────────


async def test_migration_renames_file_instead_of_redownloading(hass: HomeAssistant, tmp_path: Path) -> None:
    """When _clip_path returns a different path, the file is renamed."""
    clip_id = "abcd1234-0000-0000-0000-000000000000"
    sync_dir = tmp_path / "mirror"
    old_rel = "old_artist/Song/old_artist-Song [abcd1234].flac"
    old_file = sync_dir / old_rel
    old_file.parent.mkdir(parents=True)
    old_file.write_bytes(b"fLaC" + b"\x00" * 50)

    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip_id: {
                    "path": old_rel,
                    "title": "Song",
                    "created": "2026-03-15",
                    "sources": ["liked"],
                    "size": 54,
                    "meta_hash": "9ad1d8ab369d",
                    "quality": "high",
                }
            },
            "last_download": None,
        }
    )
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, storage, audio=audio)
    await library.async_load()

    clip = _clip_with_display(clip_id, "Song", display_name="newartist")
    plan = DesiredDownloadPlan(
        items=[DownloadItem(clip=clip, sources=["liked"], quality="high")],
        preserved_ids=set(),
        source_to_name={"liked": "Liked Songs"},
        playlist_order={},
    )
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
        desired_plan=plan,
    )

    assert not old_file.exists()
    new_rel = _clip_path(clip, "high")
    new_file = sync_dir / new_rel
    assert new_file.exists()
    assert library.state["clips"][clip_id]["path"] == new_rel


async def test_migration_moves_mp4_sidecar(hass: HomeAssistant, tmp_path: Path) -> None:
    """Video .mp4 sidecar follows its audio file when the clip is renamed."""
    clip_id = "abcd1234-0000-0000-0000-000000000000"
    sync_dir = tmp_path / "mirror"
    old_rel = "old_artist/Song/old_artist-Song [abcd1234].flac"
    old_file = sync_dir / old_rel
    old_file.parent.mkdir(parents=True)
    old_file.write_bytes(b"fLaC" + b"\x00" * 50)
    old_video = old_file.with_suffix(".mp4")
    old_video.write_bytes(b"\x00\x00\x00\x1cftypisom")

    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip_id: {
                    "path": old_rel,
                    "title": "Song",
                    "created": "2026-03-15",
                    "sources": ["liked"],
                    "size": 54,
                    "meta_hash": "9ad1d8ab369d",
                    "quality": "high",
                }
            },
            "last_download": None,
        }
    )
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, storage, audio=audio)
    await library.async_load()

    clip = _clip_with_display(clip_id, "Song", display_name="newartist")
    plan = DesiredDownloadPlan(
        items=[DownloadItem(clip=clip, sources=["liked"], quality="high")],
        preserved_ids=set(),
        source_to_name={"liked": "Liked Songs"},
        playlist_order={},
    )
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
        desired_plan=plan,
    )

    new_video = sync_dir / _video_clip_path(clip)
    assert new_video.exists()
    assert new_video.read_bytes() == b"\x00\x00\x00\x1cftypisom"
    assert not old_video.exists()
    assert "music-videos" not in str(new_video.relative_to(sync_dir))
    new_audio = sync_dir / _clip_path(clip, "high")
    assert new_video.parent == new_audio.parent


async def test_migration_cleans_old_parent_dirs(hass: HomeAssistant, tmp_path: Path) -> None:
    """Empty parent directories are cleaned up after migration."""
    clip_id = "abcd1234-0000-0000-0000-000000000000"
    sync_dir = tmp_path / "mirror"
    old_rel = "old_artist/OldTitle/old_artist-OldTitle [abcd1234].flac"
    old_file = sync_dir / old_rel
    old_file.parent.mkdir(parents=True)
    old_file.write_bytes(b"fLaC" + b"\x00" * 50)

    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip_id: {
                    "path": old_rel,
                    "title": "OldTitle",
                    "created": "2026-03-15",
                    "sources": ["liked"],
                    "size": 54,
                    "meta_hash": "8692c463e866",
                    "quality": "high",
                }
            },
            "last_download": None,
        }
    )
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, storage, audio=audio)
    await library.async_load()

    clip = _clip_with_display(clip_id, "NewTitle", display_name="newartist")
    plan = DesiredDownloadPlan(
        items=[DownloadItem(clip=clip, sources=["liked"], quality="high")],
        preserved_ids=set(),
        source_to_name={"liked": "Liked Songs"},
        playlist_order={},
    )
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
        desired_plan=plan,
    )

    assert not (sync_dir / "old_artist" / "OldTitle").exists()
    assert not (sync_dir / "old_artist").exists()


# ── Cover art handling ──────────────────────────────────────────


async def test_cover_jpg_written_on_download(hass: HomeAssistant, tmp_path: Path) -> None:
    """cover.jpg is written when a clip is downloaded with an image URL."""
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=audio)
    await library.async_load()

    clip = _clip_with_display(
        "clip-cover-0000-0000-0000-000000000000",
        "Cover Song",
        image_url="https://cdn2.suno.ai/image_abcd.jpeg",
    )
    fake_image = b"\xff\xd8\xff\xe0JFIF"

    with (
        patch("custom_components.suno.downloaded_library.async_get_clientsession"),
        patch(
            "custom_components.suno.downloaded_library.fetch_album_art",
            new_callable=AsyncMock,
            return_value=fake_image,
        ),
    ):
        await library.async_reconcile(
            {
                CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
                CONF_SHOW_LIKED: True,
                CONF_ALL_PLAYLISTS: False,
                CONF_PLAYLISTS: [],
            },
            SunoData(liked_clips=[clip]),
        )

    rel_path = _clip_path(clip, "high")
    cover_path = (tmp_path / "mirror" / rel_path).parent / "cover.jpg"
    assert cover_path.exists()
    assert cover_path.read_bytes() == fake_image


async def test_cover_hash_written_alongside_cover(hass: HomeAssistant, tmp_path: Path) -> None:
    """.cover_hash file is written alongside cover.jpg."""
    import hashlib

    audio = _FakeAudio()
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=audio)
    await library.async_load()

    image_url = "https://cdn2.suno.ai/image_hashtest.jpeg"
    clip = _clip_with_display(
        "clip-hash-0000-0000-0000-000000000000",
        "Hash Song",
        image_url=image_url,
    )
    fake_image = b"\xff\xd8\xff\xe0JFIF"

    with (
        patch("custom_components.suno.downloaded_library.async_get_clientsession"),
        patch(
            "custom_components.suno.downloaded_library.fetch_album_art",
            new_callable=AsyncMock,
            return_value=fake_image,
        ),
    ):
        await library.async_reconcile(
            {
                CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
                CONF_SHOW_LIKED: True,
                CONF_ALL_PLAYLISTS: False,
                CONF_PLAYLISTS: [],
            },
            SunoData(liked_clips=[clip]),
        )

    rel_path = _clip_path(clip, "high")
    hash_path = (tmp_path / "mirror" / rel_path).parent / ".cover_hash"
    assert hash_path.exists()
    expected_hash = hashlib.md5(image_url.encode()).hexdigest()[:12]  # noqa: S324
    assert hash_path.read_text().strip() == expected_hash


async def test_cover_art_refreshed_on_hash_change(hass: HomeAssistant, tmp_path: Path) -> None:
    """Cover art is refreshed when image URL hash differs from stored .cover_hash."""
    import hashlib

    clip_id = "clip-refresh-000-0000-0000-000000000000"
    new_image_url = "https://cdn2.suno.ai/new_image.jpeg"
    clip = _clip_with_display(clip_id, "Refresh Song", image_url=new_image_url)
    rel_path = _clip_path(clip, "high")

    sync_dir = tmp_path / "mirror"
    target = sync_dir / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fLaC" + b"\x00" * 50)

    cover_path = target.parent / "cover.jpg"
    hash_path = target.parent / ".cover_hash"
    cover_path.write_bytes(b"old_image_data")
    hash_path.write_text("old_hash_value")

    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip_id: {
                    "path": rel_path,
                    "title": "Refresh Song",
                    "created": "2026-03-15",
                    "sources": ["liked"],
                    "size": 54,
                    "meta_hash": "5ed3d7d7bb17",
                    "quality": "high",
                }
            },
            "last_download": None,
        }
    )
    library = DownloadedLibrary(hass, storage, audio=_FakeAudio())
    await library.async_load()

    plan = DesiredDownloadPlan(
        items=[DownloadItem(clip=clip, sources=["liked"], quality="high")],
        preserved_ids=set(),
        source_to_name={"liked": "Liked Songs"},
        playlist_order={},
    )
    new_image_data = b"\xff\xd8\xff\xe0NEW_IMAGE"

    with (
        patch("custom_components.suno.downloaded_library.async_get_clientsession"),
        patch(
            "custom_components.suno.downloaded_library.fetch_album_art",
            new_callable=AsyncMock,
            return_value=new_image_data,
        ) as mock_fetch,
    ):
        await library.async_reconcile(
            {
                CONF_DOWNLOAD_PATH: str(sync_dir),
                CONF_SHOW_LIKED: True,
                CONF_ALL_PLAYLISTS: False,
                CONF_PLAYLISTS: [],
            },
            SunoData(liked_clips=[clip]),
            desired_plan=plan,
        )

    assert cover_path.read_bytes() == new_image_data
    expected_hash = hashlib.md5(new_image_url.encode()).hexdigest()[:12]  # noqa: S324
    assert hash_path.read_text().strip() == expected_hash
    mock_fetch.assert_called()


async def test_cover_art_not_refetched_when_hash_matches(hass: HomeAssistant, tmp_path: Path) -> None:
    """Cover art is NOT re-fetched when .cover_hash matches current image URL."""
    import hashlib

    clip_id = "clip-cached-000-0000-0000-000000000000"
    image_url = "https://cdn2.suno.ai/same_image.jpeg"
    clip = _clip_with_display(clip_id, "Cached Song", image_url=image_url)
    rel_path = _clip_path(clip, "high")

    sync_dir = tmp_path / "mirror"
    target = sync_dir / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fLaC" + b"\x00" * 50)

    cover_path = target.parent / "cover.jpg"
    hash_path = target.parent / ".cover_hash"
    existing_image = b"existing_cover_data"
    cover_path.write_bytes(existing_image)
    url_hash = hashlib.md5(image_url.encode()).hexdigest()[:12]  # noqa: S324
    hash_path.write_text(url_hash)

    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip_id: {
                    "path": rel_path,
                    "title": "Cached Song",
                    "created": "2026-03-15",
                    "sources": ["liked"],
                    "size": 54,
                    "meta_hash": "ea0d9c4102fe",
                    "quality": "high",
                }
            },
            "last_download": None,
        }
    )
    library = DownloadedLibrary(hass, storage, audio=_FakeAudio())
    await library.async_load()

    plan = DesiredDownloadPlan(
        items=[DownloadItem(clip=clip, sources=["liked"], quality="high")],
        preserved_ids=set(),
        source_to_name={"liked": "Liked Songs"},
        playlist_order={},
    )

    with (
        patch("custom_components.suno.downloaded_library.async_get_clientsession"),
        patch(
            "custom_components.suno.downloaded_library.fetch_album_art",
            new_callable=AsyncMock,
            return_value=b"should_not_be_used",
        ) as mock_fetch,
    ):
        await library.async_reconcile(
            {
                CONF_DOWNLOAD_PATH: str(sync_dir),
                CONF_SHOW_LIKED: True,
                CONF_ALL_PLAYLISTS: False,
                CONF_PLAYLISTS: [],
            },
            SunoData(liked_clips=[clip]),
            desired_plan=plan,
        )

    mock_fetch.assert_not_called()
    assert cover_path.read_bytes() == existing_image


# ── Engine-direct helper tests (relocated from test_download.py) ─


async def test_retag_clip_returns_missing_when_target_gone(hass: HomeAssistant, tmp_path: Path) -> None:
    """_retag_clip pre-checks for missing files and signals MISSING."""
    from custom_components.suno.downloaded_library import RetagResult

    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=_FakeAudio())
    await library.async_load()

    clip = _clip("clipA-0000-0000-0000-000000000000", "Song")
    item = DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)
    library.clip_index = {clip.id: clip}
    target = tmp_path / "ghost.flac"

    result = await library._retag_clip(item, target)

    assert result is RetagResult.MISSING


async def test_retag_clip_returns_missing_when_zero_byte(hass: HomeAssistant, tmp_path: Path) -> None:
    """_retag_clip treats zero-byte files as MISSING."""
    from custom_components.suno.downloaded_library import RetagResult

    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=_FakeAudio())
    await library.async_load()

    clip = _clip("clipB-0000-0000-0000-000000000000", "Song")
    item = DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)
    library.clip_index = {clip.id: clip}
    target = tmp_path / "empty.flac"
    target.write_bytes(b"")

    result = await library._retag_clip(item, target)

    assert result is RetagResult.MISSING


async def test_update_cover_art_writes_per_track_sidecar(hass: HomeAssistant, tmp_path: Path) -> None:
    """When track_path is given, _update_cover_art writes <basename>.jpg too."""
    from custom_components.suno.downloaded_library import _update_cover_art

    track = tmp_path / "Foo.flac"
    track.write_bytes(b"fLaC")
    cover = tmp_path / "cover.jpg"
    hash_path = tmp_path / ".cover_hash"

    session = AsyncMock()
    with patch(
        "custom_components.suno.downloaded_library.fetch_album_art",
        new_callable=AsyncMock,
        return_value=b"\xff\xd8\xff" + b"\x00" * 100,
    ):
        result = await _update_cover_art(hass, session, "https://x/y.jpg", cover, hash_path, track_path=track)

    assert result is True
    assert cover.exists()
    track_jpg = track.with_suffix(".jpg")
    assert track_jpg.exists()
    assert track_jpg.read_bytes() == cover.read_bytes()


async def test_update_cover_art_backfills_missing_track_sidecar(hass: HomeAssistant, tmp_path: Path) -> None:
    """Hash-match path still backfills track sidecar if it's missing."""
    import hashlib

    from custom_components.suno.downloaded_library import _update_cover_art

    track = tmp_path / "Foo.flac"
    track.write_bytes(b"fLaC")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    hash_path = tmp_path / ".cover_hash"
    image_url = "https://x/y.jpg"
    url_hash = hashlib.md5(image_url.encode()).hexdigest()[:12]  # noqa: S324
    hash_path.write_text(url_hash)
    track_jpg = track.with_suffix(".jpg")
    assert not track_jpg.exists()

    session = AsyncMock()
    result = await _update_cover_art(hass, session, image_url, cover, hash_path, track_path=track)

    assert result is False
    assert track_jpg.exists()


def test_album_for_clip_returns_none_for_non_remix() -> None:
    """Non-remix derivatives keep their own title as the album."""
    from custom_components.suno.downloaded_library import _album_for_clip

    parent = SunoClip(
        id="parent",
        title="Parent Album",
        audio_url="x",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="2026-01-01T00:00:00Z",
        tags="",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
    )
    derived = SunoClip(
        id="child",
        title="Derived",
        audio_url="x",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="2026-01-02T00:00:00Z",
        tags="",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
        edited_clip_id="parent",
        root_ancestor_id="parent",
        is_remix=False,
    )
    index = {"parent": parent, "child": derived}
    assert _album_for_clip(derived, index) is None


def test_album_for_clip_inherits_root_for_remix() -> None:
    """Remix variants inherit the root ancestor's title as album."""
    from custom_components.suno.downloaded_library import _album_for_clip

    parent = SunoClip(
        id="parent",
        title="Original Track",
        audio_url="x",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="2026-01-01T00:00:00Z",
        tags="",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
    )
    remix = SunoClip(
        id="remix",
        title="Original Track (Disco Mix)",
        audio_url="x",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="2026-01-02T00:00:00Z",
        tags="",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
        edited_clip_id="parent",
        root_ancestor_id="parent",
        is_remix=True,
    )
    index = {"parent": parent, "remix": remix}
    assert _album_for_clip(remix, index) == "Original Track"


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


# ── Video downloads ─────────────────────────────────────────────


async def test_video_download_success(hass: HomeAssistant, tmp_path: Path) -> None:
    """Video is downloaded alongside audio when video_url is present and downloads enabled."""
    audio = _FakeAudio()
    library = DownloadedLibrary(
        hass,
        InMemoryDownloadedLibraryStorage(),
        audio=audio,
        download_videos=True,
    )
    await library.async_load()

    clip = _clip_with_display(
        "clip-vid-00000-0000-0000-000000000000",
        "Video Song",
        video_url="https://cdn1.suno.ai/clip-vid.mp4",
    )
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
    )

    video_path = tmp_path / "mirror" / _video_clip_path(clip)
    assert video_path.exists()
    assert video_path.read_bytes() == b"\x00\x00\x00\x1cftypisom"
    assert audio.video_calls == [(clip.video_url, video_path)]


async def test_video_download_skipped_when_disabled(hass: HomeAssistant, tmp_path: Path) -> None:
    """Video download is skipped when download_videos is False."""
    audio = _FakeAudio()
    library = DownloadedLibrary(
        hass,
        InMemoryDownloadedLibraryStorage(),
        audio=audio,
        download_videos=False,
    )
    await library.async_load()

    clip = _clip_with_display(
        "clip-novid-000-0000-0000-000000000000",
        "No Video",
        video_url="https://cdn1.suno.ai/clip-novid.mp4",
    )
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
    )

    video_path = tmp_path / "mirror" / _video_clip_path(clip)
    assert not video_path.exists()
    assert audio.video_calls == []


async def test_video_download_handles_failure(hass: HomeAssistant, tmp_path: Path) -> None:
    """When download_video returns without writing, no video file lands; audio still succeeds."""
    audio = _FakeAudio(video_data=None)  # No video bytes written
    library = DownloadedLibrary(
        hass,
        InMemoryDownloadedLibraryStorage(),
        audio=audio,
        download_videos=True,
    )
    await library.async_load()

    clip = _clip_with_display(
        "clip-v404-0000-0000-0000-000000000000",
        "Video 404",
        video_url="https://cdn1.suno.ai/clip-v404.mp4",
    )
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
    )

    video_path = tmp_path / "mirror" / _video_clip_path(clip)
    assert not video_path.exists()
    assert library.errors == 0
    assert library.total_files == 1


async def test_video_download_skipped_when_no_video_url(hass: HomeAssistant, tmp_path: Path) -> None:
    """Video download is skipped when clip has no video_url."""
    audio = _FakeAudio()
    library = DownloadedLibrary(
        hass,
        InMemoryDownloadedLibraryStorage(),
        audio=audio,
        download_videos=True,
    )
    await library.async_load()

    clip = _clip("clip-nourl-000-0000-0000-000000000000", "No URL")
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
    )

    assert not (tmp_path / "mirror" / _video_clip_path(clip)).exists()
    assert audio.video_calls == []


# ── Reconcile manifest / present-file / missing-file ────────────


async def test_reconcile_manifest_marks_missing_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """Manifest entries whose files are gone get path/meta_hash cleared."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    base = tmp_path / "mirror"
    base.mkdir()
    (base / "present.flac").write_bytes(b"fLaC" + b"\x00" * 50)
    clips_state: dict[str, dict[str, object]] = {
        "present-id": {"path": "present.flac", "meta_hash": "abc"},
        "missing-id": {"path": "gone.flac", "meta_hash": "def"},
    }

    count = await library._reconcile_manifest(base, clips_state)

    assert count == 1
    assert clips_state["present-id"]["path"] == "present.flac"
    assert clips_state["present-id"]["meta_hash"] == "abc"
    assert clips_state["missing-id"]["path"] == ""
    assert "meta_hash" not in clips_state["missing-id"]


async def test_reconcile_manifest_treats_zero_byte_as_missing(hass: HomeAssistant, tmp_path: Path) -> None:
    """Zero-byte files are reconciled the same as fully missing files."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    base = tmp_path / "mirror"
    base.mkdir()
    (base / "empty.flac").write_bytes(b"")
    clips_state: dict[str, dict[str, object]] = {
        "empty-id": {"path": "empty.flac", "meta_hash": "abc"},
    }

    count = await library._reconcile_manifest(base, clips_state)

    assert count == 1
    assert clips_state["empty-id"]["path"] == ""


async def test_reconcile_manifest_idempotent_when_clean(hass: HomeAssistant, tmp_path: Path) -> None:
    """Manifest with all files present: no mutation, returns 0."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    base = tmp_path / "mirror"
    base.mkdir()
    (base / "a.flac").write_bytes(b"fLaC" + b"\x00" * 10)
    (base / "b.flac").write_bytes(b"fLaC" + b"\x00" * 10)
    clips_state: dict[str, dict[str, object]] = {
        "a-id": {"path": "a.flac", "meta_hash": "h1"},
        "b-id": {"path": "b.flac", "meta_hash": "h2"},
    }
    snapshot = json.dumps(clips_state, sort_keys=True)

    count = await library._reconcile_manifest(base, clips_state)

    assert count == 0
    assert json.dumps(clips_state, sort_keys=True) == snapshot


async def test_missing_audio_file_triggers_redownload(hass: HomeAssistant, tmp_path: Path) -> None:
    """End-to-end: manifest entry whose file is gone → re-download queued."""
    from custom_components.suno.models import clip_meta_hash

    sync_dir = tmp_path / "mirror"
    sync_dir.mkdir()

    clip = _clip("clip0001-0000-0000-0000-000000000000", "Song")
    rel_path = _clip_path(clip, QUALITY_HIGH)

    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip.id: {
                    "path": rel_path,
                    "title": clip.title,
                    "created": "2026-03-15",
                    "sources": ["liked"],
                    "size": 54,
                    "meta_hash": clip_meta_hash(clip),
                    "quality": QUALITY_HIGH,
                }
            },
            "last_download": None,
        }
    )
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, storage, audio=audio)
    await library.async_load()

    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
    )

    assert audio.rendered == [clip.id]
    assert (sync_dir / rel_path).is_file()
    assert library.state["clips"][clip.id]["path"] == rel_path


async def test_present_file_does_not_redownload(hass: HomeAssistant, tmp_path: Path) -> None:
    """Negative control: file present + hash match → no re-download work."""
    from custom_components.suno.models import clip_meta_hash

    sync_dir = tmp_path / "mirror"
    sync_dir.mkdir()

    clip = _clip("clipxxxx-0000-0000-0000-000000000000", "Song")
    rel_path = _clip_path(clip, QUALITY_HIGH)
    target = sync_dir / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fLaC" + b"\x00" * 50)

    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip.id: {
                    "path": rel_path,
                    "title": clip.title,
                    "created": "2026-03-15",
                    "sources": ["liked"],
                    "size": 54,
                    "meta_hash": clip_meta_hash(clip),
                    "quality": QUALITY_HIGH,
                }
            },
            "last_download": None,
        }
    )
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, storage, audio=audio)
    await library.async_load()

    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
    )

    assert audio.rendered == []


async def test_zero_size_file_triggers_redownload(hass: HomeAssistant, tmp_path: Path) -> None:
    """A zero-size file on disk for a new clip should be re-downloaded."""
    sync_dir = tmp_path / "mirror"
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=audio)
    await library.async_load()

    clip = _clip("clip-zero", "Zero Song")
    rel_path = _clip_path(clip, QUALITY_HIGH)
    target = sync_dir / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"")

    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
    )

    assert library.errors == 0
    assert library.total_files == 1
    assert target.stat().st_size > 0


async def test_reconcile_skipped_when_nothing_changed(hass: HomeAssistant, tmp_path: Path) -> None:
    """Reconciliation is skipped when no downloads, deletions, or migrations occurred."""
    from custom_components.suno.models import clip_meta_hash

    clip_id = "clip0099-0000-0000-0000-000000000000"
    clip = _clip(clip_id, "Song")
    rel_path = _clip_path(clip, QUALITY_HIGH)

    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                clip_id: {
                    "path": rel_path,
                    "title": clip.title,
                    "created": "2026-03-15",
                    "sources": ["liked"],
                    "size": 54,
                    "meta_hash": clip_meta_hash(clip),
                    "quality": QUALITY_HIGH,
                }
            },
            "last_download": None,
        }
    )
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, storage, audio=audio)
    await library.async_load()

    dest = tmp_path / "mirror" / rel_path
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"fLaC" + b"\x00" * 50)

    with patch.object(library, "_reconcile_disk", new_callable=AsyncMock, return_value=0) as mock_reconcile:
        await library.async_reconcile(
            {
                CONF_DOWNLOAD_PATH: str(tmp_path / "mirror"),
                CONF_SHOW_LIKED: True,
                CONF_ALL_PLAYLISTS: False,
                CONF_PLAYLISTS: [],
            },
            SunoData(liked_clips=[clip]),
        )

    mock_reconcile.assert_not_called()


# ── Cache mode and Mirror/Archive coexistence ───────────────────


async def test_cache_mode_excludes_from_download_set(hass: HomeAssistant) -> None:
    """Cache-only sources produce no DownloadItems in build_desired."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    options = {
        CONF_SHOW_LIKED: True,
        CONF_SHOW_MY_SONGS: True,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
        CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_MIRROR,
        CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE,
        CONF_MY_SONGS_COUNT: 5,
        CONF_MY_SONGS_DAYS: None,
    }
    suno_data = SunoData(liked_clips=[_clip("clip-liked-1")], clips=[_clip("clip-ms-1")])
    plan = library.build_desired(options, suno_data)
    ids = {item.clip.id for item in plan.items}
    assert "clip-liked-1" in ids
    assert "clip-ms-1" not in ids


async def test_cache_mode_cleans_up_existing_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """Previously downloaded files are deleted when source switches to cache."""
    sync_dir = tmp_path / "mirror"
    sync_dir.mkdir()
    orphan = sync_dir / "old-my-songs.flac"
    orphan.write_bytes(b"fLaC" + b"\x00" * 50)

    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                "old-clip": {
                    "path": "old-my-songs.flac",
                    "title": "Old Song",
                    "created": "2026-01-01",
                    "sources": ["my_songs"],
                }
            },
            "last_download": None,
        }
    )
    library = DownloadedLibrary(hass, storage, audio=_FakeAudio())
    await library.async_load()
    assert library.total_files == 1

    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: False,
            CONF_SHOW_MY_SONGS: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
            CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE,
            CONF_MY_SONGS_COUNT: 5,
            CONF_MY_SONGS_DAYS: None,
        },
        SunoData(),
    )

    assert library.total_files == 0
    assert not orphan.exists()


async def test_all_three_modes_coexist(hass: HomeAssistant, tmp_path: Path) -> None:
    """Mirror + Archive + Cache on different sections simultaneously."""
    from custom_components.suno.models import SunoPlaylist

    storage = InMemoryDownloadedLibraryStorage(
        {
            "clips": {
                "old-pl": {
                    "path": "old-playlist.flac",
                    "title": "Old Playlist Song",
                    "created": "2026-01-01",
                    "sources": ["playlist:pl-1"],
                },
                "old-liked": {
                    "path": "old-liked.flac",
                    "title": "Old Liked Song",
                    "created": "2026-01-01",
                    "sources": ["liked"],
                },
            },
            "last_download": None,
        }
    )
    library = DownloadedLibrary(hass, storage, audio=_FakeAudio())
    await library.async_load()

    sync_dir = tmp_path / "mirror"
    sync_dir.mkdir()
    (sync_dir / "old-playlist.flac").write_bytes(b"fLaC")
    (sync_dir / "old-liked.flac").write_bytes(b"fLaC")

    suno_data = SunoData(
        playlists=[SunoPlaylist(id="pl-1", name="Test", image_url=None, num_clips=0)],
        playlist_clips={"pl-1": []},
    )
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_SHOW_MY_SONGS: True,
            CONF_SHOW_PLAYLISTS: True,
            CONF_ALL_PLAYLISTS: True,
            CONF_PLAYLISTS: [],
            CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_MIRROR,
            CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_ARCHIVE,
            CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE,
            CONF_MY_SONGS_COUNT: 5,
            CONF_MY_SONGS_DAYS: None,
        },
        suno_data,
    )

    clips = library.state.get("clips", {})
    assert "old-pl" not in clips  # mirror: deleted
    assert "old-liked" in clips  # archive: kept


# ── Album from root ancestor ────────────────────────────────────


async def test_album_set_from_root_ancestor(hass: HomeAssistant, tmp_path: Path) -> None:
    """A remix clip inherits the root ancestor's title as the album."""
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=audio)
    await library.async_load()

    root_clip = SunoClip(
        id="root-song",
        title="Original Song",
        audio_url="https://cdn1.suno.ai/root-song.mp3",
        image_url=None,
        image_large_url=None,
        is_liked=True,
        status="complete",
        created_at="2026-03-15T10:00:00Z",
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
    )
    child_clip = SunoClip(
        id="child-song",
        title="Remix Version",
        audio_url="https://cdn1.suno.ai/child-song.mp3",
        image_url=None,
        image_large_url=None,
        is_liked=True,
        status="complete",
        created_at="2026-03-16T10:00:00Z",
        tags="pop",
        duration=130.0,
        clip_type="gen",
        has_vocal=True,
        edited_clip_id="root-song",
        root_ancestor_id="root-song",
        is_remix=True,
    )

    sync_dir = tmp_path / "mirror"
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[child_clip, root_clip]),
    )

    child_meta = next(
        meta for clip_id, meta in zip(audio.rendered, audio.render_metas, strict=True) if clip_id == "child-song"
    )
    assert child_meta.album == "Original Song"


async def test_album_fallback_no_root(hass: HomeAssistant, tmp_path: Path) -> None:
    """Download falls back to clip's own title as album when no root resolved."""
    audio = _FakeAudio()
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage(), audio=audio)
    await library.async_load()

    clip = SunoClip(
        id="solo-song",
        title="Standalone Track",
        audio_url="https://cdn1.suno.ai/solo-song.mp3",
        image_url=None,
        image_large_url=None,
        is_liked=True,
        status="complete",
        created_at="2026-03-15T10:00:00Z",
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
        root_ancestor_id="",
    )

    sync_dir = tmp_path / "mirror"
    await library.async_reconcile(
        {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        },
        SunoData(liked_clips=[clip]),
    )

    assert audio.render_metas[0].album == "Standalone Track"


# ── Helpers (relocated from tests/test_download.py during Phase 1.6 collapse) ──


def _make_clip(clip_id: str, title: str = "Song", created: str = "2026-03-15T10:00:00Z") -> SunoClip:
    """Construct a minimal SunoClip for path/playlist/helper tests."""
    return SunoClip(
        id=clip_id,
        title=title,
        audio_url=f"https://cdn1.suno.ai/{clip_id}.mp3",
        image_url=None,
        image_large_url=None,
        is_liked=True,
        status="complete",
        created_at=created,
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
    )


# ── TestSafeName (relocated from tests/test_download.py:49) ──


class TestSafeName:
    def test_preserves_spaces_and_case(self) -> None:
        assert _safe_name("Hello World") == "Hello World"

    def test_unsafe_chars_replaced(self) -> None:
        result = _safe_name('test<>:"/\\|?*file')
        assert "<" not in result
        assert "/" not in result

    def test_empty_string_returns_untitled(self) -> None:
        assert _safe_name("") == "untitled"

    def test_unicode_preserved(self) -> None:
        assert _safe_name("café résumé") == "café résumé"

    def test_emoji_preserved(self) -> None:
        assert "Music" in _safe_name("🎵 Music")

    def test_traversal_neutralised(self) -> None:
        assert "/" not in _safe_name("../../etc/passwd")

    def test_windows_reserved_handled(self) -> None:
        result = _safe_name("CON")
        assert result != "CON"  # pathvalidate appends underscore

    def test_truncates_long_names(self) -> None:
        assert len(_safe_name("a" * 300)) <= 200


# ── TestClipPath (relocated from tests/test_download.py:81) ──


class TestClipPath:
    def _make_clip(
        self,
        title: str = "My Song",
        created: str = "2026-01-15T10:00:00Z",
        clip_id: str = "abcd1234-test-clip-id",
        display_name: str = "testuser",
    ):
        clip = MagicMock()
        clip.id = clip_id
        clip.title = title
        clip.created_at = created
        clip.display_name = display_name
        return clip

    def test_high_quality_flac(self) -> None:
        clip = self._make_clip()
        result = _clip_path(clip, "high")
        assert result == "testuser/My Song/testuser-My Song [abcd1234].flac"

    def test_standard_quality_mp3(self) -> None:
        clip = self._make_clip()
        result = _clip_path(clip, "standard")
        assert result == "testuser/My Song/testuser-My Song [abcd1234].mp3"

    def test_missing_display_name(self) -> None:
        clip = self._make_clip(display_name="")
        result = _clip_path(clip, "high")
        assert result == "Suno/My Song/Suno-My Song [abcd1234].flac"

    def test_different_clips_same_title_get_different_paths(self) -> None:
        clip_a = self._make_clip(clip_id="aaaaaaaa-1111-2222-3333-444444444444")
        clip_b = self._make_clip(clip_id="bbbbbbbb-1111-2222-3333-444444444444")
        assert _clip_path(clip_a, "high") != _clip_path(clip_b, "high")


# ── TestVideoClipPath (relocated from tests/test_download.py:120) ──


class TestVideoClipPath:
    """Music videos live alongside their audio file (same dir, .mp4 suffix)."""

    def _make_clip(self, clip_id: str = "abcd1234-test-clip-id", title: str = "My Song", display: str = "testuser"):
        clip = MagicMock()
        clip.id = clip_id
        clip.title = title
        clip.display_name = display
        return clip

    def test_video_path_alongside_audio_flac(self) -> None:
        clip = self._make_clip()
        assert _video_clip_path(clip) == "testuser/My Song/testuser-My Song [abcd1234].mp4"

    def test_video_path_basename_matches_audio_basename(self) -> None:
        clip = self._make_clip()
        flac = _clip_path(clip, "high")
        mp3 = _clip_path(clip, "standard")
        video = _video_clip_path(clip)
        # Same parent directory and same basename — only suffix differs.
        assert Path(video).parent == Path(flac).parent == Path(mp3).parent
        assert Path(video).stem == Path(flac).stem == Path(mp3).stem
        assert Path(video).suffix == ".mp4"

    def test_no_music_videos_directory_in_path(self) -> None:
        """The legacy music-videos/ directory should never appear in the path."""
        clip = self._make_clip()
        assert "music-videos" not in _video_clip_path(clip)

    def test_video_path_missing_display_name_falls_back_to_suno(self) -> None:
        clip = self._make_clip(display="")
        assert _video_clip_path(clip) == "Suno/My Song/Suno-My Song [abcd1234].mp4"


# ── TestWriteM3u8Playlists (relocated from tests/test_download.py:618) ──


class TestWriteM3u8Playlists:
    def _make_clip(self, clip_id: str = "clip1", title: str = "Test Song", duration: float = 120.5):
        clip = MagicMock()
        clip.id = clip_id
        clip.title = title
        clip.duration = duration
        return clip

    def test_writes_absolute_paths(self, tmp_path: Path) -> None:
        """Playlist entries must use absolute paths for Jellyfin compatibility."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "artist/test_song/artist-test_song [clip1aaa].flac", "title": "Test Song"}}
        desired = [DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        assert "./" not in content
        assert str(tmp_path / "artist/test_song/artist-test_song [clip1aaa].flac") in content

    def test_uses_clip_duration(self, tmp_path: Path) -> None:
        """Duration in #EXTINF should come from clip metadata, not hardcoded -1."""
        clip = self._make_clip(duration=95.7)
        clips_state = {"clip1": {"path": "artist/test_song/artist-test_song [clip1aaa].flac", "title": "Test Song"}}
        desired = [DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        assert "#EXTINF:95," in content

    def test_duration_fallback_when_zero(self, tmp_path: Path) -> None:
        """Duration falls back to -1 when clip has no duration."""
        clip = self._make_clip(duration=0)
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        assert "#EXTINF:-1," in content

    def test_header_format(self, tmp_path: Path) -> None:
        """M3U8 files must start with #EXTM3U and include #PLAYLIST tag."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [DownloadItem(clip=clip, sources=["playlist:pl1"], quality=QUALITY_HIGH)]
        source_to_name = {"playlist:pl1": "My Playlist"}

        _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name)

        content = (tmp_path / "My Playlist.m3u8").read_text(encoding="utf-8")
        assert content.startswith("#EXTM3U\n")
        assert "#PLAYLIST:My Playlist\n" in content

    def test_liked_and_playlist_sources(self, tmp_path: Path) -> None:
        """Clips with both liked and playlist sources appear in both M3U8 files."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [DownloadItem(clip=clip, sources=["liked", "playlist:pl1"], quality=QUALITY_HIGH)]
        source_to_name = {"liked": "Liked Songs", "playlist:pl1": "Favourites"}

        _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name)

        assert (tmp_path / "Liked Songs.m3u8").exists()
        assert (tmp_path / "Favourites.m3u8").exists()

    def test_clip_liked_and_in_playlist_no_duplicates(self, tmp_path: Path) -> None:
        """A liked clip also in a playlist appears once in each M3U8, not twice in Liked Songs."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [DownloadItem(clip=clip, sources=["liked", "playlist:pl1"], quality=QUALITY_HIGH)]
        source_to_name = {"liked": "Liked Songs", "playlist:pl1": "The Second album"}

        _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name)

        liked_content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        album_content = (tmp_path / "The Second album.m3u8").read_text(encoding="utf-8")
        assert liked_content.count("song.flac") == 1
        assert album_content.count("song.flac") == 1

    def test_clip_in_two_playlists(self, tmp_path: Path) -> None:
        """A clip in two playlists appears in both M3U8 files."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [DownloadItem(clip=clip, sources=["playlist:a", "playlist:b"], quality=QUALITY_HIGH)]
        source_to_name = {"playlist:a": "Playlist A", "playlist:b": "Playlist B"}

        _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name)

        assert (tmp_path / "Playlist A.m3u8").exists()
        assert (tmp_path / "Playlist B.m3u8").exists()
        a_content = (tmp_path / "Playlist A.m3u8").read_text(encoding="utf-8")
        b_content = (tmp_path / "Playlist B.m3u8").read_text(encoding="utf-8")
        assert "song.flac" in a_content
        assert "song.flac" in b_content

    def test_liked_plus_two_playlists(self, tmp_path: Path) -> None:
        """A clip that is liked and in two playlists appears in all three M3U8 files."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [
            DownloadItem(
                clip=clip,
                sources=["liked", "playlist:a", "playlist:b"],
                quality=QUALITY_HIGH,
            )
        ]
        source_to_name = {"liked": "Liked Songs", "playlist:a": "Keep", "playlist:b": "Zac & Xavi"}

        _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name)

        assert (tmp_path / "Liked Songs.m3u8").exists()
        assert (tmp_path / "Keep.m3u8").exists()
        assert (tmp_path / "Zac & Xavi.m3u8").exists()
        for f in ["Liked Songs.m3u8", "Keep.m3u8", "Zac & Xavi.m3u8"]:
            content = (tmp_path / f).read_text(encoding="utf-8")
            assert content.count("song.flac") == 1

    def test_my_songs_source_excluded_from_m3u8(self, tmp_path: Path) -> None:
        """Clips with only a 'my_songs' source produce no M3U8 file."""
        clip = self._make_clip()
        clips_state = {"clip1": {"path": "song.flac", "title": "Song"}}
        desired = [DownloadItem(clip=clip, sources=["my_songs"], quality=QUALITY_STANDARD)]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        assert not list(tmp_path.glob("*.m3u8"))

    def test_cleans_stale_m3u8(self, tmp_path: Path) -> None:
        """Stale M3U8 files from previous runs are removed."""
        stale = tmp_path / "Old Playlist.m3u8"
        stale.write_text("#EXTM3U\n", encoding="utf-8")

        _write_m3u8_playlists(tmp_path, {}, [])

        assert not stale.exists()

    def test_skips_clips_without_path(self, tmp_path: Path) -> None:
        """Clips missing a path in state are excluded from playlists."""
        clip = self._make_clip()
        clips_state = {"clip1": {"title": "Song"}}  # no "path" key
        desired = [DownloadItem(clip=clip, sources=["liked"], quality=QUALITY_HIGH)]

        _write_m3u8_playlists(tmp_path, clips_state, desired)

        # No M3U8 written since the only clip had no path
        assert not list(tmp_path.glob("*.m3u8"))


# ── TestBuildSyncSummary (relocated from tests/test_download.py:1019) ──


class TestBuildSyncSummary:
    def test_no_change(self) -> None:
        assert _build_download_summary(0, 0, 0) == "No change"

    def test_single_new_song(self) -> None:
        assert _build_download_summary(1, 0, 0) == "1 new song"

    def test_multiple_new_songs(self) -> None:
        assert _build_download_summary(8, 0, 0) == "8 new songs"

    def test_single_removal(self) -> None:
        assert _build_download_summary(0, 1, 0) == "1 removal"

    def test_multiple_removals(self) -> None:
        assert _build_download_summary(0, 3, 0) == "3 removals"

    def test_single_metadata_update(self) -> None:
        assert _build_download_summary(0, 0, 1) == "1 metadata update"

    def test_multiple_metadata_updates(self) -> None:
        assert _build_download_summary(0, 0, 2) == "2 metadata updates"

    def test_combined(self) -> None:
        result = _build_download_summary(1, 2, 1)
        assert result == "1 new song, 1 metadata update, 2 removals"

    def test_all_plural(self) -> None:
        result = _build_download_summary(3, 4, 5)
        assert result == "3 new songs, 5 metadata updates, 4 removals"

    def test_renamed(self) -> None:
        assert _build_download_summary(0, 0, 0, renamed=3) == "3 renamed"

    def test_retagged(self) -> None:
        assert _build_download_summary(0, 0, 0, retagged=5) == "5 re-tagged"

    def test_full_username_change(self) -> None:
        """Typical username change: renames + re-tags, no downloads."""
        result = _build_download_summary(0, 0, 0, renamed=50, retagged=0)
        assert result == "50 renamed"

    def test_all_operations(self) -> None:
        result = _build_download_summary(2, 1, 0, renamed=3, retagged=5)
        assert result == "2 new songs, 3 renamed, 5 re-tagged, 1 removal"


# ── TestSyncRetentionModes (relocated from tests/test_download.py:1278) ──


class TestSyncRetentionModes:
    """Tests for per-source sync/archive/cache retention modes."""

    def test_sync_mode_deletes_removed_clips(self) -> None:
        """Clip with source ['liked'], mode=mirror → deleted when removed from desired."""
        clips_state = {
            "clip-1": {"path": "2026-01-15/Song [clip-1].flac", "sources": ["liked"]},
        }
        seen_ids: set[str] = set()  # clip not in desired
        preserved_ids: set[str] = set()
        options = {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_MIRROR}

        to_delete = []
        for cid in clips_state:
            if cid in seen_ids or cid in preserved_ids:
                continue
            entry = clips_state[cid]
            sources = entry.get("sources", [])
            if all(not _source_preserves_files(src, options) for src in sources):
                to_delete.append(cid)

        assert to_delete == ["clip-1"]

    def test_archive_mode_keeps_removed_clips(self) -> None:
        """Clip with source ['liked'], mode=archive → NOT deleted when removed from desired."""
        clips_state = {
            "clip-1": {"path": "2026-01-15/Song [clip-1].flac", "sources": ["liked"]},
        }
        seen_ids: set[str] = set()
        preserved_ids: set[str] = set()
        options = {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_ARCHIVE}

        to_delete = []
        for cid in clips_state:
            if cid in seen_ids or cid in preserved_ids:
                continue
            entry = clips_state[cid]
            sources = entry.get("sources", [])
            if all(not _source_preserves_files(src, options) for src in sources):
                to_delete.append(cid)

        assert to_delete == []

    def test_mixed_sources_archive_wins(self) -> None:
        """Clip with sources ['liked', 'my_songs']. Liked=archive, my_songs=mirror → NOT deleted."""
        clips_state = {
            "clip-1": {"path": "2026-01-15/Song [clip-1].flac", "sources": ["liked", "my_songs"]},
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
            sources = entry.get("sources", [])
            if all(not _source_preserves_files(src, options) for src in sources):
                to_delete.append(cid)

        assert to_delete == []

    def test_empty_sources_deleted(self) -> None:
        """Clip with sources [] → deleted (orphan cleanup via all() on empty)."""
        clips_state = {
            "clip-1": {"path": "2026-01-15/Song [clip-1].flac", "sources": []},
        }
        seen_ids: set[str] = set()
        preserved_ids: set[str] = set()
        options = {}

        to_delete = []
        for cid in clips_state:
            if cid in seen_ids or cid in preserved_ids:
                continue
            entry = clips_state[cid]
            sources = entry.get("sources", [])
            if all(not _source_preserves_files(src, options) for src in sources):
                to_delete.append(cid)

        assert to_delete == ["clip-1"]

    def test_cache_mode_deletes_like_mirror(self) -> None:
        """Cache mode sources are not preserved (treated like mirror for deletion)."""
        clips_state = {
            "clip-1": {"path": "2026-01-15/Song [clip-1].flac", "sources": ["my_songs"]},
        }
        seen_ids: set[str] = set()
        preserved_ids: set[str] = set()
        options = {CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE}

        to_delete = []
        for cid in clips_state:
            if cid in seen_ids or cid in preserved_ids:
                continue
            entry = clips_state[cid]
            sources = entry.get("sources", [])
            if all(not _source_preserves_files(src, options) for src in sources):
                to_delete.append(cid)

        assert to_delete == ["clip-1"]


# ── TestAddClipQualityMerge (relocated from tests/test_download.py:1388) ──


class TestAddClipQualityMerge:
    """Tests for _add_clip quality upgrade semantics."""

    def test_flac_wins_over_mp3(self) -> None:
        """When a clip appears first as MP3 then FLAC, quality upgrades to FLAC."""
        clip = _make_clip("clip-merge-1", "Merged")
        clip_map: dict[str, DownloadItem] = {}
        _add_clip(clip_map, clip, "liked", QUALITY_STANDARD)
        _add_clip(clip_map, clip, "playlist:x", QUALITY_HIGH)
        assert clip_map["clip-merge-1"].quality == QUALITY_HIGH
        assert set(clip_map["clip-merge-1"].sources) == {"liked", "playlist:x"}

    def test_mp3_does_not_downgrade_flac(self) -> None:
        """When a clip appears first as FLAC then MP3, quality stays FLAC."""
        clip = _make_clip("clip-merge-2", "Stays High")
        clip_map: dict[str, DownloadItem] = {}
        _add_clip(clip_map, clip, "liked", QUALITY_HIGH)
        _add_clip(clip_map, clip, "my_songs", QUALITY_STANDARD)
        assert clip_map["clip-merge-2"].quality == QUALITY_HIGH
        assert set(clip_map["clip-merge-2"].sources) == {"liked", "my_songs"}

    def test_same_quality_no_change(self) -> None:
        """Same quality from both sources stays unchanged."""
        clip = _make_clip("clip-merge-3", "Same")
        clip_map: dict[str, DownloadItem] = {}
        _add_clip(clip_map, clip, "liked", QUALITY_STANDARD)
        _add_clip(clip_map, clip, "my_songs", QUALITY_STANDARD)
        assert clip_map["clip-merge-3"].quality == QUALITY_STANDARD

    def test_first_add_creates_entry(self) -> None:
        """First add creates a new DownloadItem with correct fields."""
        clip = _make_clip("clip-new", "New Song")
        clip_map: dict[str, DownloadItem] = {}
        _add_clip(clip_map, clip, "liked", QUALITY_HIGH)
        item = clip_map["clip-new"]
        assert item.clip is clip
        assert item.sources == ["liked"]
        assert item.quality == QUALITY_HIGH


# ── TestSourceUsesSyncMode (relocated from tests/test_download.py:1431) ──


class TestSourceUsesSyncMode:
    """Direct unit tests for _get_source_mode and _source_preserves_files."""

    def test_liked_mirror_mode(self) -> None:
        assert _get_source_mode("liked", {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_MIRROR}) == DOWNLOAD_MODE_MIRROR

    def test_liked_archive_mode(self) -> None:
        assert _get_source_mode("liked", {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_ARCHIVE}) == DOWNLOAD_MODE_ARCHIVE

    def test_liked_cache_mode(self) -> None:
        assert _get_source_mode("liked", {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_CACHE}) == DOWNLOAD_MODE_CACHE

    def test_playlist_mirror_mode(self) -> None:
        assert (
            _get_source_mode("playlist:abc", {CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_MIRROR})
            == DOWNLOAD_MODE_MIRROR
        )

    def test_playlist_archive_mode(self) -> None:
        assert (
            _get_source_mode("playlist:abc", {CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_ARCHIVE})
            == DOWNLOAD_MODE_ARCHIVE
        )

    def test_playlist_cache_mode(self) -> None:
        assert (
            _get_source_mode("playlist:abc", {CONF_DOWNLOAD_MODE_PLAYLISTS: DOWNLOAD_MODE_CACHE}) == DOWNLOAD_MODE_CACHE
        )

    def test_my_songs_mirror_mode(self) -> None:
        assert _get_source_mode("my_songs", {CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_MIRROR}) == DOWNLOAD_MODE_MIRROR

    def test_my_songs_archive_mode(self) -> None:
        assert (
            _get_source_mode("my_songs", {CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_ARCHIVE}) == DOWNLOAD_MODE_ARCHIVE
        )

    def test_my_songs_cache_mode(self) -> None:
        assert _get_source_mode("my_songs", {CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE}) == DOWNLOAD_MODE_CACHE

    def test_unknown_source_defaults_to_mirror(self) -> None:
        assert _get_source_mode("unknown_source", {}) == DOWNLOAD_MODE_MIRROR

    def test_default_mode_when_key_missing(self) -> None:
        """Missing config key uses DEFAULT_DOWNLOAD_MODE ('mirror')."""
        assert _get_source_mode("liked", {}) == DOWNLOAD_MODE_MIRROR

    def test_preserves_files_true_for_archive(self) -> None:
        assert _source_preserves_files("liked", {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_ARCHIVE}) is True

    def test_preserves_files_false_for_mirror(self) -> None:
        assert _source_preserves_files("liked", {CONF_DOWNLOAD_MODE_LIKED: DOWNLOAD_MODE_MIRROR}) is False

    def test_preserves_files_false_for_cache(self) -> None:
        assert _source_preserves_files("my_songs", {CONF_DOWNLOAD_MODE_MY_SONGS: DOWNLOAD_MODE_CACHE}) is False


# ── TestPlaylistOrderPreservation (relocated from tests/test_download.py:2526) ──


class TestPlaylistOrderPreservation:
    def _make_clip(self, clip_id: str = "clip1", title: str = "Test Song", duration: float = 120.5):
        clip = MagicMock()
        clip.id = clip_id
        clip.title = title
        clip.duration = duration
        return clip

    def test_playlist_order_uses_playlist_order_dict(self, tmp_path: Path) -> None:
        """Entries are sorted according to playlist_order dict."""
        clips = [self._make_clip(f"clip{i}", f"Song {i}") for i in range(4)]
        clips_state = {f"clip{i}": {"path": f"Song {i}.flac", "title": f"Song {i}"} for i in range(4)}
        desired = [DownloadItem(clip=clips[i], sources=["liked"], quality=QUALITY_HIGH) for i in range(4)]
        # API order: clip3, clip1, clip0, clip2
        playlist_order = {"liked": ["clip3", "clip1", "clip0", "clip2"]}

        _write_m3u8_playlists(tmp_path, clips_state, desired, playlist_order=playlist_order)

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        lines = [ln for ln in content.splitlines() if ln.startswith("#EXTINF")]
        assert "Song 3" in lines[0]
        assert "Song 1" in lines[1]
        assert "Song 0" in lines[2]
        assert "Song 2" in lines[3]

    def test_fallback_ordering_without_playlist_order(self, tmp_path: Path) -> None:
        """Without playlist_order, entries appear in desired iteration order."""
        clips = [self._make_clip(f"clip{i}", f"Song {i}") for i in range(3)]
        clips_state = {f"clip{i}": {"path": f"Song {i}.flac", "title": f"Song {i}"} for i in range(3)}
        desired = [DownloadItem(clip=clips[i], sources=["liked"], quality=QUALITY_HIGH) for i in range(3)]

        _write_m3u8_playlists(tmp_path, clips_state, desired, playlist_order={})

        content = (tmp_path / "Liked Songs.m3u8").read_text(encoding="utf-8")
        lines = [ln for ln in content.splitlines() if ln.startswith("#EXTINF")]
        assert len(lines) == 3
        assert "Song 0" in lines[0]
        assert "Song 1" in lines[1]
        assert "Song 2" in lines[2]

    def test_playlist_order_matches_api_response_order(self, tmp_path: Path) -> None:
        """Playlist output order matches the API response order (reversed from default)."""
        clips = [self._make_clip(f"clip{i}", f"Song {i}") for i in range(5)]
        clips_state = {f"clip{i}": {"path": f"Song {i}.flac", "title": f"Song {i}"} for i in range(5)}
        desired = [DownloadItem(clip=clips[i], sources=["playlist:abc"], quality=QUALITY_HIGH) for i in range(5)]
        source_to_name = {"playlist:abc": "My Playlist"}
        # API returned clips in reverse order
        api_order = ["clip4", "clip3", "clip2", "clip1", "clip0"]
        playlist_order = {"playlist:abc": api_order}

        _write_m3u8_playlists(tmp_path, clips_state, desired, source_to_name, playlist_order)

        content = (tmp_path / "My Playlist.m3u8").read_text(encoding="utf-8")
        lines = [ln for ln in content.splitlines() if ln.startswith("#EXTINF")]
        assert len(lines) == 5
        for idx, api_clip_id in enumerate(api_order):
            clip_num = api_clip_id.replace("clip", "")
            assert f"Song {clip_num}" in lines[idx]
