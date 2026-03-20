"""Suno API client for Home Assistant.

Communicates with Suno's internal web API via Clerk cookie authentication.
Cookie is sent only to clerk.suno.com.  The Suno API receives short-lived JWTs.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientSession

from .const import (
    CDN_BASE_URL,
    CLERK_BASE_URL,
    CLERK_JS_VERSION,
    CLERK_TOKEN_JS_VERSION,
    FEED_PAGE_SIZE,
    JWT_REFRESH_BUFFER,
    SUNO_API_BASE_URL,
)
from .exceptions import SunoApiError, SunoAuthError

_LOGGER = logging.getLogger(__name__)

# Fields we keep from clip responses (everything else is dropped for PII safety)
_CLIP_ALLOWLIST = frozenset(
    {
        "id",
        "title",
        "audio_url",
        "image_url",
        "image_large_url",
        "is_liked",
        "status",
        "created_at",
        "metadata",
    }
)

_METADATA_ALLOWLIST = frozenset(
    {
        "tags",
        "duration",
        "type",
        "has_vocal",
    }
)


@dataclass
class SunoClip:
    """A song/clip from the Suno library."""

    id: str
    title: str
    audio_url: str
    image_url: str
    image_large_url: str
    is_liked: bool
    status: str
    created_at: str
    tags: str
    duration: float
    clip_type: str
    has_vocal: bool


@dataclass
class SunoCredits:
    """Credit balance information."""

    credits_left: int
    monthly_limit: int
    monthly_usage: int
    period: str | None


@dataclass
class SunoPlaylist:
    """A playlist from the user's library."""

    id: str
    name: str
    image_url: str
    num_clips: int


def _fix_cdn_url(url: str | None) -> str:
    """Rewrite cdn2.suno.ai URLs to cdn1.suno.ai (cdn2 returns 403)."""
    if not url:
        return ""
    return url.replace("cdn2.suno.ai", "cdn1.suno.ai")


def _sanitise_clip(raw: dict[str, Any]) -> SunoClip:
    """Build a SunoClip from raw API data, keeping only allowlisted fields."""
    metadata = raw.get("metadata") or {}
    image_url = _fix_cdn_url(raw.get("image_url"))
    image_large_url = _fix_cdn_url(raw.get("image_large_url"))

    # Prefer cdn1 direct MP3 URL over audiopipe
    audio_url = raw.get("audio_url", "")
    clip_id = raw.get("id", "")
    if audio_url and "audiopipe" in audio_url and clip_id:
        audio_url = f"{CDN_BASE_URL}/{clip_id}.mp3"

    return SunoClip(
        id=clip_id,
        title=raw.get("title", "Untitled"),
        audio_url=audio_url,
        image_url=image_url,
        image_large_url=image_large_url,
        is_liked=raw.get("is_liked", False),
        status=raw.get("status", "unknown"),
        created_at=raw.get("created_at", ""),
        tags=metadata.get("tags", ""),
        duration=metadata.get("duration") or 0.0,
        clip_type=metadata.get("type", ""),
        has_vocal=metadata.get("has_vocal", False),
    )


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip sensitive headers for logging."""
    redacted = dict(headers)
    for key in ("Authorization", "Cookie", "cookie", "authorization"):
        if key in redacted:
            redacted[key] = "***REDACTED***"
    return redacted


def _decode_jwt_exp(token: str) -> int:
    """Extract the exp claim from a JWT without verification."""
    try:
        payload_b64 = token.split(".")[1]
        # Add padding
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return int(payload.get("exp", 0))
    except IndexError, ValueError, json.JSONDecodeError:
        return 0


class SunoClient:
    """Async client for the Suno internal API."""

    def __init__(self, session: ClientSession, cookie: str) -> None:
        self._session = session
        self._cookie = cookie
        self._jwt: str | None = None
        self._jwt_exp: int = 0
        self._session_id: str | None = None
        self._user_id: str | None = None

    @property
    def user_id(self) -> str | None:
        """The Suno user ID from the Clerk session."""
        return self._user_id

    async def authenticate(self) -> str:
        """Authenticate with Clerk and return the user ID.

        Raises SunoAuthError on failure.
        """
        await self._get_session_id()
        await self._refresh_jwt()
        if not self._user_id:
            msg = "Could not determine user ID from Clerk session"
            raise SunoAuthError(msg)
        return self._user_id

    async def get_feed(self, page: int = 0) -> list[SunoClip]:
        """Fetch a page of songs from the library."""
        data = await self._api_get(f"/api/feed/?page={page}")
        if not isinstance(data, list):
            return []
        return [_sanitise_clip(clip) for clip in data if clip.get("status") == "complete"]

    async def get_all_songs(self) -> list[SunoClip]:
        """Fetch all songs by paginating through the feed."""
        all_clips: list[SunoClip] = []
        page = 0
        while True:
            clips = await self.get_feed(page)
            if not clips:
                break
            all_clips.extend(clips)
            if len(clips) < FEED_PAGE_SIZE:
                break
            page += 1
        return all_clips

    async def get_playlists(self) -> list[SunoPlaylist]:
        """Fetch the user's playlists."""
        data = await self._api_get("/me/v2/playlists")
        if not isinstance(data, list):
            return []
        playlists: list[SunoPlaylist] = []
        for item in data:
            playlists.append(
                SunoPlaylist(
                    id=item.get("id", ""),
                    name=item.get("name", "Untitled"),
                    image_url=_fix_cdn_url(item.get("image_url")),
                    num_clips=item.get("num_clips", 0),
                )
            )
        return playlists

    async def get_playlist_clips(self, playlist_id: str) -> list[SunoClip]:
        """Fetch songs in a specific playlist."""
        data = await self._api_get(f"/api/playlist/{playlist_id}/")
        if isinstance(data, dict):
            clips_data = data.get("clips") or data.get("playlist_clips") or []
        elif isinstance(data, list):
            clips_data = data
        else:
            clips_data = []
        return [_sanitise_clip(clip) for clip in clips_data if clip.get("status") == "complete"]

    async def get_credits(self) -> SunoCredits:
        """Fetch credit balance information."""
        data = await self._api_get("/api/billing/info/")
        if not isinstance(data, dict):
            msg = "Unexpected credits response"
            raise SunoApiError(msg)
        return SunoCredits(
            credits_left=data.get("total_credits_left", 0),
            monthly_limit=data.get("monthly_limit", 0),
            monthly_usage=data.get("monthly_usage", 0),
            period=data.get("period"),
        )

    async def _get_session_id(self) -> None:
        """Get a Clerk session ID using the browser cookie."""
        url = f"{CLERK_BASE_URL}/v1/client?_clerk_js_version={CLERK_JS_VERSION}"
        headers = {"Cookie": self._cookie}
        _LOGGER.debug("Fetching Clerk session ID")

        try:
            async with self._session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    msg = f"Clerk session request failed with status {resp.status}"
                    raise SunoAuthError(msg)
                data = await resp.json()
        except SunoAuthError:
            raise
        except Exception as err:
            msg = "Could not connect to Clerk"
            raise SunoAuthError(msg) from err

        response = data.get("response")
        if not response:
            msg = "Invalid Clerk response.  Cookie may be expired."
            raise SunoAuthError(msg)

        self._session_id = response.get("last_active_session_id")
        if not self._session_id:
            msg = "No active session found.  Cookie may be expired."
            raise SunoAuthError(msg)

        # Extract user ID from session data
        sessions = response.get("sessions", [])
        for session in sessions:
            if session.get("id") == self._session_id:
                user = session.get("user", {})
                self._user_id = user.get("id")
                break

    async def _refresh_jwt(self) -> None:
        """Get a fresh JWT from Clerk using the session ID."""
        if not self._session_id:
            await self._get_session_id()

        url = (
            f"{CLERK_BASE_URL}/v1/client/sessions/{self._session_id}/tokens?_clerk_js_version={CLERK_TOKEN_JS_VERSION}"
        )
        headers = {"Cookie": self._cookie}

        try:
            async with self._session.post(url, headers=headers) as resp:
                if resp.status != 200:
                    msg = f"JWT refresh failed with status {resp.status}"
                    raise SunoAuthError(msg)
                data = await resp.json()
        except SunoAuthError:
            raise
        except Exception as err:
            msg = "Could not refresh JWT"
            raise SunoAuthError(msg) from err

        jwt = data.get("jwt")
        if not jwt:
            msg = "No JWT in Clerk token response"
            raise SunoAuthError(msg)

        self._jwt = jwt
        self._jwt_exp = _decode_jwt_exp(jwt)
        _LOGGER.debug("JWT refreshed, expires at %d", self._jwt_exp)

    async def _ensure_jwt(self) -> str:
        """Return a valid JWT, refreshing if needed."""
        now = int(time.time())
        if not self._jwt or now >= (self._jwt_exp - JWT_REFRESH_BUFFER):
            await self._refresh_jwt()
        if not self._jwt:
            msg = "Failed to obtain JWT"
            raise SunoAuthError(msg)
        return self._jwt

    async def _api_get(self, path: str) -> Any:
        """Make an authenticated GET request to the Suno API."""
        jwt = await self._ensure_jwt()
        url = f"{SUNO_API_BASE_URL}{path}"
        headers = {"Authorization": f"Bearer {jwt}"}

        _LOGGER.debug("GET %s", path)
        try:
            async with self._session.get(url, headers=headers) as resp:
                if resp.status == 401 or resp.status == 403:
                    msg = f"Suno API auth failed with status {resp.status}"
                    raise SunoAuthError(msg)
                if resp.status != 200:
                    text = await resp.text()
                    msg = f"Suno API returned {resp.status}: {text[:200]}"
                    raise SunoApiError(msg)
                return await resp.json()
        except SunoApiError, SunoAuthError:
            raise
        except Exception as err:
            msg = f"Suno API request failed: {err}"
            raise SunoApiError(msg) from err
