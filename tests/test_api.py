"""Tests for the Suno API client."""

from __future__ import annotations

import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.suno.api import (
    SunoClient,
)
from custom_components.suno.auth import (
    ClerkAuth,
    _decode_jwt_exp,
    _normalise_token,
)
from custom_components.suno.exceptions import SunoApiError, SunoAuthError, SunoConnectionError
from custom_components.suno.models import (
    SunoClip,
    _fix_cdn_url,
)

# ── Helper utilities ─────────────────────────────────────────────────


def _make_jwt(exp: int = 9999999999) -> str:
    """Create a fake JWT with the given exp claim."""
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"header.{payload}.signature"


def _mock_response(status: int = 200, json_data=None, text: str = "", headers: dict | None = None):
    """Create a mock aiohttp response as an async context manager."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    resp.text = AsyncMock(return_value=text)
    resp.headers = headers or {}
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_authed_client(session: AsyncMock) -> SunoClient:
    """Create a SunoClient with a pre-populated JWT (skips auth calls)."""
    auth = ClerkAuth(session, "test-cookie")
    auth._jwt = _make_jwt(int(time.time()) + 3600)
    auth._jwt_exp = int(time.time()) + 3600
    auth._session_id = "sess-123"
    auth._user_id = "user-123"
    client = SunoClient(auth)
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
    clip = SunoClip.from_api_response(raw)
    assert clip.id == "test-id"
    assert clip.title == "Test"
    assert clip.is_liked is True
    assert clip.tags == "pop"
    assert clip.duration == 60.0
    assert not hasattr(clip, "user_id")


def test_sanitise_clip_missing_fields() -> None:
    """Missing fields get safe defaults."""
    raw: dict = {"metadata": {"type": "gen"}}
    clip = SunoClip.from_api_response(raw)
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
    clip = SunoClip.from_api_response(raw)
    assert clip.tags == ""
    assert clip.duration == 0.0


def test_sanitise_clip_rewrites_audiopipe_url() -> None:
    """Audiopipe URLs should be rewritten to cdn1 direct MP3."""
    raw = {
        "id": "abc-123",
        "audio_url": "https://audiopipe.suno.ai/?item_id=abc-123",
        "metadata": {"type": "gen"},
    }
    clip = SunoClip.from_api_response(raw)
    assert clip.audio_url == "https://cdn1.suno.ai/abc-123.mp3"


def test_sanitise_clip_keeps_non_audiopipe() -> None:
    """Non-audiopipe URLs are kept as-is."""
    raw = {
        "id": "abc-123",
        "audio_url": "https://cdn1.suno.ai/abc-123.mp3",
        "metadata": {"type": "gen"},
    }
    clip = SunoClip.from_api_response(raw)
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
        "metadata": {"type": "gen"},
    }
    clip = SunoClip.from_api_response(raw)
    for url in [clip.audio_url, clip.image_url, clip.image_large_url]:
        if url:
            assert url.startswith("https://"), f"URL is not HTTPS: {url}"


# ── SunoClient property tests ───────────────────────────────────────


def test_client_user_id_initially_none() -> None:
    """user_id is None before authentication."""
    session = AsyncMock()
    auth = ClerkAuth(session, "cookie")
    client = SunoClient(auth)
    assert client.user_id is None


# ── ClerkAuth.authenticate ──────────────────────────────────────────


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

    auth = ClerkAuth(session, "test-cookie")
    user_id = await auth.authenticate()

    assert user_id == "user-456"
    assert auth.user_id == "user-456"


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

    auth = ClerkAuth(session, "test-cookie")
    with pytest.raises(SunoAuthError, match="Could not determine user ID"):
        await auth.authenticate()


# ── ClerkAuth._get_session_id ────────────────────────────────────────


async def test_get_session_id_clerk_http_error() -> None:
    """Non-200 from Clerk raises SunoAuthError."""
    session = AsyncMock()
    session.get = MagicMock(return_value=_mock_response(403))

    auth = ClerkAuth(session, "cookie")
    with pytest.raises(SunoAuthError, match="status 403"):
        await auth._get_session_id()


async def test_get_session_id_no_response() -> None:
    """Missing 'response' key raises SunoAuthError."""
    session = AsyncMock()
    session.get = MagicMock(return_value=_mock_response(200, {"response": None}))

    auth = ClerkAuth(session, "cookie")
    with pytest.raises(SunoAuthError, match="Invalid Clerk response"):
        await auth._get_session_id()


async def test_get_session_id_no_active_session() -> None:
    """No active session raises SunoAuthError."""
    session = AsyncMock()
    session.get = MagicMock(
        return_value=_mock_response(200, {"response": {"last_active_session_id": None, "sessions": []}})
    )

    auth = ClerkAuth(session, "cookie")
    with pytest.raises(SunoAuthError, match="No active session"):
        await auth._get_session_id()


async def test_get_session_id_connection_error() -> None:
    """Connection error during Clerk session raises SunoAuthError."""
    session = AsyncMock()

    error_resp = AsyncMock()
    error_resp.__aenter__ = AsyncMock(side_effect=ConnectionError("DNS fail"))
    error_resp.__aexit__ = AsyncMock(return_value=False)
    session.get = MagicMock(return_value=error_resp)

    auth = ClerkAuth(session, "cookie")
    with pytest.raises(SunoConnectionError, match="Could not connect to Clerk"):
        await auth._get_session_id()


# ── ClerkAuth._refresh_jwt ──────────────────────────────────────────


async def test_refresh_jwt_success() -> None:
    """JWT refresh sets _jwt and _jwt_exp."""
    session = AsyncMock()
    jwt_token = _make_jwt(2000000000)
    session.post = MagicMock(return_value=_mock_response(200, {"jwt": jwt_token}))

    auth = ClerkAuth(session, "cookie")
    auth._session_id = "sess-123"

    await auth._refresh_jwt()
    assert auth._jwt == jwt_token
    assert auth._jwt_exp == 2000000000


async def test_refresh_jwt_http_error() -> None:
    """Non-200 from JWT refresh raises SunoAuthError."""
    session = AsyncMock()
    session.post = MagicMock(return_value=_mock_response(401))

    auth = ClerkAuth(session, "cookie")
    auth._session_id = "sess-123"

    with pytest.raises(SunoAuthError, match="Clerk request failed"):
        await auth._refresh_jwt()


async def test_refresh_jwt_no_jwt_in_response() -> None:
    """Missing JWT in response raises SunoAuthError."""
    session = AsyncMock()
    session.post = MagicMock(return_value=_mock_response(200, {"jwt": None}))

    auth = ClerkAuth(session, "cookie")
    auth._session_id = "sess-123"

    with pytest.raises(SunoAuthError, match="No JWT"):
        await auth._refresh_jwt()


async def test_refresh_jwt_connection_error() -> None:
    """Connection error during JWT refresh raises SunoAuthError."""
    session = AsyncMock()

    error_resp = AsyncMock()
    error_resp.__aenter__ = AsyncMock(side_effect=ConnectionError("Timeout"))
    error_resp.__aexit__ = AsyncMock(return_value=False)
    session.post = MagicMock(return_value=error_resp)

    auth = ClerkAuth(session, "cookie")
    auth._session_id = "sess-123"

    with pytest.raises(SunoConnectionError, match="Could not connect to Clerk"):
        await auth._refresh_jwt()


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

    auth = ClerkAuth(session, "cookie")
    assert auth._session_id is None

    await auth._refresh_jwt()
    assert auth._session_id == "sess-auto"
    assert auth._jwt is not None


# ── ClerkAuth.ensure_jwt ─────────────────────────────────────────────


async def test_ensure_jwt_returns_valid_jwt() -> None:
    """ensure_jwt returns existing JWT when still valid."""
    session = AsyncMock()
    client = _make_authed_client(session)

    jwt = await client._auth.ensure_jwt()
    assert jwt == client._auth._jwt


async def test_ensure_jwt_refreshes_expired() -> None:
    """ensure_jwt refreshes when JWT is expired."""
    session = AsyncMock()
    client = _make_authed_client(session)
    client._auth._jwt_exp = int(time.time()) - 10  # expired

    new_jwt = _make_jwt(int(time.time()) + 7200)
    session.post = MagicMock(return_value=_mock_response(200, {"jwt": new_jwt}))

    jwt = await client._auth.ensure_jwt()
    assert jwt == new_jwt


# ── SunoClient.get_feed ─────────────────────────────────────────────


async def test_get_feed_success() -> None:
    """get_feed returns sanitised clips for complete gen songs."""
    session = AsyncMock()
    client = _make_authed_client(session)

    raw_clips = [
        {"id": "c1", "status": "complete", "audio_url": "https://cdn1.suno.ai/c1.mp3", "metadata": {"type": "gen"}},
        {"id": "c2", "status": "processing", "audio_url": "", "metadata": {"type": "gen"}},
        {"id": "c3", "status": "complete", "audio_url": "https://cdn1.suno.ai/c3.mp3", "metadata": {"type": "gen"}},
    ]
    session.get = MagicMock(return_value=_mock_response(200, {"clips": raw_clips, "has_more": False}))

    clips, has_more = await client.get_feed(0)
    assert len(clips) == 2
    assert clips[0].id == "c1"
    assert clips[1].id == "c3"
    assert has_more is False


async def test_get_feed_has_more() -> None:
    """get_feed returns has_more=True when more pages exist."""
    session = AsyncMock()
    client = _make_authed_client(session)

    raw_clips = [
        {"id": "c1", "status": "complete", "audio_url": "https://cdn1.suno.ai/c1.mp3", "metadata": {"type": "gen"}},
    ]
    session.get = MagicMock(return_value=_mock_response(200, {"clips": raw_clips, "has_more": True}))

    clips, has_more = await client.get_feed(0)
    assert len(clips) == 1
    assert has_more is True


async def test_get_feed_non_dict_response() -> None:
    """get_feed returns empty list for non-dict response."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(200, []))

    clips, has_more = await client.get_feed(0)
    assert clips == []
    assert has_more is False


async def test_get_feed_filtering() -> None:
    """get_feed excludes editor artifacts but includes all music types."""
    session = AsyncMock()
    client = _make_authed_client(session)

    raw_clips = [
        {"id": "c1", "status": "complete", "audio_url": "https://cdn1.suno.ai/c1.mp3", "metadata": {"type": "gen"}},
        {
            "id": "c2",
            "status": "complete",
            "audio_url": "https://cdn1.suno.ai/c2.mp3",
            "metadata": {"type": "gen", "task": "infill"},
        },
        {
            "id": "c3",
            "status": "complete",
            "audio_url": "https://cdn1.suno.ai/c3.mp3",
            "metadata": {"type": "gen", "task": "fixed_infill"},
        },
        {
            "id": "c4",
            "status": "complete",
            "audio_url": "https://cdn1.suno.ai/c4.mp3",
            "metadata": {"type": "gen", "task": "generate"},
        },
        {
            "id": "c5",
            "status": "complete",
            "audio_url": "https://cdn1.suno.ai/c5.mp3",
            "metadata": {"type": "upsample", "task": "upsample"},
        },
        {
            "id": "c6",
            "status": "complete",
            "audio_url": "https://cdn1.suno.ai/c6.mp3",
            "metadata": {"type": "rendered_context_window"},
        },
        {
            "id": "c7",
            "status": "complete",
            "audio_url": "https://cdn1.suno.ai/c7.mp3",
            "metadata": {"type": "upload"},
        },
        {
            "id": "c8",
            "status": "complete",
            "audio_url": "https://cdn1.suno.ai/c8.mp3",
            "metadata": {"type": "concat"},
        },
        {
            "id": "c9",
            "status": "complete",
            "audio_url": "https://cdn1.suno.ai/c9.mp3",
            "metadata": {"type": "edit_v3_export"},
        },
    ]
    session.get = MagicMock(return_value=_mock_response(200, {"clips": raw_clips, "has_more": False}))

    clips, _ = await client.get_feed(0)
    ids = [c.id for c in clips]
    # Included: gen, gen+generate, upsample, upload, concat, edit_v3_export
    assert "c1" in ids
    assert "c4" in ids
    assert "c5" in ids
    assert "c7" in ids
    assert "c8" in ids
    assert "c9" in ids
    # Excluded: infill, fixed_infill, rendered_context_window
    assert "c2" not in ids
    assert "c3" not in ids
    assert "c6" not in ids


async def test_get_feed_handles_missing_metadata() -> None:
    """get_feed handles clips with missing or None metadata gracefully."""
    session = AsyncMock()
    client = _make_authed_client(session)

    raw_clips = [
        {"id": "c1", "status": "complete", "audio_url": "https://cdn1.suno.ai/c1.mp3", "metadata": None},
        {"id": "c2", "status": "complete", "audio_url": "https://cdn1.suno.ai/c2.mp3"},
        {"id": "c3", "status": "complete", "audio_url": "https://cdn1.suno.ai/c3.mp3", "metadata": {}},
    ]
    session.get = MagicMock(return_value=_mock_response(200, {"clips": raw_clips, "has_more": False}))

    clips, _ = await client.get_feed(0)
    ids = [c.id for c in clips]
    # All included: blocklist design includes unknown/missing types
    assert "c1" in ids
    assert "c2" in ids
    assert "c3" in ids


async def test_get_liked_songs_excludes_infill_tasks() -> None:
    """get_liked_songs excludes clips with infill/fixed_infill task metadata."""
    session = AsyncMock()
    client = _make_authed_client(session)

    raw_clips = [
        {
            "id": "c1",
            "status": "complete",
            "audio_url": "https://cdn1.suno.ai/c1.mp3",
            "metadata": {"type": "gen"},
            "is_liked": True,
        },
        {
            "id": "c2",
            "status": "complete",
            "audio_url": "https://cdn1.suno.ai/c2.mp3",
            "metadata": {"type": "gen", "task": "infill"},
            "is_liked": True,
        },
    ]
    session.get = MagicMock(return_value=_mock_response(200, {"clips": raw_clips, "has_more": False}))

    clips = await client.get_liked_songs()
    assert len(clips) == 1
    assert clips[0].id == "c1"


# ── SunoClient.get_all_songs ────────────────────────────────────────


async def test_get_all_songs_pagination() -> None:
    """get_all_songs paginates through pages until has_more is false."""
    session = AsyncMock()
    client = _make_authed_client(session)

    page0_clips = [
        {
            "id": f"p0-{i}",
            "status": "complete",
            "audio_url": f"https://cdn1.suno.ai/p0-{i}.mp3",
            "metadata": {"type": "gen"},
        }
        for i in range(20)
    ]
    page1_clips = [
        {
            "id": f"p1-{i}",
            "status": "complete",
            "audio_url": f"https://cdn1.suno.ai/p1-{i}.mp3",
            "metadata": {"type": "gen"},
        }
        for i in range(5)
    ]

    call_count = 0

    def mock_get(url, **kwargs):
        nonlocal call_count
        if call_count == 0:
            data = {"clips": page0_clips, "has_more": True}
        else:
            data = {"clips": page1_clips, "has_more": False}
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
    session.get = MagicMock(return_value=_mock_response(200, {"clips": [], "has_more": False}))

    clips = await client.get_all_songs()
    assert clips == []


# ── SunoClient.get_playlists ────────────────────────────────────────


async def test_get_playlists_success() -> None:
    """get_playlists returns parsed playlist objects from /api/playlist/me."""
    session = AsyncMock()
    client = _make_authed_client(session)

    data = {
        "num_total_results": 3,
        "playlists": [
            {"id": "pl-1", "name": "Chill", "image_url": "https://cdn1.suno.ai/img.jpg", "num_total_results": 10},
            {"id": "pl-2", "name": "Workout", "image_url": "https://cdn2.suno.ai/img.jpg", "num_total_results": 5},
            {"id": "pl-3", "name": "Empty", "image_url": "", "num_total_results": 0},
        ],
    }
    session.get = MagicMock(return_value=_mock_response(200, data))

    playlists = await client.get_playlists()
    assert len(playlists) == 3
    assert playlists[0].name == "Chill"
    assert playlists[0].num_clips == 10
    assert playlists[1].name == "Workout"
    # cdn2 URL should be rewritten to cdn1
    assert "cdn1" in playlists[1].image_url
    assert playlists[2].name == "Empty"
    assert playlists[2].num_clips == 0


async def test_get_playlists_non_dict() -> None:
    """get_playlists returns empty list for non-dict response."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(200, None))

    playlists = await client.get_playlists()
    assert playlists == []


async def test_get_playlists_empty() -> None:
    """get_playlists returns empty list when no playlists exist."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(200, {"playlists": []}))

    playlists = await client.get_playlists()
    assert playlists == []


# ── SunoClient.get_playlist_clips ────────────────────────────────────


async def test_get_playlist_clips_dict_response() -> None:
    """get_playlist_clips handles dict response with playlist_clips key."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(
        return_value=_mock_response(
            200,
            {
                "playlist_clips": [
                    {
                        "clip": {
                            "id": "c1",
                            "status": "complete",
                            "audio_url": "https://cdn1.suno.ai/c1.mp3",
                            "metadata": {"type": "gen"},
                        },
                    },
                ],
                "name": "Test Playlist",
                "id": "pl-1",
                "num_total_results": 1,
            },
        )
    )

    clips = await client.get_playlist_clips("pl-1")
    assert len(clips) == 1
    assert clips[0].id == "c1"


async def test_get_playlist_clips_filters_incomplete() -> None:
    """get_playlist_clips filters out non-complete and editor artifact clips."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(
        return_value=_mock_response(
            200,
            {
                "playlist_clips": [
                    {
                        "clip": {
                            "id": "c1",
                            "status": "complete",
                            "audio_url": "https://cdn1.suno.ai/c1.mp3",
                            "metadata": {"type": "gen"},
                        },
                    },
                    {
                        "clip": {
                            "id": "c2",
                            "status": "processing",
                            "audio_url": "",
                            "metadata": {"type": "gen"},
                        },
                    },
                    {
                        "clip": {
                            "id": "c3",
                            "status": "complete",
                            "audio_url": "https://cdn1.suno.ai/c3.mp3",
                            "metadata": {"type": "upsample", "task": "upsample"},
                        },
                    },
                    {
                        "clip": {
                            "id": "c4",
                            "status": "complete",
                            "audio_url": "https://cdn1.suno.ai/c4.mp3",
                            "metadata": {"type": "rendered_context_window"},
                        },
                    },
                    {
                        "clip": {
                            "id": "c5",
                            "status": "complete",
                            "audio_url": "https://cdn1.suno.ai/c5.mp3",
                            "metadata": {"type": "gen", "task": "infill"},
                        },
                    },
                ],
            },
        )
    )

    clips = await client.get_playlist_clips("pl-1")
    ids = [c.id for c in clips]
    assert "c1" in ids
    assert "c2" not in ids  # incomplete
    assert "c3" in ids  # upsample included
    assert "c4" not in ids  # editor artifact excluded
    assert "c5" not in ids  # infill excluded


async def test_get_playlist_clips_empty() -> None:
    """get_playlist_clips returns empty for unexpected response type."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(200, "not a dict or list"))

    clips = await client.get_playlist_clips("pl-1")
    assert clips == []


async def test_get_playlist_clips_empty_playlist() -> None:
    """get_playlist_clips returns empty for playlist with no clips."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(
        return_value=_mock_response(
            200,
            {
                "playlist_clips": [],
                "name": "Empty Playlist",
                "id": "pl-1",
                "num_total_results": 0,
            },
        )
    )

    clips = await client.get_playlist_clips("pl-1")
    assert clips == []


# ── SunoClient.get_liked_songs ──────────────────────────────────────


async def test_get_liked_songs_success() -> None:
    """get_liked_songs returns all music types including uploads."""
    session = AsyncMock()
    client = _make_authed_client(session)

    raw_clips = [
        {
            "id": "c1",
            "status": "complete",
            "audio_url": "https://cdn1.suno.ai/c1.mp3",
            "metadata": {"type": "gen"},
            "is_liked": True,
            "handle": "my-handle",
        },
        {
            "id": "c2",
            "status": "complete",
            "audio_url": "https://cdn1.suno.ai/c2.mp3",
            "metadata": {"type": "upload"},
            "is_liked": True,
        },
    ]
    session.get = MagicMock(return_value=_mock_response(200, {"clips": raw_clips, "has_more": False}))

    clips = await client.get_liked_songs()
    assert len(clips) == 2
    assert clips[0].id == "c1"
    assert clips[1].id == "c2"


async def test_get_liked_songs_pagination() -> None:
    """get_liked_songs paginates until has_more is false."""
    session = AsyncMock()
    client = _make_authed_client(session)

    call_count = 0

    def mock_get(url, **kwargs):
        nonlocal call_count
        if call_count == 0:
            data = {
                "clips": [
                    {
                        "id": "c1",
                        "status": "complete",
                        "audio_url": "https://cdn1.suno.ai/c1.mp3",
                        "metadata": {"type": "gen"},
                    }
                ],
                "has_more": True,
            }
        else:
            data = {
                "clips": [
                    {
                        "id": "c2",
                        "status": "complete",
                        "audio_url": "https://cdn1.suno.ai/c2.mp3",
                        "metadata": {"type": "gen"},
                    }
                ],
                "has_more": False,
            }
        call_count += 1
        return _mock_response(200, data)

    session.get = mock_get

    clips = await client.get_liked_songs()
    assert len(clips) == 2
    assert call_count == 2


async def test_get_liked_songs_non_dict_response() -> None:
    """get_liked_songs breaks on non-dict response."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(200, []))

    clips = await client.get_liked_songs()
    assert clips == []


async def test_get_liked_songs_filters_incomplete() -> None:
    """get_liked_songs excludes non-complete clips."""
    session = AsyncMock()
    client = _make_authed_client(session)

    raw_clips = [
        {
            "id": "c1",
            "status": "complete",
            "audio_url": "https://cdn1.suno.ai/c1.mp3",
            "metadata": {"type": "gen"},
        },
        {
            "id": "c2",
            "status": "processing",
            "audio_url": "",
            "metadata": {"type": "gen"},
        },
    ]
    session.get = MagicMock(return_value=_mock_response(200, {"clips": raw_clips, "has_more": False}))

    clips = await client.get_liked_songs()
    assert len(clips) == 1
    assert clips[0].id == "c1"


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


async def test_api_get_429_retries_with_backoff() -> None:
    """429 rate limit triggers exponential backoff retries."""
    session = AsyncMock()
    client = _make_authed_client(session)

    call_count = 0

    def make_response(*args: object, **kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return _mock_response(429)
        return _mock_response(200, {"result": "ok"})

    session.get = MagicMock(side_effect=make_response)

    result = await client._api_get("/api/test")
    assert result == {"result": "ok"}
    assert call_count == 3


async def test_api_get_429_exhausts_retries() -> None:
    """429 on all retries raises SunoApiError."""
    session = AsyncMock()
    client = _make_authed_client(session)

    session.get = MagicMock(return_value=_mock_response(429))

    with pytest.raises(SunoApiError, match="after maximum retries"):
        await client._api_get("/api/test")


async def test_api_get_429_respects_retry_after_header() -> None:
    """429 with Retry-After header uses that delay."""
    session = AsyncMock()
    client = _make_authed_client(session)

    call_count = 0

    def make_response(*args: object, **kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _mock_response(429, headers={"Retry-After": "5"})
        return _mock_response(200, {"result": "ok"})

    session.get = MagicMock(side_effect=make_response)

    result = await client._api_get("/api/test")
    assert result == {"result": "ok"}
    assert call_count == 2


async def test_api_get_401_not_retried() -> None:
    """Auth errors (401) are not retried."""
    session = AsyncMock()
    client = _make_authed_client(session)

    session.get = MagicMock(return_value=_mock_response(401))

    with pytest.raises(SunoAuthError, match="auth failed"):
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


# ── TC-13: request_wav ───────────────────────────────────────────────


async def test_request_wav_non_2xx_raises() -> None:
    """request_wav with non-2xx response raises SunoApiError."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.post = MagicMock(return_value=_mock_response(500))

    with pytest.raises(SunoApiError, match="WAV conversion request failed"):
        await client.request_wav("clip-123")


async def test_request_wav_success_does_not_raise() -> None:
    """request_wav with successful 200 response does not raise."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.post = MagicMock(return_value=_mock_response(200))

    await client.request_wav("clip-123")


# ── Cookie isolation ─────────────────────────────────────────────────


async def test_client_cookie_only_sent_to_clerk() -> None:
    """Cookie must only be sent to clerk.suno.com, never to the Suno API."""
    session = AsyncMock()
    auth = ClerkAuth(session, "test-cookie")
    client = SunoClient(auth)

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
            mock_resp.json = AsyncMock(return_value={"clips": [], "has_more": False})
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

    await auth.authenticate()
    await client.get_feed(0)

    for url in cookie_urls:
        assert "clerk.suno.com" in url, f"Cookie was sent to non-Clerk URL: {url}"


# ── T9: get_wav_url ─────────────────────────────────────────────────


async def test_get_wav_url_success() -> None:
    """get_wav_url returns the URL when API returns expected dict."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(200, {"wav_file_url": "https://cdn1.suno.ai/clip.wav"}))

    result = await client.get_wav_url("clip-123")

    assert result == "https://cdn1.suno.ai/clip.wav"


async def test_get_wav_url_non_dict_response() -> None:
    """get_wav_url returns None when API returns a non-dict."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(200, ["not", "a", "dict"]))

    result = await client.get_wav_url("clip-123")

    assert result is None


async def test_get_wav_url_missing_key() -> None:
    """get_wav_url returns None when wav_file_url key is missing."""
    session = AsyncMock()
    client = _make_authed_client(session)
    session.get = MagicMock(return_value=_mock_response(200, {"other": "data"}))

    result = await client.get_wav_url("clip-123")

    assert result is None


# ── T10: _paginate_feed stops at MAX_PAGES ──────────────────────────


async def test_paginate_feed_stops_at_max_pages() -> None:
    """Pagination loop stops at MAX_PAGES even when has_more is always True."""
    from custom_components.suno.const import MAX_PAGES

    session = AsyncMock()
    client = _make_authed_client(session)

    call_count = 0

    def mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        clip = {
            "id": f"c-{call_count}",
            "status": "complete",
            "audio_url": f"https://cdn1.suno.ai/c-{call_count}.mp3",
            "metadata": {"type": "gen"},
        }
        return _mock_response(200, {"clips": [clip], "has_more": True})

    session.get = mock_get

    clips = await client.get_all_songs()

    # Should stop at MAX_PAGES exactly
    assert call_count == MAX_PAGES
    assert len(clips) == MAX_PAGES


# ── get_clip_parent ──────────────────────────────────────────────────


async def test_get_clip_parent() -> None:
    """get_clip_parent returns parent clip dict."""
    session = AsyncMock()
    client = _make_authed_client(session)
    parent_data = {"id": "parent-1", "title": "Parent Song"}
    session.get = lambda *a, **kw: _mock_response(200, parent_data)

    result = await client.get_clip_parent("child-1")
    assert result is not None
    assert result["id"] == "parent-1"


async def test_get_clip_parent_root_returns_none() -> None:
    """get_clip_parent returns None for root clips (no parent)."""
    session = AsyncMock()
    client = _make_authed_client(session)
    # API returns empty dict (no id) for root clips
    session.get = lambda *a, **kw: _mock_response(200, {})

    result = await client.get_clip_parent("root-1")
    assert result is None


async def test_display_name_updates_on_subsequent_feed_calls() -> None:
    """suno_display_name updates when the API returns a new display_name."""
    session = AsyncMock()
    client = _make_authed_client(session)

    def _feed_response(display_name: str):
        return _mock_response(
            200,
            {
                "clips": [
                    {
                        "id": "clip-1",
                        "status": "complete",
                        "display_name": display_name,
                        "metadata": {"type": "gen", "tags": "pop", "duration": 60.0, "has_vocal": True},
                    }
                ],
                "has_more": False,
            },
        )

    # First feed call sets the display_name
    session.get = lambda *a, **kw: _feed_response("OldName")
    await client.get_feed()
    assert client.suno_display_name == "OldName"

    # Second feed call with a changed display_name should update it
    session.get = lambda *a, **kw: _feed_response("NewName")
    await client.get_feed()
    assert client.suno_display_name == "NewName"


async def test_display_name_updates_during_pagination() -> None:
    """suno_display_name updates during paginated feed fetches."""
    session = AsyncMock()
    client = _make_authed_client(session)

    call_count = 0

    def _paginated_feed(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _mock_response(
                200,
                {
                    "clips": [
                        {
                            "id": "clip-1",
                            "status": "complete",
                            "display_name": "OldName",
                            "metadata": {"type": "gen", "tags": "pop", "duration": 60.0, "has_vocal": True},
                        }
                    ],
                    "has_more": True,
                },
            )
        return _mock_response(
            200,
            {
                "clips": [
                    {
                        "id": "clip-2",
                        "status": "complete",
                        "display_name": "NewName",
                        "metadata": {"type": "gen", "tags": "pop", "duration": 60.0, "has_vocal": True},
                    }
                ],
                "has_more": False,
            },
        )

    session.get = _paginated_feed
    clips = await client.get_all_songs()
    assert len(clips) == 2
    # The last page's display_name should be captured
    assert client.suno_display_name == "NewName"
