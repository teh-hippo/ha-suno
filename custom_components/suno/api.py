"""Suno API client for Home Assistant.

Communicates with Suno's internal web API via Clerk cookie authentication.
Cookie is sent only to clerk.suno.com.  The Suno API receives short-lived JWTs.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
from typing import Any

from aiohttp import ClientSession

from .const import (
    CLERK_BASE_URL,
    CLERK_JS_VERSION,
    CLERK_TOKEN_JS_VERSION,
    EXCLUDED_TASKS,
    JWT_REFRESH_BUFFER,
    MAX_PAGES,
    SUNO_API_BASE_URL,
)
from .exceptions import SunoApiError, SunoAuthError
from .helpers import _fix_cdn_url, _sanitise_clip
from .models import SunoClip, SunoCredits, SunoPlaylist

# Re-export models and helpers so existing ``from .api import ...`` still works.
__all__ = [
    "SunoClient",
    "SunoClip",
    "SunoCredits",
    "SunoPlaylist",
    "_decode_jwt_exp",
    "_fix_cdn_url",
    "_normalise_token",
    "_sanitise_clip",
]

_LOGGER = logging.getLogger(__name__)


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
    except (IndexError, ValueError, json.JSONDecodeError):
        return 0


def _normalise_token(token: str) -> str:
    """Accept a raw JWT or full cookie string and return a valid __client cookie.

    Accepts:
    - Raw JWT value (starts with eyJ...)
    - __client=eyJ... (cookie assignment)
    - Full cookie header with __client somewhere in it
    """
    token = token.strip()
    if token.startswith("eyJ"):
        return f"__client={token}"
    if "__client=" in token:
        # Extract just the __client value from a full cookie string
        for part in token.split(";"):
            part = part.strip()
            if part.startswith("__client="):
                return part
    return f"__client={token}"


class SunoClient:
    """Async client for the Suno internal API."""

    def __init__(self, session: ClientSession, token: str) -> None:
        self._session = session
        self._cookie = _normalise_token(token)
        self._jwt: str | None = None
        self._jwt_exp: int = 0
        self._jwt_lock = asyncio.Lock()
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

    async def get_feed(self, page: int = 0) -> tuple[list[SunoClip], bool]:
        """Fetch a page of songs from the v2 feed.

        Returns (clips, has_more).
        """
        data = await self._api_get(f"/api/feed/v2/?page={page}")
        if not isinstance(data, dict):
            return [], False
        raw_clips = data.get("clips") or []
        has_more = bool(data.get("has_more", False))

        clips = [
            _sanitise_clip(clip)
            for clip in raw_clips
            if clip.get("status") == "complete"
            and clip.get("metadata", {}).get("type") == "gen"
            and clip.get("metadata", {}).get("task") not in EXCLUDED_TASKS
        ]
        return clips, has_more

    async def get_all_songs(self) -> list[SunoClip]:
        """Fetch all songs by paginating through the v2 feed."""
        all_clips: list[SunoClip] = []
        page = 0
        while page < MAX_PAGES:
            clips, has_more = await self.get_feed(page)
            all_clips.extend(clips)
            if not has_more:
                break
            page += 1
            await asyncio.sleep(0.25)
        return all_clips

    async def get_liked_songs(self) -> list[SunoClip]:
        """Fetch all liked songs using the v2 feed endpoint."""
        all_clips: list[SunoClip] = []
        page = 0
        while page < MAX_PAGES:
            data = await self._api_get(f"/api/feed/v2/?is_liked=true&page={page}")
            if not isinstance(data, dict):
                break
            raw_clips = data.get("clips") or []
            has_more = bool(data.get("has_more", False))

            clips = [
                _sanitise_clip(clip)
                for clip in raw_clips
                if clip.get("status") == "complete"
                and clip.get("metadata", {}).get("type") == "gen"
                and clip.get("metadata", {}).get("task") not in EXCLUDED_TASKS
            ]
            all_clips.extend(clips)
            if not has_more:
                break
            page += 1
            await asyncio.sleep(0.25)
        return all_clips

    async def get_playlists(self) -> list[SunoPlaylist]:
        """Fetch the user's playlists via /api/playlist/me."""
        data = await self._api_get("/api/playlist/me?page=1&show_trashed=false&show_sharelist=false")
        if not isinstance(data, dict):
            return []
        raw_playlists = data.get("playlists") or []
        playlists: list[SunoPlaylist] = []
        for item in raw_playlists:
            pl_id = item.get("id", "")
            if pl_id:
                playlists.append(
                    SunoPlaylist(
                        id=pl_id,
                        name=item.get("name", "Untitled"),
                        image_url=_fix_cdn_url(item.get("image_url")),
                        num_clips=item.get("num_total_results", 0),
                    )
                )
        return playlists

    async def get_playlist_clips(self, playlist_id: str) -> list[SunoClip]:
        """Fetch songs in a specific playlist."""
        data = await self._api_get(f"/api/playlist/{playlist_id}/")
        if not isinstance(data, dict):
            return []
        raw_entries = data.get("playlist_clips") or []
        clips_data = [entry.get("clip") or entry for entry in raw_entries]
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
        async with self._jwt_lock:
            now = int(time.time())
            if not self._jwt or now >= (self._jwt_exp - JWT_REFRESH_BUFFER):
                await self._refresh_jwt()
            if not self._jwt:
                msg = "Failed to obtain JWT"
                raise SunoAuthError(msg)
            return self._jwt

    async def _api_get(self, path: str) -> Any:
        """Make an authenticated GET request to the Suno API with retry on 429."""
        max_retries = 3
        base_delay = 2.0

        for attempt in range(max_retries + 1):
            jwt = await self._ensure_jwt()
            url = f"{SUNO_API_BASE_URL}{path}"
            headers = {"Authorization": f"Bearer {jwt}"}

            _LOGGER.debug("GET %s (attempt %d)", path, attempt + 1)
            try:
                async with self._session.get(url, headers=headers) as resp:
                    if resp.status == 401 or resp.status == 403:
                        msg = f"Suno API auth failed with status {resp.status}"
                        raise SunoAuthError(msg)
                    if resp.status == 429:
                        if attempt < max_retries:
                            delay = base_delay * (2**attempt) + random.uniform(0, 1)  # noqa: S311
                            retry_after = resp.headers.get("Retry-After")
                            if retry_after and retry_after.isdigit():
                                delay = max(delay, float(retry_after))
                            _LOGGER.warning(
                                "Rate limited by Suno API, retrying in %.1fs (attempt %d/%d)",
                                delay,
                                attempt + 1,
                                max_retries,
                            )
                            await asyncio.sleep(delay)
                            continue
                        msg = "Rate limited by Suno API after retries"
                        raise SunoApiError(msg)
                    if resp.status != 200:
                        text = await resp.text()
                        msg = f"Suno API returned {resp.status}: {text[:200]}"
                        raise SunoApiError(msg)
                    return await resp.json()
            except (SunoApiError, SunoAuthError):
                raise
            except Exception as err:
                msg = f"Suno API request failed: {err}"
                raise SunoApiError(msg) from err

        msg = "Suno API request failed after retries"
        raise SunoApiError(msg)

    async def _api_post(self, path: str) -> int:
        """Make an authenticated POST request and return the status code."""
        jwt = await self._ensure_jwt()
        url = f"{SUNO_API_BASE_URL}{path}"
        headers = {"Authorization": f"Bearer {jwt}"}
        _LOGGER.debug("POST %s", path)
        try:
            async with self._session.post(url, headers=headers) as resp:
                if resp.status in (401, 403):
                    msg = f"Suno API auth failed with status {resp.status}"
                    raise SunoAuthError(msg)
                return resp.status
        except (SunoApiError, SunoAuthError):
            raise
        except Exception as err:
            msg = f"Suno API POST failed: {err}"
            raise SunoApiError(msg) from err

    async def request_wav(self, clip_id: str) -> None:
        """Trigger server-side WAV generation for a clip."""
        status = await self._api_post(f"/api/gen/{clip_id}/convert_wav/")
        _LOGGER.debug("convert_wav returned %d for %s", status, clip_id)
        if status < 200 or status >= 300:
            msg = f"WAV conversion request failed with status {status}"
            raise SunoApiError(msg)

    async def get_wav_url(self, clip_id: str) -> str | None:
        """Return the WAV download URL if available, or None."""
        data = await self._api_get(f"/api/gen/{clip_id}/wav_file/")
        return data.get("wav_file_url") if isinstance(data, dict) else None
