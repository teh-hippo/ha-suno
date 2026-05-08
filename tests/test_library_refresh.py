"""Tests for the Suno Library Refresh module."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from custom_components.suno.exceptions import SunoApiError, SunoConnectionError
from custom_components.suno.library_refresh import (
    _MAX_PARENT_LOOKUPS_PER_CYCLE,
    LINEAGE_EXTERNAL,
    LINEAGE_RESOLVED,
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


async def test_resolve_external_lineage_uses_previous_snapshot_cache() -> None:
    """LINEAGE_EXTERNAL clips skip API lookup when previous snapshot has them resolved.

    Without the cache, every refresh re-resolves all known external remixes,
    consuming the per-cycle parent-lookup budget. With the cache, the budget
    is preserved for clips that genuinely need fresh resolution.
    """
    previous = _make_clip(
        "remix",
        title="Remix",
        edited_clip_id="external-parent",
        is_remix=True,
        root_ancestor_id="external-parent",
        lineage_status=LINEAGE_EXTERNAL,
        album_title="Remixes of external",
    )
    incoming = _make_clip("remix", title="Remix", edited_clip_id="external-parent", is_remix=True)
    source = FakeSunoLibraryAdapter(clips=[incoming], parents={})
    refresh = LibraryRefresh(source, InMemoryStoredLibrary())
    refresh.current_data = SunoData(clips=[previous])
    refresh.data_version = 1

    snapshot = await refresh.async_refresh_once()

    assert source.parent_calls == [], "expected no parent lookup when previous lineage is cached"
    assert snapshot.data.clips[0].lineage_status == LINEAGE_EXTERNAL
    assert snapshot.data.clips[0].root_ancestor_id == "external-parent"


async def test_cached_lineage_self_corrects_when_root_no_longer_present() -> None:
    """A cached LINEAGE_RESOLVED reverts to LINEAGE_EXTERNAL if the root is gone.

    Locks in the invariant that ``_apply_album_details`` re-evaluates lineage
    status against the current clip set even when ``_resolve_root_ancestors_api``
    short-circuits via the cache fast-path.
    """
    previous = _make_clip(
        "child",
        title="Remix",
        edited_clip_id="root",
        is_remix=True,
        root_ancestor_id="root",
        lineage_status=LINEAGE_RESOLVED,
        album_title="Original Title",
    )
    incoming = _make_clip("child", title="Remix", edited_clip_id="root", is_remix=True)
    source = FakeSunoLibraryAdapter(clips=[incoming], parents={})
    refresh = LibraryRefresh(source, InMemoryStoredLibrary())
    refresh.current_data = SunoData(clips=[previous])
    refresh.data_version = 1

    snapshot = await refresh.async_refresh_once()

    assert source.parent_calls == []
    assert snapshot.data.clips[0].lineage_status == LINEAGE_EXTERNAL
    assert snapshot.data.clips[0].album_title == "Remixes of root"


async def test_cached_lineage_invalidated_when_edited_clip_id_changes() -> None:
    """Fast-path falls back to fresh lookup if the parent link changed upstream."""
    previous = _make_clip(
        "remix",
        title="Remix",
        edited_clip_id="old-parent",
        is_remix=True,
        root_ancestor_id="old-parent",
        lineage_status=LINEAGE_EXTERNAL,
        album_title="Remixes of old-pare",
    )
    incoming = _make_clip("remix", title="Remix", edited_clip_id="new-parent", is_remix=True)
    source = FakeSunoLibraryAdapter(clips=[incoming], parents={"remix": "new-parent", "new-parent": None})
    refresh = LibraryRefresh(source, InMemoryStoredLibrary())
    refresh.current_data = SunoData(clips=[previous])
    refresh.data_version = 1

    snapshot = await refresh.async_refresh_once()

    assert "remix" in source.parent_calls, "expected fresh lookup when edited_clip_id differs"
    assert snapshot.data.clips[0].root_ancestor_id == "new-parent"


async def test_playlist_only_remix_not_starved_by_cached_external_clips() -> None:
    """A new playlist-only remix resolves in one cycle even when prior external clips outnumber the budget.

    Reproduces the verified DnB-style starvation: the new remix appears only in
    ``playlist_clips`` (last in iteration order) while many already-resolved
    external clips would otherwise consume the per-cycle lookup budget.
    """
    cached_count = _MAX_PARENT_LOOKUPS_PER_CYCLE + 5
    cached_clips = [
        _make_clip(
            f"cached-{i}",
            title=f"Cached Remix {i}",
            edited_clip_id=f"cached-parent-{i}",
            is_remix=True,
            root_ancestor_id=f"cached-parent-{i}",
            lineage_status=LINEAGE_EXTERNAL,
            album_title="Remixes of cached-p",
        )
        for i in range(cached_count)
    ]
    fresh_cached_clips = [
        _make_clip(
            f"cached-{i}",
            title=f"Cached Remix {i}",
            edited_clip_id=f"cached-parent-{i}",
            is_remix=True,
        )
        for i in range(cached_count)
    ]
    new_remix = _make_clip(
        "new-dnb",
        title="DnB Version",
        edited_clip_id="new-parent",
        is_remix=True,
    )
    playlist = SunoPlaylist(id="pl", name="Mixes", image_url="", num_clips=1)
    source = FakeSunoLibraryAdapter(
        clips=fresh_cached_clips,
        playlists=[playlist],
        playlist_clips={"pl": [new_remix]},
        parents={"new-dnb": "new-parent", "new-parent": None},
    )
    refresh = LibraryRefresh(source, InMemoryStoredLibrary())
    refresh.current_data = SunoData(clips=cached_clips)
    refresh.data_version = 1

    snapshot = await refresh.async_refresh_once()

    playlist_clip_ids = [c.id for c in snapshot.data.playlist_clips.get("pl", [])]
    assert "new-dnb" in playlist_clip_ids, (
        "playlist-only remix must not be starved out of the playlist by cached external clips"
    )
    assert snapshot.data.hidden_pending_remix_count == 0
    assert source.parent_calls.count("new-dnb") == 1, "fresh remix should get exactly one parent lookup"
    for cached in cached_clips:
        assert cached.id not in source.parent_calls, "cached external clip should not be re-looked-up"


async def test_chain_resolves_in_memory_when_intermediate_parent_root_is_cached_in_previous() -> None:
    """A chain whose root is missing from current data resolves via cached intermediate parent.

    Reproduces the verified second starvation: child -> parent (in library) -> grandparent (NOT in library).
    The grandparent is e.g. an unowned remix-source. The parent's root_ancestor_id was resolved to
    the grandparent on a prior cycle and persisted, but the fresh API fetch returns the parent with
    empty root_ancestor_id. Without pre-population, the in-memory walk steps from child to parent,
    sees parent's empty root_ancestor_id, walks to grandparent, finds it missing from the library,
    breaks the chain, and forces both child and parent to compete for the per-cycle API budget every
    refresh. With pre-population, the parent's cached root is restored before the walk and the
    chain resolves without any API call.
    """
    previous_parent = _make_clip(
        "parent",
        title="Parent",
        edited_clip_id="external-grandparent",
        is_remix=True,
        root_ancestor_id="external-grandparent",
        lineage_status=LINEAGE_EXTERNAL,
        album_title="Remixes of external-g",
    )
    incoming_parent = _make_clip("parent", title="Parent", edited_clip_id="external-grandparent", is_remix=True)
    child = _make_clip("child", title="Child", edited_clip_id="parent", is_remix=True)
    source = FakeSunoLibraryAdapter(clips=[incoming_parent, child], parents={})
    refresh = LibraryRefresh(source, InMemoryStoredLibrary())
    refresh.current_data = SunoData(clips=[previous_parent])
    refresh.data_version = 1

    snapshot = await refresh.async_refresh_once()

    assert source.parent_calls == [], "expected no parent lookup when intermediate parent has cached external root"
    by_id = {c.id: c for c in snapshot.data.clips}
    assert by_id["child"].root_ancestor_id == "external-grandparent"
    assert by_id["child"].lineage_status == LINEAGE_EXTERNAL
    assert by_id["parent"].root_ancestor_id == "external-grandparent"
    assert by_id["parent"].lineage_status == LINEAGE_EXTERNAL


async def test_prepopulation_invalidated_when_edited_clip_id_changes() -> None:
    """Cached lineage must not be reused if the parent linkage changed upstream."""
    previous = _make_clip(
        "remix",
        title="Remix",
        edited_clip_id="old-parent",
        is_remix=True,
        root_ancestor_id="old-parent",
        lineage_status=LINEAGE_EXTERNAL,
        album_title="Remixes of old-pare",
    )
    incoming = _make_clip("remix", title="Remix", edited_clip_id="new-parent", is_remix=True)
    source = FakeSunoLibraryAdapter(clips=[incoming], parents={"remix": "new-parent", "new-parent": None})
    refresh = LibraryRefresh(source, InMemoryStoredLibrary())
    refresh.current_data = SunoData(clips=[previous])
    refresh.data_version = 1

    snapshot = await refresh.async_refresh_once()

    assert "remix" in source.parent_calls, "fresh lookup expected when edited_clip_id differs"
    assert snapshot.data.clips[0].root_ancestor_id == "new-parent"
