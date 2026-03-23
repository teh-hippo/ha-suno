"""Suno API client."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from aiohttp import ClientSession

from .auth import ClerkAuth
from .const import (
    EXCLUDED_TASKS,
    EXCLUDED_TYPES,
    MAX_PAGES,
    SUNO_API_BASE_URL,
)
from .exceptions import SunoApiError, SunoAuthError
from .models import SunoClip, SunoCredits, SunoPlaylist
from .rate_limit import SunoRateLimiter

_LOGGER = logging.getLogger(__name__)


class SunoClient:
    def __init__(self, auth: ClerkAuth, rate_limiter: SunoRateLimiter | None = None) -> None:
        self._auth = auth
        self._session: ClientSession = auth._session
        self._rate_limiter = rate_limiter
        self._last_seen_display_name: str | None = None

    @property
    def user_id(self) -> str | None:
        return self._auth.user_id

    @property
    def display_name(self) -> str:
        return self._auth.display_name

    @property
    def suno_display_name(self) -> str | None:
        return self._last_seen_display_name

    async def ensure_authenticated(self) -> None:
        """Ensure the client has a valid JWT token."""
        await self._auth.ensure_jwt()

    async def get_feed(self, page: int = 0) -> tuple[list[SunoClip], bool]:
        data = await self._api_get(f"/api/feed/v2/?page={page}")
        if not isinstance(data, dict):
            return [], False
        raw_clips = data.get("clips") or []
        # Capture display_name from first clip (user's own Suno handle)
        if raw_clips and not self._last_seen_display_name:
            self._last_seen_display_name = (raw_clips[0].get("display_name") or "").strip() or None
        return self._filter_and_sanitise(raw_clips), bool(data.get("has_more", False))

    async def get_all_songs(self) -> list[SunoClip]:
        return await self._paginate_feed()

    async def get_liked_songs(self) -> list[SunoClip]:
        return await self._paginate_feed(params={"is_liked": "true"})

    async def get_playlists(self) -> list[SunoPlaylist]:
        data = await self._api_get("/api/playlist/me?page=1&show_trashed=false&show_sharelist=false")
        return (
            [SunoPlaylist.from_api_response(item) for item in (data.get("playlists") or []) if item.get("id")]
            if isinstance(data, dict)
            else []
        )

    async def get_playlist_clips(self, playlist_id: str) -> list[SunoClip]:
        data = await self._api_get(f"/api/playlist/{playlist_id}/")
        if not isinstance(data, dict):
            return []
        clips_data = [entry.get("clip") or entry for entry in (data.get("playlist_clips") or [])]
        return self._filter_and_sanitise(clips_data)

    async def get_credits(self) -> SunoCredits:
        if not isinstance(data := await self._api_get("/api/billing/info/"), dict):
            raise SunoApiError("Unexpected credits response")
        return SunoCredits.from_api_response(data)

    def _filter_and_sanitise(self, raw_clips: list[dict[str, Any]]) -> list[SunoClip]:
        return [
            SunoClip.from_api_response(clip)
            for clip in raw_clips
            if clip.get("status") == "complete"
            and (clip.get("metadata") or {}).get("type") not in EXCLUDED_TYPES
            and (clip.get("metadata") or {}).get("task") not in EXCLUDED_TASKS
        ]

    async def _paginate_feed(self, params: dict[str, str] | None = None) -> list[SunoClip]:
        all_clips: list[SunoClip] = []
        page = 0
        query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        while page < MAX_PAGES:
            data = await self._api_get(f"/api/feed/v2/?page={page}{'&' + query if query else ''}")
            if not isinstance(data, dict):
                break
            raw_clips = data.get("clips") or []
            if raw_clips and not self._last_seen_display_name:
                self._last_seen_display_name = (raw_clips[0].get("display_name") or "").strip() or None
            all_clips.extend(self._filter_and_sanitise(raw_clips))
            if not data.get("has_more", False):
                break
            page += 1
            await asyncio.sleep(0.5)
        return all_clips

    async def _api_request(self, method: str, path: str, *, expect_json: bool = True) -> Any:
        for attempt in range(4):
            if self._rate_limiter:
                await self._rate_limiter.acquire()
            try:
                url = f"{SUNO_API_BASE_URL}{path}"
                headers = {"Authorization": f"Bearer {await self._auth.ensure_jwt()}"}
                _LOGGER.debug("%s %s (attempt %d)", method, path, attempt + 1)

                try:
                    req = self._session.get if method == "GET" else self._session.post
                    async with req(url, headers=headers) as resp:
                        if resp.status in (401, 403):
                            raise SunoAuthError(f"Suno API auth failed with status {resp.status}")
                        if resp.status == 429:
                            if attempt < 3:
                                delay = 2.0 * (2**attempt) + random.uniform(0, 1)  # noqa: S311
                                if (retry_after := resp.headers.get("Retry-After")) and retry_after.isdigit():
                                    delay = max(delay, float(retry_after))
                                if self._rate_limiter:
                                    await self._rate_limiter.report_rate_limit(delay)
                                _LOGGER.debug("Rate limited, retrying in %.1fs", delay)
                                await asyncio.sleep(delay)
                                continue
                            raise SunoApiError("Rate limited after maximum retries")
                        if expect_json:
                            if resp.status != 200:
                                raise SunoApiError(f"Suno API returned {resp.status}: {(await resp.text())[:200]}")
                            return await resp.json()
                        return resp.status
                except SunoApiError, SunoAuthError:
                    raise
                except Exception as err:
                    raise SunoApiError(f"Suno API request failed: {err}") from err
            finally:
                if self._rate_limiter:
                    self._rate_limiter.release()
        raise SunoApiError("API request failed after retries")

    async def _api_get(self, path: str) -> Any:
        return await self._api_request("GET", path)

    async def _api_post(self, path: str) -> int:
        return int(await self._api_request("POST", path, expect_json=False))

    async def request_wav(self, clip_id: str) -> None:
        if (status := await self._api_post(f"/api/gen/{clip_id}/convert_wav/")) < 200 or status >= 300:
            raise SunoApiError(f"WAV conversion request failed with status {status}")
        _LOGGER.debug("convert_wav returned %d for %s", status, clip_id)

    async def get_wav_url(self, clip_id: str) -> str | None:
        return (
            data.get("wav_file_url")
            if isinstance(data := await self._api_get(f"/api/gen/{clip_id}/wav_file/"), dict)
            else None
        )
