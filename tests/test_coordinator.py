"""Tests for the Suno coordinator."""

from __future__ import annotations

from custom_components.suno.coordinator import SunoData


def test_suno_data_defaults() -> None:
    """Test SunoData initialises with empty defaults."""
    data = SunoData()
    assert data.clips == []
    assert data.playlists == []
    assert data.credits is None
