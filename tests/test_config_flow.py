"""Tests for Suno config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.suno.const import CONF_COOKIE, DOMAIN
from custom_components.suno.exceptions import SunoAuthError


async def test_user_flow_success(hass: HomeAssistant, mock_setup_entry: AsyncMock) -> None:
    """Test successful user config flow."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch("custom_components.suno.config_flow.SunoClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.authenticate = AsyncMock(return_value="user-123")
        mock_client.get_feed = AsyncMock(return_value=[])
        mock_client_cls.return_value = mock_client

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: "test-cookie-value"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Suno"
    assert result["data"][CONF_COOKIE] == "test-cookie-value"


async def test_user_flow_invalid_cookie(hass: HomeAssistant) -> None:
    """Test config flow with invalid cookie."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    with patch("custom_components.suno.config_flow.SunoClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.authenticate = AsyncMock(side_effect=SunoAuthError("Bad cookie"))
        mock_client_cls.return_value = mock_client

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COOKIE: "bad-cookie"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_cookie"
