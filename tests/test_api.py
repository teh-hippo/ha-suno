"""Tests for the Suno API client."""

from __future__ import annotations

import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.suno.api import (
    SunoClient,
    _decode_jwt_exp,
    _fix_cdn_url,
    _normalise_token,
    _redact_headers,
    _sanitise_clip,
)
from custom_components.suno.exceptions import SunoApiError, SunoAuthError

# ── Helper utilities ─────────────────────────────────────────────────


def _make_jwt(exp: int = 9999999999) -> str:
    """Create a fake JWT with the given exp claim."""
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"header.{payload}.signature"


def _mock_response(status: int = 200, json_data=None, text: str = ""):
    """Create a mock aiohttp response as an async context manager."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    resp.text = AsyncMock(return_value=text)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_authed_client(session: AsyncMock) -> SunoClient:
    """Create a SunoClient with a pre-populated JWT (skips auth calls)."""
    client = SunoClient(session, "test-cookie")
    client._jwt = _make_jwt(int(time.time()) + 3600)
    client._jwt_exp = int(time.time()) + 3600
    client._session_id = "sess-123"
    client._user_id = "user-123"
    return client


# ── Pure function tests ──────────────────────────────────────────────


def test_fix_cdn_url_rewrites_cdn2() -> None:
    """cdn2 URLs should be rewritten to cdn1."""
    assert _fix_cdn_url("https://cdn2.suno.ai/image_abc.jpeg") == "https://cdn1.suno.ai/image_abc.jpeg"


def test_fix_cdn_url_leaves_cdn1() -> None:
    """cdn1 URLs should be left alone."""
    assert _fix_cdn_url("https://cdn1.suno.ai/image_abc.jpeg") == "https://cdn1.suno.ai/image_abc.jpeg"


def test_fix_cdn_url_handles_none() -> None:
    """None should return empty string."""
    assert _fix_cdn_url(None) == ""


def test_fix_cdn_url_handles_empty() -> None:
    """Empty string should return empty string."""
    assert _fix_cdn_url("") == ""


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
        "user_id": "secret-user-id",
        "display_name": "Secret Name",
    }
    clip = _sanitise_clip(raw)
    assert clip.id == "test-id"
    assert clip.title == "Test"
    assert clip.is_liked is True
    assert clip.tags == "pop"
    assert clip.duration == 60.0
    assert not hasattr(clip, "user_id")


def test_sanitise_clip_missing_fields() -> None:
    """Missing fields get safe defaults."""
    raw: dict = {"metadata": {}}
    clip = _sanitise_clip(raw)
    assert clip.id == ""
    assert clip.title == "Untitled"
    assert clip.audio_url == ""
    assert clip.is_liked is False
    assert clip.status == "unknown"
    assert clip.duration == 0.0
    assert clip.has_vocal is False


def test_sanitise_clip_none_metadata() -> None:
    """None metadata is handled."""
    raw: dict = {"id": "x", "metadata": None}
    clip = _sanitise_clip(raw)
    assert clip.tags == ""
    assert clip.duration == 0.0


def test_sanitise_clip_rewrites_audiopipe_url() -> None:
    """Audiopipe URLs should be rewritten to cdn1 direct MP3."""
    raw = {
        "id": "abc-123",
        "audio_url": "https://audiopipe.suno.ai/?item_id=abc-123",
        "metadata": {},
    }
    clip = _sanitise_clip(raw)
    assert clip.audio_url == "https://cdn1.suno.ai/abc-123.mp3"


def test_sanitise_clip_keeps_non_audiopipe() -> None:
    """Non-audiopipe URLs are kept as-is."""
    raw = {
        "id": "abc-123",
        "audio_url": "https://cdn1.suno.ai/abc-123.mp3",
        "metadata": {},
    }
    clip = _sanitise_clip(raw)
    assert clip.audio_url == "https://cdn1.suno.ai/abc-123.mp3"


def test_decode_jwt_exp_valid() -> None:
    """Should extract exp from a valid JWT."""
    token = _make_jwt(1700000000)
    assert _decode_jwt_exp(token) == 1700000000


def test_decode_jwt_exp_invalid() -> None:
    """Should return 0 for invalid JWTs."""
    assert _decode_jwt_exp("not.a.jwt") == 0
    assert _decode_jwt_exp("") == 0


def test_decode_jwt_exp_no_exp_claim() -> None:
    """JWT without exp claim returns 0."""
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "user"}).encode()).rstrip(b"=").decode()
    token = f"header.{payload}.sig"
    assert _decode_jwt_exp(token) == 0


def test_redact_headers() -> None:
    """Sensitive headers are redacted."""
    headers = {
        "Authorization": "Bearer secret-jwt",
        "Cookie": "secret-cookie",
        "Content-Type": "application/json",
    }
    redacted = _redact_headers(headers)
    assert redacted["Authorization"] == "***REDACTED***"
    assert redacted["Cookie"] == "***REDACTED***"
    assert redacted["Content-Type"] == "application/json"


def test_redact_headers_no_sensitive() -> None:
    """Headers without sensitive keys are unchanged."""
    headers = {"Content-Type": "text/html"}
    assert _redact_headers(headers) == {"Content-Type": "text/html"}


# ── Token normalisation tests ────────────────────────────────────────


def test_normalise_raw_jwt() -> None:
    """Raw JWT value should be wrapped as __client cookie."""
    result = _normalise_token("eyJhbGciOiJSUzI1NiJ9.payload.sig")
    assert result == "__client=eyJhbGciOiJSUzI1NiJ9.payload.sig"


def test_normalise_with_prefix() -> None:
    """Already-prefixed __client= should be returned as-is."""
    result = _normalise_token("__client=eyJhbGciOiJSUzI1NiJ9.payload.sig")
    assert result == "__client=eyJhbGciOiJSUzI1NiJ9.payload.sig"


def test_normalise_full_cookie_string() -> None:
    """Full cookie header should extract just the __client part."""
    full = "_ga=123; __client=eyJhbGciOiJSUzI1NiJ9.payload.sig; _sp=456"
    result = _normalise_token(full)
    assert result == "__client=eyJhbGciOiJSUzI1NiJ9.payload.sig"


def test_normalise_strips_whitespace() -> None:
    """Leading/trailing whitespace should be stripped."""
    result = _normalise_token("  eyJhbGciOiJSUzI1NiJ9.payload.sig  ")
    assert result == "__client=eyJhbGciOiJSUzI1NiJ9.payload.sig"


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


# ── SunoClient property tests ───────────────────────────────────────


def test_client_user_id_initially_none() -> None:
    """user_id is None before authentication."""
    session = AsyncMock()
    client = SunoClient(session, "cookie")
    assert client.user_id is None


# ── SunoClient.authenticate ─────────────────────────────────────────


async def test_authenticate_success() -> None:
    """Authenticate sets user_id and returns it."""
    session = AsyncMock()
    clerk_resp = _mock_response(
        200,
        {
            "response": {
                "last_active_session_id": "sess-123",
                "sessions": [{"id": "sess-123", "user": {"id": "user-456"}}],
            }
        },
    )
    jwt_resp = _mock_response(200, {"jwt": _make_jwt()})

    session.get = MagicMock(return_value=clerk_resp)
    session.post = MagicMock(return_value=jwt_resp)

    client = SunoClient(session, "test-cookie")
    user_id = await client.authenticate()

    assert user_id == "user-456"
    assert client.user_id == "user-456"


async def test_authenticate_no_user_id_raises() -> None:
    """Authenticate raises if user_id can't be determined."""
    session = AsyncMock()
    clerk_resp = _mock_response(
        200,
        {
            "response": {
                "last_active_session_id": "sess-123",
                "sessions": [{"id": "sess-other", "user": {"id": "user-456"}}],
            }
        },
    )
    jwt_resp = _mock_response(200, {"jwt": _make_jwt()})

    session.get = MagicMock(return_value=clerk_resp)
    session.post = MagicMock(return_value=jwt_resp)

    client = SunoClient(session, "test-cookie")
    with pytest.raises(SunoAuthError, match="Could not determine user ID"):
        await client.authenticate()


# ── SunoClient._get_session_id ───────────────────────────────────────


async def test_get_session_id_clerk_http_error() -> None:
    """Non-200 from Clerk raises SunoAuthError."""
    session = AsyncMock()
    session.get = MagicMock(return_value=_mock_response(403))

    client = SunoClient(session, "cookie")
    with pytest.raises(SunoAuthError, match="status 403"):
        await client._get_session_id()


async def test_get_session_id_no_response() -> None:
    """Missing 'response' key raises SunoAuthError."""
    session = AsyncMock()
    session.get = MagicMock(return_value=_mock_response(200, {"response": None}))

    client = SunoClient(session, "cookie")
    with pytest.raises(SunoAuthError, match="Invalid Clerk response"):
        await client._get_session_id()


async def test_get_session_id_no_active_session() -> None:
    """No active session raises SunoAuthError."""
    session = AsyncMock()
    session.get = MagicMock(
        return_value=_mock_response(200, {"response": {"last_active_session_id": None, "sessions": []}})
    )

    client = SunoClient(session, "cookie")
    with pytest.raises(SunoAuthError, match="No active session"):
        await client._get_session_id()


async def test_get_session_id_connection_error() -> None:
    """Connection error during Clerk session raises SunoAuthError."""
    session = AsyncMock()

    error_resp = AsyncMock()
    error_resp.__aenter__ = AsyncMock(side_effect=ConnectionError("DNS fail"))
    error_resp.__aexit__ = AsyncMock(return_value=False)
    session.get = MagicMock(return_value=error_resp)

    client = SunoClient(session, "cookie")
    with pytest.raises(SunoAuthError, match="Could not connect to Clerk"):
        await client._get_session_id()


# ── SunoClient._refresh_jwt ─────────────────────────────────────────


async def test_refresh_jwt_success() -> None:
    """JWT refresh sets _jwt and _jwt_exp."""
    session = AsyncMock()
    jwt_token = _make_jwt(2000000000)
    session.post = MagicMock(return_value=_mock_response(200, {"jwt": jwt_token}))

    client = SunoClient(session, "cookie")
    client._session_id = "sess-123"

    await client._refresh_jwt()
    assert client._jwt == jwt_token
    assert client._jwt_exp == 2000000000


async def test_refresh_jwt_http_error() -> None:
    """Non-200 from JWT refresh raises SunoAuthError."""
    session = AsyncMock()
    session.post = MagicMock(return_value=_mock_response(401))

    client = SunoClient(session, "cookie")
    client._session_id = "sess-123"

    with pytest.raises(SunoAuthError, match="JWT refresh failed"):
        await client._refresh_jwt()


async def test_refresh_jwt_no_jwt_in_response() -> None:
    """Missing JWT in response raises SunoAuthError."""
    session = AsyncMock()
    session.post = MagicMock(return_value=_mock_response(200, {"jwt": None}))

    client = SunoClient(session, "cookie")
    client._session_id = "sess-123"

    with pytest.raises(SunoAuthError, match="No JWT"):
        await client._refresh_jwt()


async def test_refresh_jwt_connection_error() -> None:
    """Connection error during JWT refresh raises SunoAuthError."""
    session = AsyncMock()

    error_resp = AsyncMock()
    error_resp.__aenter__ = AsyncMock(side_effect=ConnectionError("Timeout"))
    error_resp.__aexit__ = AsyncMock(return_value=False)
    session.post = MagicMock(return_value=error_resp)

    client = SunoClient(session, "cookie")
    client._session_id = "sess-123"

    with pytest.raises(SunoAuthError, match="Could not refresh JWT"):
        await client._refresh_jwt()


async def test_refresh_jwt_fetches_session_if_missing() -> None:
    """_refresh_jwt calls _get_session_id if session_id is None."""
    session = AsyncMock()
    clerk_resp = _mock_response(
        200,
        {
            "response": {
                "last_active_session_id": "sess-auto",
                "sessions": [{"id": "sess-auto", "user": {"id": "u1"}}],
            }
        },
    )
    jwt_resp = _mock_response(200, {"jwt": _make_jwt()})

    session.get = MagicMock(return_value=clerk_resp)
    session.post = MagicMock(return_value=jwt_resp)

    client = SunoClient(session, "cookie")
    assert client._session_id is None

    await client._refresh_jwt()
    assert client._session_id == "sess-auto"
    assert client._jwt is not None


# ── SunoClient._ensure_jwt ──────────────────────────────────────────


async def test_ensure_jwt_returns_valid_jwt() -> None:
    """_ensure_jwt returns existing JWT when still valid."""
    session = AsyncMock()
    client = _make_authed_client(session)

    jwt = await client._ensure_jwt()
    assert jwt == client._jwt


async def test_ensure_jwt_refreshes_expired() -> None:
    """_ensure_jwt refreshes when JWT is expired."""
    session = AsyncMock()
    client = _make_authed_client(session)
    client._jwt_exp = int(time.time()) - 10  # expired

    new_jwt = _make_jwt(int(time.time()) + 7200)
    session.post = MagicMock(return_value=_mock_response(200, {"jwt": new_jwt}))

    jwt = await client._ensure_jwt()
    assert jwt == new_jwt


# ── SunoClient.get_feed ─────────────────────────────────────────────


async def test_get_feed_success() -> None:
    """get_feed returns sanitised clips for complete songs."""
    session = AsyncMock()
    client = _make_authed_client(session)

    raw_clips = [
        {"id": "c1", "status": "complete", "audio_url": "https://cdn1.suno.ai/c1.mp3", "metadata": {}},
        {"id": "c2", "status": "processing", "audio_url": "", "metadata": {}},
        {"id": "c3", "status": "complete", "audio_url": "https://cdn1.suno.ai/c3.mp3", "metadata": {}},
    ]
    session.get = MagicMock(return_value=_mock_response(200, raw_clips))

    clips = await client.get_feed(0)
    # Only 'complete' clips are returned
    assert len(clips) == 2
    assert clips[0].id == "c1"
    assert clips[1].id == "c3"


async def test_get_feed_non_list_response() -> None:
    """get_feed returns empty list for non-list response."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(200, {"error": "unexpected"}))

    clips = await client.get_feed(0)
    assert clips == []


# ── SunoClient.get_all_songs ────────────────────────────────────────


async def test_get_all_songs_pagination() -> None:
    """get_all_songs paginates through pages until short page."""
    session = AsyncMock()
    client = _make_authed_client(session)

    # Page 0: 20 clips (full page), page 1: 5 clips (short → stop)
    page0 = [
        {"id": f"p0-{i}", "status": "complete", "audio_url": f"https://cdn1.suno.ai/p0-{i}.mp3", "metadata": {}}
        for i in range(20)
    ]
    page1 = [
        {"id": f"p1-{i}", "status": "complete", "audio_url": f"https://cdn1.suno.ai/p1-{i}.mp3", "metadata": {}}
        for i in range(5)
    ]

    call_count = 0

    def mock_get(url, **kwargs):
        nonlocal call_count
        data = page0 if call_count == 0 else page1
        call_count += 1
        return _mock_response(200, data)

    session.get = mock_get

    clips = await client.get_all_songs()
    assert len(clips) == 25
    assert call_count == 2


async def test_get_all_songs_empty_first_page() -> None:
    """get_all_songs with empty first page returns empty list."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(200, []))

    clips = await client.get_all_songs()
    assert clips == []


# ── SunoClient.get_playlists ────────────────────────────────────────


async def test_get_playlists_success() -> None:
    """get_playlists returns parsed playlist objects."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(
        return_value=_mock_response(
            200,
            [
                {"id": "pl-1", "name": "Chill", "image_url": "https://cdn1.suno.ai/img.jpg", "num_clips": 10},
                {"id": "pl-2", "name": "Workout", "image_url": "https://cdn2.suno.ai/img.jpg", "num_clips": 5},
            ],
        )
    )

    playlists = await client.get_playlists()
    assert len(playlists) == 2
    assert playlists[0].name == "Chill"
    # cdn2 URL should be rewritten to cdn1
    assert "cdn1" in playlists[1].image_url


async def test_get_playlists_non_list() -> None:
    """get_playlists returns empty list for non-list response."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(200, None))

    playlists = await client.get_playlists()
    assert playlists == []


# ── SunoClient.get_playlist_clips ────────────────────────────────────


async def test_get_playlist_clips_dict_response() -> None:
    """get_playlist_clips handles dict response with clips key."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(
        return_value=_mock_response(
            200,
            {
                "clips": [
                    {"id": "c1", "status": "complete", "audio_url": "https://cdn1.suno.ai/c1.mp3", "metadata": {}},
                ]
            },
        )
    )

    clips = await client.get_playlist_clips("pl-1")
    assert len(clips) == 1


async def test_get_playlist_clips_list_response() -> None:
    """get_playlist_clips handles list response."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(
        return_value=_mock_response(
            200,
            [
                {"id": "c1", "status": "complete", "audio_url": "https://cdn1.suno.ai/c1.mp3", "metadata": {}},
            ],
        )
    )

    clips = await client.get_playlist_clips("pl-1")
    assert len(clips) == 1


async def test_get_playlist_clips_empty() -> None:
    """get_playlist_clips returns empty for unexpected response type."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(200, "not a dict or list"))

    clips = await client.get_playlist_clips("pl-1")
    assert clips == []


async def test_get_playlist_clips_playlist_clips_key() -> None:
    """get_playlist_clips handles 'playlist_clips' key in dict response."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(
        return_value=_mock_response(
            200,
            {
                "playlist_clips": [
                    {"id": "c1", "status": "complete", "audio_url": "https://cdn1.suno.ai/c1.mp3", "metadata": {}},
                ]
            },
        )
    )

    clips = await client.get_playlist_clips("pl-1")
    assert len(clips) == 1


# ── SunoClient.get_credits ──────────────────────────────────────────


async def test_get_credits_success() -> None:
    """get_credits returns parsed credit info."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(
        return_value=_mock_response(
            200,
            {
                "total_credits_left": 500,
                "monthly_limit": 1000,
                "monthly_usage": 500,
                "period": "2026-04",
            },
        )
    )

    credits = await client.get_credits()
    assert credits.credits_left == 500
    assert credits.monthly_limit == 1000
    assert credits.period == "2026-04"


async def test_get_credits_non_dict_raises() -> None:
    """get_credits raises SunoApiError for non-dict response."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(200, []))

    with pytest.raises(SunoApiError, match="Unexpected credits response"):
        await client.get_credits()


# ── SunoClient._api_get error handling ───────────────────────────────


async def test_api_get_401_raises_auth_error() -> None:
    """401 from Suno API raises SunoAuthError."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(401))

    with pytest.raises(SunoAuthError, match="auth failed"):
        await client._api_get("/api/test")


async def test_api_get_403_raises_auth_error() -> None:
    """403 from Suno API raises SunoAuthError."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(403))

    with pytest.raises(SunoAuthError, match="auth failed"):
        await client._api_get("/api/test")


async def test_api_get_500_raises_api_error() -> None:
    """500 from Suno API raises SunoApiError."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(500, text="Internal Server Error"))

    with pytest.raises(SunoApiError, match="returned 500"):
        await client._api_get("/api/test")


async def test_api_get_connection_error() -> None:
    """Connection error during API call raises SunoApiError."""
    session = AsyncMock()
    client = _make_authed_client(session)

    error_resp = AsyncMock()
    error_resp.__aenter__ = AsyncMock(side_effect=ConnectionError("Timeout"))
    error_resp.__aexit__ = AsyncMock(return_value=False)
    session.get = MagicMock(return_value=error_resp)

    with pytest.raises(SunoApiError, match="request failed"):
        await client._api_get("/api/test")


# ── Cookie isolation ─────────────────────────────────────────────────


async def test_client_cookie_only_sent_to_clerk() -> None:
    """Cookie must only be sent to clerk.suno.com, never to the Suno API."""
    session = AsyncMock()
    client = SunoClient(session, "test-cookie")

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
        mock_resp.json = AsyncMock(return_value={"jwt": _make_jwt()})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        return mock_resp

    session.get = capture_get
    session.post = capture_post

    await client.authenticate()
    await client.get_feed(0)

    for url in cookie_urls:
        assert "clerk.suno.com" in url, f"Cookie was sent to non-Clerk URL: {url}"
