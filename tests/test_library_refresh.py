"""Tests for the Suno Library Refresh module."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from custom_components.suno.exceptions import SunoApiError, SunoConnectionError
from custom_components.suno.library_refresh import (
    LINEAGE_EXTERNAL,
    LINEAGE_UNAVAILABLE,
    InMemoryStoredLibrary,
    LibraryRefresh,
    ParentLookup,
    SunoData,
)
from custom_components.suno.models import SunoClip, SunoCredits, SunoPlaylist


class FakeSunoLibraryAdapter:
    def __init__(
        self,
        *,
        clips: list[SunoClip] | BaseException | None = None,
        liked_clips: list[SunoClip] | BaseException | None = None,
        playlists: list[SunoPlaylist] | BaseException | None = None,
        playlist_clips: dict[str, list[SunoClip] | BaseException] | None = None,
        credits: SunoCredits | BaseException | None = None,
        parents: dict[str, str | None | BaseException] | None = None,
    ) -> None:
        self.user_id = "user-1"
        self.display_name = "Suno"
        self.suno_identity: str | None = None
        self.clips = clips if clips is not None else []
        self.liked_clips = liked_clips if liked_clips is not None else []
        self.playlists = playlists if playlists is not None else []
        self.playlist_clips = playlist_clips or {}
        self.credits = credits
        self.parents = parents or {}
        self.parent_calls: list[str] = []

    async def ensure_authenticated(self) -> None:
        return None

    async def get_all_songs(self) -> list[SunoClip]:
        if isinstance(self.clips, BaseException):
            raise self.clips
        return self.clips

    async def get_liked_songs(self) -> list[SunoClip]:
        if isinstance(self.liked_clips, BaseException):
            raise self.liked_clips
        return self.liked_clips

    async def get_playlists(self) -> list[SunoPlaylist]:
        if isinstance(self.playlists, BaseException):
            raise self.playlists
        return self.playlists

    async def get_playlist_clips(self, playlist_id: str) -> list[SunoClip]:
        result = self.playlist_clips.get(playlist_id, [])
        if isinstance(result, BaseException):
            raise result
        return result

    async def get_credits(self) -> SunoCredits:
        if isinstance(self.credits, BaseException):
            raise self.credits
        return self.credits or SunoCredits(credits_left=0, monthly_limit=0, monthly_usage=0, period=None)

    async def get_clip_parent(self, clip_id: str) -> ParentLookup:
        self.parent_calls.append(clip_id)
        result = self.parents.get(clip_id)
        if isinstance(result, BaseException):
            raise result
        return ParentLookup(result)


def _make_clip(
    clip_id: str,
    *,
    title: str = "Song",
    edited_clip_id: str = "",
    is_remix: bool = False,
    root_ancestor_id: str = "",
    lineage_status: str = "",
    album_title: str = "",
) -> SunoClip:
    return SunoClip(
        id=clip_id,
        title=title,
        audio_url=f"https://cdn1.suno.ai/{clip_id}.mp3",
        image_url="",
        image_large_url="",
        is_liked=False,
        status="complete",
        created_at="2026-01-01T00:00:00Z",
        tags="pop",
        duration=120.0,
        clip_type="gen",
        has_vocal=True,
        edited_clip_id=edited_clip_id,
        is_remix=is_remix,
        root_ancestor_id=root_ancestor_id,
        lineage_status=lineage_status,
        album_title=album_title,
    )


async def test_cold_update_publishes_empty_library_and_schedules_refresh() -> None:
    """Cold start returns an empty Suno Library immediately."""
    started = asyncio.Event()

    class SlowSource(FakeSunoLibraryAdapter):
        async def get_all_songs(self) -> list[SunoClip]:
            started.set()
            await asyncio.sleep(30)
            return []

    tasks: list[asyncio.Task[None]] = []

    def _task_factory(coro: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
        task = asyncio.create_task(coro, name=name)
        tasks.append(task)
        return task

    refresh = LibraryRefresh(SlowSource(), InMemoryStoredLibrary(), task_factory=_task_factory)

    data = await refresh.async_update()

    assert data == SunoData()
    assert refresh.data_version == 1
    assert tasks and not tasks[0].done()
    await started.wait()
    tasks[0].cancel()


async def test_section_failure_preserves_previous_section() -> None:
    """A failed section produces a Partial Suno Library instead of failing refresh."""
    previous_clip = _make_clip("previous", title="Previous")
    new_liked = _make_clip("liked", title="Liked")
    source = FakeSunoLibraryAdapter(clips=SunoApiError("songs failed"), liked_clips=[new_liked])
    refresh = LibraryRefresh(source, InMemoryStoredLibrary())
    refresh.current_data = SunoData(clips=[previous_clip])
    refresh.data_version = 1

    snapshot = await refresh.async_refresh_once()

    assert snapshot.data.clips == [previous_clip]
    assert snapshot.data.liked_clips == [new_liked]
    assert "clips" in snapshot.data.stale_sections
    assert snapshot.version == 2


async def test_remote_unavailable_preserves_stored_library() -> None:
    """SunoConnectionError preserves the previous or Stored Library snapshot."""

    class OfflineSource(FakeSunoLibraryAdapter):
        async def ensure_authenticated(self) -> None:
            raise SunoConnectionError("dns")

    previous_clip = _make_clip("stored", title="Stored")
    refresh = LibraryRefresh(OfflineSource(), InMemoryStoredLibrary())
    refresh.current_data = SunoData(clips=[previous_clip])
    refresh.data_version = 1

    snapshot = await refresh.async_refresh_once()

    assert snapshot.data.clips == [previous_clip]
    assert set(snapshot.data.stale_sections) == {"clips", "liked_clips", "playlists", "playlist_clips", "credits"}


async def test_stored_library_load_and_save() -> None:
    """Stored Library persistence is exercised through its adapter seam."""
    stored_clip = _make_clip("stored", title="Stored")
    storage = InMemoryStoredLibrary(SunoData(clips=[stored_clip]))
    refresh = LibraryRefresh(FakeSunoLibraryAdapter(clips=[]), storage)

    loaded = await refresh.async_load_stored_library()

    assert loaded is not None
    assert loaded.clips == [stored_clip]
    assert refresh.data_version == 1

    await refresh.async_refresh_once()
    assert storage.data is not None
    assert storage.data.clips == []


async def test_external_lineage_root_sets_synthetic_album_details() -> None:
    """External Lineage Root uses Remixes of <short-root-id> for Album Details."""
    remix = _make_clip("remix", title="Remix", edited_clip_id="missing-parent", is_remix=True)
    source = FakeSunoLibraryAdapter(clips=[remix], parents={"remix": "external-root-123", "external-root-123": None})
    refresh = LibraryRefresh(source, InMemoryStoredLibrary())

    snapshot = await refresh.async_refresh_once()

    published = snapshot.data.clips[0]
    assert published.root_ancestor_id == "external-root-123"
    assert published.lineage_status == LINEAGE_EXTERNAL
    assert published.album_title == "Remixes of external"


async def test_unavailable_lineage_publishes_honest_album_details() -> None:
    """Unavailable Lineage is publishable but marked honestly."""
    remix = _make_clip("remix", title="Remix", edited_clip_id="hidden-parent", is_remix=True)
    source = FakeSunoLibraryAdapter(clips=[remix], parents={"remix": None})
    refresh = LibraryRefresh(source, InMemoryStoredLibrary())

    snapshot = await refresh.async_refresh_once()

    published = snapshot.data.clips[0]
    assert published.lineage_status == LINEAGE_UNAVAILABLE
    assert published.album_title == "Remixes of unknown root"
    assert snapshot.data.unavailable_lineage_count == 1


async def test_pending_lineage_hides_new_remix() -> None:
    """New remixes with pending Clip Lineage are not published."""
    remix = _make_clip("remix", title="Remix", edited_clip_id="parent", is_remix=True)
    source = FakeSunoLibraryAdapter(clips=[remix], parents={"remix": SunoConnectionError("offline")})
    refresh = LibraryRefresh(source, InMemoryStoredLibrary())

    snapshot = await refresh.async_refresh_once()

    assert snapshot.data.clips == []
    assert snapshot.data.hidden_pending_remix_count == 1


async def test_pending_lineage_preserves_previously_published_remix() -> None:
    """A published remix stays visible while newer lineage is pending."""
    previous = _make_clip(
        "remix",
        title="Old Remix",
        edited_clip_id="parent",
        is_remix=True,
        root_ancestor_id="root",
        lineage_status=LINEAGE_EXTERNAL,
        album_title="Remixes of root",
    )
    incoming = _make_clip("remix", title="New Remix", edited_clip_id="new-parent", is_remix=True)
    source = FakeSunoLibraryAdapter(clips=[incoming], parents={"remix": SunoConnectionError("offline")})
    refresh = LibraryRefresh(source, InMemoryStoredLibrary())
    refresh.current_data = SunoData(clips=[previous])
    refresh.data_version = 1

    snapshot = await refresh.async_refresh_once()

    assert snapshot.data.clips == [previous]
    assert snapshot.data.hidden_pending_remix_count == 0
    assert snapshot.data.clips[0].title == "Old Remix"
