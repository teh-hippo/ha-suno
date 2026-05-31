"""Tests for the typed ManifestEntry dataclass.

These cover the lifecycle methods (apply_clip_metadata,
apply_file_state, clear_for_redownload, needs_retag) that replace the
docstring-only clip-mirror vs file-mirror split.
"""

from __future__ import annotations

import pytest

from custom_components.suno.downloaded_library.contracts import ManifestEntry
from custom_components.suno.models import (
    SunoClip,
    clip_meta_hash,
    image_url_hash,
    selected_image_url,
)


def _clip(clip_id: str = "clip-aaa-111", title: str = "Song", *, image: str = "") -> SunoClip:
    return SunoClip(
        id=clip_id,
        title=title,
        audio_url=f"https://cdn1.suno.ai/{clip_id}.mp3",
        image_url="",
        image_large_url=image,
        is_liked=True,
        status="complete",
        created_at="2026-03-15T10:00:00Z",
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
        display_name="artist",
    )


# ── apply_clip_metadata ──────────────────────────────────────────────


def test_apply_clip_metadata_refreshes_clip_mirror_fields_only() -> None:
    """Clip-mirror update must not touch file-mirror fields."""
    entry = ManifestEntry(
        path="keep.flac",
        size=1234,
        embedded_art_hash="file-hash-old",
        title="Stale Title",
        meta_hash="stale",
    )
    clip = _clip(title="Fresh Title", image="https://images.example/cover.jpg")

    entry.apply_clip_metadata(clip, album="Liked Songs")

    assert entry.title == "Fresh Title"
    assert entry.created == "2026-03-15"
    assert entry.meta_hash == clip_meta_hash(clip)
    assert entry.album == "Liked Songs"
    assert entry.path == "keep.flac"
    assert entry.size == 1234
    assert entry.embedded_art_hash == "file-hash-old"


def test_apply_clip_metadata_with_no_album_marker_clears_album() -> None:
    entry = ManifestEntry(album="Old Album", embedded_art_hash="art-hash", size=99)
    clip = _clip()

    entry.apply_clip_metadata(clip)

    assert entry.album is None
    assert entry.embedded_art_hash == "art-hash"
    assert entry.size == 99


# ── apply_file_state ─────────────────────────────────────────────────


def test_apply_file_state_writes_size_and_embedded_art_hash() -> None:
    entry = ManifestEntry()
    clip = _clip(image="https://images.example/cover.jpg")

    entry.apply_file_state(clip, 4321)

    assert entry.size == 4321
    assert entry.embedded_art_hash == image_url_hash(selected_image_url(clip))


def test_apply_file_state_clears_embedded_art_hash_when_no_image() -> None:
    entry = ManifestEntry(embedded_art_hash="stale")
    clip = _clip()  # no image

    entry.apply_file_state(clip, 0)

    assert entry.size == 0
    assert entry.embedded_art_hash == ""


# ── clear_for_redownload (regression for v6.3.1-v6.3.4 leak) ─────────


def test_clear_for_redownload_clears_embedded_art_hash() -> None:
    """Closes the v6.3 leak where 3 sites cleared path+meta_hash but
    forgot ``embedded_art_hash``, leaving a stale art sentinel that
    suppressed the next retag."""
    entry = ManifestEntry(
        path="some/file.flac",
        meta_hash="abc123",
        album="Some Album",
        embedded_art_hash="stale-art-hash",
        size=12345,
        sources=["liked"],
        quality="high",
    )

    entry.clear_for_redownload()

    assert entry.path == ""
    assert entry.meta_hash == ""
    assert entry.album is None
    assert entry.embedded_art_hash == ""
    assert entry.sources == ["liked"]
    assert entry.quality == "high"
    assert entry.size == 12345


# ── needs_retag ─────────────────────────────────────────────────────


def test_needs_retag_returns_none_when_everything_matches() -> None:
    clip = _clip(image="https://images.example/cover.jpg")
    entry = ManifestEntry(
        meta_hash=clip_meta_hash(clip),
        embedded_art_hash=image_url_hash(selected_image_url(clip)),
        album=None,
    )

    assert entry.needs_retag(clip, resolved_album=None) is None


def test_needs_retag_returns_meta_when_meta_hash_changes() -> None:
    clip = _clip(image="https://images.example/cover.jpg")
    entry = ManifestEntry(
        meta_hash="0000_outdated",
        embedded_art_hash=image_url_hash(selected_image_url(clip)),
    )

    assert entry.needs_retag(clip, resolved_album=None) == "meta"


def test_needs_retag_returns_art_when_embedded_art_hash_changes() -> None:
    clip = _clip(image="https://images.example/cover.jpg")
    entry = ManifestEntry(
        meta_hash=clip_meta_hash(clip),
        embedded_art_hash="0000_outdated_art",
    )

    assert entry.needs_retag(clip, resolved_album=None) == "art"


def test_needs_retag_returns_album_when_resolved_album_changes() -> None:
    clip = _clip(image="https://images.example/cover.jpg")
    entry = ManifestEntry(
        meta_hash=clip_meta_hash(clip),
        embedded_art_hash=image_url_hash(selected_image_url(clip)),
        album="Old Album Marker",
    )

    assert entry.needs_retag(clip, resolved_album="New Album Marker") == "album"


def test_needs_retag_returns_album_when_album_disappears() -> None:
    clip = _clip(image="https://images.example/cover.jpg")
    entry = ManifestEntry(
        meta_hash=clip_meta_hash(clip),
        embedded_art_hash=image_url_hash(selected_image_url(clip)),
        album="Old Album",
    )

    assert entry.needs_retag(clip, resolved_album=None) == "album"


def test_needs_retag_no_album_change_when_both_none() -> None:
    clip = _clip(image="https://images.example/cover.jpg")
    entry = ManifestEntry(
        meta_hash=clip_meta_hash(clip),
        embedded_art_hash=image_url_hash(selected_image_url(clip)),
        album=None,
    )

    assert entry.needs_retag(clip, resolved_album=None) is None


def test_needs_retag_returns_first_reason_in_priority_order() -> None:
    """When multiple reasons apply, return 'meta' (most general) first."""
    clip = _clip(image="https://images.example/cover.jpg")
    entry = ManifestEntry(
        meta_hash="0000_outdated",
        embedded_art_hash="0000_outdated",
        album="0000_outdated",
    )

    assert entry.needs_retag(clip, resolved_album="new") == "meta"


# ── to_dict / from_dict round-trip ───────────────────────────────────


def test_to_dict_omits_optional_fields_when_unset() -> None:
    """Match the v6.3.4 on-disk shape: optional fields appear only when set."""
    entry = ManifestEntry(
        path="x.flac",
        sources=["liked"],
        source_modes={"liked": "mirror"},
        quality="high",
        title="Song",
        created="2026-03-15",
        meta_hash="abc",
        size=100,
    )

    d = entry.to_dict()

    assert "album" not in d
    assert "embedded_art_hash" not in d
    assert "video_url_hash" not in d
    assert "video_art_settings" not in d
    assert "video_art_failed" not in d


def test_to_dict_includes_optional_fields_when_set() -> None:
    entry = ManifestEntry(
        path="x.flac",
        album="My Album",
        embedded_art_hash="art-h",
        video_url_hash="vid-h",
        video_art_settings={"width": 720},
    )

    d = entry.to_dict()

    assert d["album"] == "My Album"
    assert d["embedded_art_hash"] == "art-h"
    assert d["video_url_hash"] == "vid-h"
    assert d["video_art_settings"] == {"width": 720}


def test_from_dict_to_dict_round_trip_preserves_known_fields() -> None:
    raw = {
        "path": "AmeonAI/Bowser Clean/AmeonAI-Bowser Clean [3548e601].flac",
        "title": "Bowser Clean",
        "created": "2026-04-08",
        "sources": ["liked", "playlist:abc"],
        "size": 14418971,
        "meta_hash": "896a8e20939d",
        "quality": "high",
        "source_modes": {"liked": "mirror", "playlist:abc": "mirror"},
        "embedded_art_hash": "648814de8933",
    }

    round_tripped = ManifestEntry.from_dict(raw).to_dict()

    for key, value in raw.items():
        assert round_tripped[key] == value


def test_from_dict_preserves_unknown_keys_in_extras() -> None:
    """A newer build's fields must survive a load-save cycle in an older build."""
    raw = {
        "path": "x.flac",
        "future_field": "future_value",
        "another_one": {"nested": 1},
    }

    entry = ManifestEntry.from_dict(raw)
    round_tripped = entry.to_dict()

    assert round_tripped["future_field"] == "future_value"
    assert round_tripped["another_one"] == {"nested": 1}


def test_album_empty_string_normalises_to_none() -> None:
    """Guard against accidental album="" writes that would persist a stored empty string."""
    entry = ManifestEntry(album="")

    assert entry.album is None
    assert "album" not in entry.to_dict()


@pytest.mark.parametrize("field_name", ["path", "title", "meta_hash", "embedded_art_hash"])
def test_default_values_are_empty_strings_not_none(field_name: str) -> None:
    """String fields default to '' to avoid Optional[str] noise across the engine."""
    entry = ManifestEntry()
    assert getattr(entry, field_name) == ""
