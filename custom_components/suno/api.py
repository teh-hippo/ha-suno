"""Suno API client for Home Assistant.

Communicates with Suno's internal web API via Clerk cookie authentication.
Cookie is sent only to clerk.suno.com.  The Suno API receives short-lived JWTs.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from aiohttp import ClientSession

from .auth import ClerkAuth
from .const import (
    EXCLUDED_TASKS,
    MAX_PAGES,
    SUNO_API_BASE_URL,
)
from .exceptions import SunoApiError, SunoAuthError
from .models import SunoClip, SunoCredits, SunoPlaylist

_LOGGER = logging.getLogger(__name__)


class SunoClient:
    """Async client for the Suno internal API."""

    def __init__(self, auth: ClerkAuth) -> None:
        self._auth = auth
        self._session: ClientSession = auth._session
        self._throttle_until: float = 0

    @property
    def user_id(self) -> str | None:
        """The Suno user ID from the Clerk session."""
        return self._auth.user_id

    @property
    def display_name(self) -> str:
        """Return the user's display name."""
        return self._auth.display_name

    async def get_feed(self, page: int = 0) -> tuple[list[SunoClip], bool]:
        """Fetch a page of songs from the v2 feed.

        Returns (clips, has_more).
        """
        data = await self._api_get(f"/api/feed/v2/?page={page}")
        if not isinstance(data, dict):
            return [], False
        raw_clips = data.get("clips") or []
        has_more = bool(data.get("has_more", False))
        return self._filter_and_sanitise(raw_clips), has_more

    async def get_all_songs(self) -> list[SunoClip]:
        """Fetch all songs by paginating through the v2 feed."""
        return await self._paginate_feed()

    async def get_liked_songs(self) -> list[SunoClip]:
        """Fetch all liked songs using the v2 feed endpoint."""
        return await self._paginate_feed(params={"is_liked": "true"})

    async def get_playlists(self) -> list[SunoPlaylist]:
        """Fetch the user's playlists via /api/playlist/me."""
        data = await self._api_get("/api/playlist/me?page=1&show_trashed=false&show_sharelist=false")
        if not isinstance(data, dict):
            return []
        raw_playlists = data.get("playlists") or []
        return [SunoPlaylist.from_api_response(item) for item in raw_playlists if item.get("id")]

    async def get_playlist_clips(self, playlist_id: str) -> list[SunoClip]:
        """Fetch songs in a specific playlist."""
        data = await self._api_get(f"/api/playlist/{playlist_id}/")
        if not isinstance(data, dict):
            return []
        raw_entries = data.get("playlist_clips") or []
        clips_data = [entry.get("clip") or entry for entry in raw_entries]
        return [SunoClip.from_api_response(clip) for clip in clips_data if clip.get("status") == "complete"]

    async def get_credits(self) -> SunoCredits:
        """Fetch credit balance information."""
        data = await self._api_get("/api/billing/info/")
        if not isinstance(data, dict):
            msg = "Unexpected credits response"
            raise SunoApiError(msg)
        return SunoCredits.from_api_response(data)

    def _filter_and_sanitise(self, raw_clips: list[dict[str, Any]]) -> list[SunoClip]:
        """Apply status/type/task filters and build SunoClip instances."""
        return [
            SunoClip.from_api_response(clip)
            for clip in raw_clips
            if clip.get("status") == "complete"
            and clip.get("metadata", {}).get("type") == "gen"
            and clip.get("metadata", {}).get("task") not in EXCLUDED_TASKS
        ]

    async def _paginate_feed(self, params: dict[str, str] | None = None) -> list[SunoClip]:
        """Paginate through the v2 feed, filtering and collecting clips."""
        all_clips: list[SunoClip] = []
        page = 0
        query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        while page < MAX_PAGES:
            sep = "&" if query else ""
            data = await self._api_get(f"/api/feed/v2/?page={page}{sep}{query}")
            if not isinstance(data, dict):
                break
            raw_clips = data.get("clips") or []
            has_more = bool(data.get("has_more", False))
            all_clips.extend(self._filter_and_sanitise(raw_clips))
            if not has_more:
                break
            page += 1
            await asyncio.sleep(0.25)
        return all_clips

    async def _api_request(self, method: str, path: str, *, expect_json: bool = True) -> Any:
        """Make an authenticated request with retry on 429 and adaptive throttling."""
        max_retries = 3
        base_delay = 2.0

        for attempt in range(max_retries + 1):
            # Adaptive throttle: wait if we recently got rate limited
            if self._throttle_until > 0:
                wait = self._throttle_until - time.monotonic()
                if wait > 0:
                    _LOGGER.debug("Throttling for %.1fs", wait)
                    await asyncio.sleep(wait)

            jwt = await self._auth.ensure_jwt()
            url = f"{SUNO_API_BASE_URL}{path}"
            headers = {"Authorization": f"Bearer {jwt}"}

            _LOGGER.debug("%s %s (attempt %d)", method, path, attempt + 1)
            try:
                req = self._session.get if method == "GET" else self._session.post
                async with req(url, headers=headers) as resp:
                    if resp.status in (401, 403):
                        msg = f"Suno API auth failed with status {resp.status}"
                        raise SunoAuthError(msg)
                    if resp.status == 429:
                        if attempt < max_retries:
                            delay = base_delay * (2**attempt) + random.uniform(0, 1)  # noqa: S311
                            retry_after = resp.headers.get("Retry-After")
                            if retry_after and retry_after.isdigit():
                                delay = max(delay, float(retry_after))
                            # Set global throttle so other calls also wait
                            self._throttle_until = time.monotonic() + delay
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
                    if expect_json:
                        if resp.status != 200:
                            text = await resp.text()
                            msg = f"Suno API returned {resp.status}: {text[:200]}"
                            raise SunoApiError(msg)
                        return await resp.json()
                    return resp.status
            except SunoApiError, SunoAuthError:
                raise
            except Exception as err:
                msg = f"Suno API request failed: {err}"
                raise SunoApiError(msg) from err

        msg = "Suno API request failed after retries"
        raise SunoApiError(msg)

    async def _api_get(self, path: str) -> Any:
        """Make an authenticated GET request."""
        return await self._api_request("GET", path)

    async def _api_post(self, path: str) -> int:
        """Make an authenticated POST request and return the status code."""
        result = await self._api_request("POST", path, expect_json=False)
        return int(result)

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
