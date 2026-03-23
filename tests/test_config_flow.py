"""Tests for Suno config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.suno.const import (
    CONF_ALL_PLAYLISTS,
    CONF_CACHE_MAX_SIZE,
    CONF_COOKIE,
    CONF_CREATE_PLAYLISTS,
    CONF_DOWNLOAD_MODE_LATEST,
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_LATEST_COUNT,
    CONF_LATEST_DAYS,
    CONF_PLAYLISTS,
    CONF_QUALITY_LATEST,
    CONF_QUALITY_LIKED,
    CONF_QUALITY_PLAYLISTS,
    CONF_SHOW_LATEST,
    CONF_SHOW_LIKED,
    CONF_SHOW_PLAYLISTS,
    DEFAULT_CACHE_MAX_SIZE,
    DEFAULT_DOWNLOAD_MODE,
    DEFAULT_LATEST_COUNT,
    DEFAULT_LATEST_DAYS,
    DOMAIN,
    QUALITY_HIGH,
    QUALITY_STANDARD,
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
    assert result["options"][CONF_SHOW_LIKED] is True
    assert result["options"][CONF_SHOW_LATEST] is True
    assert result["options"][CONF_SHOW_PLAYLISTS] is True
    assert result["options"][CONF_DOWNLOAD_PATH] == ""
    assert result["options"][CONF_CREATE_PLAYLISTS] is True
    assert result["options"][CONF_CACHE_MAX_SIZE] == DEFAULT_CACHE_MAX_SIZE
    assert result["options"][CONF_QUALITY_LIKED] == QUALITY_HIGH
    assert result["options"][CONF_QUALITY_PLAYLISTS] == QUALITY_HIGH
    assert result["options"][CONF_QUALITY_LATEST] == QUALITY_STANDARD
    assert result["options"][CONF_DOWNLOAD_MODE_LIKED] == DEFAULT_DOWNLOAD_MODE
    assert result["options"][CONF_DOWNLOAD_MODE_PLAYLISTS] == DEFAULT_DOWNLOAD_MODE
    assert result["options"][CONF_DOWNLOAD_MODE_LATEST] == DEFAULT_DOWNLOAD_MODE
    assert result["options"][CONF_LATEST_COUNT] == DEFAULT_LATEST_COUNT
    assert result["options"][CONF_LATEST_DAYS] == DEFAULT_LATEST_DAYS
    assert result["options"][CONF_ALL_PLAYLISTS] is True
    assert result["options"][CONF_PLAYLISTS] == []


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
                CONF_SHOW_LATEST: False,
                CONF_SHOW_PLAYLISTS: False,
                CONF_DOWNLOAD_PATH: "",
                CONF_CREATE_PLAYLISTS: False,
                CONF_CACHE_MAX_SIZE: 1000,
            },
        )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.options[CONF_SHOW_LIKED] is False
    assert entry.options[CONF_SHOW_LATEST] is False
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


async def test_options_flow_library_step(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Options flow Library page has the 6 expected fields."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    schema_keys = {str(k) for k in result["data_schema"].schema}
    assert CONF_SHOW_PLAYLISTS in schema_keys
    assert CONF_SHOW_LIKED in schema_keys
    assert CONF_SHOW_LATEST in schema_keys
    assert CONF_DOWNLOAD_PATH in schema_keys
    assert CONF_CREATE_PLAYLISTS in schema_keys
    assert CONF_CACHE_MAX_SIZE in schema_keys


async def test_options_flow_conditional_routing(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Only enabled content types get per-source config pages."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Disable all content types
    with patch.object(
        type(hass.config_entries.options._progress[result["flow_id"]]),
        "_validate_download_path",
        return_value=True,
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_SHOW_PLAYLISTS: False,
                CONF_SHOW_LIKED: False,
                CONF_SHOW_LATEST: False,
                CONF_DOWNLOAD_PATH: "",
                CONF_CREATE_PLAYLISTS: True,
                CONF_CACHE_MAX_SIZE: DEFAULT_CACHE_MAX_SIZE,
            },
        )

    # Should skip straight to CREATE_ENTRY (no playlists/liked/latest pages)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SHOW_PLAYLISTS] is False
    assert result["data"][CONF_SHOW_LIKED] is False
    assert result["data"][CONF_SHOW_LATEST] is False


async def test_options_flow_saves(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Options flow saves updated values across steps."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Step 1: Library page (all content types enabled)
    with patch.object(
        type(hass.config_entries.options._progress[result["flow_id"]]),
        "_validate_download_path",
        return_value=True,
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_SHOW_LIKED: True,
                CONF_SHOW_LATEST: True,
                CONF_SHOW_PLAYLISTS: True,
                CONF_DOWNLOAD_PATH: "/media/suno",
                CONF_CREATE_PLAYLISTS: True,
                CONF_CACHE_MAX_SIZE: 2000,
            },
        )

    # Should advance to playlists step
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "playlists"

    # Step 2: Playlists config
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_QUALITY_PLAYLISTS: "standard",
            CONF_DOWNLOAD_MODE_PLAYLISTS: "collect",
            CONF_ALL_PLAYLISTS: True,
        },
    )
    # Should advance to liked step
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "liked"

    # Step 3: Liked config
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_QUALITY_LIKED: "high",
            CONF_DOWNLOAD_MODE_LIKED: "mirror",
        },
    )
    # Should advance to latest step
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "latest"

    # Step 4: Latest config
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_QUALITY_LATEST: "standard",
            CONF_DOWNLOAD_MODE_LATEST: "mirror",
            CONF_LATEST_COUNT: 50,
            CONF_LATEST_DAYS: 14,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CACHE_MAX_SIZE] == 2000
    assert result["data"][CONF_QUALITY_PLAYLISTS] == "standard"
    assert result["data"][CONF_DOWNLOAD_MODE_PLAYLISTS] == "collect"
    assert result["data"][CONF_QUALITY_LIKED] == "high"
    assert result["data"][CONF_DOWNLOAD_MODE_LIKED] == "mirror"
    assert result["data"][CONF_LATEST_COUNT] == 50


# ── Migration ────────────────────────────────────────────────────────


async def test_migration_v1_to_v2(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Migrating a v1 entry adds per-source quality/mode defaults and renames keys."""
    from custom_components.suno import async_migrate_entry

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
    assert entry.version == 3  # Migrates through v2 to v3

    opts = entry.options
    # Per-source quality defaults added
    assert opts[CONF_QUALITY_LIKED] == QUALITY_HIGH
    assert opts[CONF_QUALITY_PLAYLISTS] == QUALITY_HIGH
    assert opts[CONF_QUALITY_LATEST] == QUALITY_STANDARD
    # Per-source mode defaults added (v2 sets "mirror" as default)
    assert opts[CONF_DOWNLOAD_MODE_LIKED] == "mirror"
    assert opts[CONF_DOWNLOAD_MODE_PLAYLISTS] == "mirror"
    assert opts[CONF_DOWNLOAD_MODE_LATEST] == "mirror"
    # Keys renamed from recent → latest via v2 then v3
    assert "sync_recent_count" not in opts
    assert "sync_recent_days" not in opts
    assert opts[CONF_LATEST_COUNT] == 25
    assert opts[CONF_LATEST_DAYS] == 7
    # Float → int coercion
    assert isinstance(opts[CONF_LATEST_COUNT], int)


async def test_migration_v2_to_v3(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Migrating a v2 entry renames sync→download keys, maps mode values, guards sync_enabled."""
    from custom_components.suno import async_migrate_entry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Suno",
        unique_id=MOCK_USER_ID,
        data={CONF_COOKIE: MOCK_COOKIE},
        options={
            "show_liked": True,
            "show_playlists": True,
            "show_recent": True,
            "sync_path": "/media/suno",
            "sync_enabled": True,
            "sync_liked": True,
            "sync_mode_liked": "sync",
            "sync_mode_playlists": "copy",
            "sync_mode_latest": "sync",
            "sync_quality_liked": "high",
            "sync_quality_playlists": "standard",
            "sync_quality_latest": "standard",
            "sync_latest_count": 25,
            "sync_latest_days": 7,
            "sync_all_playlists": True,
            "sync_playlists": ["pl-1"],
            "sync_playlists_m3u": True,
            "audio_quality": "high",
            "cache_ttl_minutes": 30,
            "cache_enabled": True,
            "cache_max_size_mb": 500,
            "quality_liked": "high",
            "quality_playlists": "high",
            "quality_latest": "standard",
            "download_mode_liked": "mirror",
            "download_mode_playlists": "mirror",
            "download_mode_latest": "mirror",
        },
        version=2,
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)
    assert result is True
    assert entry.version == 3

    opts = entry.options
    # Key renames
    assert "sync_path" not in opts
    assert opts[CONF_DOWNLOAD_PATH] == "/media/suno"
    assert opts["show_latest"] is True  # show_recent → show_latest
    assert "show_recent" not in opts

    # Mode value renames: sync→mirror, copy→collect
    assert opts[CONF_DOWNLOAD_MODE_LIKED] == "mirror"
    assert opts[CONF_DOWNLOAD_MODE_PLAYLISTS] == "collect"
    assert opts[CONF_DOWNLOAD_MODE_LATEST] == "mirror"

    # Quality keys renamed
    assert opts[CONF_QUALITY_LIKED] == "high"
    assert opts[CONF_QUALITY_PLAYLISTS] == "standard"

    # Latest keys renamed
    assert opts[CONF_LATEST_COUNT] == 25
    assert opts[CONF_LATEST_DAYS] == 7
    assert opts["all_playlists"] is True
    assert opts["playlists"] == ["pl-1"]
    assert opts["create_playlists"] is True

    # Deprecated keys removed
    assert "audio_quality" not in opts
    assert "cache_ttl_minutes" not in opts
    assert "cache_enabled" not in opts
    assert "sync_enabled" not in opts
    assert "sync_liked" not in opts


async def test_migration_v2_to_v3_sync_disabled_guard(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """V2→V3: sync_enabled=False should remove download_path to avoid accidental activation."""
    from custom_components.suno import async_migrate_entry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Suno",
        unique_id=MOCK_USER_ID,
        data={CONF_COOKIE: MOCK_COOKIE},
        options={
            "sync_enabled": False,
            "sync_path": "/old/path",
            "cache_max_size_mb": 500,
            "quality_liked": "high",
            "quality_playlists": "high",
            "quality_latest": "standard",
            "download_mode_liked": "mirror",
            "download_mode_playlists": "mirror",
            "download_mode_latest": "mirror",
        },
        version=2,
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)
    assert result is True
    assert entry.version == 3

    opts = entry.options
    # sync_enabled=False means download_path should be removed
    assert CONF_DOWNLOAD_PATH not in opts or opts.get(CONF_DOWNLOAD_PATH, "") == ""
