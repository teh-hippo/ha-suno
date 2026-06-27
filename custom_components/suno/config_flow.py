"""Config flow for the Suno integration."""

from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .auth import ClerkAuth
from .const import (
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
    CONF_VIDEO_ART_MODE,
    CONF_VIDEO_FFMPEG_EXTRA_ARGS,
    CONF_VIDEO_LOSSLESS,
    CONF_VIDEO_MAX_FPS,
    CONF_VIDEO_MAX_WIDTH,
    CONF_VIDEO_QUALITY,
    DEFAULT_ALL_PLAYLISTS,
    DEFAULT_CACHE_MAX_SIZE,
    DEFAULT_CREATE_PLAYLISTS,
    DEFAULT_DOWNLOAD_MODE,
    DEFAULT_DOWNLOAD_MODE_MY_SONGS,
    DEFAULT_MY_SONGS_COUNT,
    DEFAULT_MY_SONGS_DAYS,
    DEFAULT_MY_SONGS_MINIMUM,
    DEFAULT_SHOW_LIKED,
    DEFAULT_SHOW_MY_SONGS,
    DEFAULT_SHOW_PLAYLISTS,
    DEFAULT_VIDEO_FFMPEG_EXTRA_ARGS,
    DEFAULT_VIDEO_LOSSLESS,
    DEFAULT_VIDEO_MAX_FPS,
    DEFAULT_VIDEO_MAX_WIDTH,
    DEFAULT_VIDEO_QUALITY,
    DOMAIN,
    DOWNLOAD_MODE_ARCHIVE,
    DOWNLOAD_MODE_CACHE,
    DOWNLOAD_MODE_MIRROR,
    QUALITY_HIGH,
    QUALITY_STANDARD,
    VIDEO_ART_BOTH,
    VIDEO_ART_CONVERT,
    VIDEO_ART_DOWNLOAD,
    VIDEO_ART_OFF,
)
from .exceptions import SunoAuthError, SunoConnectionError
from .runtime import paths_overlap

_LOGGER = logging.getLogger(__name__)

_DOCS_URL = "https://github.com/teh-hippo/ha-suno/blob/master/docs/login.md"
_SUNO_URL = "https://suno.com"

QUALITY_OPTIONS = [
    SelectOptionDict(value="standard", label="Standard (MP3)"),
    SelectOptionDict(value="high", label="High (FLAC)"),
]
MODE_OPTIONS = [
    SelectOptionDict(value=DOWNLOAD_MODE_MIRROR, label="Mirror"),
    SelectOptionDict(value=DOWNLOAD_MODE_ARCHIVE, label="Archive"),
    SelectOptionDict(value=DOWNLOAD_MODE_CACHE, label="Cache"),
]
VIDEO_ART_OPTIONS = [
    SelectOptionDict(value=VIDEO_ART_OFF, label="Off"),
    SelectOptionDict(value=VIDEO_ART_DOWNLOAD, label="Download"),
    SelectOptionDict(value=VIDEO_ART_CONVERT, label="Convert"),
    SelectOptionDict(value=VIDEO_ART_BOTH, label="Both"),
]


def _quality_selector() -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(options=QUALITY_OPTIONS, mode=SelectSelectorMode.DROPDOWN))


def _mode_selector() -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(options=MODE_OPTIONS, mode=SelectSelectorMode.DROPDOWN))


def _clean_library_input(user_input: dict[str, Any], errors: dict[str, str]) -> dict[str, Any]:
    """Strip and validate advanced library options."""
    cleaned_input = {**user_input}
    if CONF_VIDEO_FFMPEG_EXTRA_ARGS not in cleaned_input:
        return cleaned_input

    value = cleaned_input[CONF_VIDEO_FFMPEG_EXTRA_ARGS]
    if not isinstance(value, str):
        errors[CONF_VIDEO_FFMPEG_EXTRA_ARGS] = "invalid_ffmpeg_args"
        return cleaned_input

    extra_args = value.strip()
    if extra_args:
        try:
            shlex.split(extra_args)
        except ValueError:
            errors[CONF_VIDEO_FFMPEG_EXTRA_ARGS] = "invalid_ffmpeg_args"
    cleaned_input[CONF_VIDEO_FFMPEG_EXTRA_ARGS] = extra_args
    return cleaned_input


def _download_path_conflict(hass: HomeAssistant, path: str, current_entry_id: str | None) -> bool:
    """Return True if another Suno entry's download path overlaps ``path``.

    Overlap means equal, parent, or child directories, not just exact
    equality, so two accounts cannot be pointed at directories where one
    account's mirror reconciliation would delete the other's files.
    """
    if not path:
        return False
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == current_entry_id:
            continue
        other_path = entry.options.get(CONF_DOWNLOAD_PATH)
        if other_path and paths_overlap(path, str(other_path)):
            return True
    return False


async def _async_validate_download_path(hass: HomeAssistant, path: str) -> bool:
    """Check that the download path is writable."""

    def _check(target_path: str) -> bool:
        try:
            target = Path(target_path).resolve()
            target.mkdir(parents=True, exist_ok=True)
            test_file = target / ".suno_write_test"
            test_file.touch()
            test_file.unlink()
            return True
        except OSError, PermissionError:
            return False

    return await hass.async_add_executor_job(_check, path)


def _library_schema(opts: dict[str, Any]) -> vol.Schema:
    """Build schema for the Library options page."""
    schema: dict[Any, Any] = {
        vol.Required(
            CONF_SHOW_PLAYLISTS,
            default=opts.get(CONF_SHOW_PLAYLISTS, DEFAULT_SHOW_PLAYLISTS),
        ): BooleanSelector(),
        vol.Required(
            CONF_SHOW_LIKED,
            default=opts.get(CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED),
        ): BooleanSelector(),
        vol.Required(
            CONF_SHOW_MY_SONGS,
            default=opts.get(CONF_SHOW_MY_SONGS, DEFAULT_SHOW_MY_SONGS),
        ): BooleanSelector(),
        vol.Required(
            CONF_DOWNLOAD_PATH,
            default=opts.get(CONF_DOWNLOAD_PATH, ""),
        ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
    }
    if opts.get(CONF_DOWNLOAD_PATH):
        schema[
            vol.Required(
                CONF_CREATE_PLAYLISTS,
                default=opts.get(CONF_CREATE_PLAYLISTS, DEFAULT_CREATE_PLAYLISTS),
            )
        ] = BooleanSelector()
        schema[
            vol.Required(
                CONF_VIDEO_ART_MODE,
                default=opts.get(CONF_VIDEO_ART_MODE, VIDEO_ART_OFF),
            )
        ] = SelectSelector(SelectSelectorConfig(options=VIDEO_ART_OPTIONS, mode=SelectSelectorMode.DROPDOWN))
        schema[
            vol.Optional(
                CONF_VIDEO_QUALITY,
                default=opts.get(CONF_VIDEO_QUALITY, DEFAULT_VIDEO_QUALITY),
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=NumberSelectorMode.SLIDER,
            )
        )
        schema[
            vol.Optional(
                CONF_VIDEO_LOSSLESS,
                default=opts.get(CONF_VIDEO_LOSSLESS, DEFAULT_VIDEO_LOSSLESS),
            )
        ] = BooleanSelector()
        schema[
            vol.Optional(
                CONF_VIDEO_MAX_FPS,
                default=opts.get(CONF_VIDEO_MAX_FPS, DEFAULT_VIDEO_MAX_FPS),
            )
        ] = NumberSelector(NumberSelectorConfig(min=0, max=60, step=1))
        schema[
            vol.Optional(
                CONF_VIDEO_MAX_WIDTH,
                default=opts.get(CONF_VIDEO_MAX_WIDTH, DEFAULT_VIDEO_MAX_WIDTH),
            )
        ] = NumberSelector(NumberSelectorConfig(min=0, max=4000, step=1))
        schema[
            vol.Optional(
                CONF_VIDEO_FFMPEG_EXTRA_ARGS,
                default=opts.get(CONF_VIDEO_FFMPEG_EXTRA_ARGS, DEFAULT_VIDEO_FFMPEG_EXTRA_ARGS),
            )
        ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
    schema[
        vol.Required(
            CONF_CACHE_MAX_SIZE,
            default=opts.get(CONF_CACHE_MAX_SIZE, DEFAULT_CACHE_MAX_SIZE),
        )
    ] = NumberSelector(
        NumberSelectorConfig(
            min=100,
            max=10000,
            step=100,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="MB",
        )
    )
    return vol.Schema(schema)


class SunoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Suno."""

    VERSION = 1
    MINOR_VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step: cookie input."""
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie = user_input[CONF_COOKIE]
            session = async_get_clientsession(self.hass)
            auth = ClerkAuth(session, cookie)

            try:
                user_id = await auth.authenticate()
            except SunoAuthError:
                errors["base"] = "invalid_cookie"
            except aiohttp.ClientError, TimeoutError, SunoConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Suno authentication")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=auth.display_name,
                    data={CONF_COOKIE: cookie},
                    options={
                        CONF_SHOW_PLAYLISTS: True,
                        CONF_SHOW_LIKED: True,
                        CONF_SHOW_MY_SONGS: True,
                        CONF_DOWNLOAD_PATH: "",
                        CONF_CREATE_PLAYLISTS: True,
                        CONF_VIDEO_ART_MODE: VIDEO_ART_OFF,
                        CONF_VIDEO_QUALITY: DEFAULT_VIDEO_QUALITY,
                        CONF_VIDEO_LOSSLESS: DEFAULT_VIDEO_LOSSLESS,
                        CONF_VIDEO_MAX_FPS: DEFAULT_VIDEO_MAX_FPS,
                        CONF_VIDEO_MAX_WIDTH: DEFAULT_VIDEO_MAX_WIDTH,
                        CONF_VIDEO_FFMPEG_EXTRA_ARGS: DEFAULT_VIDEO_FFMPEG_EXTRA_ARGS,
                        CONF_CACHE_MAX_SIZE: DEFAULT_CACHE_MAX_SIZE,
                        CONF_QUALITY_LIKED: QUALITY_HIGH,
                        CONF_QUALITY_PLAYLISTS: QUALITY_HIGH,
                        CONF_QUALITY_MY_SONGS: QUALITY_STANDARD,
                        CONF_DOWNLOAD_MODE_LIKED: DEFAULT_DOWNLOAD_MODE,
                        CONF_DOWNLOAD_MODE_PLAYLISTS: DEFAULT_DOWNLOAD_MODE,
                        CONF_DOWNLOAD_MODE_MY_SONGS: DEFAULT_DOWNLOAD_MODE_MY_SONGS,
                        CONF_MY_SONGS_COUNT: DEFAULT_MY_SONGS_COUNT,
                        CONF_MY_SONGS_DAYS: DEFAULT_MY_SONGS_DAYS,
                        CONF_MY_SONGS_MINIMUM: DEFAULT_MY_SONGS_MINIMUM,
                        CONF_ALL_PLAYLISTS: True,
                        CONF_PLAYLISTS: [],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_COOKIE): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD, multiline=True)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"docs_url": _DOCS_URL, "suno_url": _SUNO_URL},
        )

    async def async_step_reauth(self, _entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauth when cookie expires."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle reauth confirmation with new cookie."""
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie = user_input[CONF_COOKIE]
            session = async_get_clientsession(self.hass)
            auth = ClerkAuth(session, cookie)

            try:
                user_id = await auth.authenticate()
            except SunoAuthError:
                errors["base"] = "invalid_cookie"
            except aiohttp.ClientError, TimeoutError, SunoConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Suno re-authentication")
                errors["base"] = "unknown"
            else:
                entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
                if entry and entry.unique_id and user_id != entry.unique_id:
                    # The new cookie authenticates as a different Suno account
                    # than the one originally configured. Refuse silently rather
                    # than corrupt the existing entry's identity.
                    return self.async_abort(reason="wrong_account")
                if entry:
                    self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_COOKIE: cookie})
                    await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_COOKIE): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD, multiline=True)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"docs_url": _DOCS_URL, "suno_url": _SUNO_URL},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle reconfiguration of library options."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if not entry:
            return self.async_abort(reason="unknown")
        errors: dict[str, str] = {}
        if user_input is not None:
            path = user_input.get(CONF_DOWNLOAD_PATH, "")
            if path and not await _async_validate_download_path(self.hass, path):
                errors[CONF_DOWNLOAD_PATH] = "invalid_download_path"
            elif path and _download_path_conflict(self.hass, path, entry.entry_id):
                errors[CONF_DOWNLOAD_PATH] = "download_path_conflict"
            if not errors:
                cleaned_input = _clean_library_input(user_input, errors)
            if not errors:
                return self.async_update_reload_and_abort(entry, options={**entry.options, **cleaned_input})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_library_schema(dict(entry.options)),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        _config_entry: ConfigEntry,
    ) -> OptionsFlowWithReload:
        return SunoOptionsFlow()


class SunoOptionsFlow(OptionsFlowWithReload):
    """Handle Suno options."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._done_playlists = False
        self._done_liked = False
        self._done_my_songs = False

    async def _next_content_step(self) -> ConfigFlowResult:
        """Route to the next enabled content-type step, or finish."""
        if self._options.get(CONF_SHOW_PLAYLISTS) and not self._done_playlists:
            return await self.async_step_playlists()
        if self._options.get(CONF_SHOW_LIKED) and not self._done_liked:
            return await self.async_step_liked()
        if self._options.get(CONF_SHOW_MY_SONGS) and not self._done_my_songs:
            return await self.async_step_my_songs()
        return self.async_create_entry(data=self._options)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: Library — content toggles, download path, cache."""
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned_input = user_input
            path = user_input.get(CONF_DOWNLOAD_PATH, "")
            if path and not await self._validate_download_path(path):
                errors[CONF_DOWNLOAD_PATH] = "invalid_download_path"
            elif path and self._check_download_path_conflict(path):
                errors[CONF_DOWNLOAD_PATH] = "download_path_conflict"
            if not errors:
                cleaned_input = _clean_library_input(user_input, errors)
            if not errors:
                self._options = {**self.config_entry.options, **cleaned_input}
                return await self._next_content_step()
        return self.async_show_form(
            step_id="init",
            data_schema=_library_schema(dict(self.config_entry.options)),
            errors=errors,
        )

    async def async_step_playlists(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Configure playlist download settings."""
        if user_input is not None:
            self._options.update(user_input)
            self._done_playlists = True
            if not user_input.get(CONF_ALL_PLAYLISTS, True):
                return await self.async_step_select_playlists()
            self._options[CONF_PLAYLISTS] = []
            return await self._next_content_step()

        opts = self._options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DOWNLOAD_MODE_PLAYLISTS,
                    default=opts.get(CONF_DOWNLOAD_MODE_PLAYLISTS, DEFAULT_DOWNLOAD_MODE),
                ): _mode_selector(),
                vol.Required(
                    CONF_QUALITY_PLAYLISTS,
                    default=opts.get(CONF_QUALITY_PLAYLISTS, QUALITY_HIGH),
                ): _quality_selector(),
                vol.Required(
                    CONF_ALL_PLAYLISTS,
                    default=opts.get(CONF_ALL_PLAYLISTS, DEFAULT_ALL_PLAYLISTS),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="playlists", data_schema=schema)

    async def async_step_select_playlists(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Select specific playlists to download."""
        if user_input is not None:
            self._options.update(user_input)
            return await self._next_content_step()

        runtime = self.config_entry.runtime_data
        playlist_options = [SelectOptionDict(value=p.id, label=p.name) for p in runtime.suno_library.playlists]
        return self.async_show_form(
            step_id="select_playlists",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_PLAYLISTS,
                        default=self._options.get(CONF_PLAYLISTS, []),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=playlist_options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_liked(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Configure liked songs download settings."""
        if user_input is not None:
            self._options.update(user_input)
            self._done_liked = True
            return await self._next_content_step()

        opts = self._options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DOWNLOAD_MODE_LIKED,
                    default=opts.get(CONF_DOWNLOAD_MODE_LIKED, DEFAULT_DOWNLOAD_MODE),
                ): _mode_selector(),
                vol.Required(
                    CONF_QUALITY_LIKED,
                    default=opts.get(CONF_QUALITY_LIKED, QUALITY_HIGH),
                ): _quality_selector(),
            }
        )
        return self.async_show_form(step_id="liked", data_schema=schema)

    async def async_step_my_songs(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Configure my songs download settings."""
        if user_input is not None:
            self._options.update(user_input)
            self._done_my_songs = True
            return await self._next_content_step()

        opts = self._options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DOWNLOAD_MODE_MY_SONGS,
                    default=opts.get(CONF_DOWNLOAD_MODE_MY_SONGS, DEFAULT_DOWNLOAD_MODE_MY_SONGS),
                ): _mode_selector(),
                vol.Required(
                    CONF_QUALITY_MY_SONGS,
                    default=opts.get(CONF_QUALITY_MY_SONGS, QUALITY_STANDARD),
                ): _quality_selector(),
                vol.Required(
                    CONF_MY_SONGS_COUNT,
                    default=opts.get(CONF_MY_SONGS_COUNT, DEFAULT_MY_SONGS_COUNT),
                ): NumberSelector(NumberSelectorConfig(min=0, max=500, step=1, mode=NumberSelectorMode.BOX)),
                vol.Required(
                    CONF_MY_SONGS_DAYS,
                    default=opts.get(CONF_MY_SONGS_DAYS, DEFAULT_MY_SONGS_DAYS),
                ): NumberSelector(NumberSelectorConfig(min=0, max=365, step=1, mode=NumberSelectorMode.BOX)),
                vol.Required(
                    CONF_MY_SONGS_MINIMUM,
                    default=opts.get(CONF_MY_SONGS_MINIMUM, DEFAULT_MY_SONGS_MINIMUM),
                ): NumberSelector(NumberSelectorConfig(min=0, max=500, step=1, mode=NumberSelectorMode.BOX)),
            }
        )
        return self.async_show_form(step_id="my_songs", data_schema=schema)

    def _check_download_path_conflict(self, path: str) -> bool:
        """Check if another config entry's download path overlaps this one."""
        return _download_path_conflict(self.hass, path, self.config_entry.entry_id)

    async def _validate_download_path(self, path: str) -> bool:
        """Check that the download path is writable."""
        return await _async_validate_download_path(self.hass, path)
