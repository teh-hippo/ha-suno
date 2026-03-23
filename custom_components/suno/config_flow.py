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
    CONF_AUDIO_QUALITY,
    CONF_CACHE_ENABLED,
    CONF_CACHE_MAX_SIZE,
    CONF_CACHE_TTL,
    CONF_COOKIE,
    CONF_RECENT_COUNT,
    CONF_SHOW_LIKED,
    CONF_SHOW_PLAYLISTS,
    CONF_SHOW_RECENT,
    CONF_SYNC_ALL_PLAYLISTS,
    CONF_SYNC_ENABLED,
    CONF_SYNC_LATEST_COUNT,
    CONF_SYNC_LATEST_DAYS,
    CONF_SYNC_LIKED,
    CONF_SYNC_MODE_LATEST,
    CONF_SYNC_MODE_LIKED,
    CONF_SYNC_MODE_PLAYLISTS,
    CONF_SYNC_PATH,
    CONF_SYNC_PLAYLISTS,
    CONF_SYNC_PLAYLISTS_M3U,
    CONF_SYNC_QUALITY_LATEST,
    CONF_SYNC_QUALITY_LIKED,
    CONF_SYNC_QUALITY_PLAYLISTS,
    DEFAULT_AUDIO_QUALITY,
    DEFAULT_CACHE_ENABLED,
    DEFAULT_CACHE_MAX_SIZE,
    DEFAULT_CACHE_TTL,
    DEFAULT_RECENT_COUNT,
    DEFAULT_SHOW_LIKED,
    DEFAULT_SHOW_PLAYLISTS,
    DEFAULT_SHOW_RECENT,
    DEFAULT_SYNC_ALL_PLAYLISTS,
    DEFAULT_SYNC_ENABLED,
    DEFAULT_SYNC_LIKED,
    DEFAULT_SYNC_MODE,
    DEFAULT_SYNC_PLAYLISTS_M3U,
    DOMAIN,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from .exceptions import SunoAuthError

_LOGGER = logging.getLogger(__name__)


def _display_schema(opts: dict[str, Any]) -> vol.Schema:
    """Build schema for display and cache options."""
    return vol.Schema(
        {
            vol.Required(
                CONF_SHOW_PLAYLISTS,
                default=opts.get(CONF_SHOW_PLAYLISTS, DEFAULT_SHOW_PLAYLISTS),
            ): BooleanSelector(),
            vol.Required(
                CONF_SHOW_LIKED,
                default=opts.get(CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED),
            ): BooleanSelector(),
            vol.Required(
                CONF_SHOW_RECENT,
                default=opts.get(CONF_SHOW_RECENT, DEFAULT_SHOW_RECENT),
            ): BooleanSelector(),
            vol.Required(
                CONF_RECENT_COUNT,
                default=opts.get(CONF_RECENT_COUNT, DEFAULT_RECENT_COUNT),
            ): NumberSelector(NumberSelectorConfig(min=1, max=50, step=1, mode=NumberSelectorMode.SLIDER)),
            vol.Required(
                CONF_AUDIO_QUALITY,
                default=opts.get(CONF_AUDIO_QUALITY, DEFAULT_AUDIO_QUALITY),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value="standard", label="Standard (MP3)"),
                        SelectOptionDict(value="high", label="High (FLAC)"),
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_CACHE_TTL,
                default=opts.get(CONF_CACHE_TTL, DEFAULT_CACHE_TTL),
            ): NumberSelector(NumberSelectorConfig(min=5, max=120, step=5, mode=NumberSelectorMode.SLIDER)),
            vol.Required(
                CONF_CACHE_ENABLED,
                default=opts.get(CONF_CACHE_ENABLED, DEFAULT_CACHE_ENABLED),
            ): BooleanSelector(),
            vol.Required(
                CONF_CACHE_MAX_SIZE,
                default=opts.get(CONF_CACHE_MAX_SIZE, DEFAULT_CACHE_MAX_SIZE),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=100,
                    max=10000,
                    step=100,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="MB",
                )
            ),
        }
    )


class SunoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Suno."""

    VERSION = 2

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
                    title="Suno",
                    data={CONF_COOKIE: cookie},
                    options={
                        CONF_SHOW_LIKED: DEFAULT_SHOW_LIKED,
                        CONF_SHOW_RECENT: DEFAULT_SHOW_RECENT,
                        CONF_RECENT_COUNT: DEFAULT_RECENT_COUNT,
                        CONF_SHOW_PLAYLISTS: DEFAULT_SHOW_PLAYLISTS,
                        CONF_CACHE_TTL: DEFAULT_CACHE_TTL,
                        CONF_AUDIO_QUALITY: DEFAULT_AUDIO_QUALITY,
                        CONF_CACHE_ENABLED: DEFAULT_CACHE_ENABLED,
                        CONF_CACHE_MAX_SIZE: DEFAULT_CACHE_MAX_SIZE,
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

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
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
        """Handle reconfiguration of display options."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if not entry:
            return self.async_abort(reason="unknown")
        if user_input is not None:
            return self.async_update_reload_and_abort(entry, options={**entry.options, **user_input})
        return self.async_show_form(step_id="reconfigure", data_schema=_display_schema(dict(entry.options)))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowWithReload:
        return SunoOptionsFlow()


class SunoOptionsFlow(OptionsFlowWithReload):
    """Handle Suno options."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: media browser and cache options."""
        if user_input is not None:
            self._options = {**self.config_entry.options, **user_input}
            return await self.async_step_sync()
        return self.async_show_form(step_id="init", data_schema=_display_schema(dict(self.config_entry.options)))

    async def async_step_sync(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 2: sync general settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_SYNC_ENABLED, False):
                path = user_input.get(CONF_SYNC_PATH, "")
                if path and not await self._validate_sync_path(path):
                    errors[CONF_SYNC_PATH] = "invalid_sync_path"
            if not errors:
                merged = {**self._options, **user_input}
                if user_input.get(CONF_SYNC_ENABLED):
                    self._options = merged
                    return await self.async_step_sync_sources()
                merged[CONF_SYNC_PLAYLISTS] = []
                return self.async_create_entry(data=merged)
        opts = self.config_entry.options
        default_path = self._get_default_sync_path()
        schema: dict[vol.Marker, Any] = {
            vol.Required(
                CONF_SYNC_ENABLED, default=opts.get(CONF_SYNC_ENABLED, DEFAULT_SYNC_ENABLED)
            ): BooleanSelector(),
            vol.Required(CONF_SYNC_PATH, default=opts.get(CONF_SYNC_PATH, default_path)): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
            vol.Required(
                CONF_SYNC_PLAYLISTS_M3U,
                default=opts.get(CONF_SYNC_PLAYLISTS_M3U, DEFAULT_SYNC_PLAYLISTS_M3U),
            ): BooleanSelector(),
        }
        return self.async_show_form(step_id="sync", data_schema=vol.Schema(schema), errors=errors)

    async def async_step_sync_sources(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 3: configure sync sources with quality and mode."""
        errors: dict[str, str] = {}
        if user_input is not None:
            merged = {**self._options, **user_input}
            if user_input.get(CONF_SYNC_ALL_PLAYLISTS, True):
                merged[CONF_SYNC_PLAYLISTS] = []
                return self.async_create_entry(data=merged)
            else:
                self._options = merged
                return await self.async_step_sync_playlists()

        opts = self.config_entry.options
        quality_options = [
            SelectOptionDict(value="standard", label="Standard (MP3)"),
            SelectOptionDict(value="high", label="High (FLAC)"),
        ]
        mode_options = [
            SelectOptionDict(value="sync", label="Sync (remove deleted songs)"),
            SelectOptionDict(value="copy", label="Copy (keep all downloads)"),
        ]
        quality_selector = SelectSelector(
            SelectSelectorConfig(options=quality_options, mode=SelectSelectorMode.DROPDOWN)
        )
        mode_selector = SelectSelector(SelectSelectorConfig(options=mode_options, mode=SelectSelectorMode.DROPDOWN))

        schema = vol.Schema(
            {
                # Liked songs
                vol.Required(CONF_SYNC_LIKED, default=opts.get(CONF_SYNC_LIKED, DEFAULT_SYNC_LIKED)): BooleanSelector(),
                vol.Required(
                    CONF_SYNC_QUALITY_LIKED, default=opts.get(CONF_SYNC_QUALITY_LIKED, QUALITY_HIGH)
                ): quality_selector,
                vol.Required(
                    CONF_SYNC_MODE_LIKED, default=opts.get(CONF_SYNC_MODE_LIKED, DEFAULT_SYNC_MODE)
                ): mode_selector,
                # Playlists
                vol.Required(
                    CONF_SYNC_ALL_PLAYLISTS,
                    default=opts.get(CONF_SYNC_ALL_PLAYLISTS, DEFAULT_SYNC_ALL_PLAYLISTS),
                ): BooleanSelector(),
                vol.Required(
                    CONF_SYNC_QUALITY_PLAYLISTS,
                    default=opts.get(CONF_SYNC_QUALITY_PLAYLISTS, QUALITY_HIGH),
                ): quality_selector,
                vol.Required(
                    CONF_SYNC_MODE_PLAYLISTS,
                    default=opts.get(CONF_SYNC_MODE_PLAYLISTS, DEFAULT_SYNC_MODE),
                ): mode_selector,
                # Latest songs
                vol.Optional(
                    CONF_SYNC_LATEST_COUNT,
                    description={"suggested_value": opts.get(CONF_SYNC_LATEST_COUNT)},
                ): NumberSelector(NumberSelectorConfig(min=0, max=500, step=1, mode=NumberSelectorMode.BOX)),
                vol.Optional(
                    CONF_SYNC_LATEST_DAYS,
                    description={"suggested_value": opts.get(CONF_SYNC_LATEST_DAYS)},
                ): NumberSelector(NumberSelectorConfig(min=0, max=365, step=1, mode=NumberSelectorMode.BOX)),
                vol.Required(
                    CONF_SYNC_QUALITY_LATEST,
                    default=opts.get(CONF_SYNC_QUALITY_LATEST, QUALITY_STANDARD),
                ): quality_selector,
                vol.Required(
                    CONF_SYNC_MODE_LATEST, default=opts.get(CONF_SYNC_MODE_LATEST, DEFAULT_SYNC_MODE)
                ): mode_selector,
            }
        )

        return self.async_show_form(step_id="sync_sources", data_schema=schema, errors=errors)

    async def async_step_sync_playlists(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 4: select specific playlists to sync."""
        if user_input is not None:
            return self.async_create_entry(data={**self._options, **user_input})
        coordinator = self.config_entry.runtime_data
        playlist_options = [SelectOptionDict(value=p.id, label=p.name) for p in coordinator.data.playlists]
        return self.async_show_form(
            step_id="sync_playlists",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SYNC_PLAYLISTS, default=self.config_entry.options.get(CONF_SYNC_PLAYLISTS, [])
                    ): SelectSelector(
                        SelectSelectorConfig(options=playlist_options, multiple=True, mode=SelectSelectorMode.LIST)
                    ),
                }
            ),
        )

    def _get_default_sync_path(self) -> str:
        """Compute default sync path from HA media dirs."""
        import os

        media_dir = self.hass.config.media_dirs.get("local")
        return os.path.join(media_dir, "suno") if media_dir else self.hass.config.path("media", "suno")

    async def _validate_sync_path(self, path: str) -> bool:
        """Check that the sync path is writable."""
        from pathlib import Path as _Path

        def _check(p: str) -> bool:
            try:
                target = _Path(p)
                target.mkdir(parents=True, exist_ok=True)
                test_file = target / ".suno_write_test"
                test_file.touch()
                test_file.unlink()
                return True
            except OSError, PermissionError:
                return False

        return await self.hass.async_add_executor_job(_check, path)
