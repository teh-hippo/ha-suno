"""Tests for the Suno media proxy."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

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


async def test_view_returns_404_for_missing_clip(hass: HomeAssistant, mock_suno_client: AsyncMock, hass_client) -> None:
    """GET request for a missing clip returns HTTP 404."""
    entry = make_entry()
    with patch("custom_components.suno.SunoClient", return_value=mock_suno_client):
        await setup_entry(hass, entry)

    client = await hass_client()
    resp = await client.get("/api/suno/media/nonexistent-clip")
    assert resp.status == 404
