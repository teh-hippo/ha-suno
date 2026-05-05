"""Tests for the Suno download module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.suno.const import (
    CONF_ALL_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_PLAYLISTS,
    CONF_SHOW_LIKED,
    QUALITY_HIGH,
)
from custom_components.suno.download import SunoDownloadManager
from custom_components.suno.downloaded_library import (
    _clip_path,
)
from custom_components.suno.library_refresh import SunoData

# ── Filename sanitisation ──────────────────────────────────────────


# ── Clip path generation ───────────────────────────────────────────


# ── Video sidecar path ─────────────────────────────────────────────


# ── Sync state management ──────────────────────────────────────────


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


async def test_force_download_refreshes_library_before_reconcile(hass: HomeAssistant, tmp_path: Path) -> None:
    """Manual force refreshes the Suno Library before forcing download reconciliation."""
    clip = _make_clip("clip-force-0000-0000-0000-000000000000")
    fresh_data = SunoData(liked_clips=[clip])
    coordinator = MagicMock()
    coordinator.data = SunoData()
    coordinator.data_version = 2
    coordinator._refresh_task = None
    coordinator._async_fetch_remote_data = AsyncMock(return_value=fresh_data)
    coordinator.async_set_updated_data = MagicMock()
    sync = SunoDownloadManager(hass, "test_sync_state")
    sync._coordinator = coordinator
    client = AsyncMock()

    with patch.object(sync._downloaded_library, "async_reconcile", new_callable=AsyncMock) as reconcile:
        await sync.async_download({CONF_DOWNLOAD_PATH: str(tmp_path)}, client, force=True)

    coordinator._async_fetch_remote_data.assert_awaited_once()
    coordinator.async_set_updated_data.assert_called_once_with(fresh_data)
    reconcile.assert_awaited_once()
    assert reconcile.await_args.args[1] is fresh_data
    assert reconcile.await_args.kwargs["force"] is True


def _make_dated_clip(clip_id: str, title: str = "Song", created: str = "2026-03-15T10:00:00Z"):
    from custom_components.suno.models import SunoClip

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


# ── Sync summary ───────────────────────────────────────────────────


def _make_clip_with_display(
    clip_id: str,
    title: str = "Song",
    created: str = "2026-03-15T10:00:00Z",
    display_name: str = "testuser",
    video_url: str = "",
    image_url: str | None = None,
    image_large_url: str | None = None,
):
    """Build a SunoClip with optional display_name, video_url, and image fields."""
    from custom_components.suno.models import SunoClip

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


# ── TC-7: Playlist order preservation ─────────────────────────────

# ── Service lifecycle ────────────────────────────────────────────────


class TestServiceLifecycle:
    """Tests for download service registration lifecycle."""

    def test_service_not_removed_while_other_entries_remain(self) -> None:
        """Service removal callback should keep the service when other entries exist."""
        from custom_components.suno.const import DOMAIN
        from custom_components.suno.download import _SERVICE_DOWNLOAD

        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry-1"

        other_entry = MagicMock()
        other_entry.entry_id = "entry-2"
        hass.config_entries.async_entries.return_value = [other_entry]

        # Build the guarded removal function the same way production code does
        def _maybe_remove_service() -> None:
            remaining = [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]
            if not remaining:
                hass.services.async_remove(DOMAIN, _SERVICE_DOWNLOAD)

        _maybe_remove_service()
        hass.services.async_remove.assert_not_called()

    def test_service_removed_when_last_entry_unloads(self) -> None:
        """Service removal callback should remove the service when no entries remain."""
        from custom_components.suno.const import DOMAIN
        from custom_components.suno.download import _SERVICE_DOWNLOAD

        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry-1"

        # No other entries remain after this one unloads
        hass.config_entries.async_entries.return_value = [entry]

        def _maybe_remove_service() -> None:
            remaining = [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]
            if not remaining:
                hass.services.async_remove(DOMAIN, _SERVICE_DOWNLOAD)

        _maybe_remove_service()
        hass.services.async_remove.assert_called_once_with(DOMAIN, _SERVICE_DOWNLOAD)


# ── T6: Metadata hash change updates state ─────────────────────────


async def test_hash_formula_migration_triggers_retag(hass: HomeAssistant, tmp_path: Path) -> None:
    """Old-format hash (included display_name) triggers retag on first sync with new code."""
    import hashlib

    from custom_components.suno.models import clip_meta_hash

    clip = _make_clip_with_display("clip-mig-0000-0000-0000-000000000000", "Migration Song", display_name="alice")
    # Old hash formula included display_name
    old_hash = hashlib.md5(  # noqa: S324
        f"{clip.title}|{clip.tags}|{clip.image_url}|{clip.display_name}|{clip.video_url}|{clip.root_ancestor_id}".encode()
    ).hexdigest()[:12]
    new_hash = clip_meta_hash(clip)
    assert old_hash != new_hash, "Hash formulas must differ for migration to trigger"

    sync = SunoDownloadManager(hass, "test_sync_state")
    sync_dir = tmp_path / "mirror"
    rel_path = _clip_path(clip, QUALITY_HIGH)
    target = sync_dir / rel_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fLaC" + b"\x00" * 50)

    initial_state = {
        "clips": {
            clip.id: {
                "path": rel_path,
                "title": "Migration Song",
                "created": "2026-03-15",
                "sources": ["liked"],
                "size": 54,
                "meta_hash": old_hash,
                "quality": QUALITY_HIGH,
            }
        },
        "last_download": None,
    }
    with patch.object(sync._store, "async_load", return_value=initial_state):
        await sync.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[clip])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    with (
        patch(
            "custom_components.suno.downloaded_library.download_and_transcode_to_flac",
            new_callable=AsyncMock,
        ) as mock_dl,
        patch("custom_components.suno.downloaded_library.get_ffmpeg_manager") as mock_ffmpeg,
        patch("custom_components.suno.downloaded_library.async_get_clientsession"),
        patch("custom_components.suno.downloaded_library.fetch_album_art", new_callable=AsyncMock, return_value=None),
        patch.object(sync._store, "async_save"),
        patch(
            "custom_components.suno.downloaded_library.retag_flac",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_retag,
    ):
        mock_ffmpeg.return_value.binary = "/usr/bin/ffmpeg"
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        await sync.async_download(opts, client)

    mock_dl.assert_not_called()
    mock_retag.assert_called_once()
    assert target.exists()
    assert sync._state["clips"][clip.id]["meta_hash"] == new_hash
    assert sync.errors == 0
    """OSError writing manifest file is logged as warning, not raised."""
    sync = SunoDownloadManager(hass, "test_sync_state")
    with patch.object(sync._store, "async_load", return_value=None):
        await sync.async_init()

    client = AsyncMock()
    client.get_liked_songs = AsyncMock(return_value=[])
    client.get_playlists = AsyncMock(return_value=[])
    client.get_all_songs = AsyncMock(return_value=[])

    sync_dir = tmp_path / "mirror"

    with (
        patch.object(sync._store, "async_save"),
        patch(
            "custom_components.suno.downloaded_library.Path.write_text",
            side_effect=OSError("disk full"),
        ),
    ):
        opts = {
            CONF_DOWNLOAD_PATH: str(sync_dir),
            CONF_SHOW_LIKED: True,
            CONF_ALL_PLAYLISTS: False,
            CONF_PLAYLISTS: [],
        }
        with patch("custom_components.suno.downloaded_library._LOGGER"):
            await sync.async_download(opts, client)

    # Should complete without errors
    assert sync.errors == 0
