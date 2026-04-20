"""Tests for the Suno media proxy."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.suno.audio import (
    _build_id3_header,
    _extract_apic,
    _skip_existing_id3,
)
from custom_components.suno.models import TrackMetadata
from custom_components.suno.proxy import (
    SunoMediaProxyView,
)

from .conftest import make_entry, patch_suno_setup, setup_entry

# ── ID3 header builder ──────────────────────────────────────────────


class TestBuildId3Header:
    """Tests for the minimal ID3v2.4 header builder."""

    def test_header_starts_with_id3_magic(self) -> None:
        result = _build_id3_header(TrackMetadata(title="Title", artist="Artist"))
        assert result[:3] == b"ID3"

    def test_header_version_is_2_4(self) -> None:
        result = _build_id3_header(TrackMetadata(title="Title", artist="Artist"))
        assert result[3:5] == b"\x04\x00"

    def test_contains_tit2_frame(self) -> None:
        result = _build_id3_header(TrackMetadata(title="My Song", artist="Artist"))
        assert b"TIT2" in result
        assert b"My Song" in result

    def test_contains_tpe1_frame(self) -> None:
        result = _build_id3_header(TrackMetadata(title="Title", artist="Suno"))
        assert b"TPE1" in result
        assert b"Suno" in result

    def test_utf8_encoding_byte(self) -> None:
        """Each text frame should use UTF-8 encoding (0x03)."""
        result = _build_id3_header(TrackMetadata(title="Title", artist="Artist"))
        # After TIT2 frame header (4 id + 4 size + 2 flags = 10 bytes)
        tit2_pos = result.index(b"TIT2")
        encoding_byte = result[tit2_pos + 10]
        assert encoding_byte == 0x03

    def test_syncsafe_size(self) -> None:
        """The header size field should be a valid syncsafe integer."""
        result = _build_id3_header(TrackMetadata(title="Title", artist="Artist"))
        raw = result[6:10]
        # Each byte must have bit 7 clear (syncsafe)
        for byte in raw:
            assert byte & 0x80 == 0

    def test_unicode_title(self) -> None:
        result = _build_id3_header(TrackMetadata(title="日本語タイトル", artist="アーティスト"))
        assert "日本語タイトル".encode() in result
        assert "アーティスト".encode() in result

    def test_empty_strings(self) -> None:
        result = _build_id3_header(TrackMetadata(title="", artist=""))
        assert result[:3] == b"ID3"
        assert b"TIT2" in result
        assert b"TPE1" in result

    def test_round_trip_size(self) -> None:
        """The size in the header should match the actual frame data size."""
        result = _build_id3_header(TrackMetadata(title="Test Title", artist="Test Artist"))
        raw = result[6:10]
        decoded_size = (raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]
        # Total bytes = 10 (header) + frames
        assert decoded_size == len(result) - 10


# ── Skip existing ID3 ───────────────────────────────────────────────


class TestSkipExistingId3:
    """Tests for stripping existing ID3v2 tags from upstream data."""

    def test_no_id3_tag_passthrough(self) -> None:
        """Non-ID3 data should pass through unchanged."""
        data = b"\xff\xfb\x90\x00" + b"\x00" * 100
        assert _skip_existing_id3(data) == data

    def test_strips_id3_tag(self) -> None:
        """An ID3v2 header followed by audio data should have the tag stripped."""
        # Build a fake 20-byte ID3 tag (size = 20 in syncsafe)
        tag_size = 20
        syncsafe = (
            ((tag_size >> 21) & 0x7F),
            ((tag_size >> 14) & 0x7F),
            ((tag_size >> 7) & 0x7F),
            (tag_size & 0x7F),
        )
        id3_header = b"ID3\x04\x00\x00" + bytes(syncsafe)
        tag_body = b"\x00" * tag_size
        audio_data = b"\xff\xfb\x90\x00audio_payload"
        chunk = id3_header + tag_body + audio_data
        result = _skip_existing_id3(chunk)
        assert result == audio_data

    def test_chunk_too_short(self) -> None:
        """Chunks shorter than 10 bytes pass through even if they start with ID3."""
        data = b"ID3\x04\x00"
        assert _skip_existing_id3(data) == data

    def test_entire_chunk_is_id3_tag(self) -> None:
        """If the whole chunk is ID3 tag data, result should be empty."""
        tag_size = 20
        syncsafe = (0, 0, 0, tag_size)
        id3_header = b"ID3\x04\x00\x00" + bytes(syncsafe)
        chunk = id3_header + b"\x00" * tag_size
        result = _skip_existing_id3(chunk)
        assert result == b""


# ── View integration tests ──────────────────────────────────────────


async def test_view_registered_on_setup(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """The proxy view should be registered when the entry loads."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    # Check that a route matching our pattern exists
    router = hass.http.app.router  # type: ignore[union-attr]
    route_names = [r.get_info().get("formatter", "") for r in router.routes()]
    assert any("/api/suno/media/{clip_id}.{ext}" in name for name in route_names)


async def test_view_clip_not_found(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Requesting a nonexistent clip falls back to first coordinator."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    view = SunoMediaProxyView(hass)
    clip, coordinator = view._find_clip("nonexistent-id")
    assert clip is None
    # Coordinator is the fallback first coordinator (entry exists)
    assert coordinator is not None


async def test_view_finds_clip_in_main_clips(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """The view can find a clip from the main clips list."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    view = SunoMediaProxyView(hass)
    clip, coordinator = view._find_clip("clip-aaa-111")
    assert clip is not None
    assert clip.title == "Test Song Alpha"
    assert coordinator is not None


async def test_view_finds_clip_in_liked(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """The view can find a clip that only exists in liked_clips."""
    mock_suno_client.get_all_songs.return_value = []

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    view = SunoMediaProxyView(hass)
    clip, coordinator = view._find_clip("clip-aaa-111")
    assert clip is not None
    assert clip.title == "Test Song Alpha"
    assert coordinator is not None


async def test_view_falls_back_for_uncached_clip(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """GET for a clip not in cache attempts CDN fetch (returns 502 if CDN fails)."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    client = await hass_client()
    resp = await client.get("/api/suno/media/nonexistent-clip.mp3")
    # Proxy tries CDN but it fails for a fake ID
    assert resp.status == 502


# ── Auth (Release 1: proxy now requires auth) ────────────────────────


def test_view_requires_auth_attribute() -> None:
    """Regression guard: proxy view must declare requires_auth=True."""
    assert SunoMediaProxyView.requires_auth is True


async def test_view_unauthenticated_returns_401(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client_no_auth
) -> None:
    """Unauthenticated GET to the proxy must be rejected with 401."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    client = await hass_client_no_auth()
    for ext in ("mp3", "flac"):
        resp = await client.get(f"/api/suno/media/clip-aaa-111.{ext}")
        assert resp.status == 401, f"{ext} did not require auth"


async def test_view_cancelled_error_propagates(
    hass: HomeAssistant, mock_suno_client: AsyncMock
) -> None:
    """Cache-hit branch must NOT swallow CancelledError."""
    import asyncio as _asyncio

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    view = SunoMediaProxyView(hass)
    # Pre-populate inflight with a future that will appear cancelled.
    fut: _asyncio.Future[bytes | None] = _asyncio.get_running_loop().create_future()
    fut.cancel()
    view._inflight["clip-x.flac"] = fut

    # Build a minimal Request-like stub; we exercise _handle_hq directly.
    # Cancellation from asyncio.shield on a cancelled future should raise CancelledError,
    # not return a 200/502 response.
    raised = False
    try:
        await view._handle_hq("clip-x", None, "t", "a", "audio/flac", None, "", client=None)
    except _asyncio.CancelledError:
        raised = True
    finally:
        view._inflight.pop("clip-x.flac", None)
    assert raised, "CancelledError was swallowed"


# ── RIFF INFO builder ───────────────────────────────────────────────


async def test_view_cache_hit_serves_file(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client, tmp_path
) -> None:
    """When cache has a file, the proxy should serve it directly."""

    entry = make_entry(
        options={
            **make_entry().options,
        }
    )
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    # Set up a fake cached file
    cache_file = tmp_path / "clip-aaa-111.mp3"
    cache_file.write_bytes(b"ID3" + b"\x00" * 100)

    mock_cache = AsyncMock()
    mock_cache.async_get = AsyncMock(return_value=cache_file)
    entry.runtime_data.cache = mock_cache

    client = await hass_client()
    resp = await client.get("/api/suno/media/clip-aaa-111.mp3")
    assert resp.status == 200
    body = await resp.read()
    assert body == b"ID3" + b"\x00" * 100


# ── _find_clip / _get_entry_options edge cases ──────────────────────


async def test_find_clip_skips_entries_without_runtime_data(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Entries with runtime_data=None should be skipped (line 115)."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    # Add a second entry with no runtime_data to exercise the `continue`
    entry2 = make_entry(unique_id="other-user")
    entry2.add_to_hass(hass)
    # Don't set up entry2 — no runtime_data

    view = SunoMediaProxyView(hass)
    # Should still find clip from entry1, skipping entry2
    clip, coordinator = view._find_clip("clip-aaa-111")
    assert clip is not None
    assert coordinator is not None


# ── Streaming handler tests ─────────────────────────────────────────


async def _async_iter(chunks):
    """Helper to create an async iterator from a list of byte chunks."""
    for chunk in chunks:
        yield chunk


async def test_stream_mp3_with_cache(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """MP3 streaming should inject ID3 header and collect chunks for cache (lines 211-246)."""

    entry = make_entry(options={**make_entry().options})
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    mock_cache = AsyncMock()
    mock_cache.async_get = AsyncMock(return_value=None)
    mock_cache.async_put = AsyncMock()
    entry.runtime_data.cache = mock_cache

    audio_data = b"\xff\xfb\x90\x00" + b"\xab" * 200
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content.iter_chunked = lambda size: _async_iter([audio_data])
    mock_response.close = MagicMock()

    with (
        patch("custom_components.suno.proxy.async_get_clientsession") as mock_session,
        patch("custom_components.suno.proxy.fetch_album_art", return_value=None),
    ):
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.mp3")

    assert resp.status == 200
    body = await resp.read()
    assert body[:3] == b"ID3"
    assert audio_data in body
    mock_cache.async_put.assert_awaited_once()


async def test_stream_mp3_without_cache(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """MP3 streaming without cache should not attempt cache write."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    audio_data = b"\xff\xfb\x90\x00" + b"\xab" * 200
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content.iter_chunked = lambda size: _async_iter([audio_data])
    mock_response.close = MagicMock()

    with (
        patch("custom_components.suno.proxy.async_get_clientsession") as mock_session,
        patch("custom_components.suno.proxy.fetch_album_art", return_value=None),
    ):
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.mp3")

    assert resp.status == 200
    body = await resp.read()
    assert body[:3] == b"ID3"


async def test_stream_mp3_strips_existing_id3(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """MP3 streaming should strip existing ID3 from the first upstream chunk."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    # Build a fake upstream response with an existing ID3 tag
    tag_size = 20
    syncsafe = bytes([0, 0, 0, tag_size])
    existing_id3 = b"ID3\x04\x00\x00" + syncsafe + b"\x00" * tag_size
    real_audio = b"\xff\xfb\x90\x00" + b"\xcd" * 50
    upstream_data = existing_id3 + real_audio

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content.iter_chunked = lambda size: _async_iter([upstream_data])
    mock_response.close = MagicMock()

    with (
        patch("custom_components.suno.proxy.async_get_clientsession") as mock_session,
        patch("custom_components.suno.proxy.fetch_album_art", return_value=None),
    ):
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.mp3")

    body = await resp.read()
    assert body[:3] == b"ID3"
    assert real_audio in body


async def test_stream_hq_with_cache(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """High quality should transcode WAV to FLAC and write to cache."""

    entry = make_entry(
        options={
            **make_entry().options,
        }
    )
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    mock_cache = AsyncMock()
    mock_cache.async_get = AsyncMock(return_value=None)
    mock_cache.async_put = AsyncMock()
    entry.runtime_data.cache = mock_cache

    wav_data = _make_test_wav()
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.headers = {"Content-Type": "audio/wav", "Content-Length": str(len(wav_data))}
    mock_response.read = AsyncMock(return_value=wav_data)
    mock_response.close = MagicMock()

    with patch("custom_components.suno.proxy.async_get_clientsession") as mock_session:
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.flac")

    assert resp.status == 200
    body = await resp.read()
    assert body[:4] == b"fLaC"
    mock_cache.async_put.assert_awaited_once()


def _make_test_wav() -> bytes:
    """Build a minimal valid WAV file for testing."""
    import struct

    sample_rate, channels, bits = 48000, 2, 16
    num_samples = 480  # 10ms
    data_size = num_samples * channels * (bits // 8)
    wav = bytearray()
    wav.extend(b"RIFF")
    wav.extend(struct.pack("<I", 36 + data_size))
    wav.extend(b"WAVE")
    wav.extend(b"fmt ")
    wav.extend(
        struct.pack(
            "<IHHIIHH",
            16,
            1,
            channels,
            sample_rate,
            sample_rate * channels * bits // 8,
            channels * bits // 8,
            bits,
        )
    )
    wav.extend(b"data")
    wav.extend(struct.pack("<I", data_size))
    wav.extend(b"\x00" * data_size)
    return bytes(wav)


async def test_stream_hq_without_cache(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """High quality without cache should transcode WAV to FLAC."""

    entry = make_entry(options={**make_entry().options})
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    wav_data = _make_test_wav()
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.headers = {"Content-Type": "audio/wav", "Content-Length": str(len(wav_data))}
    mock_response.read = AsyncMock(return_value=wav_data)
    mock_response.close = MagicMock()

    with patch("custom_components.suno.proxy.async_get_clientsession") as mock_session:
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.flac")

    assert resp.status == 200
    body = await resp.read()
    assert body[:4] == b"fLaC"


async def test_upstream_non_200_returns_502(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """Non-200 upstream response should return 502 (lines 173-178)."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    mock_response = AsyncMock()
    mock_response.status = 404
    mock_response.close = MagicMock()

    with patch("custom_components.suno.proxy.async_get_clientsession") as mock_session:
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.mp3")

    assert resp.status == 502
    text = await resp.text()
    assert "404" in text


async def test_upstream_200_wav_path(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """Upstream 200 with high quality should transcode WAV to FLAC."""

    entry = make_entry(options={**make_entry().options})
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    wav_data = _make_test_wav()
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.headers = {"Content-Type": "audio/wav", "Content-Length": str(len(wav_data))}
    mock_response.read = AsyncMock(return_value=wav_data)
    mock_response.close = MagicMock()

    with patch("custom_components.suno.proxy.async_get_clientsession") as mock_session:
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.flac")

    assert resp.status == 200
    body = await resp.read()
    assert body[:4] == b"fLaC"


async def test_mp3_uses_clip_audio_url(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """For known MP3 clips, the proxy should use clip.audio_url (line 162)."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    audio_data = b"\xff\xfb\x90\x00" + b"\x00" * 50
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content.iter_chunked = lambda size: _async_iter([audio_data])
    mock_response.close = MagicMock()

    with (
        patch("custom_components.suno.proxy.async_get_clientsession") as mock_session,
        patch("custom_components.suno.proxy.fetch_album_art", return_value=None),
    ):
        mock_get = AsyncMock(return_value=mock_response)
        mock_session.return_value.get = mock_get
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.mp3")

    assert resp.status == 200
    # Should have used the clip's audio_url, not CDN_BASE_URL
    called_url = mock_get.call_args[0][0]
    assert called_url == "https://cdn1.suno.ai/clip-aaa-111.mp3"


async def test_save_to_cache_failure_is_silent(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """Cache write failure should be silently logged (lines 292-296)."""

    entry = make_entry(options={**make_entry().options})
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    mock_cache = AsyncMock()
    mock_cache.async_get = AsyncMock(return_value=None)
    mock_cache.async_put = AsyncMock(side_effect=OSError("disk full"))
    entry.runtime_data.cache = mock_cache

    audio_data = b"\xff\xfb\x90\x00" + b"\xab" * 100
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content.iter_chunked = lambda size: _async_iter([audio_data])
    mock_response.close = MagicMock()

    with (
        patch("custom_components.suno.proxy.async_get_clientsession") as mock_session,
        patch("custom_components.suno.proxy.fetch_album_art", return_value=None),
    ):
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.mp3")

    # Should still succeed even though cache write failed
    assert resp.status == 200


async def test_save_to_cache_bytes_failure_is_silent(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client
) -> None:
    """Cache write failure for WAV should be silently logged (lines 301-304)."""

    entry = make_entry(
        options={
            **make_entry().options,
        }
    )
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    mock_cache = AsyncMock()
    mock_cache.async_get = AsyncMock(return_value=None)
    mock_cache.async_put = AsyncMock(side_effect=OSError("disk full"))
    entry.runtime_data.cache = mock_cache

    wav_data = _make_test_wav()
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.headers = {"Content-Type": "audio/wav", "Content-Length": str(len(wav_data))}
    mock_response.read = AsyncMock(return_value=wav_data)
    mock_response.close = MagicMock()

    with patch("custom_components.suno.proxy.async_get_clientsession") as mock_session:
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.flac")

    assert resp.status == 200


# ── ffmpeg integration tests ────────────────────────────────────────


async def test_wav_to_flac_uses_ffmpeg_manager_binary(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client
) -> None:
    """_wav_to_flac should resolve the binary path via get_ffmpeg_manager."""

    entry = make_entry(options={**make_entry().options})
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    wav_data = _make_test_wav()
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.read = AsyncMock(return_value=wav_data)
    mock_response.close = MagicMock()

    with (
        patch("custom_components.suno.proxy.async_get_clientsession") as mock_session,
        patch("custom_components.suno.proxy.get_ffmpeg_manager") as mock_ffmpeg_mgr,
    ):
        mock_ffmpeg_mgr.return_value.binary = "ffmpeg"
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.flac")

    assert resp.status == 200
    body = await resp.read()
    assert body[:4] == b"fLaC"
    mock_ffmpeg_mgr.assert_called_once_with(hass)


async def test_wav_to_flac_returns_502_on_ffmpeg_failure(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client
) -> None:
    """If ffmpeg returns non-zero, the proxy should return 502."""

    entry = make_entry(options={**make_entry().options})
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    wav_data = _make_test_wav()
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.read = AsyncMock(return_value=wav_data)
    mock_response.close = MagicMock()

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"error details"))
    mock_proc.returncode = 1

    with (
        patch("custom_components.suno.proxy.async_get_clientsession") as mock_session,
        patch("custom_components.suno.proxy.get_ffmpeg_manager") as mock_ffmpeg_mgr,
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
    ):
        mock_ffmpeg_mgr.return_value.binary = "/usr/bin/ffmpeg"
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.flac")

    assert resp.status == 502


async def test_wav_to_flac_returns_502_on_ffmpeg_not_found(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client
) -> None:
    """If the ffmpeg binary is missing, the proxy should return 502."""

    entry = make_entry(options={**make_entry().options})
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    wav_data = _make_test_wav()
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.read = AsyncMock(return_value=wav_data)
    mock_response.close = MagicMock()

    with (
        patch("custom_components.suno.proxy.async_get_clientsession") as mock_session,
        patch("custom_components.suno.proxy.get_ffmpeg_manager") as mock_ffmpeg_mgr,
        patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError),
    ):
        mock_ffmpeg_mgr.return_value.binary = "/nonexistent/ffmpeg"
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.flac")

    assert resp.status == 502


async def test_hq_uses_api_to_get_wav(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """HQ mode should use the API to get/generate WAV URL then transcode."""

    entry = make_entry(options={**make_entry().options})
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    # Mock the WAV API: get_wav_url returns URL immediately
    mock_suno_client.get_wav_url = AsyncMock(return_value="https://cdn1.suno.ai/clip-aaa-111.wav")
    mock_suno_client.request_wav = AsyncMock()

    wav_data = _make_test_wav()
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.read = AsyncMock(return_value=wav_data)
    mock_response.close = MagicMock()

    with patch("custom_components.suno.proxy.async_get_clientsession") as mock_session:
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.flac")

    assert resp.status == 200
    body = await resp.read()
    assert body[:4] == b"fLaC"
    # Should NOT have called request_wav since get_wav_url returned immediately
    mock_suno_client.request_wav.assert_not_awaited()


async def test_hq_triggers_wav_generation(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """HQ mode should trigger convert_wav then poll when WAV not ready."""

    entry = make_entry(options={**make_entry().options})
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    # First call returns None, second returns URL
    mock_suno_client.get_wav_url = AsyncMock(side_effect=[None, "https://cdn1.suno.ai/clip-aaa-111.wav"])
    mock_suno_client.request_wav = AsyncMock()

    wav_data = _make_test_wav()
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.read = AsyncMock(return_value=wav_data)
    mock_response.close = MagicMock()

    with (
        patch("custom_components.suno.proxy.async_get_clientsession") as mock_session,
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.flac")

    assert resp.status == 200
    mock_suno_client.request_wav.assert_awaited_once()


async def test_hq_returns_502_when_no_client(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """HQ mode should return 502 if no SunoClient is available."""

    entry = make_entry(options={**make_entry().options})
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    # Remove runtime_data to simulate no client
    entry.runtime_data = None

    client = await hass_client()
    resp = await client.get("/api/suno/media/clip-aaa-111.flac")
    assert resp.status == 502


# ── Sync file serving ───────────────────────────────────────────────


async def test_serves_synced_mp3_with_correct_mime(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client, tmp_path
) -> None:
    """When sync has an MP3 and playback quality is standard, serve with audio/mpeg."""
    from pathlib import Path

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    synced_file = tmp_path / "clip-aaa-111.mp3"
    synced_file.write_bytes(b"ID3" + b"\x00" * 100)

    mock_dm = MagicMock()
    mock_dm.get_downloaded_path = MagicMock(return_value=Path(synced_file))
    entry.runtime_data.download_manager = mock_dm

    client = await hass_client()
    resp = await client.get("/api/suno/media/clip-aaa-111.mp3")
    assert resp.status == 200
    assert resp.headers["Content-Type"] == "audio/mpeg"
    body = await resp.read()
    assert body == b"ID3" + b"\x00" * 100


async def test_skips_synced_flac_when_mp3_requested(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client
) -> None:
    """When sync has FLAC but playback quality is standard, fall through to CDN."""
    from pathlib import Path

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    synced_file = Path("/fake/clip-aaa-111.flac")

    mock_dm = MagicMock()
    mock_dm.get_downloaded_path = MagicMock(return_value=synced_file)
    entry.runtime_data.download_manager = mock_dm

    # Falls through to CDN; with no CDN mock it will 502
    with patch("custom_components.suno.proxy.async_get_clientsession") as mock_session:
        mock_session.return_value.get = AsyncMock(side_effect=Exception("CDN not mocked"))
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.mp3")
    assert resp.status == 502


async def test_serves_synced_flac_with_correct_mime(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client, tmp_path
) -> None:
    """When sync has a FLAC and playback quality is high, serve with audio/flac."""
    from pathlib import Path

    entry = make_entry(
        options={**make_entry().options},
    )
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    synced_file = tmp_path / "clip-aaa-111.flac"
    synced_file.write_bytes(b"fLaC" + b"\x00" * 100)

    mock_dm = MagicMock()
    mock_dm.get_downloaded_path = MagicMock(return_value=Path(synced_file))
    entry.runtime_data.download_manager = mock_dm

    client = await hass_client()
    resp = await client.get("/api/suno/media/clip-aaa-111.flac")
    assert resp.status == 200
    assert resp.headers["Content-Type"] == "audio/flac"
    body = await resp.read()
    assert body == b"fLaC" + b"\x00" * 100


async def test_skips_synced_mp3_when_flac_requested(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client
) -> None:
    """When sync has MP3 but playback quality is high, fall through to HQ pipeline."""
    from pathlib import Path

    entry = make_entry(
        options={**make_entry().options},
    )
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    synced_file = Path("/fake/clip-aaa-111.mp3")

    mock_dm = MagicMock()
    mock_dm.get_downloaded_path = MagicMock(return_value=synced_file)
    entry.runtime_data.download_manager = mock_dm

    # Falls through to HQ pipeline; without mocks it will fail
    client = await hass_client()
    resp = await client.get("/api/suno/media/clip-aaa-111.flac")
    assert resp.status == 502


# ── TC-8: Proxy concurrency and disconnect tests ────────────────────


async def test_hq_inflight_coalescing(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """When an inflight future exists, _handle_hq awaits it instead of running a new pipeline."""

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    view = SunoMediaProxyView(hass)
    flac_data = b"fLaC" + b"\x00" * 10

    # Pre-set an inflight future that resolves to FLAC data
    fut: asyncio.Future[bytes | None] = hass.loop.create_future()
    fut.set_result(flac_data)
    view._inflight["clip-aaa-111.flac"] = fut

    resp = await view._handle_hq("clip-aaa-111", None, "Title", "Artist", "audio/flac", None, "", None)
    assert resp.status == 200
    assert resp.body == flac_data
    # Future should have been consumed
    view._inflight.pop("clip-aaa-111.flac", None)


async def test_hq_inflight_timeout_returns_none(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """When inflight future times out, result is None and pipeline runs fresh."""

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    view = SunoMediaProxyView(hass)

    # Pre-set an inflight future that never resolves
    fut: asyncio.Future[bytes | None] = hass.loop.create_future()
    view._inflight["clip-aaa-111.flac"] = fut

    with patch("custom_components.suno.proxy.asyncio.wait_for", side_effect=TimeoutError):
        # With client=None, pipeline returns None → 502
        resp = await view._handle_hq("clip-aaa-111", None, "Title", "Artist", "audio/flac", None, "", None)
        assert resp.status == 502

    fut.cancel()
    view._inflight.pop("clip-aaa-111.flac", None)


async def test_hq_cancelled_error_reraised(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """CancelledError during HQ pipeline is re-raised, not swallowed."""
    import asyncio

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    view = SunoMediaProxyView(hass)

    with (
        patch.object(view, "_run_hq_pipeline", side_effect=asyncio.CancelledError),
        pytest.raises(asyncio.CancelledError),
    ):
        await view._handle_hq("clip-x", None, "T", "A", "audio/flac", None, "", object())

    # Inflight should be cleaned up
    assert "clip-x.flac" not in view._inflight


async def test_mp3_connection_reset_handled(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """ConnectionResetError during MP3 streaming should be caught gracefully."""

    from custom_components.suno.models import SunoClip

    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    view = SunoMediaProxyView(hass)
    clip = SunoClip(
        id="clip-test",
        title="Test",
        audio_url="http://test/a.mp3",
        status="complete",
        image_url="",
        image_large_url="",
        is_liked=False,
        created_at="2026-01-01",
        tags="",
        duration=60.0,
        clip_type="gen",
        has_vocal=True,
    )

    # Mock upstream that raises ConnectionResetError mid-stream
    async def _iter_error(*_a, **_k):
        yield b"some audio data"
        raise ConnectionResetError("gone")

    mock_upstream = MagicMock()
    mock_upstream.status = 200
    mock_upstream.content.iter_chunked = _iter_error
    mock_upstream.close = MagicMock()

    mock_request = MagicMock()
    mock_request.protocol = MagicMock()
    mock_request.protocol.transport = MagicMock()

    # Patch prepare to be a no-op
    with patch("aiohttp.web.StreamResponse.prepare", new_callable=AsyncMock):
        with patch("aiohttp.web.StreamResponse.write", new_callable=AsyncMock):
            resp = await view._handle_mp3(
                mock_request, mock_upstream, "clip-test", clip, "Test", "Suno", "audio/mpeg", None
            )
            # Should complete without raising — ConnectionResetError is caught
            assert resp is not None


# ── TC-8 addendum: concurrency and disconnect ───────────────────────


async def test_concurrent_hq_coalesce(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Two concurrent HQ requests for the same clip_id coalesce — only one pipeline runs."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    view = SunoMediaProxyView(hass)
    gate = asyncio.Event()
    flac_bytes = b"fLaC" + b"\x00" * 100
    call_count = 0

    async def slow_pipeline(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        await gate.wait()
        return flac_bytes

    with patch.object(view, "_run_hq_pipeline", slow_pipeline):
        task1 = asyncio.create_task(view._handle_hq("clip-aaa-111", None, "T", "A", "audio/flac", None, "", object()))
        await asyncio.sleep(0)
        task2 = asyncio.create_task(view._handle_hq("clip-aaa-111", None, "T", "A", "audio/flac", None, "", object()))
        await asyncio.sleep(0)

        gate.set()
        resp1, resp2 = await asyncio.gather(task1, task2)

    assert resp1.status == 200
    assert resp2.status == 200
    assert resp1.body == flac_bytes
    assert resp2.body == flac_bytes
    assert call_count == 1


async def test_inflight_timeout_produces_502(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """When the inflight future resolves to None, the fallback pipeline returns 502."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    view = SunoMediaProxyView(hass)
    fut: asyncio.Future[bytes | None] = asyncio.get_running_loop().create_future()
    fut.set_result(None)
    view._inflight["clip-aaa-111.flac"] = fut

    # client=None → _run_hq_pipeline returns None → 502
    resp = await view._handle_hq("clip-aaa-111", None, "T", "A", "audio/flac", None, "", None)
    assert resp.status == 502


async def test_connection_reset_clears_collected(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """ConnectionResetError during MP3 streaming clears collected — cache is not written."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    mock_cache = AsyncMock()
    mock_cache.async_get = AsyncMock(return_value=None)
    mock_cache.async_put = AsyncMock()
    entry.runtime_data.cache = mock_cache

    audio_data = b"\xff\xfb\x90\x00" + b"\xab" * 200

    async def _error_after_chunk(_size):
        yield audio_data
        raise ConnectionResetError("gone")

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content.iter_chunked = _error_after_chunk
    mock_response.close = MagicMock()

    with (
        patch("custom_components.suno.proxy.async_get_clientsession") as mock_session,
        patch("custom_components.suno.proxy.fetch_album_art", return_value=None),
    ):
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.mp3")

    assert resp.status == 200
    # collected was cleared so cache must not be written
    mock_cache.async_put.assert_not_awaited()


async def test_cancelled_error_in_hq_reraised(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """CancelledError during HQ pipeline is re-raised and inflight future cleaned up."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    view = SunoMediaProxyView(hass)

    view._run_hq_pipeline = AsyncMock(side_effect=asyncio.CancelledError)

    with pytest.raises(asyncio.CancelledError):
        await view._handle_hq("clip-aaa-111", None, "T", "A", "audio/flac", None, "", object())

    assert "clip-aaa-111.flac" not in view._inflight


# ── T11: Downloaded file vanishes before FileResponse ────────────────


async def test_downloaded_file_vanishes_falls_through(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client
) -> None:
    """Proxy falls through to streaming when FileResponse raises for vanished file."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    from pathlib import Path as RealPath

    dm = MagicMock()
    dm.get_downloaded_path = MagicMock(return_value=RealPath("/nonexistent/clip.mp3"))
    entry.runtime_data.download_manager = dm

    # Mock cache to return None so it falls through to streaming
    mock_cache = AsyncMock()
    mock_cache.async_get = AsyncMock(return_value=None)
    entry.runtime_data.cache = mock_cache

    # Make FileResponse raise FileNotFoundError at construction time
    audio_data = b"\xff\xfb\x90\x00" + b"\xab" * 200
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content.iter_chunked = lambda size: _async_iter([audio_data])
    mock_response.close = MagicMock()

    with (
        patch("custom_components.suno.proxy.web.FileResponse", side_effect=FileNotFoundError("gone")),
        patch("custom_components.suno.proxy.async_get_clientsession") as mock_session,
        patch("custom_components.suno.proxy.fetch_album_art", return_value=None),
    ):
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.mp3")

    # Should fall through to CDN streaming
    assert resp.status == 200
    body = await resp.read()
    assert body[:3] == b"ID3"


async def test_mp3_stream_includes_album_art(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """MP3 streaming should fetch album art and embed it as an APIC frame."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    audio_data = b"\xff\xfb\x90\x00" + b"\xab" * 200
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content.iter_chunked = lambda size: _async_iter([audio_data])
    mock_response.close = MagicMock()

    fake_art = b"\xff\xd8\xff\xe0" + b"\x00" * 50  # minimal JPEG-like bytes

    with (
        patch("custom_components.suno.proxy.async_get_clientsession") as mock_session,
        patch("custom_components.suno.proxy.fetch_album_art", return_value=fake_art) as mock_fetch,
    ):
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.mp3")

    assert resp.status == 200
    body = await resp.read()
    assert body[:3] == b"ID3"
    # Verify the APIC frame is present and contains the album art
    extracted = _extract_apic(body)
    assert extracted == fake_art
    mock_fetch.assert_awaited_once()


async def test_mp3_stream_graceful_when_art_unavailable(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client
) -> None:
    """MP3 streaming should still work when album art fetch fails."""
    entry = make_entry()
    with patch_suno_setup(mock_suno_client):
        await setup_entry(hass, entry)

    audio_data = b"\xff\xfb\x90\x00" + b"\xab" * 200
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content.iter_chunked = lambda size: _async_iter([audio_data])
    mock_response.close = MagicMock()

    with (
        patch("custom_components.suno.proxy.async_get_clientsession") as mock_session,
        patch("custom_components.suno.proxy.fetch_album_art", return_value=None),
    ):
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111.mp3")

    assert resp.status == 200
    body = await resp.read()
    assert body[:3] == b"ID3"
    # No APIC frame should be present
    assert _extract_apic(body) is None
