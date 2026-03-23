"""Tests for Suno config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

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

from .conftest import MOCK_COOKIE, MOCK_USER_ID, make_entry, patch_suno_setup, setup_entry


def _patch_client(mock_client: AsyncMock):
    """Patch ClerkAuth at the config_flow import path."""
    mock_auth = mock_client._auth
    return patch("custom_components.suno.config_flow.ClerkAuth", return_value=mock_auth)


def _make_flow_client(**auth_kwargs) -> AsyncMock:
    """Create a mock client for config flow tests with _auth set up."""
    from .conftest import _make_mock_auth  # noqa: PLC0415

    mock = AsyncMock()
    mock._auth = _make_mock_auth()
    for key, value in auth_kwargs.items():
        setattr(mock._auth, key, value)
    return mock


# ── User flow ────────────────────────────────────────────────────────


async def test_user_flow_shows_form(hass: HomeAssistant) -> None:
    """Initialising the user flow shows the cookie form."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_flow_success(hass: HomeAssistant, mock_setup_entry: AsyncMock) -> None:
    """Test successful user config flow."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_client = _make_flow_client(authenticate=AsyncMock(return_value=MOCK_USER_ID))
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

    mock_client = _make_flow_client(authenticate=AsyncMock(side_effect=SunoAuthError("Bad cookie")))

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

    mock_client = _make_flow_client(authenticate=AsyncMock(side_effect=aiohttp.ClientError("Connection refused")))

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

    mock_client = _make_flow_client(authenticate=AsyncMock(side_effect=TimeoutError("Timed out")))

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

    mock_client = _make_flow_client(authenticate=AsyncMock(side_effect=RuntimeError("Boom")))

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

    mock_client = _make_flow_client(authenticate=AsyncMock(return_value=MOCK_USER_ID))
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
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    mock_client = _make_flow_client(authenticate=AsyncMock(return_value=MOCK_USER_ID))
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
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    result = await entry.start_reauth_flow(hass)

    mock_client = _make_flow_client(authenticate=AsyncMock(side_effect=SunoAuthError("Expired")))

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
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    result = await entry.start_reauth_flow(hass)

    mock_client = _make_flow_client(authenticate=AsyncMock(side_effect=aiohttp.ClientError("Connection refused")))

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
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    result = await entry.start_reauth_flow(hass)

    mock_client = _make_flow_client(authenticate=AsyncMock(side_effect=RuntimeError("Boom")))

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
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"


async def test_reconfigure_flow_updates_options(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Reconfigure flow updates options and reloads."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    result = await entry.start_reconfigure_flow(hass)

    with patch_suno_setup(mock_suno_client):
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
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_options_flow_saves(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Options flow saves updated values across steps."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
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
        CONF_SYNC_ENABLED,
        CONF_SYNC_PATH,
        CONF_SYNC_PLAYLISTS_M3U,
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SYNC_ENABLED: False,
            CONF_SYNC_PATH: "/media/suno",
            CONF_SYNC_PLAYLISTS_M3U: False,
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


# ── Migration ────────────────────────────────────────────────────────


async def test_migration_v1_to_v2(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Migrating a v1 entry adds per-source quality/mode defaults and renames keys."""
    from custom_components.suno import async_migrate_entry
    from custom_components.suno.const import (
        CONF_SYNC_LATEST_COUNT,
        CONF_SYNC_LATEST_DAYS,
        CONF_SYNC_MODE_LATEST,
        CONF_SYNC_MODE_LIKED,
        CONF_SYNC_MODE_PLAYLISTS,
        CONF_SYNC_QUALITY_LATEST,
        CONF_SYNC_QUALITY_LIKED,
        CONF_SYNC_QUALITY_PLAYLISTS,
        QUALITY_HIGH,
        QUALITY_STANDARD,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Suno",
        unique_id=MOCK_USER_ID,
        data={CONF_COOKIE: MOCK_COOKIE},
        options={
            **make_entry().options,
            "sync_recent_count": 25.0,
            "sync_recent_days": 7.0,
            "cache_ttl_minutes": 30.0,
        },
        version=1,
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)
    assert result is True
    assert entry.version == 2

    opts = entry.options
    # Per-source quality defaults added
    assert opts[CONF_SYNC_QUALITY_LIKED] == QUALITY_HIGH
    assert opts[CONF_SYNC_QUALITY_PLAYLISTS] == QUALITY_HIGH
    assert opts[CONF_SYNC_QUALITY_LATEST] == QUALITY_STANDARD
    # Per-source mode defaults added
    assert opts[CONF_SYNC_MODE_LIKED] == "sync"
    assert opts[CONF_SYNC_MODE_PLAYLISTS] == "sync"
    assert opts[CONF_SYNC_MODE_LATEST] == "sync"
    # Keys renamed from recent → latest
    assert "sync_recent_count" not in opts
    assert "sync_recent_days" not in opts
    assert opts[CONF_SYNC_LATEST_COUNT] == 25
    assert opts[CONF_SYNC_LATEST_DAYS] == 7
    # Float → int coercion
    assert opts["cache_ttl_minutes"] == 30
    assert isinstance(opts["cache_ttl_minutes"], int)


# ── Sync sources step ───────────────────────────────────────────────


async def test_options_sync_sources_step(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Options flow with sync enabled goes through sync_sources step."""
    from custom_components.suno.const import (
        CONF_SYNC_ALL_PLAYLISTS,
        CONF_SYNC_ENABLED,
        CONF_SYNC_LATEST_COUNT,
        CONF_SYNC_LIKED,
        CONF_SYNC_MODE_LATEST,
        CONF_SYNC_MODE_LIKED,
        CONF_SYNC_MODE_PLAYLISTS,
        CONF_SYNC_PATH,
        CONF_SYNC_PLAYLISTS_M3U,
        CONF_SYNC_QUALITY_LATEST,
        CONF_SYNC_QUALITY_LIKED,
        CONF_SYNC_QUALITY_PLAYLISTS,
    )

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Step 1: display + cache
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SHOW_LIKED: True,
            CONF_SHOW_RECENT: True,
            CONF_RECENT_COUNT: 20,
            CONF_SHOW_PLAYLISTS: True,
            CONF_CACHE_TTL: 30,
            CONF_AUDIO_QUALITY: "standard",
            CONF_CACHE_ENABLED: False,
            CONF_CACHE_MAX_SIZE: 500,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "sync"

    # Step 2: sync general (enabled)
    with patch.object(
        type(hass.config_entries.options._progress[result["flow_id"]]),
        "_validate_sync_path",
        return_value=True,
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_SYNC_ENABLED: True,
                CONF_SYNC_PATH: "/media/suno",
                CONF_SYNC_PLAYLISTS_M3U: True,
            },
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "sync_sources"

    # Step 3: sync sources
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SYNC_LIKED: True,
            CONF_SYNC_QUALITY_LIKED: "high",
            CONF_SYNC_MODE_LIKED: "sync",
            CONF_SYNC_ALL_PLAYLISTS: True,
            CONF_SYNC_QUALITY_PLAYLISTS: "standard",
            CONF_SYNC_MODE_PLAYLISTS: "copy",
            CONF_SYNC_LATEST_COUNT: 50.0,
            CONF_SYNC_QUALITY_LATEST: "standard",
            CONF_SYNC_MODE_LATEST: "sync",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SYNC_ENABLED] is True
    assert result["data"][CONF_SYNC_QUALITY_LIKED] == "high"
    assert result["data"][CONF_SYNC_QUALITY_PLAYLISTS] == "standard"
    assert result["data"][CONF_SYNC_MODE_PLAYLISTS] == "copy"
    assert result["data"][CONF_SYNC_PLAYLISTS_M3U] is True
