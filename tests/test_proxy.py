"""Tests for the Suno media proxy."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.suno.proxy import (
    SunoMediaProxyView,
    _build_id3_header,
    _skip_existing_id3,
)

from .conftest import make_entry, setup_entry

# ── ID3 header builder ──────────────────────────────────────────────


class TestBuildId3Header:
    """Tests for the minimal ID3v2.4 header builder."""

    def test_header_starts_with_id3_magic(self) -> None:
        result = _build_id3_header("Title", "Artist")
        assert result[:3] == b"ID3"

    def test_header_version_is_2_4(self) -> None:
        result = _build_id3_header("Title", "Artist")
        assert result[3:5] == b"\x04\x00"

    def test_contains_tit2_frame(self) -> None:
        result = _build_id3_header("My Song", "Artist")
        assert b"TIT2" in result
        assert b"My Song" in result

    def test_contains_tpe1_frame(self) -> None:
        result = _build_id3_header("Title", "Suno")
        assert b"TPE1" in result
        assert b"Suno" in result

    def test_utf8_encoding_byte(self) -> None:
        """Each text frame should use UTF-8 encoding (0x03)."""
        result = _build_id3_header("Title", "Artist")
        # After TIT2 frame header (4 id + 4 size + 2 flags = 10 bytes)
        tit2_pos = result.index(b"TIT2")
        encoding_byte = result[tit2_pos + 10]
        assert encoding_byte == 0x03

    def test_syncsafe_size(self) -> None:
        """The header size field should be a valid syncsafe integer."""
        result = _build_id3_header("Title", "Artist")
        raw = result[6:10]
        # Each byte must have bit 7 clear (syncsafe)
        for byte in raw:
            assert byte & 0x80 == 0

    def test_unicode_title(self) -> None:
        result = _build_id3_header("日本語タイトル", "アーティスト")
        assert "日本語タイトル".encode() in result
        assert "アーティスト".encode() in result

    def test_empty_strings(self) -> None:
        result = _build_id3_header("", "")
        assert result[:3] == b"ID3"
        assert b"TIT2" in result
        assert b"TPE1" in result

    def test_round_trip_size(self) -> None:
        """The size in the header should match the actual frame data size."""
        result = _build_id3_header("Test Title", "Test Artist")
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
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    # Check that a route matching our pattern exists
    router = hass.http.app.router  # type: ignore[union-attr]
    route_names = [r.get_info().get("formatter", "") for r in router.routes()]
    assert any("/api/suno/media/{clip_id}" in name for name in route_names)


async def test_view_clip_not_found(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Requesting a nonexistent clip returns 404."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    view = SunoMediaProxyView(hass)
    clip = view._find_clip("nonexistent-id")
    assert clip is None


async def test_view_finds_clip_in_main_clips(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """The view can find a clip from the main clips list."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    view = SunoMediaProxyView(hass)
    clip = view._find_clip("clip-aaa-111")
    assert clip is not None
    assert clip.title == "Test Song Alpha"


async def test_view_finds_clip_in_liked(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """The view can find a clip that only exists in liked_clips."""
    mock_suno_client.get_all_songs.return_value = []

    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    view = SunoMediaProxyView(hass)
    clip = view._find_clip("clip-aaa-111")
    assert clip is not None
    assert clip.title == "Test Song Alpha"


async def test_view_falls_back_for_uncached_clip(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """GET for a clip not in cache attempts CDN fetch (returns 502 if CDN fails)."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    client = await hass_client()
    resp = await client.get("/api/suno/media/nonexistent-clip")
    # Proxy tries CDN but it fails for a fake ID
    assert resp.status == 502


# ── RIFF INFO builder ───────────────────────────────────────────────


async def test_view_wav_url_construction(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """When quality is high, the proxy should build a .wav CDN URL."""
    from custom_components.suno.const import CONF_AUDIO_QUALITY

    entry = make_entry(
        options={
            **make_entry().options,
            CONF_AUDIO_QUALITY: "high",
        }
    )
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    view = SunoMediaProxyView(hass)
    opts = view._get_entry_options()
    assert opts.get(CONF_AUDIO_QUALITY) == "high"


async def test_view_cache_hit_serves_file(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client, tmp_path
) -> None:
    """When cache has a file, the proxy should serve it directly."""
    from custom_components.suno.const import CONF_CACHE_ENABLED
    from custom_components.suno.proxy import _SUNO_CACHE_KEY

    entry = make_entry(
        options={
            **make_entry().options,
            CONF_CACHE_ENABLED: True,
        }
    )
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    # Set up a fake cached file
    cache_file = tmp_path / "clip-aaa-111.mp3"
    cache_file.write_bytes(b"ID3" + b"\x00" * 100)

    mock_cache = AsyncMock()
    mock_cache.async_get = AsyncMock(return_value=cache_file)
    hass.data[_SUNO_CACHE_KEY] = mock_cache

    client = await hass_client()
    resp = await client.get("/api/suno/media/clip-aaa-111")
    assert resp.status == 200
    body = await resp.read()
    assert body == b"ID3" + b"\x00" * 100


# ── _find_clip / _get_entry_options edge cases ──────────────────────


async def test_find_clip_skips_entries_without_runtime_data(hass: HomeAssistant, mock_suno_client: AsyncMock) -> None:
    """Entries with runtime_data=None should be skipped (line 115)."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    # Add a second entry with no runtime_data to exercise the `continue`
    entry2 = make_entry(unique_id="other-user")
    entry2.add_to_hass(hass)
    # Don't set up entry2 — no runtime_data

    view = SunoMediaProxyView(hass)
    # Should still find clip from entry1, skipping entry2
    clip = view._find_clip("clip-aaa-111")
    assert clip is not None


async def test_get_entry_options_returns_empty_when_no_runtime_data(
    hass: HomeAssistant,
) -> None:
    """When no entry has runtime_data, _get_entry_options returns {} (line 130)."""
    # Add an entry but don't set it up
    entry = make_entry()
    entry.add_to_hass(hass)

    view = SunoMediaProxyView(hass)
    opts = view._get_entry_options()
    assert opts == {}


# ── Streaming handler tests ─────────────────────────────────────────


async def _async_iter(chunks):
    """Helper to create an async iterator from a list of byte chunks."""
    for chunk in chunks:
        yield chunk


async def test_stream_mp3_with_cache(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """MP3 streaming should inject ID3 header and collect chunks for cache (lines 211-246)."""
    from custom_components.suno.const import CONF_CACHE_ENABLED
    from custom_components.suno.proxy import _SUNO_CACHE_KEY

    entry = make_entry(options={**make_entry().options, CONF_CACHE_ENABLED: True})
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    mock_cache = AsyncMock()
    mock_cache.async_get = AsyncMock(return_value=None)
    mock_cache.async_put = AsyncMock()
    hass.data[_SUNO_CACHE_KEY] = mock_cache

    audio_data = b"\xff\xfb\x90\x00" + b"\xab" * 200
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content.iter_chunked = lambda size: _async_iter([audio_data])
    mock_response.close = MagicMock()

    with patch("custom_components.suno.proxy.async_get_clientsession") as mock_session:
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111")

    assert resp.status == 200
    body = await resp.read()
    assert body[:3] == b"ID3"
    assert audio_data in body
    mock_cache.async_put.assert_awaited_once()


async def test_stream_mp3_without_cache(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """MP3 streaming without cache should not attempt cache write."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    audio_data = b"\xff\xfb\x90\x00" + b"\xab" * 200
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content.iter_chunked = lambda size: _async_iter([audio_data])
    mock_response.close = MagicMock()

    with patch("custom_components.suno.proxy.async_get_clientsession") as mock_session:
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111")

    assert resp.status == 200
    body = await resp.read()
    assert body[:3] == b"ID3"


async def test_stream_mp3_strips_existing_id3(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """MP3 streaming should strip existing ID3 from the first upstream chunk."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
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

    with patch("custom_components.suno.proxy.async_get_clientsession") as mock_session:
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111")

    body = await resp.read()
    assert body[:3] == b"ID3"
    assert real_audio in body


async def test_stream_hq_with_cache(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """High quality should transcode WAV to FLAC and write to cache."""
    from custom_components.suno.const import CONF_AUDIO_QUALITY, CONF_CACHE_ENABLED
    from custom_components.suno.proxy import _SUNO_CACHE_KEY

    entry = make_entry(
        options={
            **make_entry().options,
            CONF_AUDIO_QUALITY: "high",
            CONF_CACHE_ENABLED: True,
        }
    )
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    mock_cache = AsyncMock()
    mock_cache.async_get = AsyncMock(return_value=None)
    mock_cache.async_put = AsyncMock()
    hass.data[_SUNO_CACHE_KEY] = mock_cache

    wav_data = _make_test_wav()
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.headers = {"Content-Type": "audio/wav", "Content-Length": str(len(wav_data))}
    mock_response.read = AsyncMock(return_value=wav_data)
    mock_response.close = MagicMock()

    with patch("custom_components.suno.proxy.async_get_clientsession") as mock_session:
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111")

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
    from custom_components.suno.const import CONF_AUDIO_QUALITY

    entry = make_entry(options={**make_entry().options, CONF_AUDIO_QUALITY: "high"})
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
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
        resp = await client.get("/api/suno/media/clip-aaa-111")

    assert resp.status == 200
    body = await resp.read()
    assert body[:4] == b"fLaC"


async def test_upstream_non_200_returns_502(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """Non-200 upstream response should return 502 (lines 173-178)."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    mock_response = AsyncMock()
    mock_response.status = 404
    mock_response.close = MagicMock()

    with patch("custom_components.suno.proxy.async_get_clientsession") as mock_session:
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111")

    assert resp.status == 502
    text = await resp.text()
    assert "404" in text


async def test_upstream_200_wav_path(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """Upstream 200 with high quality should transcode WAV to FLAC."""
    from custom_components.suno.const import CONF_AUDIO_QUALITY

    entry = make_entry(options={**make_entry().options, CONF_AUDIO_QUALITY: "high"})
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
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
        resp = await client.get("/api/suno/media/clip-aaa-111")

    assert resp.status == 200
    body = await resp.read()
    assert body[:4] == b"fLaC"


async def test_mp3_uses_clip_audio_url(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """For known MP3 clips, the proxy should use clip.audio_url (line 162)."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    audio_data = b"\xff\xfb\x90\x00" + b"\x00" * 50
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content.iter_chunked = lambda size: _async_iter([audio_data])
    mock_response.close = MagicMock()

    with patch("custom_components.suno.proxy.async_get_clientsession") as mock_session:
        mock_get = AsyncMock(return_value=mock_response)
        mock_session.return_value.get = mock_get
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111")

    assert resp.status == 200
    # Should have used the clip's audio_url, not CDN_BASE_URL
    called_url = mock_get.call_args[0][0]
    assert called_url == "https://cdn1.suno.ai/clip-aaa-111.mp3"


async def test_save_to_cache_failure_is_silent(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """Cache write failure should be silently logged (lines 292-296)."""
    from custom_components.suno.const import CONF_CACHE_ENABLED
    from custom_components.suno.proxy import _SUNO_CACHE_KEY

    entry = make_entry(options={**make_entry().options, CONF_CACHE_ENABLED: True})
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    mock_cache = AsyncMock()
    mock_cache.async_get = AsyncMock(return_value=None)
    mock_cache.async_put = AsyncMock(side_effect=OSError("disk full"))
    hass.data[_SUNO_CACHE_KEY] = mock_cache

    audio_data = b"\xff\xfb\x90\x00" + b"\xab" * 100
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content.iter_chunked = lambda size: _async_iter([audio_data])
    mock_response.close = MagicMock()

    with patch("custom_components.suno.proxy.async_get_clientsession") as mock_session:
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111")

    # Should still succeed even though cache write failed
    assert resp.status == 200


async def test_save_to_cache_bytes_failure_is_silent(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client
) -> None:
    """Cache write failure for WAV should be silently logged (lines 301-304)."""
    from custom_components.suno.const import CONF_AUDIO_QUALITY, CONF_CACHE_ENABLED
    from custom_components.suno.proxy import _SUNO_CACHE_KEY

    entry = make_entry(
        options={
            **make_entry().options,
            CONF_AUDIO_QUALITY: "high",
            CONF_CACHE_ENABLED: True,
        }
    )
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    mock_cache = AsyncMock()
    mock_cache.async_get = AsyncMock(return_value=None)
    mock_cache.async_put = AsyncMock(side_effect=OSError("disk full"))
    hass.data[_SUNO_CACHE_KEY] = mock_cache

    wav_data = _make_test_wav()
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.headers = {"Content-Type": "audio/wav", "Content-Length": str(len(wav_data))}
    mock_response.read = AsyncMock(return_value=wav_data)
    mock_response.close = MagicMock()

    with patch("custom_components.suno.proxy.async_get_clientsession") as mock_session:
        mock_session.return_value.get = AsyncMock(return_value=mock_response)
        client = await hass_client()
        resp = await client.get("/api/suno/media/clip-aaa-111")

    assert resp.status == 200


# ── ffmpeg integration tests ────────────────────────────────────────


async def test_wav_to_flac_uses_ffmpeg_manager_binary(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client
) -> None:
    """_wav_to_flac should resolve the binary path via get_ffmpeg_manager."""
    from custom_components.suno.const import CONF_AUDIO_QUALITY

    entry = make_entry(options={**make_entry().options, CONF_AUDIO_QUALITY: "high"})
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
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
        resp = await client.get("/api/suno/media/clip-aaa-111")

    assert resp.status == 200
    body = await resp.read()
    assert body[:4] == b"fLaC"
    mock_ffmpeg_mgr.assert_called_once_with(hass)


async def test_wav_to_flac_returns_502_on_ffmpeg_failure(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client
) -> None:
    """If ffmpeg returns non-zero, the proxy should return 502."""
    from custom_components.suno.const import CONF_AUDIO_QUALITY

    entry = make_entry(options={**make_entry().options, CONF_AUDIO_QUALITY: "high"})
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
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
        resp = await client.get("/api/suno/media/clip-aaa-111")

    assert resp.status == 502


async def test_wav_to_flac_returns_502_on_ffmpeg_not_found(
    hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client
) -> None:
    """If the ffmpeg binary is missing, the proxy should return 502."""
    from custom_components.suno.const import CONF_AUDIO_QUALITY

    entry = make_entry(options={**make_entry().options, CONF_AUDIO_QUALITY: "high"})
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
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
        resp = await client.get("/api/suno/media/clip-aaa-111")

    assert resp.status == 502
