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

from .api import SunoClient
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
from .exceptions import SunoAuthError

_LOGGER = logging.getLogger(__name__)


class SunoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Suno."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step: cookie input."""
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie = user_input[CONF_COOKIE]
            session = async_get_clientsession(self.hass)
            client = SunoClient(session, cookie)

            try:
                user_id = await client.authenticate()
                await client.get_feed(0)
            except SunoAuthError:
                errors["base"] = "invalid_cookie"
            except (aiohttp.ClientError, TimeoutError):
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
            client = SunoClient(session, cookie)

            try:
                await client.authenticate()
                await client.get_feed(0)
            except SunoAuthError:
                errors["base"] = "invalid_cookie"
            except (aiohttp.ClientError, TimeoutError):
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
            return self.async_update_reload_and_abort(
                entry,
                options={**entry.options, **user_input},
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SHOW_LIKED,
                        default=entry.options.get(CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_SHOW_RECENT,
                        default=entry.options.get(CONF_SHOW_RECENT, DEFAULT_SHOW_RECENT),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_RECENT_COUNT,
                        default=entry.options.get(CONF_RECENT_COUNT, DEFAULT_RECENT_COUNT),
                    ): NumberSelector(NumberSelectorConfig(min=5, max=50, step=5, mode=NumberSelectorMode.SLIDER)),
                    vol.Required(
                        CONF_SHOW_PLAYLISTS,
                        default=entry.options.get(CONF_SHOW_PLAYLISTS, DEFAULT_SHOW_PLAYLISTS),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_CACHE_TTL,
                        default=entry.options.get(CONF_CACHE_TTL, DEFAULT_CACHE_TTL),
                    ): NumberSelector(NumberSelectorConfig(min=5, max=120, step=5, mode=NumberSelectorMode.SLIDER)),
                    vol.Required(
                        CONF_AUDIO_QUALITY,
                        default=entry.options.get(CONF_AUDIO_QUALITY, DEFAULT_AUDIO_QUALITY),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value="standard", label="Standard (MP3)"),
                                SelectOptionDict(value="high", label="High Quality (FLAC)"),
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_CACHE_ENABLED,
                        default=entry.options.get(CONF_CACHE_ENABLED, DEFAULT_CACHE_ENABLED),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_CACHE_MAX_SIZE,
                        default=entry.options.get(CONF_CACHE_MAX_SIZE, DEFAULT_CACHE_MAX_SIZE),
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
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowWithReload:
        """Return the options flow handler."""
        return SunoOptionsFlow()


class SunoOptionsFlow(OptionsFlowWithReload):
    """Handle Suno options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage display options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SHOW_LIKED,
                        default=self.config_entry.options.get(CONF_SHOW_LIKED, DEFAULT_SHOW_LIKED),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_SHOW_RECENT,
                        default=self.config_entry.options.get(CONF_SHOW_RECENT, DEFAULT_SHOW_RECENT),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_RECENT_COUNT,
                        default=self.config_entry.options.get(CONF_RECENT_COUNT, DEFAULT_RECENT_COUNT),
                    ): NumberSelector(NumberSelectorConfig(min=5, max=50, step=5, mode=NumberSelectorMode.SLIDER)),
                    vol.Required(
                        CONF_SHOW_PLAYLISTS,
                        default=self.config_entry.options.get(CONF_SHOW_PLAYLISTS, DEFAULT_SHOW_PLAYLISTS),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_CACHE_TTL,
                        default=self.config_entry.options.get(CONF_CACHE_TTL, DEFAULT_CACHE_TTL),
                    ): NumberSelector(NumberSelectorConfig(min=5, max=120, step=5, mode=NumberSelectorMode.SLIDER)),
                    vol.Required(
                        CONF_AUDIO_QUALITY,
                        default=self.config_entry.options.get(CONF_AUDIO_QUALITY, DEFAULT_AUDIO_QUALITY),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value="standard", label="Standard (MP3)"),
                                SelectOptionDict(value="high", label="High Quality (FLAC)"),
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_CACHE_ENABLED,
                        default=self.config_entry.options.get(CONF_CACHE_ENABLED, DEFAULT_CACHE_ENABLED),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_CACHE_MAX_SIZE,
                        default=self.config_entry.options.get(CONF_CACHE_MAX_SIZE, DEFAULT_CACHE_MAX_SIZE),
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
            ),
        )
