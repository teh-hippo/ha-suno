"""Config flow for the Suno integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
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
    DEFAULT_ALL_PLAYLISTS,
    DEFAULT_CACHE_MAX_SIZE,
    DEFAULT_CREATE_PLAYLISTS,
    DEFAULT_DOWNLOAD_ENABLED,
    DEFAULT_DOWNLOAD_MODE,
    DEFAULT_LATEST_COUNT,
    DEFAULT_LATEST_DAYS,
    DEFAULT_LATEST_MINIMUM,
    DEFAULT_SHOW_LATEST,
    DEFAULT_SHOW_LIKED,
    DEFAULT_SHOW_PLAYLISTS,
    DOMAIN,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from .exceptions import SunoAuthError

_LOGGER = logging.getLogger(__name__)

QUALITY_OPTIONS = [
    SelectOptionDict(value="standard", label="Standard (MP3)"),
    SelectOptionDict(value="high", label="High (FLAC)"),
]
MODE_OPTIONS = [
    SelectOptionDict(value="mirror", label="Mirror"),
    SelectOptionDict(value="collect", label="Keep"),
]


def _quality_selector() -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(options=QUALITY_OPTIONS, mode=SelectSelectorMode.DROPDOWN))


def _mode_selector() -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(options=MODE_OPTIONS, mode=SelectSelectorMode.DROPDOWN))


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
            CONF_SHOW_LATEST,
            default=opts.get(CONF_SHOW_LATEST, DEFAULT_SHOW_LATEST),
        ): BooleanSelector(),
        vol.Required(
            CONF_DOWNLOAD_ENABLED,
            default=opts.get(CONF_DOWNLOAD_ENABLED, DEFAULT_DOWNLOAD_ENABLED),
        ): BooleanSelector(),
    }
    if opts.get(CONF_DOWNLOAD_ENABLED, DEFAULT_DOWNLOAD_ENABLED):
        schema[
            vol.Required(
                CONF_DOWNLOAD_PATH,
                default=opts.get(CONF_DOWNLOAD_PATH, ""),
            )
        ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
        if opts.get(CONF_DOWNLOAD_PATH):
            schema[
                vol.Required(
                    CONF_CREATE_PLAYLISTS,
                    default=opts.get(CONF_CREATE_PLAYLISTS, DEFAULT_CREATE_PLAYLISTS),
                )
            ] = BooleanSelector()
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

    VERSION = 3

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
            except aiohttp.ClientError, TimeoutError:
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
                        CONF_SHOW_LATEST: True,
                        CONF_DOWNLOAD_ENABLED: DEFAULT_DOWNLOAD_ENABLED,
                        CONF_DOWNLOAD_PATH: "",
                        CONF_CREATE_PLAYLISTS: True,
                        CONF_CACHE_MAX_SIZE: DEFAULT_CACHE_MAX_SIZE,
                        CONF_QUALITY_LIKED: QUALITY_HIGH,
                        CONF_QUALITY_PLAYLISTS: QUALITY_HIGH,
                        CONF_QUALITY_LATEST: QUALITY_STANDARD,
                        CONF_DOWNLOAD_MODE_LIKED: DEFAULT_DOWNLOAD_MODE,
                        CONF_DOWNLOAD_MODE_PLAYLISTS: DEFAULT_DOWNLOAD_MODE,
                        CONF_DOWNLOAD_MODE_LATEST: DEFAULT_DOWNLOAD_MODE,
                        CONF_LATEST_COUNT: DEFAULT_LATEST_COUNT,
                        CONF_LATEST_DAYS: DEFAULT_LATEST_DAYS,
                        CONF_LATEST_MINIMUM: DEFAULT_LATEST_MINIMUM,
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
            description_placeholders={"suno_url": "https://suno.com"},
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
                await auth.authenticate()
            except SunoAuthError:
                errors["base"] = "invalid_cookie"
            except aiohttp.ClientError, TimeoutError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Suno re-authentication")
                errors["base"] = "unknown"
            else:
                entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
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
            description_placeholders={"suno_url": "https://suno.com"},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle reconfiguration of library options."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if not entry:
            return self.async_abort(reason="unknown")
        if user_input is not None:
            return self.async_update_reload_and_abort(entry, options={**entry.options, **user_input})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_library_schema(dict(entry.options)),
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
        self._done_latest = False

    async def _next_content_step(self) -> ConfigFlowResult:
        """Route to the next enabled content-type step, or finish."""
        if self._options.get(CONF_SHOW_PLAYLISTS) and not self._done_playlists:
            return await self.async_step_playlists()
        if self._options.get(CONF_SHOW_LIKED) and not self._done_liked:
            return await self.async_step_liked()
        if self._options.get(CONF_SHOW_LATEST) and not self._done_latest:
            return await self.async_step_latest()
        return self.async_create_entry(data=self._options)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: Library — content toggles, download path, cache."""
        errors: dict[str, str] = {}
        if user_input is not None:
            path = user_input.get(CONF_DOWNLOAD_PATH, "")
            if path and not await self._validate_download_path(path):
                errors[CONF_DOWNLOAD_PATH] = "invalid_download_path"
            elif path and self._check_download_path_conflict(path):
                errors[CONF_DOWNLOAD_PATH] = "download_path_conflict"
            if not errors:
                self._options = {**self.config_entry.options, **user_input}
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
                    CONF_QUALITY_PLAYLISTS,
                    default=opts.get(CONF_QUALITY_PLAYLISTS, QUALITY_HIGH),
                ): _quality_selector(),
                vol.Required(
                    CONF_DOWNLOAD_MODE_PLAYLISTS,
                    default=opts.get(CONF_DOWNLOAD_MODE_PLAYLISTS, DEFAULT_DOWNLOAD_MODE),
                ): _mode_selector(),
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

        coordinator = self.config_entry.runtime_data
        playlist_options = [SelectOptionDict(value=p.id, label=p.name) for p in coordinator.data.playlists]
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
                    CONF_QUALITY_LIKED,
                    default=opts.get(CONF_QUALITY_LIKED, QUALITY_HIGH),
                ): _quality_selector(),
                vol.Required(
                    CONF_DOWNLOAD_MODE_LIKED,
                    default=opts.get(CONF_DOWNLOAD_MODE_LIKED, DEFAULT_DOWNLOAD_MODE),
                ): _mode_selector(),
            }
        )
        return self.async_show_form(step_id="liked", data_schema=schema)

    async def async_step_latest(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Configure latest songs download settings."""
        if user_input is not None:
            self._options.update(user_input)
            self._done_latest = True
            return await self._next_content_step()

        opts = self._options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_QUALITY_LATEST,
                    default=opts.get(CONF_QUALITY_LATEST, QUALITY_STANDARD),
                ): _quality_selector(),
                vol.Required(
                    CONF_DOWNLOAD_MODE_LATEST,
                    default=opts.get(CONF_DOWNLOAD_MODE_LATEST, DEFAULT_DOWNLOAD_MODE),
                ): _mode_selector(),
                vol.Required(
                    CONF_LATEST_COUNT,
                    default=opts.get(CONF_LATEST_COUNT, DEFAULT_LATEST_COUNT),
                ): NumberSelector(NumberSelectorConfig(min=0, max=500, step=1, mode=NumberSelectorMode.BOX)),
                vol.Required(
                    CONF_LATEST_DAYS,
                    default=opts.get(CONF_LATEST_DAYS, DEFAULT_LATEST_DAYS),
                ): NumberSelector(NumberSelectorConfig(min=0, max=365, step=1, mode=NumberSelectorMode.BOX)),
                vol.Required(
                    CONF_LATEST_MINIMUM,
                    default=opts.get(CONF_LATEST_MINIMUM, DEFAULT_LATEST_MINIMUM),
                ): NumberSelector(NumberSelectorConfig(min=0, max=500, step=1, mode=NumberSelectorMode.BOX)),
            }
        )
        return self.async_show_form(step_id="latest", data_schema=schema)

    def _check_download_path_conflict(self, path: str) -> bool:
        """Check if another config entry already uses this download path."""
        from pathlib import Path as _Path

        if not path:
            return False
        resolved = _Path(path).resolve()
        current_entry_id = getattr(self, "config_entry", None)
        current_id = current_entry_id.entry_id if current_entry_id else None

        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id == current_id:
                continue
            other_path = entry.options.get(CONF_DOWNLOAD_PATH)
            if other_path and _Path(other_path).resolve() == resolved:
                return True
        return False

    async def _validate_download_path(self, path: str) -> bool:
        """Check that the download path is writable."""
        from pathlib import Path as _Path

        def _check(p: str) -> bool:
            try:
                target = _Path(p).resolve()
                target.mkdir(parents=True, exist_ok=True)
                test_file = target / ".suno_write_test"
                test_file.touch()
                test_file.unlink()
                return True
            except OSError, PermissionError:
                return False

        return await self.hass.async_add_executor_job(_check, path)
