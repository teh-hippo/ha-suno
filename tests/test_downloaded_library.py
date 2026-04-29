"""Tests for the Downloaded Library seam."""

from __future__ import annotations

from pathlib import Path

from homeassistant.core import HomeAssistant

from custom_components.suno.const import (
    CONF_ALL_PLAYLISTS,
    CONF_DOWNLOAD_MODE_LIKED,
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
)
from custom_components.suno.downloaded_library import (
    DownloadedLibrary,
    InMemoryDownloadedLibraryStorage,
    RenderedAudio,
    _clip_path,
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
    def __init__(self, data: bytes = b"fLaC" + b"\x00" * 50) -> None:
        self.data = data
        self.rendered: list[str] = []

    async def fetch_image(self, _image_url: str) -> bytes | None:
        return None

    async def render(
        self,
        clip: SunoClip,
        quality: str,
        _meta: TrackMetadata,
        _image_url: str | None,
    ) -> RenderedAudio | None:
        self.rendered.append(clip.id)
        return RenderedAudio(self.data, "flac" if quality == QUALITY_HIGH else "mp3")

    async def retag(self, _target: Path, _meta: TrackMetadata) -> bool:
        return True

    async def download_video(self, _video_url: str, _target: Path) -> None:
        return


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
