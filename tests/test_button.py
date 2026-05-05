"""Tests for the Suno button platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.suno.button import (
    SunoClearCacheButton,
    SunoDownloadLibraryButton,
)
from custom_components.suno.const import CONF_DOWNLOAD_PATH
from custom_components.suno.coordinator import SunoCoordinator
from custom_components.suno.models import SunoData
from custom_components.suno.runtime import HomeAssistantRuntime

from .conftest import make_entry, patch_suno_setup, setup_entry

# ── Unit tests for button press handlers ──────────────────────────


def _make_button(cls, *, options=None):
    """Create a button with a mocked Home Assistant Runtime, bypassing __init__."""
    coordinator = MagicMock(spec=SunoCoordinator)
    coordinator.data = SunoData()
    runtime = MagicMock(spec=HomeAssistantRuntime)
    runtime.coordinator = coordinator
    runtime.async_clear_cache = AsyncMock()
    runtime.async_force_download = AsyncMock()

    entry = make_entry(options=options)
    button = cls.__new__(cls)
    button.coordinator = coordinator
    button._runtime = runtime
    button._entry = entry
    return button


@pytest.mark.asyncio
async def test_clear_cache_press_calls_async_clear() -> None:
    """SunoClearCacheButton.async_press calls cache.async_clear()."""
    button = _make_button(SunoClearCacheButton)

    await button.async_press()

    button._runtime.async_clear_cache.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_cache_press_no_cache() -> None:
    """async_press delegates no-op handling to the runtime."""
    button = _make_button(SunoClearCacheButton)
    await button.async_press()
    button._runtime.async_clear_cache.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_library_press_calls_async_download() -> None:
    """SunoDownloadLibraryButton.async_press calls download_manager.async_download(force=True)."""
    button = _make_button(SunoDownloadLibraryButton)

    await button.async_press()

    button._runtime.async_force_download.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_library_press_no_dm() -> None:
    """async_press delegates no-op handling to the runtime."""
    button = _make_button(SunoDownloadLibraryButton)
    await button.async_press()
    button._runtime.async_force_download.assert_awaited_once()


# ── Integration tests for platform setup ──────────────────────────


async def test_button_setup_creates_clear_cache(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Platform setup creates the clear_cache button by default."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    states = hass.states.async_all("button")
    entity_ids = {s.entity_id for s in states}
    assert "button.suno_clear_cache" in entity_ids


async def test_button_setup_no_download_button_without_path(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Download button is NOT created when CONF_DOWNLOAD_PATH is empty."""
    entry = make_entry()  # default options have CONF_DOWNLOAD_PATH = ""
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    from homeassistant.helpers import entity_registry as er_mod

    registry = er_mod.async_get(hass)
    entities = er_mod.async_entries_for_config_entry(registry, entry.entry_id)
    unique_ids = {e.unique_id for e in entities if e.domain == "button"}
    assert "test-user-id-123_sync_library" not in unique_ids


async def test_button_setup_creates_download_button_with_path(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Download button IS created when CONF_DOWNLOAD_PATH is set."""
    opts = {**make_entry().options, CONF_DOWNLOAD_PATH: "/music/suno"}
    entry = make_entry(options=dict(opts))
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    from homeassistant.helpers import entity_registry as er_mod

    registry = er_mod.async_get(hass)
    entities = er_mod.async_entries_for_config_entry(registry, entry.entry_id)
    unique_ids = {e.unique_id for e in entities if e.domain == "button"}
    assert "test-user-id-123_clear_cache" in unique_ids
    assert "test-user-id-123_sync_library" in unique_ids
