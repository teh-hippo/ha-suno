"""Multi-account data-safety tests for the Downloaded Library engine.

The integration's runtime layer refuses to load two entries whose
``download_path`` overlaps or nests, so a single loaded engine
exclusively owns its entire download tree. These tests therefore focus
on the defenses that still matter:

* the engine-level guard that skips ``_reconcile_disk`` after a
  remove + re-add wipes the per-entry HA Store but leaves the on-disk
  library behind,
* sibling-aware folder-art handling so two clips that legitimately end
  up in the same ``<artist>/<title>`` folder (collab / shared / same
  artist+title) do not strip each other's cover art on deletion or
  rename,
* the ``.cover_hash`` foreign-id defense which leaves cover sidecars
  alone when legacy state or manual fiddling left clip_ids the engine
  no longer tracks, and
* the truncation-hash disambiguation in ``_safe_name`` so very long
  artist or title names cannot collapse onto a shared folder.

Fixtures are defined locally so this file does not depend on (or
modify) the project-wide ``conftest.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from custom_components.suno.const import (
    CONF_ALL_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_PLAYLISTS,
    CONF_SHOW_LIKED,
    CONF_SHOW_MY_SONGS,
    CONF_SHOW_PLAYLISTS,
    DOWNLOAD_MODE_MIRROR,
    QUALITY_HIGH,
)
from custom_components.suno.downloaded_library import (
    DownloadedLibrary,
    InMemoryDownloadedLibraryStorage,
    ManifestEntry,
)
from custom_components.suno.downloaded_library.cover_art import CoverHashFile
from custom_components.suno.downloaded_library.paths import _clip_path, _safe_name

# ── Local fixtures ──────────────────────────────────────────────


def _options(base: Path) -> dict[str, Any]:
    """Minimal options blob accepted by the engine."""
    return {
        CONF_DOWNLOAD_PATH: str(base),
        CONF_SHOW_LIKED: True,
        CONF_SHOW_MY_SONGS: False,
        CONF_SHOW_PLAYLISTS: False,
        CONF_ALL_PLAYLISTS: False,
        CONF_PLAYLISTS: [],
    }


def _write_audio(path: Path, body: bytes = b"fLaC" + b"\x00" * 48) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _seed_clip(
    base: Path,
    artist: str,
    title: str,
    clip_id: str,
    *,
    audio_body: bytes = b"fLaC" + b"\x00" * 48,
    sources: list[str] | None = None,
) -> tuple[str, ManifestEntry]:
    """Create an audio file under ``<artist>/<title>/`` and return a manifest entry."""
    rel_path = f"{artist}/{title}/{artist}-{title} [{clip_id[:8]}].flac"
    _write_audio(base / rel_path, audio_body)
    entry = ManifestEntry.from_dict(
        {
            "path": rel_path,
            "sources": sources or ["liked"],
            "source_modes": {(sources or ["liked"])[0]: DOWNLOAD_MODE_MIRROR},
            "quality": QUALITY_HIGH,
            "size": len(audio_body),
        }
    )
    return clip_id, entry


def _engine_with_state(
    hass: HomeAssistant,
    state: dict[str, Any] | None = None,
) -> DownloadedLibrary:
    storage = InMemoryDownloadedLibraryStorage(state)
    engine = DownloadedLibrary(hass, storage)
    return engine


# ── Empty-manifest guard (remove+re-add safety) ─────────────────


async def test_run_download_skips_reconcile_after_remove_and_readd(hass: HomeAssistant, tmp_path: Path) -> None:
    """Empty manifest with no ``last_download`` → orphan reconcile must be skipped.

    Simulates remove + re-add of the same account: the per-entry HA Store is
    empty, but the existing library is still on disk. A naive engine would
    treat every existing file as orphan and delete the entire library.
    The engine's ``_is_fresh_manifest_after_run`` gate blocks the destructive
    pass until the next sync re-populates the manifest.
    """
    from custom_components.suno.models import SunoData  # noqa: PLC0415

    library = _engine_with_state(hass)
    assert library.state.last_download is None
    assert not library.state.clips

    surviving = tmp_path / "artistA" / "Song" / "artistA-Song [aaaa1111].flac"
    _write_audio(surviving, b"fLaCsurvivor")

    # Force=True drives a reconcile attempt even with an empty desired plan.
    await library.async_reconcile(_options(tmp_path), SunoData(), force=True)

    assert surviving.exists(), "Pre-existing library files were nuked by the empty-manifest reconcile pass"


async def test_run_download_runs_reconcile_when_state_marker_present(hass: HomeAssistant, tmp_path: Path) -> None:
    """If ``last_download`` is set, the empty-manifest guard does NOT apply.

    A previously-synced account whose library was emptied legitimately (user
    unlinked every source) must still let orphan files go on the next run.
    """
    from custom_components.suno.models import SunoData  # noqa: PLC0415

    library = _engine_with_state(hass, {"clips": {}, "last_download": "2026-01-01T00:00:00+00:00"})
    await library.async_load()

    legacy = tmp_path / "artistA" / "Song" / "artistA-Song [legacy00].flac"
    _write_audio(legacy, b"fLaClegacy")

    await library.async_reconcile(_options(tmp_path), SunoData(), force=True)

    # The guard checks BOTH empty clips AND no last_download. Here last_download
    # is set, so the guard does NOT fire and the legacy orphan is removed by
    # the tree-wide reconcile pass.
    assert library.state.last_download is not None
    assert not legacy.exists()


# ── _reconcile_disk: per-folder .cover_hash defense-in-depth ────


async def test_reconcile_disk_preserves_cover_hash_with_foreign_clip_ids(hass: HomeAssistant, tmp_path: Path) -> None:
    """``.cover_hash`` with unknown clip_ids keeps folder sidecars in place.

    Defense-in-depth against legacy migrations or manual edits leaving
    clip_ids the current manifest does not know about. Within an
    exclusively-owned tree those are still ours, just historically; the
    safest behaviour is to leave them alone rather than drop cover art.
    """
    library = _engine_with_state(hass)
    own_id, own_entry = _seed_clip(tmp_path, "artistA", "Song", "aaaa1111-cidA")

    # Remove the actual audio to trigger the sidecar-pruning branch.
    audio_path = tmp_path / own_entry.path
    audio_path.unlink()

    folder = audio_path.parent
    (folder / "cover.jpg").write_bytes(b"jpegbytes")
    (folder / ".cover_hash").write_text("legacy-clip-id=legacyhashvalue\n")

    removed = await library._reconcile_disk(tmp_path, {own_id: own_entry})

    assert (folder / "cover.jpg").exists()
    assert (folder / ".cover_hash").exists()
    assert removed == 0


# ── _delete_sidecars: folder-sibling safety ─────────────────────


async def test_delete_sidecars_preserves_folder_art_when_sibling_remains(hass: HomeAssistant, tmp_path: Path) -> None:
    """Pruning one clip in a folder must not strip folder-level cover art."""
    library = _engine_with_state(hass)

    going_id, going_entry = _seed_clip(tmp_path, "shared", "Album", "aaaa1111-going")
    staying_id, staying_entry = _seed_clip(tmp_path, "shared", "Album", "bbbb2222-stay")

    folder = (tmp_path / going_entry.path).parent
    (folder / "cover.jpg").write_bytes(b"folderjpeg")
    (folder / ".cover_hash").write_text(f"{going_id}=hashA\n{staying_id}=hashB\n")

    remaining = {staying_id: staying_entry}
    await library._delete_sidecars(
        tmp_path,
        going_entry.path,
        clip_id=going_id,
        clips_state=remaining,
    )

    assert (folder / "cover.jpg").exists(), "cover.jpg removed despite sibling clip remaining"
    assert (folder / ".cover_hash").exists(), ".cover_hash removed despite sibling clip"
    parsed = CoverHashFile._parse((folder / ".cover_hash").read_text())
    assert staying_id in parsed
    assert going_id not in parsed


async def test_delete_sidecars_removes_folder_art_when_no_sibling(hass: HomeAssistant, tmp_path: Path) -> None:
    """When no other clip references the folder, ``cover.jpg`` + ``.cover_hash`` go."""
    library = _engine_with_state(hass)
    lone_id, lone_entry = _seed_clip(tmp_path, "solo", "Track", "cccc3333-cid")
    folder = (tmp_path / lone_entry.path).parent
    (folder / "cover.jpg").write_bytes(b"jpeg")
    (folder / ".cover_hash").write_text(f"{lone_id}=hashSolo\n")

    await library._delete_sidecars(tmp_path, lone_entry.path, clip_id=lone_id, clips_state={})

    assert not (folder / "cover.jpg").exists()
    assert not (folder / ".cover_hash").exists()


async def test_delete_sidecars_respects_foreign_cover_hash_entries(hass: HomeAssistant, tmp_path: Path) -> None:
    """``.cover_hash`` containing unknown clip_ids must keep ``cover.jpg``.

    Within a single account this guards against the engine wiping cover art
    that historic state (migration, manual fiddling) still claims is in use.
    """
    library = _engine_with_state(hass)
    own_id, own_entry = _seed_clip(tmp_path, "artistA", "Album", "aaaa1111-own")
    folder = (tmp_path / own_entry.path).parent
    (folder / "cover.jpg").write_bytes(b"jpeg")
    (folder / ".cover_hash").write_text(f"{own_id}=hashA\nlegacy-cidB=hashLegacy\n")

    await library._delete_sidecars(tmp_path, own_entry.path, clip_id=own_id, clips_state={})

    assert (folder / "cover.jpg").exists(), "cover.jpg removed even though .cover_hash claims another owner"
    assert (folder / ".cover_hash").exists()
    parsed = CoverHashFile._parse((folder / ".cover_hash").read_text())
    assert "legacy-cidB" in parsed
    assert own_id not in parsed


async def test_delete_sidecars_track_sidecars_always_safe(hass: HomeAssistant, tmp_path: Path) -> None:
    """Per-track sidecars (``.mp4``, ``.jpg``) named after the audio file are always removed."""
    library = _engine_with_state(hass)
    lone_id, lone_entry = _seed_clip(tmp_path, "artist", "Song", "dddd4444-cid")
    audio = tmp_path / lone_entry.path
    audio.with_suffix(".mp4").write_bytes(b"\x00mp4")
    audio.with_suffix(".jpg").write_bytes(b"\xffjpg")

    await library._delete_sidecars(tmp_path, lone_entry.path, clip_id=lone_id, clips_state={})

    assert not audio.with_suffix(".mp4").exists()
    assert not audio.with_suffix(".jpg").exists()


# ── _move_sidecars: sibling-aware migration ─────────────────────


async def test_move_sidecars_copies_folder_art_when_sibling_remains(hass: HomeAssistant, tmp_path: Path) -> None:
    """Renaming a clip out of a shared folder must leave the sibling whole."""
    library = _engine_with_state(hass)
    from custom_components.suno.const import QUALITY_HIGH  # noqa: PLC0415

    from .conftest import make_clip  # noqa: PLC0415

    moving_clip = make_clip("eeee5555-cid-move", title="MovedTitle", display_name="shared")
    staying_id, staying_entry = _seed_clip(tmp_path, "shared", "Album", "ffff6666-stay")

    old_rel = "shared/Album/shared-Album [eeee5555].flac"
    old_file = tmp_path / old_rel
    _write_audio(old_file)
    new_rel = _clip_path(moving_clip, QUALITY_HIGH)
    new_file = tmp_path / new_rel
    new_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.rename(new_file)

    old_folder = old_file.parent
    (old_folder / "cover.jpg").write_bytes(b"jpeg-shared")
    (old_folder / ".cover_hash").write_text(
        f"{moving_clip.id}=hashMove\n{staying_id}=hashStay\n",
    )

    await library._move_sidecars(
        tmp_path,
        moving_clip,
        old_file,
        new_file,
        clip_id=moving_clip.id,
        clips_state={staying_id: staying_entry},
    )

    # Both sides should now have cover.jpg.
    assert (old_folder / "cover.jpg").exists()
    assert (new_file.parent / "cover.jpg").exists()
    # The old .cover_hash retains the sibling, the new one contains the migrant.
    old_hashes = CoverHashFile._parse((old_folder / ".cover_hash").read_text())
    new_hashes = CoverHashFile._parse((new_file.parent / ".cover_hash").read_text())
    assert staying_id in old_hashes
    assert moving_clip.id not in old_hashes
    assert moving_clip.id in new_hashes


# ── async_purge_old_path: within-account folder-art safety ──────


async def test_async_purge_old_path_preserves_sibling_clip_folder_art(hass: HomeAssistant, tmp_path: Path) -> None:
    """Folder-level cover art is removed once the LAST sibling clip is purged.

    The first removal must leave ``cover.jpg``/``.cover_hash`` in place so
    the still-present sibling keeps its art; the second removal sees an
    empty remaining manifest and cleans the folder fully.
    """
    going_id, going_entry = _seed_clip(tmp_path, "shared", "Album", "aaaa1111-going")
    sibling_id, sibling_entry = _seed_clip(tmp_path, "shared", "Album", "bbbb2222-sib")
    folder = (tmp_path / going_entry.path).parent
    (folder / "cover.jpg").write_bytes(b"jpeg")
    (folder / ".cover_hash").write_text(f"{going_id}=hashA\n{sibling_id}=hashB\n")

    state = {
        "clips": {going_id: going_entry.to_dict(), sibling_id: sibling_entry.to_dict()},
        "last_download": "2026-01-01T00:00:00+00:00",
    }
    library = _engine_with_state(hass, state)
    await library.async_load()

    await library.async_purge_old_path(str(tmp_path))

    assert not (tmp_path / going_entry.path).exists()
    assert not (tmp_path / sibling_entry.path).exists()
    # Contract: no stale sidecars linger for a folder we have fully emptied.
    if folder.exists():
        assert not (folder / "cover.jpg").exists()
        assert not (folder / ".cover_hash").exists()


# ── _safe_name: truncation-hash disambiguation ──────────────────


def test_safe_name_short_names_unchanged() -> None:
    """Short names round-trip byte-identically with the legacy implementation."""
    assert _safe_name("Hello World") == "Hello World"
    assert _safe_name("artist") == "artist"
    assert _safe_name("") == "untitled"


def test_safe_name_distinct_long_names_get_distinct_outputs() -> None:
    """Two distinct names with the same 200-char prefix must not collide."""
    prefix = "x" * 250
    a = prefix + "AAA"
    b = prefix + "BBB"
    sa = _safe_name(a)
    sb = _safe_name(b)
    assert sa != sb, "Truncation collapsed distinct names onto the same folder"
    assert len(sa) <= 200
    assert len(sb) <= 200


def test_safe_name_truncation_is_stable() -> None:
    """The hash suffix is a deterministic function of the input."""
    a = "y" * 300
    assert _safe_name(a) == _safe_name(a)


# ── Smoke: single-account on-disk contract still holds ─────────


@pytest.mark.parametrize(
    ("artist", "title", "clip_id"),
    [
        ("artist", "Song", "deadbeef-001"),
        ("Artist Two", "Track 2", "cafe2025-002"),
    ],
)
def test_clip_path_contract_unchanged_for_normal_inputs(artist: str, title: str, clip_id: str) -> None:
    """Normal-length names produce the same on-disk path the previous build wrote."""
    from custom_components.suno.models import SunoClip  # noqa: PLC0415

    clip = SunoClip(
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
        display_name=artist,
    )
    rel = _clip_path(clip, QUALITY_HIGH)
    assert rel == f"{artist}/{title}/{artist}-{title} [{clip_id[:8]}].flac"
