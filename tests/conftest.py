"""Fixtures for Suno integration tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.suno.api import SunoClip, SunoCredits, SunoPlaylist
from custom_components.suno.const import (
    CONF_CACHE_TTL,
    CONF_COOKIE,
    CONF_RECENT_COUNT,
    CONF_SHOW_LIKED,
    CONF_SHOW_PLAYLISTS,
    CONF_SHOW_RECENT,
    DEFAULT_CACHE_TTL,
    DEFAULT_RECENT_COUNT,
    DEFAULT_SHOW_LIKED,
    DEFAULT_SHOW_PLAYLISTS,
    DEFAULT_SHOW_RECENT,
    DOMAIN,
)

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
        data=data or {CONF_COOKIE: MOCK_COOKIE},
        options=options
        or {
            CONF_SHOW_LIKED: DEFAULT_SHOW_LIKED,
            CONF_SHOW_RECENT: DEFAULT_SHOW_RECENT,
            CONF_RECENT_COUNT: DEFAULT_RECENT_COUNT,
            CONF_SHOW_PLAYLISTS: DEFAULT_SHOW_PLAYLISTS,
            CONF_CACHE_TTL: DEFAULT_CACHE_TTL,
        },
    )


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    """Return a MockConfigEntry registered with HA."""
    return make_entry()


@pytest.fixture
def mock_suno_client() -> AsyncMock:
    """Return a mocked SunoClient."""
    client = AsyncMock()
    client.user_id = MOCK_USER_ID
    client.handle = "test-handle"
    client.authenticate = AsyncMock(return_value=MOCK_USER_ID)
    client.get_feed = AsyncMock(return_value=(sample_clips(), False))
    client.get_all_songs = AsyncMock(return_value=sample_clips())
    client.get_liked_songs = AsyncMock(return_value=sample_liked_clips())
    client.get_playlists = AsyncMock(return_value=sample_playlists())
    client.get_playlist_clips = AsyncMock(return_value=sample_clips()[:1])
    client.get_credits = AsyncMock(return_value=sample_credits())
    return client


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


def sample_liked_clips() -> list[SunoClip]:
    """Return sample liked clips for testing."""
    return [
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
    ]


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
