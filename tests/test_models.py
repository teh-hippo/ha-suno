"""Tests for model defensive loading and lineage metadata."""

from custom_components.suno.models import (
    SunoClip,
    _safe_clip,
    _safe_clips,
    _safe_playlist,
    _safe_playlists,
    clip_meta_hash,
)


def test_clip_meta_hash_changes_when_gpt_description_prompt_changes() -> None:
    base_kwargs = {
        "id": "c1",
        "title": "T",
        "audio_url": "",
        "image_url": "",
        "image_large_url": "",
        "is_liked": False,
        "status": "complete",
        "created_at": "",
        "tags": "",
        "duration": 0.0,
        "clip_type": "",
        "has_vocal": False,
    }
    clip_a = SunoClip(**base_kwargs, gpt_description_prompt="desc A")
    clip_b = SunoClip(**base_kwargs, gpt_description_prompt="desc B")

    assert clip_meta_hash(clip_a) != clip_meta_hash(clip_b)


def test_clip_meta_hash_changes_when_handle_changes() -> None:

    base_kwargs = dict(
        id="c1",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0.0,
        clip_type="",
        has_vocal=False,
    )
    clip_a = SunoClip(**base_kwargs, handle="@alice")
    clip_b = SunoClip(**base_kwargs, handle="@alice-renamed")

    assert clip_meta_hash(clip_a) != clip_meta_hash(clip_b)


# ── TestFromApiResponse (converted to free functions) ────────────────────

"""Tests for fresh Suno API payload parsing."""


def _from_api_response_raw(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "id": "clip-1",
        "title": "Test",
        "audio_url": "https://cdn1.suno.ai/clip-1.mp3",
        "image_url": "",
        "image_large_url": "",
        "is_liked": False,
        "status": "complete",
        "created_at": "2026-01-01T00:00:00Z",
        "metadata": {
            "tags": "pop",
            "duration": 120.0,
            "type": "gen",
            "has_vocal": True,
        },
    }
    raw.update(overrides)
    return raw


def test_from_api_response_from_api_response_extracts_root_ancestor_id_when_present() -> None:
    clip = SunoClip.from_api_response(_from_api_response_raw(root_ancestor_id="abc123"))

    assert clip.root_ancestor_id == "abc123"


def test_from_api_response_from_api_response_extracts_lineage_status_when_present() -> None:
    clip = SunoClip.from_api_response(_from_api_response_raw(lineage_status="resolved"))

    assert clip.lineage_status == "resolved"


def test_from_api_response_from_api_response_extracts_album_title_when_present() -> None:
    clip = SunoClip.from_api_response(_from_api_response_raw(album_title="Original Album"))

    assert clip.album_title == "Original Album"


def test_from_api_response_from_api_response_defaults_lineage_fields_when_absent() -> None:
    clip = SunoClip.from_api_response(_from_api_response_raw())

    assert clip.root_ancestor_id == ""
    assert clip.lineage_status == ""
    assert clip.album_title == ""


# ── TestSafeClip (converted to free functions) ────────────────────

"""Tests for defensive clip loading."""


def test_safe_clip_safe_clip_ignores_unknown_fields() -> None:
    """Extra keys in stored data are filtered out, clip loads successfully."""
    raw = {
        "id": "clip-1",
        "title": "Test",
        "audio_url": "https://example.com/audio.mp3",
        "image_url": "https://example.com/img.jpg",
        "image_large_url": "https://example.com/img_lg.jpg",
        "is_liked": False,
        "status": "complete",
        "created_at": "2026-01-01T00:00:00Z",
        "tags": "pop",
        "duration": 120.0,
        "clip_type": "gen",
        "has_vocal": True,
        "future_field": "should be ignored",
        "another_unknown": 42,
    }
    clip = _safe_clip(raw)
    assert clip.id == "clip-1"
    assert clip.title == "Test"


def test_safe_clip_safe_clip_missing_optional_fields() -> None:
    """Old stored data without root_ancestor_id loads with default empty string."""
    raw = {
        "id": "clip-2",
        "title": "Old Song",
        "audio_url": "https://example.com/audio.mp3",
        "image_url": "",
        "image_large_url": "",
        "is_liked": False,
        "status": "complete",
        "created_at": "2025-01-01T00:00:00Z",
        "tags": "",
        "duration": 60.0,
        "clip_type": "gen",
        "has_vocal": False,
    }
    clip = _safe_clip(raw)
    assert clip.root_ancestor_id == ""
    assert clip.edited_clip_id == ""


def test_safe_clip_safe_clip_minimal_required_only() -> None:
    """Clip with only required fields (all others defaulted) succeeds."""
    raw = {
        "id": "clip-3",
        "title": "Minimal",
        "audio_url": "",
        "image_url": "",
        "image_large_url": "",
        "is_liked": False,
        "status": "complete",
        "created_at": "",
        "tags": "",
        "duration": 0.0,
        "clip_type": "",
        "has_vocal": False,
    }
    clip = _safe_clip(raw)
    assert clip.id == "clip-3"
    assert clip.root_ancestor_id == ""


def test_safe_clip_safe_clips_skips_corrupt_entries() -> None:
    """Batch function logs and skips corrupt entries, keeps valid ones."""
    raw_list = [
        {
            "id": "good-1",
            "title": "Good",
            "audio_url": "",
            "image_url": "",
            "image_large_url": "",
            "is_liked": False,
            "status": "complete",
            "created_at": "",
            "tags": "",
            "duration": 0.0,
            "clip_type": "",
            "has_vocal": False,
        },
        {},  # corrupt - missing all required fields
        {
            "id": "good-2",
            "title": "Also Good",
            "audio_url": "",
            "image_url": "",
            "image_large_url": "",
            "is_liked": False,
            "status": "complete",
            "created_at": "",
            "tags": "",
            "duration": 0.0,
            "clip_type": "",
            "has_vocal": False,
        },
    ]
    clips = _safe_clips(raw_list)
    assert len(clips) == 2
    assert clips[0].id == "good-1"
    assert clips[1].id == "good-2"


def test_safe_clip_meta_hash_includes_root_ancestor() -> None:
    """clip_meta_hash changes when root_ancestor_id is set."""
    clip = _safe_clip(
        {
            "id": "clip-hash",
            "title": "Test",
            "audio_url": "",
            "image_url": "",
            "image_large_url": "",
            "is_liked": False,
            "status": "complete",
            "created_at": "",
            "tags": "pop",
            "duration": 60.0,
            "clip_type": "gen",
            "has_vocal": False,
        }
    )
    hash_before = clip_meta_hash(clip)
    clip.root_ancestor_id = "root-123"
    hash_after = clip_meta_hash(clip)
    assert hash_before != hash_after


def test_safe_clip_clip_meta_hash_changes_when_prompt_changes() -> None:
    """clip_meta_hash changes when the lyrics-driving prompt changes."""
    base_kwargs = dict(
        id="c1",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0.0,
        clip_type="",
        has_vocal=False,
    )
    clip_a = SunoClip(**base_kwargs, prompt="prompt A")
    clip_b = SunoClip(**base_kwargs, prompt="prompt B")
    assert clip_meta_hash(clip_a) != clip_meta_hash(clip_b)


# ── TestSafePlaylist (converted to free functions) ────────────────────

"""Tests for defensive playlist loading."""


def test_safe_playlist_safe_playlist_ignores_unknown_fields() -> None:
    """Extra keys in stored data are filtered out, playlist loads successfully."""
    raw = {"id": "pl-1", "name": "My Playlist", "image_url": "", "num_clips": 5, "extra": "ignored"}
    pl = _safe_playlist(raw)
    assert pl.id == "pl-1"
    assert pl.name == "My Playlist"


def test_safe_playlist_safe_playlists_skips_corrupt() -> None:
    """Batch function logs and skips corrupt entries, keeps valid ones."""
    raw_list = [
        {"id": "pl-1", "name": "Good", "image_url": "", "num_clips": 3},
        {},
        {"id": "pl-2", "name": "Also Good", "image_url": "", "num_clips": 1},
    ]
    pls = _safe_playlists(raw_list)
    assert len(pls) == 2


# ── TestToTrackMetadataAlbumArtist (converted to free functions) ────────────────────

"""Album artist propagation (Release 2: 2.11)."""


def _to_track_metadata_album_artist_make(display_name: str = "AmeonAI"):

    return SunoClip(
        id="abc",
        title="Song",
        audio_url="https://x/y.mp3",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="2026-04-20T00:00:00Z",
        tags="",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
        display_name=display_name,
    )


def test_to_track_metadata_album_artist_album_artist_uses_display_name() -> None:
    meta = _to_track_metadata_album_artist_make().to_track_metadata()
    assert meta.album_artist == "AmeonAI"


def test_to_track_metadata_album_artist_album_artist_falls_back_to_suno() -> None:
    meta = _to_track_metadata_album_artist_make(display_name="").to_track_metadata()
    assert meta.album_artist == "Suno"


# ── Moved from legacy test_audio.py during Round 2 test restructure ──

# ── clip_meta_hash ──────────────────────────────────────────────────


def test_clip_meta_hash_deterministic() -> None:
    """Same clip metadata always produces the same hash."""
    clip = SunoClip(
        id="clip-aaa-111",
        title="Test Song",
        audio_url="https://cdn1.suno.ai/clip-aaa-111.mp3",
        image_url="https://cdn1.suno.ai/image.jpeg",
        image_large_url="https://cdn1.suno.ai/image_large.jpeg",
        is_liked=True,
        status="complete",
        created_at="2026-03-19T10:00:00Z",
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
    )

    hash1 = clip_meta_hash(clip)
    hash2 = clip_meta_hash(clip)

    assert hash1 == hash2
    assert len(hash1) == 12
    assert isinstance(hash1, str)


# ── SunoClip properties ───────────────────────────────────────────


def test_suno_model_combined() -> None:
    """suno_model property combines model_name and major_model_version."""
    clip = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        model_name="chirp-crow",
        major_model_version="v5",
    )
    assert clip.suno_model == "chirp-crow (v5)"


def test_suno_model_no_major() -> None:
    """suno_model with empty major_model_version returns just model_name."""
    clip = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        model_name="chirp-chirp",
        major_model_version="",
    )
    assert clip.suno_model == "chirp-chirp"


def test_suno_lineage_remix() -> None:
    """suno_lineage formats remix with parent ID."""
    clip = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        is_remix=True,
        edited_clip_id="d57c503f-cbaa-4651-aaf4-628d363ccf4c",
    )
    assert clip.suno_lineage == "Remix of d57c503f"


def test_suno_lineage_with_history() -> None:
    """suno_lineage formats edit history with time ranges and lyrics."""
    clip = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        edited_clip_id="d57c503f-cbaa-4651-aaf4-628d363ccf4c",
        history=[
            {
                "id": "d57c503f-cbaa-4651-aaf4-628d363ccf4c",
                "infill_start_s": 58.44,
                "infill_end_s": 61.8,
                "infill_lyrics": "everybody said\nHISSSSS!",
            }
        ],
    )
    result = clip.suno_lineage
    assert "Derived from d57c503f" in result
    assert "Edit 00:58-01:01" in result
    assert "everybody said" in result


def test_clip_meta_hash_excludes_display_name() -> None:
    """Meta hash does NOT change when only display_name changes.

    Path-affecting fields like display_name are handled by path comparison,
    not the content hash.
    """
    clip1 = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="img.jpg",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        display_name="user1",
    )
    clip2 = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="img.jpg",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        display_name="user2",
    )
    assert clip_meta_hash(clip1) == clip_meta_hash(clip2)


# ── TC-11: from_api_response new fields ───────────────────────────


def test_from_api_response_parses_video_url() -> None:
    """from_api_response correctly parses video_url from API data."""
    raw = {
        "id": "vid-clip",
        "status": "complete",
        "video_url": "https://cdn2.suno.ai/vid-clip.mp4",
        "metadata": {"type": "gen"},
    }
    clip = SunoClip.from_api_response(raw)
    assert clip.video_url == "https://cdn1.suno.ai/vid-clip.mp4"


def test_from_api_response_parses_display_name_and_handle() -> None:
    """from_api_response correctly parses display_name and handle."""
    raw = {
        "id": "dn-clip",
        "status": "complete",
        "display_name": "Cool Artist",
        "handle": "cool-artist-42",
        "metadata": {"type": "gen"},
    }
    clip = SunoClip.from_api_response(raw)
    assert clip.display_name == "Cool Artist"
    assert clip.handle == "cool-artist-42"


def test_from_api_response_parses_edited_clip_id_and_is_remix() -> None:
    """from_api_response correctly parses edited_clip_id and is_remix."""
    raw = {
        "id": "remix-clip",
        "status": "complete",
        "metadata": {
            "type": "gen",
            "edited_clip_id": "parent-abcd-1234",
            "is_remix": True,
        },
    }
    clip = SunoClip.from_api_response(raw)
    assert clip.edited_clip_id == "parent-abcd-1234"
    assert clip.is_remix is True


# ── TC-12: suno_lineage edge cases ────────────────────────────────


def test_suno_lineage_multiple_history_entries() -> None:
    """suno_lineage with multiple history entries."""
    clip = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        edited_clip_id="aaaa1111-bbbb-cccc-dddd-eeee2222ffff",
        history=[
            {
                "id": "aaaa1111-bbbb-cccc-dddd-eeee2222ffff",
                "infill_start_s": 0,
                "infill_end_s": 30,
                "infill_lyrics": "first edit",
            },
            {
                "id": "bbbb2222-cccc-dddd-eeee-ffff3333aaaa",
                "infill_start_s": 60,
                "infill_end_s": 90,
                "infill_lyrics": "second edit",
            },
        ],
    )
    result = clip.suno_lineage
    lines = result.split("\n")
    assert len(lines) == 3
    assert "Derived from aaaa1111" in lines[0]
    assert "Edit 00:00-00:30" in lines[1]
    assert "first edit" in lines[1]
    assert "Edit 01:00-01:30" in lines[2]
    assert "second edit" in lines[2]


def test_suno_lineage_remix_empty_edited_clip_id() -> None:
    """suno_lineage when is_remix=True but edited_clip_id is empty."""
    clip = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        is_remix=True,
        edited_clip_id="",
    )
    assert clip.suno_lineage == ""


def test_suno_lineage_history_none() -> None:
    """suno_lineage when history is None."""
    clip = SunoClip(
        id="test",
        title="T",
        audio_url="",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        edited_clip_id="",
        history=None,
    )
    assert clip.suno_lineage == ""


def test_clip_meta_hash_changes_when_video_cover_url_changes() -> None:
    """clip_meta_hash changes when video_cover_url changes."""
    base = dict(
        id="test",
        title="T",
        audio_url="",
        image_url="img.jpg",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="",
        tags="",
        duration=0,
        clip_type="",
        has_vocal=False,
        display_name="user",
    )
    clip1 = SunoClip(**base, video_cover_url="https://cdn1.suno.ai/video_gen_a_processed_video.mp4")
    clip2 = SunoClip(**base, video_cover_url="https://cdn1.suno.ai/video_gen_b_processed_video.mp4")
    assert clip_meta_hash(clip1) != clip_meta_hash(clip2)


# ── T13: from_api_response CDN rewrite ──────────────────────────────


def test_from_api_response_cdn_rewrite() -> None:
    """from_api_response rewrites cdn2 URLs and handles None URLs."""
    raw = {
        "id": "clip-cdn-test",
        "title": "CDN Test",
        "audio_url": "https://cdn1.suno.ai/clip-cdn-test.mp3",
        "image_url": "https://cdn2.suno.ai/image_clip-cdn-test.jpeg",
        "image_large_url": "https://cdn2.suno.ai/image_large_clip-cdn-test.jpeg",
        "video_url": "https://cdn2.suno.ai/clip-cdn-test.mp4",
        "video_cover_url": None,
        "is_liked": True,
        "status": "complete",
        "created_at": "2026-03-19T10:00:00Z",
        "metadata": {
            "tags": "pop",
            "duration": 120.0,
            "type": "gen",
            "has_vocal": True,
        },
    }

    clip = SunoClip.from_api_response(raw)

    # cdn2 URLs should be rewritten to cdn1
    assert "cdn2" not in clip.image_url
    assert clip.image_url == "https://cdn1.suno.ai/image_clip-cdn-test.jpeg"
    assert "cdn2" not in clip.image_large_url
    assert clip.image_large_url == "https://cdn1.suno.ai/image_large_clip-cdn-test.jpeg"
    assert "cdn2" not in clip.video_url
    assert clip.video_url == "https://cdn1.suno.ai/clip-cdn-test.mp4"
    # None video_cover_url should become empty string
    assert clip.video_cover_url == ""
