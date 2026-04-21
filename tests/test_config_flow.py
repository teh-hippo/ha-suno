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
    CONF_DOWNLOAD_MODE_LIKED,
    CONF_DOWNLOAD_MODE_MY_SONGS,
    CONF_DOWNLOAD_MODE_PLAYLISTS,
    CONF_DOWNLOAD_PATH,
    CONF_MY_SONGS_COUNT,
    CONF_MY_SONGS_DAYS,
    CONF_MY_SONGS_MINIMUM,
    CONF_PLAYLISTS,
    CONF_QUALITY_LIKED,
    CONF_QUALITY_MY_SONGS,
    CONF_QUALITY_PLAYLISTS,
    CONF_SHOW_LIKED,
    CONF_SHOW_MY_SONGS,
    CONF_SHOW_PLAYLISTS,
    DEFAULT_CACHE_MAX_SIZE,
    DEFAULT_DOWNLOAD_MODE,
    DEFAULT_DOWNLOAD_MODE_MY_SONGS,
    DEFAULT_MY_SONGS_COUNT,
    DEFAULT_MY_SONGS_DAYS,
    DEFAULT_MY_SONGS_MINIMUM,
    DOMAIN,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from custom_components.suno.exceptions import SunoAuthError, SunoConnectionError

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
    assert result["options"][CONF_SHOW_MY_SONGS] is True
    assert result["options"][CONF_SHOW_PLAYLISTS] is True
    assert result["options"][CONF_DOWNLOAD_PATH] == ""
    assert result["options"][CONF_CREATE_PLAYLISTS] is True
    assert result["options"][CONF_CACHE_MAX_SIZE] == DEFAULT_CACHE_MAX_SIZE
    assert result["options"][CONF_QUALITY_LIKED] == QUALITY_HIGH
    assert result["options"][CONF_QUALITY_PLAYLISTS] == QUALITY_HIGH
    assert result["options"][CONF_QUALITY_MY_SONGS] == QUALITY_STANDARD
    assert result["options"][CONF_DOWNLOAD_MODE_LIKED] == DEFAULT_DOWNLOAD_MODE
    assert result["options"][CONF_DOWNLOAD_MODE_PLAYLISTS] == DEFAULT_DOWNLOAD_MODE
    assert result["options"][CONF_DOWNLOAD_MODE_MY_SONGS] == DEFAULT_DOWNLOAD_MODE_MY_SONGS
    assert result["options"][CONF_MY_SONGS_COUNT] == DEFAULT_MY_SONGS_COUNT
    assert result["options"][CONF_MY_SONGS_DAYS] == DEFAULT_MY_SONGS_DAYS
    assert result["options"][CONF_MY_SONGS_MINIMUM] == DEFAULT_MY_SONGS_MINIMUM
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
                CONF_SHOW_MY_SONGS: False,
                CONF_SHOW_PLAYLISTS: False,
                CONF_CACHE_MAX_SIZE: 1000,
            },
        )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.options[CONF_SHOW_LIKED] is False
    assert entry.options[CONF_SHOW_MY_SONGS] is False
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
    assert CONF_SHOW_MY_SONGS in schema_keys
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
                CONF_SHOW_MY_SONGS: False,
                CONF_CACHE_MAX_SIZE: DEFAULT_CACHE_MAX_SIZE,
            },
        )

    # Should skip straight to CREATE_ENTRY (no playlists/liked/my_songs pages)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SHOW_PLAYLISTS] is False
    assert result["data"][CONF_SHOW_LIKED] is False
    assert result["data"][CONF_SHOW_MY_SONGS] is False


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
                CONF_SHOW_MY_SONGS: True,
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
            CONF_DOWNLOAD_MODE_PLAYLISTS: "archive",
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
    # Should advance to my_songs step
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "my_songs"

    # Step 4: My Songs config
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_QUALITY_MY_SONGS: "standard",
            CONF_DOWNLOAD_MODE_MY_SONGS: "mirror",
            CONF_MY_SONGS_COUNT: 50,
            CONF_MY_SONGS_DAYS: 14,
            CONF_MY_SONGS_MINIMUM: 10,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CACHE_MAX_SIZE] == 2000
    assert result["data"][CONF_QUALITY_PLAYLISTS] == "standard"
    assert result["data"][CONF_DOWNLOAD_MODE_PLAYLISTS] == "archive"
    assert result["data"][CONF_QUALITY_LIKED] == "high"
    assert result["data"][CONF_DOWNLOAD_MODE_LIKED] == "mirror"
    assert result["data"][CONF_MY_SONGS_COUNT] == 50
    assert result["data"][CONF_MY_SONGS_MINIMUM] == 10


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
                CONF_SHOW_MY_SONGS: True,
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
                CONF_SHOW_MY_SONGS: False,
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
                CONF_SHOW_MY_SONGS: False,
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


# ── Download path validation ─────────────────────────────────────────


async def test_validate_download_path_permission_error(hass: HomeAssistant, tmp_path) -> None:
    """PermissionError during path validation returns False."""
    from custom_components.suno.config_flow import SunoOptionsFlow

    flow = SunoOptionsFlow.__new__(SunoOptionsFlow)
    flow.hass = hass

    # Use a path that will trigger PermissionError on mkdir
    bad_path = str(tmp_path / "forbidden_dir")
    with patch("pathlib.Path.mkdir", side_effect=PermissionError("denied")):
        result = await flow._validate_download_path(bad_path)

    assert result is False


async def test_validate_download_path_oserror(hass: HomeAssistant, tmp_path) -> None:
    """OSError during path validation returns False."""
    from custom_components.suno.config_flow import SunoOptionsFlow

    flow = SunoOptionsFlow.__new__(SunoOptionsFlow)
    flow.hass = hass

    bad_path = str(tmp_path / "broken_dir")
    with patch("pathlib.Path.mkdir", side_effect=OSError("disk error")):
        result = await flow._validate_download_path(bad_path)

    assert result is False


# ── Default mode tests ───────────────────────────────────────────────


async def test_my_songs_default_mode_is_cache(hass: HomeAssistant, mock_setup_entry) -> None:
    """Verify My Songs section defaults to Cache Only mode."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_client = _make_flow_client(authenticate=AsyncMock(return_value=MOCK_USER_ID))
    mock_client.get_feed = AsyncMock(return_value=[])

    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: MOCK_COOKIE},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_DOWNLOAD_MODE_MY_SONGS] == DEFAULT_DOWNLOAD_MODE_MY_SONGS
    assert result["options"][CONF_DOWNLOAD_MODE_MY_SONGS] == "cache"


async def test_playlists_liked_default_mode_is_mirror(hass: HomeAssistant, mock_setup_entry) -> None:
    """Verify Playlists and Liked Songs default to Mirror mode."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_client = _make_flow_client(authenticate=AsyncMock(return_value=MOCK_USER_ID))
    mock_client.get_feed = AsyncMock(return_value=[])

    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: MOCK_COOKIE},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_DOWNLOAD_MODE_PLAYLISTS] == DEFAULT_DOWNLOAD_MODE
    assert result["options"][CONF_DOWNLOAD_MODE_PLAYLISTS] == "mirror"
    assert result["options"][CONF_DOWNLOAD_MODE_LIKED] == DEFAULT_DOWNLOAD_MODE
    assert result["options"][CONF_DOWNLOAD_MODE_LIKED] == "mirror"


async def test_options_flow_archive_mode_valid(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Verify Archive is a selectable mode in the options flow."""
    entry = make_entry(options={**make_entry().options, CONF_DOWNLOAD_PATH: "/media/suno"})
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Step 1: Library page — enable playlists only
    with patch.object(
        type(hass.config_entries.options._progress[result["flow_id"]]),
        "_validate_download_path",
        return_value=True,
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_SHOW_PLAYLISTS: True,
                CONF_SHOW_LIKED: False,
                CONF_SHOW_MY_SONGS: False,
                CONF_DOWNLOAD_PATH: "/media/suno",
                CONF_CREATE_PLAYLISTS: True,
                CONF_CACHE_MAX_SIZE: DEFAULT_CACHE_MAX_SIZE,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "playlists"

    # Step 2: Submit archive mode for playlists
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_QUALITY_PLAYLISTS: QUALITY_HIGH,
            CONF_DOWNLOAD_MODE_PLAYLISTS: "archive",
            CONF_ALL_PLAYLISTS: True,
        },
    )

    # Should finish (liked and my_songs disabled)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DOWNLOAD_MODE_PLAYLISTS] == "archive"


# ── SunoConnectionError handling + reauth identity (Release 2: 2.6) ─────


async def test_user_flow_handles_suno_connection_error(hass: HomeAssistant) -> None:
    """SunoConnectionError during user setup maps to cannot_connect."""
    result = await hass.config_entries.flow.async_init("suno", context={"source": config_entries.SOURCE_USER})
    mock_client = _make_flow_client(authenticate=AsyncMock(side_effect=SunoConnectionError("network down")))
    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: MOCK_COOKIE},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_reauth_flow_handles_suno_connection_error(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """SunoConnectionError during reauth maps to cannot_connect."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)
    result = await entry.start_reauth_flow(hass)
    mock_client = _make_flow_client(authenticate=AsyncMock(side_effect=SunoConnectionError("network down")))
    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: "new-cookie"},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_reauth_flow_aborts_on_wrong_account(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Reauth with a cookie for a different Suno account aborts cleanly."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    assert entry.unique_id == MOCK_USER_ID
    result = await entry.start_reauth_flow(hass)

    different_user_id = MOCK_USER_ID + "-other"
    mock_client = _make_flow_client(authenticate=AsyncMock(return_value=different_user_id))
    mock_client.get_feed = AsyncMock(return_value=[])

    original_cookie = entry.data[CONF_COOKIE]
    with _patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: "stranger-cookie"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    # Original cookie must NOT have been overwritten.
    assert entry.data[CONF_COOKIE] == original_cookie
