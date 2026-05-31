"""Tests for the reconciliation submodule of the Downloaded Library engine.

Split from the legacy 5129-line ``test_downloaded_library.py`` by the
Round 2 test restructure.
"""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.core import HomeAssistant

from custom_components.suno.const import (
    CONF_ALL_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_PLAYLISTS,
    CONF_SHOW_LIKED,
    CONF_SHOW_MY_SONGS,
    CONF_SHOW_PLAYLISTS,
    QUALITY_HIGH,
    VIDEO_ART_DOWNLOAD,
)
from custom_components.suno.downloaded_library import (
    DownloadedLibrary,
    InMemoryDownloadedLibraryStorage,
    ManifestEntry,
    RenderedAudio,
)
from custom_components.suno.models import (
    SunoClip,
    TrackMetadata,
)

from .conftest import make_clip

# ── Shared test fixtures (from legacy test_downloaded_library.py) ──


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

    clips_state = {"clip-id": ManifestEntry.from_dict({"path": rel})}
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
    """mp4 sidecars sharing an audio file's basename are kept when the mode expects them."""
    library = DownloadedLibrary(
        hass,
        InMemoryDownloadedLibraryStorage(),
        video_art_mode=VIDEO_ART_DOWNLOAD,
    )
    rel = "artist/Song/artist-Song [abcd1234].flac"
    audio_path = tmp_path / rel
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"fLaC" + b"\x00" * 50)
    video = audio_path.with_suffix(".mp4")
    video.write_bytes(b"\x00\x00\x00\x1cftypisom")

    clips_state = {"abcd1234": ManifestEntry.from_dict({"path": rel})}
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


# ── Reconcile manifest / present-file / missing-file ────────────


async def test_reconcile_manifest_marks_missing_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """Manifest entries whose files are gone get path/meta_hash cleared."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    base = tmp_path / "mirror"
    base.mkdir()
    (base / "present.flac").write_bytes(b"fLaC" + b"\x00" * 50)
    clips_state: dict[str, dict[str, object]] = {
        "present-id": ManifestEntry.from_dict({"path": "present.flac", "meta_hash": "abc"}),
        "missing-id": ManifestEntry.from_dict({"path": "gone.flac", "meta_hash": "def"}),
    }

    count = await library._reconcile_manifest(base, clips_state)

    assert count == 1
    assert clips_state["present-id"].path == "present.flac"
    assert clips_state["present-id"].meta_hash == "abc"
    assert clips_state["missing-id"].path == ""
    assert (clips_state["missing-id"].meta_hash if clips_state["missing-id"].meta_hash else "") == ""


async def test_reconcile_manifest_clears_embedded_art_hash_for_missing_files(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Regression: missing files must also clear embedded_art_hash.

    Reproduces the v6.3.1-v6.3.4 leak where the engine's three "file is
    gone" branches cleared ``path`` + ``meta_hash`` but forgot
    ``embedded_art_hash``. A stale art-hash on a re-downloaded file
    suppressed the next retag pass, leaving the file tagged with
    obsolete album art forever. Round 1 migrated the three engine
    branches to ``_clear_for_redownload`` but missed this one in
    ``reconciliation.py``; Round 2 closes it.
    """
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    base = tmp_path / "mirror"
    base.mkdir()
    clips_state: dict[str, dict[str, object]] = {
        "missing-with-stale-art": ManifestEntry.from_dict(
            {
                "path": "gone.flac",
                "meta_hash": "def",
                "embedded_art_hash": "stale-art-hash",
                "album": "Old Album",
            }
        ),
    }

    count = await library._reconcile_manifest(base, clips_state)

    assert count == 1
    entry = clips_state["missing-with-stale-art"]
    assert entry.path == ""
    assert entry.meta_hash == ""
    assert not entry.embedded_art_hash
    assert entry.album is None


async def test_reconcile_manifest_treats_zero_byte_as_missing(hass: HomeAssistant, tmp_path: Path) -> None:
    """Zero-byte files are reconciled the same as fully missing files."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    base = tmp_path / "mirror"
    base.mkdir()
    (base / "empty.flac").write_bytes(b"")
    clips_state: dict[str, dict[str, object]] = {
        "empty-id": ManifestEntry.from_dict({"path": "empty.flac", "meta_hash": "abc"}),
    }

    count = await library._reconcile_manifest(base, clips_state)

    assert count == 1
    assert clips_state["empty-id"].path == ""


async def test_reconcile_manifest_idempotent_when_clean(hass: HomeAssistant, tmp_path: Path) -> None:
    """Manifest with all files present: no mutation, returns 0."""
    library = DownloadedLibrary(hass, InMemoryDownloadedLibraryStorage())
    base = tmp_path / "mirror"
    base.mkdir()
    (base / "a.flac").write_bytes(b"fLaC" + b"\x00" * 10)
    (base / "b.flac").write_bytes(b"fLaC" + b"\x00" * 10)
    clips_state: dict[str, dict[str, object]] = {
        "a-id": ManifestEntry.from_dict({"path": "a.flac", "meta_hash": "h1"}),
        "b-id": ManifestEntry.from_dict({"path": "b.flac", "meta_hash": "h2"}),
    }
    snapshot = json.dumps({k: v.to_dict() for k, v in clips_state.items()}, sort_keys=True)

    count = await library._reconcile_manifest(base, clips_state)

    assert count == 0
    assert json.dumps({k: v.to_dict() for k, v in clips_state.items()}, sort_keys=True) == snapshot
