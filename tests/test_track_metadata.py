"""Tests for the TrackMetadata.__post_init__ album-defaulting behaviour."""

from __future__ import annotations

from custom_components.suno.models import TrackMetadata


def test_post_init_defaults_album_to_title() -> None:
    meta = TrackMetadata(title="My Song")

    assert meta.album == "My Song"


def test_post_init_preserves_explicit_album() -> None:
    meta = TrackMetadata(title="My Song", album="My Album")

    assert meta.album == "My Album"


def test_post_init_leaves_album_empty_when_title_also_empty() -> None:
    meta = TrackMetadata()

    assert meta.album == ""
    assert meta.title == ""


def test_post_init_defaults_album_when_title_set_album_empty() -> None:
    meta = TrackMetadata(title="X", album="")

    assert meta.album == "X"


def test_post_init_idempotent_when_album_already_matches_title() -> None:
    meta = TrackMetadata(title="X", album="X")

    assert meta.album == "X"
    assert meta.title == "X"
