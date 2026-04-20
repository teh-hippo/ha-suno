"""Tests for model defensive loading and lineage metadata."""

from custom_components.suno.models import (
    _safe_clip,
    _safe_clips,
    _safe_playlist,
    _safe_playlists,
    clip_meta_hash,
)


class TestSafeClip:
    """Tests for defensive clip loading."""

    def test_safe_clip_ignores_unknown_fields(self) -> None:
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

    def test_safe_clip_missing_optional_fields(self) -> None:
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

    def test_safe_clip_minimal_required_only(self) -> None:
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

    def test_safe_clips_skips_corrupt_entries(self) -> None:
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

    def test_meta_hash_includes_root_ancestor(self) -> None:
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


class TestSafePlaylist:
    """Tests for defensive playlist loading."""

    def test_safe_playlist_ignores_unknown_fields(self) -> None:
        """Extra keys in stored data are filtered out, playlist loads successfully."""
        raw = {"id": "pl-1", "name": "My Playlist", "image_url": "", "num_clips": 5, "extra": "ignored"}
        pl = _safe_playlist(raw)
        assert pl.id == "pl-1"
        assert pl.name == "My Playlist"

    def test_safe_playlists_skips_corrupt(self) -> None:
        """Batch function logs and skips corrupt entries, keeps valid ones."""
        raw_list = [
            {"id": "pl-1", "name": "Good", "image_url": "", "num_clips": 3},
            {},
            {"id": "pl-2", "name": "Also Good", "image_url": "", "num_clips": 1},
        ]
        pls = _safe_playlists(raw_list)
        assert len(pls) == 2


class TestToTrackMetadataAlbumArtist:
    """Album artist propagation (Release 2: 2.11)."""

    def _make(self, display_name: str = "AmeonAI"):
        from custom_components.suno.models import SunoClip

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

    def test_album_artist_uses_display_name(self) -> None:
        meta = self._make().to_track_metadata()
        assert meta.album_artist == "AmeonAI"

    def test_album_artist_falls_back_to_suno(self) -> None:
        meta = self._make(display_name="").to_track_metadata()
        assert meta.album_artist == "Suno"
