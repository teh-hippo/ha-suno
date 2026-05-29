"""Tests for the video_art module (animated WebP conversion)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.suno.downloaded_library.video_art import (
    convert_mp4_to_webp,
    probe_libwebp_anim,
)


async def test_probe_libwebp_anim_present(hass: HomeAssistant) -> None:
    """Probe returns True when libwebp_anim is in ffmpeg output."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "V..... libwebp_anim         libwebp_anim encoder"
        result = await probe_libwebp_anim(hass, "/usr/bin/ffmpeg")
    assert result is True
    mock_run.assert_called_once()


async def test_probe_libwebp_anim_missing(hass: HomeAssistant) -> None:
    """Probe returns False when libwebp_anim is not available."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "V..... libwebp              libwebp encoder"
        result = await probe_libwebp_anim(hass, "/usr/bin/ffmpeg")
    assert result is False


async def test_probe_libwebp_anim_error(hass: HomeAssistant) -> None:
    """Probe returns False on OSError (ffmpeg not found)."""
    with patch("subprocess.run", side_effect=OSError("not found")):
        result = await probe_libwebp_anim(hass, "/usr/bin/ffmpeg")
    assert result is False


async def test_convert_mp4_to_webp_success(hass: HomeAssistant, tmp_path: Path) -> None:
    """Successful conversion creates the webp file."""
    mp4_path = tmp_path / "test.mp4"
    mp4_path.write_bytes(b"\x00" * 100)
    webp_path = tmp_path / "cover.webp"

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()

    # The convert function writes to .webp.tmp then os.replace to .webp
    # Simulate success by creating the tmp file in a side effect

    async def fake_subprocess(*args: object, **kwargs: object) -> AsyncMock:
        # Simulate ffmpeg writing the output file
        tmp_file = tmp_path / "cover.webp.tmp"
        tmp_file.write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        result = await convert_mp4_to_webp(hass, "/usr/bin/ffmpeg", mp4_path, webp_path)

    assert result is True
    assert webp_path.exists()


async def test_convert_mp4_to_webp_ffmpeg_failure(hass: HomeAssistant, tmp_path: Path) -> None:
    """Failed conversion returns False and cleans up tmp file."""
    mp4_path = tmp_path / "test.mp4"
    mp4_path.write_bytes(b"\x00" * 100)
    webp_path = tmp_path / "cover.webp"

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: codec not found"))
    mock_proc.returncode = 1
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()

    async def fake_subprocess(*args: object, **kwargs: object) -> AsyncMock:
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        result = await convert_mp4_to_webp(hass, "/usr/bin/ffmpeg", mp4_path, webp_path)

    assert result is False
    assert not webp_path.exists()


async def test_convert_mp4_to_webp_missing_source(hass: HomeAssistant, tmp_path: Path) -> None:
    """Conversion returns False when source MP4 doesn't exist."""
    mp4_path = tmp_path / "nonexistent.mp4"
    webp_path = tmp_path / "cover.webp"

    result = await convert_mp4_to_webp(hass, "/usr/bin/ffmpeg", mp4_path, webp_path)
    assert result is False


async def test_convert_mp4_to_webp_timeout(hass: HomeAssistant, tmp_path: Path) -> None:
    """Conversion returns False on timeout and kills the process."""
    mp4_path = tmp_path / "test.mp4"
    mp4_path.write_bytes(b"\x00" * 100)
    webp_path = tmp_path / "cover.webp"

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(side_effect=TimeoutError)
    mock_proc.returncode = None
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()

    async def fake_subprocess(*args: object, **kwargs: object) -> AsyncMock:
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        result = await convert_mp4_to_webp(hass, "/usr/bin/ffmpeg", mp4_path, webp_path)

    assert result is False
    mock_proc.kill.assert_called_once()
