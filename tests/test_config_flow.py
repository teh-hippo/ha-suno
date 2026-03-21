"""Tests for Suno config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.suno.const import (
    CONF_AUDIO_QUALITY,
    CONF_CACHE_ENABLED,
    CONF_CACHE_MAX_SIZE,
    CONF_CACHE_TTL,
    CONF_COOKIE,
    CONF_RECENT_COUNT,
    CONF_SHOW_LIKED,
    CONF_SHOW_PLAYLISTS,
    CONF_SHOW_RECENT,
    DEFAULT_AUDIO_QUALITY,
    DEFAULT_CACHE_ENABLED,
    DEFAULT_CACHE_MAX_SIZE,
    DEFAULT_CACHE_TTL,
    DEFAULT_RECENT_COUNT,
    DEFAULT_SHOW_LIKED,
    DEFAULT_SHOW_PLAYLISTS,
    DEFAULT_SHOW_RECENT,
    DOMAIN,
)
from custom_components.suno.exceptions import SunoAuthError

from .conftest import MOCK_COOKIE, MOCK_USER_ID, make_entry, setup_entry


def _patch_client(mock_client: AsyncMock):
    """Patch SunoClient at the config_flow import path."""
    return patch("custom_components.suno.config_flow.SunoClient", return_value=mock_client)


# ── User flow ────────────────────────────────────────────────────────


async def test_user_flow_shows_form(hass: HomeAssistant) -> None:
    """Initialising the user flow shows the cookie form."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_flow_success(hass: HomeAssistant, mock_setup_entry: AsyncMock) -> None:
    """Test successful user config flow."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_client = AsyncMock()
    mock_client.authenticate = AsyncMock(return_value=MOCK_USER_ID)
    mock_client.get_feed = AsyncMock(return_value=[])

    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: MOCK_COOKIE},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Suno"
    assert result["data"][CONF_COOKIE] == MOCK_COOKIE
    assert result["options"][CONF_SHOW_LIKED] == DEFAULT_SHOW_LIKED
    assert result["options"][CONF_SHOW_RECENT] == DEFAULT_SHOW_RECENT
    assert result["options"][CONF_RECENT_COUNT] == DEFAULT_RECENT_COUNT
    assert result["options"][CONF_SHOW_PLAYLISTS] == DEFAULT_SHOW_PLAYLISTS
    assert result["options"][CONF_CACHE_TTL] == DEFAULT_CACHE_TTL
    assert result["options"][CONF_AUDIO_QUALITY] == DEFAULT_AUDIO_QUALITY
    assert result["options"][CONF_CACHE_ENABLED] == DEFAULT_CACHE_ENABLED
    assert result["options"][CONF_CACHE_MAX_SIZE] == DEFAULT_CACHE_MAX_SIZE


async def test_user_flow_invalid_cookie(hass: HomeAssistant) -> None:
    """Test config flow with invalid cookie."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_client = AsyncMock()
    mock_client.authenticate = AsyncMock(side_effect=SunoAuthError("Bad cookie"))

    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: "bad-cookie"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_cookie"


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    """Test config flow with connection error."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_client = AsyncMock()
    mock_client.authenticate = AsyncMock(side_effect=aiohttp.ClientError("Connection refused"))

    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: "some-cookie"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_user_flow_cannot_connect_timeout(hass: HomeAssistant) -> None:
    """Test config flow with timeout error."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_client = AsyncMock()
    mock_client.authenticate = AsyncMock(side_effect=TimeoutError("Timed out"))

    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: "some-cookie"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_user_flow_unknown_error(hass: HomeAssistant) -> None:
    """Test config flow with unexpected exception."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_client = AsyncMock()
    mock_client.authenticate = AsyncMock(side_effect=RuntimeError("Boom"))

    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: "some-cookie"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "unknown"


async def test_user_flow_duplicate_entry(hass: HomeAssistant, mock_setup_entry: AsyncMock) -> None:
    """Duplicate user ID aborts with already_configured."""
    # Create an existing entry with same unique_id
    existing = make_entry()
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_client = AsyncMock()
    mock_client.authenticate = AsyncMock(return_value=MOCK_USER_ID)
    mock_client.get_feed = AsyncMock(return_value=[])

    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: "new-cookie"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ── Reauth flow ──────────────────────────────────────────────────────


async def test_reauth_flow_success(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Reauth flow with valid new cookie succeeds."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    mock_client = AsyncMock()
    mock_client.authenticate = AsyncMock(return_value=MOCK_USER_ID)
    mock_client.get_feed = AsyncMock(return_value=[])

    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: "new-fresh-cookie"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_COOKIE] == "new-fresh-cookie"


async def test_reauth_flow_invalid_cookie(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Reauth flow with bad cookie shows error."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    result = await entry.start_reauth_flow(hass)

    mock_client = AsyncMock()
    mock_client.authenticate = AsyncMock(side_effect=SunoAuthError("Expired"))

    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: "expired-cookie"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_cookie"


async def test_reauth_flow_cannot_connect(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Reauth flow with connection error shows cannot_connect."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    result = await entry.start_reauth_flow(hass)

    mock_client = AsyncMock()
    mock_client.authenticate = AsyncMock(side_effect=aiohttp.ClientError("Connection refused"))

    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: "some-cookie"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_reauth_flow_unknown_error(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Reauth flow with unexpected error shows unknown."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    result = await entry.start_reauth_flow(hass)

    mock_client = AsyncMock()
    mock_client.authenticate = AsyncMock(side_effect=RuntimeError("Boom"))

    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: "some-cookie"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "unknown"


# ── Reconfigure flow ─────────────────────────────────────────────────


async def test_reconfigure_flow_shows_form(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Reconfigure flow shows form with current options."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"


async def test_reconfigure_flow_updates_options(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Reconfigure flow updates options and reloads."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    result = await entry.start_reconfigure_flow(hass)

    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_SHOW_LIKED: False,
                CONF_SHOW_RECENT: False,
                CONF_RECENT_COUNT: 10,
                CONF_SHOW_PLAYLISTS: False,
                CONF_CACHE_TTL: 60,
                CONF_AUDIO_QUALITY: "high",
                CONF_CACHE_ENABLED: True,
                CONF_CACHE_MAX_SIZE: 1000,
            },
        )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.options[CONF_SHOW_LIKED] is False
    assert entry.options[CONF_CACHE_TTL] == 60
    assert entry.options[CONF_AUDIO_QUALITY] == "high"
    assert entry.options[CONF_CACHE_ENABLED] is True
    assert entry.options[CONF_CACHE_MAX_SIZE] == 1000


# ── Options flow ─────────────────────────────────────────────────────


async def test_options_flow_shows_form(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Options flow shows form with current values."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_options_flow_saves(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Options flow saves updated values across steps."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    # Step 1: media browser + cache
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SHOW_LIKED: False,
            CONF_SHOW_RECENT: True,
            CONF_RECENT_COUNT: 30,
            CONF_SHOW_PLAYLISTS: True,
            CONF_CACHE_TTL: 15,
            CONF_AUDIO_QUALITY: "high",
            CONF_CACHE_ENABLED: True,
            CONF_CACHE_MAX_SIZE: 2000,
        },
    )
    # Should advance to sync step
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "sync"

    # Step 2: sync options (all defaults, sync disabled)
    from custom_components.suno.const import (
        CONF_SYNC_ALL_PLAYLISTS,
        CONF_SYNC_ENABLED,
        CONF_SYNC_LIKED,
        CONF_SYNC_PATH,
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SYNC_ENABLED: False,
            CONF_SYNC_PATH: "/media/suno",
            CONF_SYNC_LIKED: True,
            CONF_SYNC_ALL_PLAYLISTS: True,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SHOW_LIKED] is False
    assert result["data"][CONF_RECENT_COUNT] == 30
    assert result["data"][CONF_CACHE_TTL] == 15
    assert result["data"][CONF_AUDIO_QUALITY] == "high"
    assert result["data"][CONF_CACHE_ENABLED] is True
    assert result["data"][CONF_CACHE_MAX_SIZE] == 2000
    assert result["data"][CONF_SYNC_ENABLED] is False
