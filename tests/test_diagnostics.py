"""Tests for Suno diagnostics."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.suno.diagnostics import async_get_config_entry_diagnostics

from .conftest import make_entry, setup_entry


async def test_diagnostics_output(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Diagnostics returns expected structure with redacted cookie."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert "config_entry" in result
    assert "library" in result
    assert "credits" in result

    # Cookie must be redacted
    assert result["config_entry"]["data"]["cookie"] == "**REDACTED**"
    assert result["config_entry"]["unique_id"] == entry.unique_id

    assert result["library"]["total_clips"] == 2
    assert result["library"]["playlists"] == 1
    assert result["credits"]["credits_left"] == 1500
    assert result["credits"]["monthly_limit"] == 2500


async def test_diagnostics_no_credits(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Diagnostics handles missing credits gracefully."""
    mock_suno_client.get_credits.side_effect = Exception("Credits error")
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["credits"]["credits_left"] is None
    assert result["credits"]["monthly_limit"] is None
    assert result["credits"]["monthly_usage"] is None
