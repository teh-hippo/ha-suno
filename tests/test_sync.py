"""Tests for the Suno sync module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.suno.const import (
    CONF_SYNC_ALL_PLAYLISTS,
    CONF_SYNC_ENABLED,
    CONF_SYNC_LIKED,
    CONF_SYNC_ORGANISE,
    CONF_SYNC_PATH,
    CONF_SYNC_PLAYLISTS,
)
from custom_components.suno.sync import SunoSync, _clip_path, _sanitise_filename

# ── Filename sanitisation ───────────────────────────────────────────


class TestSanitiseFilename:
    def test_removes_unsafe_chars(self) -> None:
        assert _sanitise_filename('test<>:"/\\|?*file') == "test_________file"

    def test_strips_dots_and_spaces(self) -> None:
        assert _sanitise_filename("  ..hello.. ") == "hello"

    def test_truncates_long_names(self) -> None:
        result = _sanitise_filename("a" * 300)
        assert len(result) == 200

    def test_empty_string_returns_untitled(self) -> None:
        assert _sanitise_filename("") == "untitled"

    def test_unicode_preserved(self) -> None:
        assert _sanitise_filename("日本語タイトル") == "日本語タイトル"


# ── Clip path generation ───────────────────────────────────────────


class TestClipPath:
    def _make_clip(self, title: str = "My Song", created: str = "2026-03-15T10:00:00Z"):
        clip = MagicMock()
        clip.title = title
        clip.created_at = created
        return clip

    def test_date_organisation(self) -> None:
        clip = self._make_clip()
        result = _clip_path(clip, 0, "date", "Liked Songs")
        assert result == "2026-03-15/01 - My Song.flac"

    def test_playlist_organisation(self) -> None:
        clip = self._make_clip()
        result = _clip_path(clip, 2, "playlist", "Zac & Xavi")
        assert result == "Zac & Xavi/03 - My Song.flac"

    def test_flat_organisation(self) -> None:
        clip = self._make_clip()
        result = _clip_path(clip, 4, "flat", "anything")
        assert result == "05 - My Song.flac"

    def test_missing_created_date(self) -> None:
        clip = self._make_clip(created=None)
        result = _clip_path(clip, 0, "date", "Liked Songs")
        assert result == "unknown/01 - My Song.flac"


# ── Sync state management ──────────────────────────────────────────


async def test_sync_init_loads_state(hass: HomeAssistant) -> None:
    """async_init should load persisted state."""
    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value={"clips": {"abc": {}}, "last_sync": "2026-01-01"}):
        await sync.async_init()
    assert sync.total_files == 1
    assert sync.last_sync == "2026-01-01"


async def test_sync_init_handles_empty_store(hass: HomeAssistant) -> None:
    """async_init with empty store should use defaults."""
    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()
    assert sync.total_files == 0
    assert sync.last_sync is None


async def test_sync_skips_when_disabled(hass: HomeAssistant) -> None:
    """Sync should do nothing when disabled."""
    sync = SunoSync(hass, "test_sync_state")
    client = AsyncMock()
    await sync.async_sync({CONF_SYNC_ENABLED: False}, client)
    client.get_liked_songs.assert_not_called()


async def test_sync_skips_when_no_path(hass: HomeAssistant) -> None:
    """Sync should skip when path is empty."""
    sync = SunoSync(hass, "test_sync_state")
    client = AsyncMock()
    await sync.async_sync({CONF_SYNC_ENABLED: True, CONF_SYNC_PATH: ""}, client)
    client.get_liked_songs.assert_not_called()


async def test_sync_skips_when_already_running(hass: HomeAssistant) -> None:
    """Sync should not run concurrently."""
    sync = SunoSync(hass, "test_sync_state")
    sync._running = True
    client = AsyncMock()
    await sync.async_sync({CONF_SYNC_ENABLED: True, CONF_SYNC_PATH: "/safe/path"}, client)  # noqa: S108
    client.get_liked_songs.assert_not_called()


# ── Download and cleanup ───────────────────────────────────────────


def _make_clip(clip_id: str, title: str = "Song", created: str = "2026-03-15T10:00:00Z"):
    from custom_components.suno.models import SunoClip

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


async def test_sync_downloads_new_clips(hass: HomeAssistant, tmp_path: Path) -> None:
    """Sync should download clips not in state."""
    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[_make_clip("clip-1", "Test Song")])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])
    client.get_wav_url = AsyncMock(return_value="https://cdn1.suno.ai/clip-1.wav")
    client.request_wav = AsyncMock()

    fake_wav = b"RIFF" + b"\x00" * 100
    fake_flac = b"fLaC" + b"\x00" * 50

    with (
        patch.object(sync, "_wav_to_flac", return_value=fake_flac),
        patch("custom_components.suno.sync.async_get_clientsession") as mock_session,
        patch.object(sync._store, "async_save"),
    ):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=fake_wav)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value.get = MagicMock(return_value=mock_resp)

        opts = {
            CONF_SYNC_ENABLED: True,
            CONF_SYNC_PATH: str(tmp_path / "sync"),
            CONF_SYNC_LIKED: True,
            CONF_SYNC_ALL_PLAYLISTS: False,
            CONF_SYNC_PLAYLISTS: [],
            CONF_SYNC_ORGANISE: "flat",
        }
        await sync.async_sync(opts, client)

    assert sync.total_files == 1
    assert sync.errors == 0


async def test_sync_deletes_orphaned_clips(hass: HomeAssistant, tmp_path: Path) -> None:
    """Sync should delete files that are no longer in desired set."""
    sync_dir = tmp_path / "sync"
    sync_dir.mkdir()
    orphan = sync_dir / "old-file.flac"
    orphan.write_bytes(b"fLaC" + b"\x00" * 50)

    sync = SunoSync(hass, "test_sync_state")
    initial_state = {
        "clips": {
            "orphan-id": {
                "path": "old-file.flac",
                "title": "Old Song",
                "created": "2026-01-01",
                "sources": ["liked"],
            }
        },
        "last_sync": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    assert sync.total_files == 1

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    with patch.object(sync._store, "async_save"):
        opts = {
            CONF_SYNC_ENABLED: True,
            CONF_SYNC_PATH: str(sync_dir),
            CONF_SYNC_LIKED: True,
            CONF_SYNC_ALL_PLAYLISTS: False,
            CONF_SYNC_PLAYLISTS: [],
            CONF_SYNC_ORGANISE: "flat",
        }
        await sync.async_sync(opts, client)

    assert sync.total_files == 0
    assert not orphan.exists()


async def test_sync_writes_manifest(hass: HomeAssistant, tmp_path: Path) -> None:
    """Sync should write .suno_sync.json manifest."""
    sync = SunoSync(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    sync_dir = tmp_path / "sync"
    with patch.object(sync._store, "async_save"):
        await sync.async_sync(
            {
                CONF_SYNC_ENABLED: True,
                CONF_SYNC_PATH: str(sync_dir),
                CONF_SYNC_LIKED: True,
                CONF_SYNC_ALL_PLAYLISTS: False,
                CONF_SYNC_PLAYLISTS: [],
                CONF_SYNC_ORGANISE: "flat",
            },
            client,
        )

    manifest = sync_dir / ".suno_sync.json"
    assert manifest.exists()
    data = json.loads(manifest.read_text())
    assert "last_sync" in data
    assert "clips" in data


async def test_cleanup_tmp_files(hass: HomeAssistant, tmp_path: Path) -> None:
    """cleanup_tmp_files should remove .tmp files."""
    sync_dir = tmp_path / "sync"
    sync_dir.mkdir()
    (sync_dir / "song.flac.tmp").write_bytes(b"partial")
    (sync_dir / "real.flac").write_bytes(b"fLaC")

    sync = SunoSync(hass, "test_sync_state")
    await sync.cleanup_tmp_files(str(sync_dir))

    assert not (sync_dir / "song.flac.tmp").exists()
    assert (sync_dir / "real.flac").exists()


# ── Properties ──────────────────────────────────────────────────────


async def test_sync_properties(hass: HomeAssistant) -> None:
    """Properties should reflect current state."""
    sync = SunoSync(hass, "test_sync_state")
    assert sync.is_running is False
    assert sync.total_files == 0
    assert sync.pending == 0
    assert sync.errors == 0
    assert sync.last_sync is None
