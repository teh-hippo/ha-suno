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
from custom_components.suno.coordinator import SunoCoordinator, SunoData

from .conftest import make_entry, patch_suno_setup, setup_entry

# ── Unit tests for button press handlers ──────────────────────────


def _make_button(cls, *, cache=None, download_manager=None, options=None):
    """Create a button with a mocked coordinator, bypassing __init__."""
    coordinator = MagicMock(spec=SunoCoordinator)
    coordinator.cache = cache
    coordinator.download_manager = download_manager
    coordinator.client = MagicMock()
    coordinator.data = SunoData()

    entry = make_entry(options=options)
    button = cls.__new__(cls)
    button.coordinator = coordinator
    button._entry = entry
    return button


@pytest.mark.asyncio
async def test_clear_cache_press_calls_async_clear() -> None:
    """SunoClearCacheButton.async_press calls cache.async_clear()."""
    mock_cache = MagicMock()
    mock_cache.async_clear = AsyncMock()
    button = _make_button(SunoClearCacheButton, cache=mock_cache)

    await button.async_press()

    mock_cache.async_clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_cache_press_no_cache() -> None:
    """async_press does nothing when coordinator.cache is None."""
    button = _make_button(SunoClearCacheButton, cache=None)
    await button.async_press()  # should not raise


@pytest.mark.asyncio
async def test_download_library_press_calls_async_download() -> None:
    """SunoDownloadLibraryButton.async_press calls download_manager.async_download(force=True)."""
    dm = MagicMock()
    dm.async_download = AsyncMock()
    button = _make_button(SunoDownloadLibraryButton, download_manager=dm)

    await button.async_press()

    dm.async_download.assert_awaited_once()
    call_kwargs = dm.async_download.call_args
    assert call_kwargs.kwargs.get("force") is True or call_kwargs[1].get("force") is True


@pytest.mark.asyncio
async def test_download_library_press_no_dm() -> None:
    """async_press does nothing when coordinator.download_manager is None."""
    button = _make_button(SunoDownloadLibraryButton, download_manager=None)
    await button.async_press()  # should not raise


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
    assert "test-user-id-123_download_library" not in unique_ids


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
    assert "test-user-id-123_download_library" in unique_ids
