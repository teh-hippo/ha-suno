"""Tests for the Suno credits sensor."""

from __future__ import annotations

from custom_components.suno.api import SunoCredits
from custom_components.suno.coordinator import SunoData


def test_credits_data() -> None:
    """Test SunoCredits dataclass."""
    credits = SunoCredits(
        credits_left=1500,
        monthly_limit=2500,
        monthly_usage=1000,
        period="2026-03",
    )
    assert credits.credits_left == 1500
    assert credits.monthly_limit == 2500
    assert credits.monthly_usage == 1000
    assert credits.period == "2026-03"


def test_suno_data_defaults() -> None:
    """Test SunoData defaults."""
    data = SunoData()
    assert data.clips == []
    assert data.playlists == []
    assert data.credits is None
