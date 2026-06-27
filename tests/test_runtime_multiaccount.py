"""Multi-account runtime, lifecycle, and identity tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir

from custom_components.suno.const import CONF_DOWNLOAD_PATH, DOMAIN
from custom_components.suno.runtime import (
    _SERVICE_DOWNLOAD,
    _conflicting_entry,
    iter_entry_runtimes,
    paths_overlap,
)

from .conftest import (
    make_entry,
    make_mock_auth,
    patch_suno_setup,
    sample_clips,
    sample_credits,
    sample_playlists,
    setup_entry,
)

_RECONCILE = "custom_components.suno.downloaded_library.DownloadedLibrary.async_reconcile"


def _client(user_id: str) -> AsyncMock:
    """Build a mock SunoClient whose auth resolves to ``user_id``."""
    client = AsyncMock()
    client.user_id = user_id
    client.display_name = "Suno"
    client.suno_display_name = None
    auth = make_mock_auth()
    auth.user_id = user_id
    auth.authenticate = AsyncMock(return_value=user_id)
    client._auth = auth
    client.authenticate = AsyncMock(return_value=user_id)
    client.get_feed = AsyncMock(return_value=(sample_clips(), False))
    client.get_all_songs = AsyncMock(return_value=sample_clips())
    client.get_liked_songs = AsyncMock(return_value=sample_clips(1))
    client.get_playlists = AsyncMock(return_value=sample_playlists())
    client.get_playlist_clips = AsyncMock(return_value=sample_clips()[:1])
    client.get_credits = AsyncMock(return_value=sample_credits())
    client.get_clip_parent_raw = AsyncMock(return_value=None)
    client.get_wav_url = AsyncMock(return_value="https://cdn1.suno.ai/clip-aaa-111.wav")
    client.request_wav = AsyncMock()
    return client


def _download_entry(unique_id: str, path: str):
    options = {**make_entry().options, CONF_DOWNLOAD_PATH: path}
    return make_entry(unique_id=unique_id, options=options)


# ── paths_overlap ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("/media/suno", "/media/suno", True),
        ("/media/suno/", "/media/suno", True),
        ("/media/suno/a", "/media/suno", True),  # child under parent
        ("/media/suno", "/media/suno/a", True),  # parent over child
        ("/media/suno-a", "/media/suno-b", False),  # siblings
        ("/media/suno", "/media/other", False),
        ("", "/media/suno", False),
        ("/media/suno", "", False),
    ],
)
def test_paths_overlap(first: str, second: str, expected: bool) -> None:
    """Equal/parent/child paths overlap; siblings and empties do not."""
    assert paths_overlap(first, second) is expected


# ── Download-path conflict invariant ─────────────────────────────────


async def test_setup_blocks_overlapping_download_path(hass: HomeAssistant, tmp_path: Path) -> None:
    """A second account whose download path overlaps refuses to start."""
    shared = str(tmp_path / "shared")
    entry_a = _download_entry("user-a", shared)
    entry_b = _download_entry("user-b", shared)

    with patch(_RECONCILE, new_callable=AsyncMock):
        with patch_suno_setup(_client("user-a")):
            await setup_entry(hass, entry_a)
        assert entry_a.state is ConfigEntryState.LOADED

        with patch_suno_setup(_client("user-b")):
            await setup_entry(hass, entry_b)

    assert entry_b.state is ConfigEntryState.SETUP_ERROR
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"download_path_conflict_{entry_b.entry_id}")
    assert issue is not None


# ── iter_entry_runtimes only yields loaded entries ───────────────────


async def test_iter_entry_runtimes_skips_not_loaded(hass: HomeAssistant) -> None:
    """A configured-but-unloaded sibling is not yielded to proxy/media/service."""
    entry_a = make_entry(unique_id="user-a")
    entry_b = make_entry(unique_id="user-b")
    with patch_suno_setup(_client("user-a")):
        await setup_entry(hass, entry_a)
    entry_b.add_to_hass(hass)  # never set up -> NOT_LOADED

    loaded = list(iter_entry_runtimes(hass))
    assert [e.entry_id for e, _ in loaded] == [entry_a.entry_id]


# ── Per-account throttle, shared concurrency gate ────────────────────


async def test_rate_limiter_throttle_is_per_account(hass: HomeAssistant) -> None:
    """A 429 against one account does not throttle another, but they share the gate."""
    entry_a = make_entry(unique_id="user-a")
    entry_b = make_entry(unique_id="user-b")
    with patch_suno_setup(_client("user-a")):
        await setup_entry(hass, entry_a)
    with patch_suno_setup(_client("user-b")):
        await setup_entry(hass, entry_b)

    rt_a = entry_a.runtime_data
    rt_b = entry_b.runtime_data

    await rt_a.rate_limiter.report_rate_limit(retry_after=30.0)
    assert rt_a.rate_limiter.is_throttled is True
    assert rt_b.rate_limiter.is_throttled is False

    gate = hass.data[DOMAIN]["concurrency_gate"]
    assert isinstance(gate, asyncio.Semaphore)
    assert rt_a.rate_limiter._semaphore is gate is rt_b.rate_limiter._semaphore


# ── download_library service routing ─────────────────────────────────


async def test_service_drives_all_accounts(hass: HomeAssistant, tmp_path: Path) -> None:
    """Calling the service without a target reconciles every loaded account."""
    entry_a = _download_entry("user-a", str(tmp_path / "a"))
    entry_b = _download_entry("user-b", str(tmp_path / "b"))
    with patch(_RECONCILE, new_callable=AsyncMock):
        with patch_suno_setup(_client("user-a")):
            await setup_entry(hass, entry_a)
        with patch_suno_setup(_client("user-b")):
            await setup_entry(hass, entry_b)

        entry_a.runtime_data.async_run_download = AsyncMock()
        entry_b.runtime_data.async_run_download = AsyncMock()

        await hass.services.async_call(DOMAIN, _SERVICE_DOWNLOAD, {"force": True}, blocking=True)

    entry_a.runtime_data.async_run_download.assert_awaited_once_with(force=True)
    entry_b.runtime_data.async_run_download.assert_awaited_once_with(force=True)


async def test_service_targets_single_account(hass: HomeAssistant, tmp_path: Path) -> None:
    """A config_entry_id target reconciles only that account."""
    entry_a = _download_entry("user-a", str(tmp_path / "a"))
    entry_b = _download_entry("user-b", str(tmp_path / "b"))
    with patch(_RECONCILE, new_callable=AsyncMock):
        with patch_suno_setup(_client("user-a")):
            await setup_entry(hass, entry_a)
        with patch_suno_setup(_client("user-b")):
            await setup_entry(hass, entry_b)

        entry_a.runtime_data.async_run_download = AsyncMock()
        entry_b.runtime_data.async_run_download = AsyncMock()

        await hass.services.async_call(
            DOMAIN,
            _SERVICE_DOWNLOAD,
            {"config_entry_id": entry_a.entry_id},
            blocking=True,
        )

    entry_a.runtime_data.async_run_download.assert_awaited_once()
    entry_b.runtime_data.async_run_download.assert_not_awaited()


async def test_service_unknown_target_raises(hass: HomeAssistant, tmp_path: Path) -> None:
    """An unknown config_entry_id raises a validation error."""
    entry = _download_entry("user-a", str(tmp_path / "a"))
    with patch(_RECONCILE, new_callable=AsyncMock):
        with patch_suno_setup(_client("user-a")):
            await setup_entry(hass, entry)

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                _SERVICE_DOWNLOAD,
                {"config_entry_id": "does-not-exist"},
                blocking=True,
            )


# ── async_at_started cancellation on unload ──────────────────────────


async def test_start_listener_cancelled_on_unload(hass: HomeAssistant, tmp_path: Path) -> None:
    """The HA-start listener's cancel callback is invoked when the entry unloads."""
    entry = _download_entry("user-a", str(tmp_path / "a"))
    cancel = MagicMock()
    with (
        patch(_RECONCILE, new_callable=AsyncMock),
        patch("custom_components.suno.runtime.async_at_started", return_value=cancel) as mock_at_started,
    ):
        with patch_suno_setup(_client("user-a")):
            await setup_entry(hass, entry)
        mock_at_started.assert_called_once()
        cancel.assert_not_called()

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    cancel.assert_called_once()


# ── Identity backfill and mismatch ───────────────────────────────────


async def test_unique_id_backfilled_on_setup(hass: HomeAssistant) -> None:
    """A missing unique_id is backfilled from the authenticated Suno user_id."""
    entry = make_entry(unique_id=None)
    client = _client("recovered-user")
    with (
        patch("custom_components.suno.ClerkAuth", return_value=client._auth),
        patch_suno_setup(client),
    ):
        await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.unique_id == "recovered-user"


async def test_wrong_account_flags_issue_without_failing(hass: HomeAssistant) -> None:
    """A cookie for a different account loads but raises a repair issue."""
    entry = make_entry(unique_id="user-a")
    with patch_suno_setup(_client("user-b")):
        await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"wrong_account_{entry.entry_id}")
    assert issue is not None


async def test_download_path_conflict_tiebreaker(hass: HomeAssistant) -> None:
    """Of two entries with overlapping paths, only the later entry_id conflicts.

    The deterministic tiebreaker keeps exactly one of an overlapping pair
    loadable instead of taking both accounts down on a concurrent restart.
    """
    entry_a = _download_entry("user-a", "/media/suno")
    entry_b = _download_entry("user-b", "/media/suno/nested")
    entry_a.add_to_hass(hass)
    entry_b.add_to_hass(hass)

    first, second = sorted((entry_a, entry_b), key=lambda entry: entry.entry_id)
    # The earliest entry_id wins (no conflict) and is allowed to load.
    assert _conflicting_entry(hass, first) is None
    # The later entry_id defers to the earlier one and refuses to load.
    assert _conflicting_entry(hass, second) is first
