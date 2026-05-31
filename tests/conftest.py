"""Fixtures for Suno integration tests."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.suno.const import (
    CONF_ALL_PLAYLISTS,
    CONF_CACHE_MAX_SIZE,
    CONF_COOKIE,
    CONF_CREATE_PLAYLISTS,
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_MY_SONGS,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_MY_SONGS_COUNT,
    CONF_MY_SONGS_DAYS,
    CONF_MY_SONGS_MINIMUM,
    CONF_PLAYLISTS,
    CONF_QUALITY_LIKED,
    CONF_QUALITY_MY_SONGS,
    CONF_QUALITY_PLAYLISTS,
    CONF_SHOW_LIKED,
    CONF_SHOW_MY_SONGS,
    CONF_SHOW_PLAYLISTS,
    DEFAULT_CACHE_MAX_SIZE,
    DEFAULT_CREATE_PLAYLISTS,
    DEFAULT_DOWNLOAD_MODE,
    DEFAULT_DOWNLOAD_MODE_MY_SONGS,
    DEFAULT_MY_SONGS_COUNT,
    DEFAULT_MY_SONGS_DAYS,
    DEFAULT_MY_SONGS_MINIMUM,
    DEFAULT_SHOW_LIKED,
    DEFAULT_SHOW_MY_SONGS,
    DEFAULT_SHOW_PLAYLISTS,
    DOMAIN,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from custom_components.suno.downloaded_library.contracts import ManifestEntry
from custom_components.suno.models import SunoClip, SunoCredits, SunoPlaylist

MOCK_COOKIE = "__client=test-cookie-value; __client_uat=1234567890"
MOCK_USER_ID = "test-user-id-123"


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):  # noqa: PT004
    """Enable custom integrations for all tests."""


def make_entry(
    *,
    data: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    unique_id: str = MOCK_USER_ID,
) -> MockConfigEntry:
    """Create a MockConfigEntry with sensible defaults."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Suno",
        unique_id=unique_id,
        version=1,
        data=data or {CONF_COOKIE: MOCK_COOKIE},
        options=options
        or {
            CONF_SHOW_PLAYLISTS: DEFAULT_SHOW_PLAYLISTS,
            CONF_SHOW_LIKED: DEFAULT_SHOW_LIKED,
            CONF_SHOW_MY_SONGS: DEFAULT_SHOW_MY_SONGS,
            CONF_DOWNLOAD_PATH: "",
            CONF_CREATE_PLAYLISTS: DEFAULT_CREATE_PLAYLISTS,
            CONF_CACHE_MAX_SIZE: DEFAULT_CACHE_MAX_SIZE,
            CONF_QUALITY_LIKED: QUALITY_HIGH,
            CONF_QUALITY_PLAYLISTS: QUALITY_HIGH,
            CONF_QUALITY_MY_SONGS: QUALITY_STANDARD,
            CONF_DOWNLOAD_MODE_LIKED: DEFAULT_DOWNLOAD_MODE,
            CONF_DOWNLOAD_MODE_PLAYLISTS: DEFAULT_DOWNLOAD_MODE,
            CONF_DOWNLOAD_MODE_MY_SONGS: DEFAULT_DOWNLOAD_MODE_MY_SONGS,
            CONF_MY_SONGS_COUNT: DEFAULT_MY_SONGS_COUNT,
            CONF_MY_SONGS_DAYS: DEFAULT_MY_SONGS_DAYS,
            CONF_MY_SONGS_MINIMUM: DEFAULT_MY_SONGS_MINIMUM,
            CONF_ALL_PLAYLISTS: True,
            CONF_PLAYLISTS: [],
        },
    )


def make_mock_auth() -> AsyncMock:
    """Return a mocked ClerkAuth."""
    auth = AsyncMock()
    auth.user_id = MOCK_USER_ID
    auth.display_name = "Suno"
    auth.authenticate = AsyncMock(return_value=MOCK_USER_ID)
    auth.ensure_jwt = AsyncMock(return_value="mock-jwt-token")
    return auth


# Backward-compat alias for existing imports.
_make_mock_auth = make_mock_auth


@pytest.fixture
def mock_suno_client() -> AsyncMock:
    """Return a mocked SunoClient."""
    client = AsyncMock()
    client.user_id = MOCK_USER_ID
    client.display_name = "Suno"
    client.suno_display_name = None
    client._auth = make_mock_auth()
    client.authenticate = AsyncMock(return_value=MOCK_USER_ID)
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


@contextmanager
def patch_suno_setup(mock_client: AsyncMock, module: str = "custom_components.suno.runtime"):
    """Patch both ClerkAuth and SunoClient for setup tests."""
    mock_auth = mock_client._auth
    with (
        patch(f"{module}.ClerkAuth", return_value=mock_auth),
        patch(f"{module}.SunoClient", return_value=mock_client),
    ):
        yield mock_client


@pytest.fixture
def mock_setup_entry() -> AsyncMock:
    """Mock the setup entry."""
    with patch("custom_components.suno.async_setup_entry", return_value=True) as mock:
        yield mock


async def setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set up a config entry fully (add + setup + block)."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _await_entry_refresh(hass, entry)


async def _await_entry_refresh(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Wait for the initial Suno library background refresh."""
    runtime = getattr(entry, "runtime_data", None)
    coordinator = getattr(runtime, "coordinator", None)
    refresh_task = getattr(coordinator, "_refresh_task", None)
    if refresh_task is not None:
        await refresh_task
        await hass.async_block_till_done()
    unschedule_refresh = getattr(coordinator, "_async_unsub_refresh", None)
    if unschedule_refresh is not None:
        unschedule_refresh()


def make_clip(
    clip_id: str = "clip-test-0000-0000-0000-000000000000",
    *,
    title: str = "Song",
    created_at: str = "2026-03-15T10:00:00Z",
    audio_url: str | None = None,
    image_url: str = "",
    image_large_url: str = "",
    video_cover_url: str = "",
    is_liked: bool = True,
    status: str = "complete",
    tags: str = "pop",
    duration: float = 120.0,
    clip_type: str = "gen",
    has_vocal: bool = True,
    display_name: str = "",
    handle: str = "",
    edited_clip_id: str = "",
    is_remix: bool = False,
    root_ancestor_id: str = "",
    lineage_status: str = "",
    album_title: str = "",
    prompt: str = "",
    gpt_description_prompt: str = "",
) -> SunoClip:
    """Build a SunoClip with sensible test defaults.

    Centralises the per-test ``_clip`` / ``_make_clip`` helpers that
    drifted across ``test_downloaded_library.py`` and
    ``test_library_refresh.py``. Pass overrides via keyword.
    """
    return SunoClip(
        id=clip_id,
        title=title,
        audio_url=audio_url if audio_url is not None else f"https://cdn1.suno.ai/{clip_id}.mp3",
        image_url=image_url,
        image_large_url=image_large_url,
        video_cover_url=video_cover_url,
        is_liked=is_liked,
        status=status,
        created_at=created_at,
        tags=tags,
        duration=duration,
        clip_type=clip_type,
        has_vocal=has_vocal,
        display_name=display_name,
        handle=handle,
        edited_clip_id=edited_clip_id,
        is_remix=is_remix,
        root_ancestor_id=root_ancestor_id,
        lineage_status=lineage_status,
        album_title=album_title,
        prompt=prompt,
        gpt_description_prompt=gpt_description_prompt,
    )


def sample_clips(count: int = 2) -> list[SunoClip]:
    """Return sample clips for testing."""
    clips = [
        SunoClip(
            id="clip-aaa-111",
            title="Test Song Alpha",
            audio_url="https://cdn1.suno.ai/clip-aaa-111.mp3",
            image_url="https://cdn1.suno.ai/image_clip-aaa-111.jpeg",
            image_large_url="https://cdn1.suno.ai/image_large_clip-aaa-111.jpeg",
            is_liked=True,
            status="complete",
            created_at="2026-03-19T10:00:00Z",
            tags="pop, upbeat",
            duration=120.5,
            clip_type="gen",
            has_vocal=True,
        ),
        SunoClip(
            id="clip-bbb-222",
            title="Test Song Beta",
            audio_url="https://cdn1.suno.ai/clip-bbb-222.mp3",
            image_url="https://cdn1.suno.ai/image_clip-bbb-222.jpeg",
            image_large_url="https://cdn1.suno.ai/image_large_clip-bbb-222.jpeg",
            is_liked=False,
            status="complete",
            created_at="2026-03-18T10:00:00Z",
            tags="rock, guitar",
            duration=90.0,
            clip_type="gen",
            has_vocal=False,
        ),
    ]
    return clips[:count]


def sample_playlists() -> list[SunoPlaylist]:
    """Return sample playlists for testing."""
    return [
        SunoPlaylist(
            id="pl-001",
            name="My Favourites",
            image_url="https://cdn1.suno.ai/image_pl-001.jpeg",
            num_clips=5,
        ),
    ]


def sample_credits() -> SunoCredits:
    """Return sample credits for testing."""
    return SunoCredits(
        credits_left=1500,
        monthly_limit=2500,
        monthly_usage=1000,
        period="2026-03",
    )


def make_entry_state(**kwargs: Any) -> ManifestEntry:
    """Build a ManifestEntry for tests, accepting v6.3.6 dict-shape kwargs.

    Centralises the conversion from the dict-shape entries the test
    suite was written against to the typed ``ManifestEntry`` the Round
    2 engine takes. Tests can pass legacy keys (``path``, ``meta_hash``,
    ``embedded_art_hash``, etc.) unchanged.
    """
    return ManifestEntry.from_dict(kwargs)


def make_clips_state(**clip_dicts: dict[str, Any]) -> dict[str, ManifestEntry]:
    """Build a ``dict[clip_id, ManifestEntry]`` from dict-shape kwargs."""
    return {clip_id: ManifestEntry.from_dict(d) for clip_id, d in clip_dicts.items()}
