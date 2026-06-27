"""Multi-account media source and proxy routing tests."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.media_source import MediaSourceItem
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.suno.const import (
    CONF_QUALITY_LIKED,
    CONF_QUALITY_MY_SONGS,
    CONF_QUALITY_PLAYLISTS,
    CONF_SHOW_LIKED,
    CONF_SHOW_MY_SONGS,
    CONF_SHOW_PLAYLISTS,
    DOMAIN,
    QUALITY_HIGH,
    QUALITY_STANDARD,
)
from custom_components.suno.media_source import SunoMediaSource
from custom_components.suno.models import SunoClip, SunoData, SunoUser
from custom_components.suno.proxy import SunoMediaProxyView
from custom_components.suno.runtime import HomeAssistantRuntime

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _signed_paths() -> Any:
    with patch(
        "custom_components.suno.media_source.async_sign_path",
        side_effect=lambda _hass, path, _expiration, **_kwargs: f"{path}?authSig=test",
    ):
        yield


def _entry(unique_id: str, title: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        unique_id=unique_id,
        data={"cookie": f"cookie-{unique_id}"},
        options={
            CONF_SHOW_LIKED: True,
            CONF_SHOW_MY_SONGS: True,
            CONF_SHOW_PLAYLISTS: True,
            CONF_QUALITY_LIKED: QUALITY_HIGH,
            CONF_QUALITY_PLAYLISTS: QUALITY_HIGH,
            CONF_QUALITY_MY_SONGS: QUALITY_STANDARD,
        },
    )


def _clip(
    clip_id: str,
    title: str,
    *,
    display_name: str = "Artist",
    is_liked: bool = True,
    model_name: str = "chirp",
    major_model_version: str = "v4",
    created_at: str = "2026-06-01T00:00:00Z",
) -> SunoClip:
    return SunoClip(
        id=clip_id,
        title=title,
        audio_url=f"https://cdn1.suno.ai/{clip_id}.mp3",
        image_url=f"https://cdn1.suno.ai/{clip_id}.jpg",
        image_large_url=f"https://cdn1.suno.ai/{clip_id}-large.jpg",
        is_liked=is_liked,
        status="complete",
        created_at=created_at,
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
        display_name=display_name,
        model_name=model_name,
        major_model_version=major_model_version,
    )


def _attach_runtime(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    clips: list[SunoClip],
    liked_clips: list[SunoClip] | None = None,
    user_name: str | None = None,
) -> HomeAssistantRuntime:
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    coordinator = MagicMock()
    coordinator.data = SunoData(clips=clips, liked_clips=liked_clips if liked_clips is not None else clips)
    coordinator.user = SunoUser(id=str(entry.unique_id), display_name=user_name or entry.title)
    coordinator.data_version = 1
    cache = MagicMock()
    cache.async_get = AsyncMock(return_value=None)
    cache.async_put = AsyncMock(return_value=None)
    runtime = HomeAssistantRuntime(hass, entry, coordinator, MagicMock(), cache, MagicMock())
    entry.runtime_data = runtime
    return runtime


def _item(hass: HomeAssistant, identifier: str) -> MediaSourceItem:
    return MediaSourceItem(hass, DOMAIN, identifier, None)


async def test_single_account_media_identifiers_and_proxy_url_remain_legacy(hass: HomeAssistant) -> None:
    entry = _entry("user-a", "Account A")
    clip = _clip("clip-a", "Song A")
    _attach_runtime(hass, entry, clips=[clip])

    source = SunoMediaSource(hass)
    root = await source.async_browse_media(_item(hass, ""))
    assert [child.identifier for child in root.children] == ["liked", "my_songs", "all"]

    liked = await source.async_browse_media(_item(hass, "liked"))
    assert liked.children[0].identifier == "clip/clip-a"

    resolved = await source.async_resolve_media(_item(hass, "clip/clip-a"))
    assert resolved.url.startswith("/api/suno/media/clip-a.flac?authSig=")
    assert resolved.mime_type == "audio/flac"


async def test_multi_account_browse_and_resolve_use_account_scoped_identifiers(hass: HomeAssistant) -> None:
    entry_a = _entry("user-a", "Account A")
    entry_b = _entry("user-b", "Account B")
    clip_a = _clip("clip-a", "Song A", display_name="Artist A")
    clip_b = _clip("clip-b", "Song B", display_name="Artist B")
    _attach_runtime(hass, entry_a, clips=[clip_a], user_name="Account A")
    _attach_runtime(hass, entry_b, clips=[clip_b], user_name="Account B")

    source = SunoMediaSource(hass)
    root = await source.async_browse_media(_item(hass, ""))
    assert [(child.identifier, child.title) for child in root.children] == [
        (f"account/{entry_a.entry_id}", "Account A"),
        (f"account/{entry_b.entry_id}", "Account B"),
    ]

    account_b = await source.async_browse_media(_item(hass, f"account/{entry_b.entry_id}"))
    assert [child.identifier for child in account_b.children] == [
        f"account/{entry_b.entry_id}/liked",
        f"account/{entry_b.entry_id}/my_songs",
        f"account/{entry_b.entry_id}/all",
    ]

    liked_b = await source.async_browse_media(_item(hass, f"account/{entry_b.entry_id}/liked"))
    assert liked_b.children[0].identifier == f"account/{entry_b.entry_id}/clip/clip-b"

    scoped = await source.async_resolve_media(_item(hass, f"account/{entry_b.entry_id}/clip/clip-b"))
    assert scoped.url.startswith(f"/api/suno/media/{entry_b.entry_id}/clip-b.flac?authSig=")
    assert scoped.mime_type == "audio/flac"

    legacy = await source.async_resolve_media(_item(hass, "clip/clip-b"))
    assert legacy.url.startswith(f"/api/suno/media/{entry_b.entry_id}/clip-b.flac?authSig=")


async def test_scoped_proxy_route_uses_requested_runtime_for_shared_clip(hass: HomeAssistant) -> None:
    entry_a = _entry("user-a", "Account A")
    entry_b = _entry("user-b", "Account B")
    shared_a = _clip("shared", "Shared A", display_name="Artist A")
    shared_b = _clip("shared", "Shared B", display_name="Artist B")
    _attach_runtime(hass, entry_a, clips=[shared_a])
    runtime_b = _attach_runtime(hass, entry_b, clips=[shared_b])

    view = SunoMediaProxyView(hass)
    calls: list[tuple[str, str]] = []

    async def _pipeline(*args: Any, **_kwargs: Any) -> bytes:
        clip = args[1]
        runtime = args[4]
        calls.append((clip.title, runtime.entry.entry_id))
        return b"fLaCscoped"

    with patch.object(view, "_run_hq_pipeline", side_effect=_pipeline):
        response = await view.get(MagicMock(), "shared", "flac", entry_id=entry_b.entry_id)

    assert response.status == 200
    assert response.body == b"fLaCscoped"
    assert calls == [("Shared B", runtime_b.entry.entry_id)]


async def test_hq_inflight_key_is_account_scoped(hass: HomeAssistant) -> None:
    entry_a = _entry("user-a", "Account A")
    entry_b = _entry("user-b", "Account B")
    shared_a = _clip("shared", "Shared A")
    shared_b = _clip("shared", "Shared B")
    _attach_runtime(hass, entry_a, clips=[shared_a])
    runtime_b = _attach_runtime(hass, entry_b, clips=[shared_b])

    view = SunoMediaProxyView(hass)
    old_account_future: asyncio.Future[bytes | None]
    old_account_future = hass.loop.create_future()
    old_account_future.set_result(b"fLaCold")
    view._inflight[(entry_a.entry_id, "shared", "flac")] = old_account_future

    with patch.object(view, "_run_hq_pipeline", AsyncMock(return_value=b"fLaCnew")) as pipeline:
        response = await view._handle_hq(
            "shared",
            shared_b,
            "Shared B",
            "Artist B",
            "audio/flac",
            runtime_b,
            "hash-b",
            entry_id=entry_b.entry_id,
        )

    assert response.status == 200
    assert response.body == b"fLaCnew"
    pipeline.assert_awaited_once()
    view._inflight.pop((entry_a.entry_id, "shared", "flac"), None)
