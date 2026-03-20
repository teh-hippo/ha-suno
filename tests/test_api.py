"""Tests for the Suno API client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.suno.api import (
    SunoClient,
    _decode_jwt_exp,
    _fix_cdn_url,
    _sanitise_clip,
)


def test_fix_cdn_url_rewrites_cdn2() -> None:
    """cdn2 URLs should be rewritten to cdn1."""
    assert _fix_cdn_url("https://cdn2.suno.ai/image_abc.jpeg") == "https://cdn1.suno.ai/image_abc.jpeg"


def test_fix_cdn_url_leaves_cdn1() -> None:
    """cdn1 URLs should be left alone."""
    assert _fix_cdn_url("https://cdn1.suno.ai/image_abc.jpeg") == "https://cdn1.suno.ai/image_abc.jpeg"


def test_fix_cdn_url_handles_none() -> None:
    """None should return empty string."""
    assert _fix_cdn_url(None) == ""


def test_sanitise_clip_allowlist() -> None:
    """Only allowlisted fields should make it through."""
    raw = {
        "id": "test-id",
        "title": "Test",
        "audio_url": "https://cdn1.suno.ai/test-id.mp3",
        "image_url": "https://cdn1.suno.ai/image_test-id.jpeg",
        "image_large_url": "https://cdn1.suno.ai/image_large_test-id.jpeg",
        "is_liked": True,
        "status": "complete",
        "created_at": "2026-01-01T00:00:00Z",
        "metadata": {"tags": "pop", "duration": 60.0, "type": "gen", "has_vocal": True},
        # PII fields that must not appear in the result
        "user_id": "secret-user-id",
        "display_name": "Secret Name",
        "handle": "secret_handle",
        "email": "secret@example.com",
    }
    clip = _sanitise_clip(raw)
    assert clip.id == "test-id"
    assert clip.title == "Test"
    assert clip.is_liked is True
    # Verify PII is not accessible on the dataclass
    assert not hasattr(clip, "user_id")
    assert not hasattr(clip, "display_name")
    assert not hasattr(clip, "handle")
    assert not hasattr(clip, "email")


def test_sanitise_clip_rewrites_audiopipe_url() -> None:
    """Audiopipe URLs should be rewritten to cdn1 direct MP3."""
    raw = {
        "id": "abc-123",
        "audio_url": "https://audiopipe.suno.ai/?item_id=abc-123",
        "metadata": {},
    }
    clip = _sanitise_clip(raw)
    assert clip.audio_url == "https://cdn1.suno.ai/abc-123.mp3"


def test_decode_jwt_exp_valid() -> None:
    """Should extract exp from a valid JWT."""
    import base64

    payload = base64.urlsafe_b64encode(json.dumps({"exp": 1700000000}).encode()).rstrip(b"=").decode()
    token = f"header.{payload}.signature"
    assert _decode_jwt_exp(token) == 1700000000


def test_decode_jwt_exp_invalid() -> None:
    """Should return 0 for invalid JWTs."""
    assert _decode_jwt_exp("not.a.jwt") == 0
    assert _decode_jwt_exp("") == 0


def test_all_audio_urls_are_https() -> None:
    """All audio and image URLs from sanitise_clip must be HTTPS."""
    raw = {
        "id": "test",
        "audio_url": "https://cdn1.suno.ai/test.mp3",
        "image_url": "https://cdn1.suno.ai/image_test.jpeg",
        "image_large_url": "https://cdn1.suno.ai/image_large_test.jpeg",
        "metadata": {},
    }
    clip = _sanitise_clip(raw)
    for url in [clip.audio_url, clip.image_url, clip.image_large_url]:
        if url:
            assert url.startswith("https://"), f"URL is not HTTPS: {url}"


@pytest.mark.asyncio
async def test_client_cookie_only_sent_to_clerk() -> None:
    """Cookie must only be sent to clerk.suno.com, never to the Suno API."""
    session = AsyncMock()
    client = SunoClient(session, "test-cookie")

    # Track what URLs get the cookie header
    cookie_urls: list[str] = []

    def capture_get(url: str, **kwargs: object) -> MagicMock:
        headers = kwargs.get("headers", {})
        if isinstance(headers, dict) and "Cookie" in headers:
            cookie_urls.append(url)
        mock_resp = AsyncMock()
        mock_resp.status = 200
        if "clerk.suno.com" in url:
            mock_resp.json = AsyncMock(
                return_value={
                    "response": {
                        "last_active_session_id": "sess-123",
                        "sessions": [{"id": "sess-123", "user": {"id": "user-123"}}],
                    }
                }
            )
        else:
            mock_resp.json = AsyncMock(return_value=[])
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        return mock_resp

    def capture_post(url: str, **kwargs: object) -> MagicMock:
        headers = kwargs.get("headers", {})
        if isinstance(headers, dict) and "Cookie" in headers:
            cookie_urls.append(url)
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"jwt": "fake.eyJleHAiOjk5OTk5OTk5OTl9.sig"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        return mock_resp

    session.get = capture_get
    session.post = capture_post

    await client.authenticate()
    await client.get_feed(0)

    # All cookie-bearing requests must be to clerk.suno.com
    for url in cookie_urls:
        assert "clerk.suno.com" in url, f"Cookie was sent to non-Clerk URL: {url}"
