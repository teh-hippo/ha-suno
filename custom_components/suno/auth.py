"""Clerk authentication for the Suno integration.

Handles cookie-based Clerk auth, session management, and JWT lifecycle.
Cookie is sent only to clerk.suno.com.  The Suno API receives short-lived JWTs.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

from aiohttp import ClientSession

from .const import (
    CLERK_BASE_URL,
    CLERK_JS_VERSION,
    CLERK_TOKEN_JS_VERSION,
    JWT_REFRESH_BUFFER,
)
from .exceptions import SunoAuthError, SunoConnectionError
from .models import SunoUser

_LOGGER = logging.getLogger(__name__)


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


class ClerkAuth:
    """Manages Clerk cookie authentication and JWT lifecycle."""

    def __init__(self, session: ClientSession, token: str) -> None:
        self._session = session
        self._cookie = _normalise_token(token)
        self._jwt: str | None = None
        self._jwt_exp: int = 0
        self._jwt_lock = asyncio.Lock()
        self._session_id: str | None = None
        self._user_id: str | None = None
        self._display_name: str | None = None

    @property
    def jwt(self) -> str | None:
        """Return the current JWT token."""
        return self._jwt

    @property
    def user_id(self) -> str | None:
        """The Suno user ID from the Clerk session."""
        return self._user_id

    @property
    def display_name(self) -> str:
        """Return the user's display name."""
        return self._display_name or "Suno"

    @property
    def user(self) -> SunoUser:
        """Return a SunoUser for the authenticated user."""
        return SunoUser(
            id=self._user_id or "",
            display_name=self.display_name,
        )

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

    async def ensure_jwt(self) -> str:
        """Return a valid JWT, refreshing if needed."""
        async with self._jwt_lock:
            now = int(time.time())
            if not self._jwt or now >= (self._jwt_exp - JWT_REFRESH_BUFFER):
                await self._refresh_jwt()
            if not self._jwt:
                msg = "Failed to obtain JWT"
                raise SunoAuthError(msg)
            return self._jwt

    async def _clerk_request(self, method: str, url: str) -> dict[str, Any]:
        """Make a Clerk API request with standard error handling."""
        try:
            req = self._session.get if method == "GET" else self._session.post
            async with req(url, headers={"Cookie": self._cookie}) as resp:
                if resp.status != 200:
                    msg = f"Clerk request failed with status {resp.status}"
                    raise SunoAuthError(msg)
                result: dict[str, Any] = await resp.json()
                return result
        except SunoAuthError:
            raise
        except Exception as err:
            raise SunoConnectionError(f"Could not connect to Clerk: {err}") from err

    async def _get_session_id(self) -> None:
        """Get a Clerk session ID using the browser cookie."""
        url = f"{CLERK_BASE_URL}/v1/client?_clerk_js_version={CLERK_JS_VERSION}"
        _LOGGER.debug("Fetching Clerk session ID")
        data = await self._clerk_request("GET", url)

        response = data.get("response")
        if not response:
            msg = "Invalid Clerk response.  Cookie may be expired."
            raise SunoAuthError(msg)

        self._session_id = response.get("last_active_session_id")
        if not self._session_id:
            msg = "No active session found.  Cookie may be expired."
            raise SunoAuthError(msg)

        for session in response.get("sessions", []):
            if session.get("id") == self._session_id:
                user = session.get("user", {})
                self._user_id = user.get("id")
                first = (user.get("first_name") or "").strip()
                last = (user.get("last_name") or "").strip()
                username = (user.get("username") or "").strip()

                if username and "@" not in username:
                    self._display_name = username
                elif first and "@" not in first:
                    self._display_name = f"{first} {last}".strip() if last else first
                else:
                    self._display_name = None
                break

    async def _refresh_jwt(self) -> None:
        """Get a fresh JWT from Clerk using the session ID."""
        if not self._session_id:
            await self._get_session_id()

        url = (
            f"{CLERK_BASE_URL}/v1/client/sessions/{self._session_id}/tokens?_clerk_js_version={CLERK_TOKEN_JS_VERSION}"
        )
        data = await self._clerk_request("POST", url)

        jwt = data.get("jwt")
        if not jwt:
            msg = "No JWT in Clerk token response"
            raise SunoAuthError(msg)

        self._jwt = jwt
        self._jwt_exp = _decode_jwt_exp(jwt)
        _LOGGER.debug("JWT refreshed, expires at %d", self._jwt_exp)
