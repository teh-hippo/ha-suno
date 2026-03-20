"""Fixtures for Suno integration tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.suno.api import SunoClip, SunoCredits, SunoPlaylist


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):  # noqa: PT004
    """Enable custom integrations for all tests."""


@pytest.fixture
def mock_suno_client() -> AsyncMock:
    """Return a mocked SunoClient."""
    client = AsyncMock()
    client.user_id = "test-user-id-123"
    client.authenticate = AsyncMock(return_value="test-user-id-123")
    client.get_feed = AsyncMock(return_value=_sample_clips())
    client.get_all_songs = AsyncMock(return_value=_sample_clips())
    client.get_playlists = AsyncMock(return_value=_sample_playlists())
    client.get_playlist_clips = AsyncMock(return_value=_sample_clips()[:1])
    client.get_credits = AsyncMock(return_value=_sample_credits())
    return client


@pytest.fixture
def mock_setup_entry() -> AsyncMock:
    """Mock the setup entry."""
    with patch("custom_components.suno.async_setup_entry", return_value=True) as mock:
        yield mock


def _sample_clips() -> list[SunoClip]:
    """Return sample clips for testing."""
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


def _sample_playlists() -> list[SunoPlaylist]:
    """Return sample playlists for testing."""
    return [
        SunoPlaylist(
            id="pl-001",
            name="My Favourites",
            image_url="https://cdn1.suno.ai/image_pl-001.jpeg",
            num_clips=5,
        ),
    ]


def _sample_credits() -> SunoCredits:
    """Return sample credits for testing."""
    return SunoCredits(
        credits_left=1500,
        monthly_limit=2500,
        monthly_usage=1000,
        period="2026-03",
    )
