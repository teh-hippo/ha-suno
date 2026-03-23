"""Tests for Suno config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.suno.const import (
    CONF_ALL_PLAYLISTS,
    CONF_CACHE_MAX_SIZE,
    CONF_COOKIE,
    CONF_CREATE_PLAYLISTS,
    CONF_DOWNLOAD_ENABLED,
    CONF_DOWNLOAD_MODE_LATEST,
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_LATEST_COUNT,
    CONF_LATEST_DAYS,
    CONF_LATEST_MINIMUM,
    CONF_PLAYLISTS,
    CONF_QUALITY_LATEST,
    CONF_QUALITY_LIKED,
    CONF_QUALITY_PLAYLISTS,
    CONF_SHOW_LATEST,
    CONF_SHOW_LIKED,
    CONF_SHOW_PLAYLISTS,
    DEFAULT_CACHE_MAX_SIZE,
    DEFAULT_DOWNLOAD_ENABLED,
    DEFAULT_DOWNLOAD_MODE,
    DEFAULT_LATEST_COUNT,
    DEFAULT_LATEST_DAYS,
    DEFAULT_LATEST_MINIMUM,
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
    assert result["options"][CONF_DOWNLOAD_ENABLED] is DEFAULT_DOWNLOAD_ENABLED
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
    assert result["options"][CONF_LATEST_MINIMUM] == DEFAULT_LATEST_MINIMUM
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
                CONF_DOWNLOAD_ENABLED: False,
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
    """Options flow Library page has the expected fields with download toggle."""
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
    assert CONF_DOWNLOAD_ENABLED in schema_keys
    assert CONF_DOWNLOAD_PATH in schema_keys
    assert CONF_CACHE_MAX_SIZE in schema_keys
    # create_playlists only shown when download_path is non-empty (default is "")
    assert CONF_CREATE_PLAYLISTS not in schema_keys


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
                CONF_DOWNLOAD_ENABLED: False,
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
    entry = make_entry(options={**make_entry().options, CONF_DOWNLOAD_PATH: "/media/suno"})
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
                CONF_DOWNLOAD_ENABLED: True,
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
            CONF_LATEST_MINIMUM: 10,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CACHE_MAX_SIZE] == 2000
    assert result["data"][CONF_QUALITY_PLAYLISTS] == "standard"
    assert result["data"][CONF_DOWNLOAD_MODE_PLAYLISTS] == "collect"
    assert result["data"][CONF_QUALITY_LIKED] == "high"
    assert result["data"][CONF_DOWNLOAD_MODE_LIKED] == "mirror"
    assert result["data"][CONF_LATEST_COUNT] == 50
    assert result["data"][CONF_LATEST_MINIMUM] == 10


# ── Download path conflict ───────────────────────────────────────────


async def test_download_path_conflict_detected(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Two entries with the same download path should show an error."""
    entry = make_entry(
        options={
            **make_entry().options,
            CONF_DOWNLOAD_PATH: "/media/suno",
        },
    )
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Add a second entry with the same download path
    other = make_entry(unique_id="other-user")
    other_opts = dict(other.options)
    other_opts[CONF_DOWNLOAD_PATH] = "/media/suno"
    other = make_entry(unique_id="other-user", options=other_opts)
    other.add_to_hass(hass)

    flow = hass.config_entries.options._progress[result["flow_id"]]
    with patch.object(type(flow), "_validate_download_path", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_SHOW_PLAYLISTS: True,
                CONF_SHOW_LIKED: True,
                CONF_SHOW_LATEST: True,
                CONF_DOWNLOAD_ENABLED: True,
                CONF_DOWNLOAD_PATH: "/media/suno",
                CONF_CREATE_PLAYLISTS: True,
                CONF_CACHE_MAX_SIZE: DEFAULT_CACHE_MAX_SIZE,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"][CONF_DOWNLOAD_PATH] == "download_path_conflict"


async def test_download_path_no_conflict_different_paths(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Different download paths should not conflict."""
    entry = make_entry(
        options={
            **make_entry().options,
            CONF_DOWNLOAD_PATH: "/media/suno-a",
        },
    )
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    other = make_entry(unique_id="other-user", options={**make_entry().options, CONF_DOWNLOAD_PATH: "/media/suno-b"})
    other.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow = hass.config_entries.options._progress[result["flow_id"]]
    with patch.object(type(flow), "_validate_download_path", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_SHOW_PLAYLISTS: False,
                CONF_SHOW_LIKED: False,
                CONF_SHOW_LATEST: False,
                CONF_DOWNLOAD_ENABLED: True,
                CONF_DOWNLOAD_PATH: "/media/suno-a",
                CONF_CREATE_PLAYLISTS: True,
                CONF_CACHE_MAX_SIZE: DEFAULT_CACHE_MAX_SIZE,
            },
        )

    # No conflict — should proceed (CREATE_ENTRY because all toggles are off)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_download_path_self_reference_no_conflict(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Same entry re-saving its own path should not conflict."""
    entry = make_entry(
        options={
            **make_entry().options,
            CONF_DOWNLOAD_PATH: "/media/suno",
        },
    )
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow = hass.config_entries.options._progress[result["flow_id"]]
    with patch.object(type(flow), "_validate_download_path", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_SHOW_PLAYLISTS: False,
                CONF_SHOW_LIKED: False,
                CONF_SHOW_LATEST: False,
                CONF_DOWNLOAD_ENABLED: True,
                CONF_DOWNLOAD_PATH: "/media/suno",
                CONF_CREATE_PLAYLISTS: True,
                CONF_CACHE_MAX_SIZE: DEFAULT_CACHE_MAX_SIZE,
            },
        )

    # Should succeed — own entry path is excluded from conflict check
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_dynamic_title_from_auth(hass: HomeAssistant, mock_setup_entry: AsyncMock) -> None:
    """Config entry title uses auth.display_name from the flow."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_client = _make_flow_client(
        authenticate=AsyncMock(return_value=MOCK_USER_ID),
        display_name="CoolArtist",
    )

    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: MOCK_COOKIE},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "CoolArtist"
