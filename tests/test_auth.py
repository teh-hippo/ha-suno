"""Tests for Suno Clerk authentication."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from custom_components.suno.auth import ClerkAuth


def _make_clerk_response(*, username: str = "", first_name: str = "", last_name: str = "") -> dict:
    """Build a Clerk /v1/client response with the given user fields."""
    return {
        "response": {
            "last_active_session_id": "sess_test",
            "sessions": [
                {
                    "id": "sess_test",
                    "user": {
                        "id": "user_test_123",
                        "username": username,
                        "first_name": first_name,
                        "last_name": last_name,
                    },
                }
            ],
        }
    }


def _make_token_response(jwt: str = "eyJhbGciOiJSUzI1NiJ9.eyJleHAiOjk5OTk5OTk5OTl9.sig") -> dict:
    """Build a Clerk token response."""
    return {"jwt": jwt}


async def _create_auth_with_user(*, username: str = "", first_name: str = "", last_name: str = "") -> ClerkAuth:
    """Create a ClerkAuth instance and run authentication with mocked responses."""
    session = AsyncMock()

    client_resp = AsyncMock()
    client_resp.status = 200
    client_resp.json = AsyncMock(
        return_value=_make_clerk_response(username=username, first_name=first_name, last_name=last_name)
    )
    client_resp.__aenter__ = AsyncMock(return_value=client_resp)
    client_resp.__aexit__ = AsyncMock(return_value=False)

    token_resp = AsyncMock()
    token_resp.status = 200
    token_resp.json = AsyncMock(return_value=_make_token_response())
    token_resp.__aenter__ = AsyncMock(return_value=token_resp)
    token_resp.__aexit__ = AsyncMock(return_value=False)

    # First call = GET (client), second call = POST (token)
    session.get = MagicMock(return_value=client_resp)
    session.post = MagicMock(return_value=token_resp)

    auth = ClerkAuth(session, "__client=test-cookie")
    await auth.authenticate()
    return auth


async def test_display_name_email_local_part_fallback() -> None:
    """When Clerk username is an email, display_name should be the local part."""
    auth = await _create_auth_with_user(username="user@domain.com")
    assert auth.display_name == "user"


async def test_display_name_apple_relay() -> None:
    """Apple relay email username returns the local part."""
    auth = await _create_auth_with_user(username="yshvq8dp9v@privaterelay.appleid.com")
    assert auth.display_name == "yshvq8dp9v"


async def test_display_name_empty_email_local_part() -> None:
    """When username is '@domain.com', display_name should be None (fallback to 'Suno')."""
    auth = await _create_auth_with_user(username="@domain.com")
    # The local part is empty, so _display_name is set to None
    # The property returns "Suno" as fallback
    assert auth._display_name is None
    assert auth.display_name == "Suno"


async def test_display_name_plain_username() -> None:
    """A plain username (no @) is used directly."""
    auth = await _create_auth_with_user(username="cooluser")
    assert auth.display_name == "cooluser"


async def test_display_name_first_last_name() -> None:
    """First and last name are used when username contains @."""
    auth = await _create_auth_with_user(username="user@example.com", first_name="Alice", last_name="Smith")
    # first_name doesn't contain @, so it takes priority over email fallback
    assert auth.display_name == "Alice Smith"


async def test_display_name_first_name_only() -> None:
    """First name only (no last name) is used when username contains @."""
    auth = await _create_auth_with_user(username="user@example.com", first_name="Alice")
    assert auth.display_name == "Alice"
