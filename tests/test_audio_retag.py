"""Tests for the ``audio_retag`` module (retag_mp3, retag_flac).

Split from the legacy 1134-line ``test_audio.py`` by the Round 2 test
restructure.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from custom_components.suno.audio_metadata import (
    build_id3_header,
    extract_apic,
)
from custom_components.suno.audio_retag import retag_flac, retag_mp3
from custom_components.suno.models import TrackMetadata

# ── retag_mp3 ──────────────────────────────────────────────────────


def test_retag_mp3_updates_metadata(tmp_path) -> None:
    """retag_mp3 strips old ID3 and writes new metadata."""
    # Build an MP3 with old metadata
    old_meta = TrackMetadata(title="Old Title", artist="Old Artist", album="Old Album")
    old_header = build_id3_header(old_meta)
    audio_data = b"\xff\xfb\x90\x00" + b"\x00" * 100  # Fake MP3 frame
    mp3_path = tmp_path / "test.mp3"
    mp3_path.write_bytes(old_header + audio_data)

    # Re-tag with new metadata
    new_meta = TrackMetadata(title="New Title", artist="New Artist", album="New Album")
    assert retag_mp3(mp3_path, new_meta) is True

    # Verify new file has correct structure
    result = mp3_path.read_bytes()
    assert result[:3] == b"ID3"
    # Audio data should be preserved
    assert result.endswith(audio_data)


def test_retag_mp3_preserves_existing_album_art(tmp_path) -> None:
    """retag_mp3 preserves album art from the existing ID3 APIC frame."""
    image_data = b"\xff\xd8\xff\xe0" + b"\x42" * 50  # Fake JPEG
    old_meta = TrackMetadata(title="Song", artist="Old", album="Album", image_data=image_data)
    old_header = build_id3_header(old_meta)
    audio_data = b"\xff\xfb\x90\x00" + b"\x00" * 100
    mp3_path = tmp_path / "test.mp3"
    mp3_path.write_bytes(old_header + audio_data)

    # Re-tag WITHOUT providing image_data
    new_meta = TrackMetadata(title="Song", artist="New Artist", album="Album")
    assert retag_mp3(mp3_path, new_meta) is True

    # The re-tagged file should still contain the APIC frame
    result = mp3_path.read_bytes()
    extracted = extract_apic(result)
    assert extracted is not None
    assert extracted == image_data


def test_retag_mp3_falls_back_to_cover_jpg(tmp_path) -> None:
    """retag_mp3 reads cover.jpg as fallback when no APIC frame exists."""
    # MP3 with no album art
    old_meta = TrackMetadata(title="Song", artist="Old")
    old_header = build_id3_header(old_meta)
    audio_data = b"\xff\xfb\x90\x00" + b"\x00" * 100
    mp3_dir = tmp_path / "artist" / "album"
    mp3_dir.mkdir(parents=True)
    mp3_path = mp3_dir / "test.mp3"
    mp3_path.write_bytes(old_header + audio_data)

    # Place a cover.jpg sidecar
    cover_data = b"\xff\xd8\xff\xe0COVER"
    (mp3_dir / "cover.jpg").write_bytes(cover_data)

    new_meta = TrackMetadata(title="Song", artist="New")
    assert retag_mp3(mp3_path, new_meta) is True

    # Should have embedded the cover.jpg data
    result = mp3_path.read_bytes()
    extracted = extract_apic(result)
    assert extracted == cover_data


def test_retag_mp3_missing_file(tmp_path) -> None:
    """retag_mp3 returns False for non-existent file."""
    meta = TrackMetadata(title="Song", artist="Artist")
    assert retag_mp3(tmp_path / "nonexistent.mp3", meta) is False


def test_retag_mp3_atomic_write(tmp_path) -> None:
    """retag_mp3 uses atomic write -- no .tmp file remains on success."""
    old_meta = TrackMetadata(title="Old", artist="Old")
    audio_data = b"\xff\xfb\x90\x00" + b"\x00" * 50
    mp3_path = tmp_path / "test.mp3"
    mp3_path.write_bytes(build_id3_header(old_meta) + audio_data)

    new_meta = TrackMetadata(title="New", artist="New")
    retag_mp3(mp3_path, new_meta)

    assert not (tmp_path / "test.tmp").exists()
    assert mp3_path.exists()


# ── retag_flac ─────────────────────────────────────────────────────


async def test_retag_flac_calls_ffmpeg_with_copy(tmp_path) -> None:
    """retag_flac uses ffmpeg with -c:a copy (no transcoding)."""
    flac_path = tmp_path / "test.flac"
    flac_data = b"fLaC" + b"\x00" * 50
    flac_path.write_bytes(flac_data)

    meta = TrackMetadata(title="New Title", artist="New Artist", album="New Album")

    proc_mock = AsyncMock()
    proc_mock.communicate = AsyncMock(return_value=(b"", b""))
    proc_mock.returncode = 0

    with patch(
        "custom_components.suno.audio_retag.asyncio.create_subprocess_exec", return_value=proc_mock
    ) as mock_exec:
        # The retag writes to .retag.tmp which we need to simulate
        tmp_out = flac_path.with_suffix(".retag.tmp")

        async def _fake_communicate(*args, **kwargs):
            tmp_out.write_bytes(flac_data)
            return b"", b""

        proc_mock.communicate = _fake_communicate

        result = await retag_flac("/usr/bin/ffmpeg", flac_path, meta)

    assert result is True
    # Verify -c:a copy was in the ffmpeg args (no transcoding)
    call_args = mock_exec.call_args[0]
    assert "-c:a" in call_args
    copy_idx = list(call_args).index("-c:a")
    assert call_args[copy_idx + 1] == "copy"
    # Verify metadata flags
    assert f"title={meta.title}" in " ".join(str(a) for a in call_args)
    assert f"artist={meta.artist}" in " ".join(str(a) for a in call_args)


async def test_retag_flac_applies_cover_type_fix(tmp_path) -> None:
    """retag_flac re-applies fix_flac_cover_type when image_data is provided."""
    flac_path = tmp_path / "test.flac"
    flac_data = b"fLaC" + b"\x00" * 50
    flac_path.write_bytes(flac_data)

    image_data = b"\xff\xd8\xff\xe0IMG"
    meta = TrackMetadata(title="T", artist="A", album="A", image_data=image_data)

    proc_mock = AsyncMock()
    proc_mock.returncode = 0

    with (
        patch("custom_components.suno.audio_retag.asyncio.create_subprocess_exec", return_value=proc_mock),
        patch("custom_components.suno.audio_retag.fix_flac_cover_type", return_value=flac_data) as mock_fix,
    ):
        tmp_out = flac_path.with_suffix(".retag.tmp")

        async def _fake_communicate(*args, **kwargs):
            tmp_out.write_bytes(flac_data)
            return b"", b""

        proc_mock.communicate = _fake_communicate

        result = await retag_flac("/usr/bin/ffmpeg", flac_path, meta)

    assert result is True
    mock_fix.assert_called_once()


async def test_retag_flac_returns_false_on_ffmpeg_failure(tmp_path) -> None:
    """retag_flac returns False when ffmpeg exits with non-zero."""
    flac_path = tmp_path / "test.flac"
    flac_path.write_bytes(b"fLaC" + b"\x00" * 50)

    meta = TrackMetadata(title="T", artist="A", album="A")
    proc_mock = AsyncMock()
    proc_mock.communicate = AsyncMock(return_value=(b"", b"error output"))
    proc_mock.returncode = 1

    with patch("custom_components.suno.audio_retag.asyncio.create_subprocess_exec", return_value=proc_mock):
        result = await retag_flac("/usr/bin/ffmpeg", flac_path, meta)

    assert result is False


async def test_retag_flac_missing_file(tmp_path) -> None:
    """retag_flac returns False for non-existent file."""
    meta = TrackMetadata(title="T", artist="A", album="A")
    result = await retag_flac("/usr/bin/ffmpeg", tmp_path / "missing.flac", meta)
    assert result is False


async def test_retag_flac_ffmpeg_not_found(tmp_path) -> None:
    """retag_flac returns False when ffmpeg binary doesn't exist."""
    flac_path = tmp_path / "test.flac"
    flac_path.write_bytes(b"fLaC" + b"\x00" * 50)

    meta = TrackMetadata(title="T", artist="A", album="A")

    with patch(
        "custom_components.suno.audio_retag.asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError,
    ):
        result = await retag_flac("/nonexistent/ffmpeg", flac_path, meta)

    assert result is False
