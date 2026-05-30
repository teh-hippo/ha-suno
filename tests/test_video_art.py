"""Tests for the video_art module (animated WebP conversion)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import HomeAssistant

from custom_components.suno.downloaded_library.video_art import (
    VideoArtSettings,
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
    mock_proc.kill = Mock()
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


async def _capture_convert_args(
    hass: HomeAssistant,
    tmp_path: Path,
    settings: VideoArtSettings | None = None,
) -> list[object]:
    mp4_path = tmp_path / "test.mp4"
    mp4_path.write_bytes(b"\x00" * 100)
    webp_path = tmp_path / "cover.webp"
    captured_args: list[object] = []

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    mock_proc.kill = Mock()
    mock_proc.wait = AsyncMock()

    async def fake_subprocess(*args: object, **kwargs: object) -> AsyncMock:
        captured_args.extend(args)
        (tmp_path / "cover.webp.tmp").write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        if settings is None:
            result = await convert_mp4_to_webp(hass, "/usr/bin/ffmpeg", mp4_path, webp_path)
        else:
            result = await convert_mp4_to_webp(hass, "/usr/bin/ffmpeg", mp4_path, webp_path, settings)

    assert result is True
    return captured_args


def _arg_after(cmd: list[object], flag: str) -> object:
    return cmd[cmd.index(flag) + 1]


async def test_convert_args_default_is_lossy_q100_no_downsample(hass: HomeAssistant, tmp_path: Path) -> None:
    """Default conversion uses lossy q=100 and preserves source frames."""
    cmd = await _capture_convert_args(hass, tmp_path)

    assert "-quality" in cmd and _arg_after(cmd, "-quality") == "100"
    assert "-lossless" not in cmd
    assert "-vf" not in cmd
    assert "libwebp_anim" in cmd
    assert "-compression_level" in cmd and _arg_after(cmd, "-compression_level") == "6"
    assert "-loop" in cmd and _arg_after(cmd, "-loop") == "0"
    assert "-an" in cmd


async def test_convert_args_lossless_when_enabled(hass: HomeAssistant, tmp_path: Path) -> None:
    """Lossless mode uses libwebp lossless settings and ignores quality."""
    cmd = await _capture_convert_args(hass, tmp_path, VideoArtSettings(video_lossless=True))

    assert "-lossless" in cmd and _arg_after(cmd, "-lossless") == "1"
    assert "-compression_level" in cmd and _arg_after(cmd, "-compression_level") == "6"
    assert "-quality" not in cmd


async def test_convert_args_lossy_custom_quality(hass: HomeAssistant, tmp_path: Path) -> None:
    """Lossy conversion honours the configured WebP quality."""
    cmd = await _capture_convert_args(hass, tmp_path, VideoArtSettings(video_quality=85))

    assert "-quality" in cmd and _arg_after(cmd, "-quality") == "85"
    assert "-lossless" not in cmd


async def test_convert_args_with_fps_cap(hass: HomeAssistant, tmp_path: Path) -> None:
    """A configured max FPS adds a single fps filter."""
    cmd = await _capture_convert_args(hass, tmp_path, VideoArtSettings(video_max_fps=15))

    assert "-vf" in cmd and _arg_after(cmd, "-vf") == "fps=15"
    assert "scale=" not in str(_arg_after(cmd, "-vf"))


async def test_convert_args_with_width_cap(hass: HomeAssistant, tmp_path: Path) -> None:
    """A configured max width adds a single scale filter."""
    cmd = await _capture_convert_args(hass, tmp_path, VideoArtSettings(video_max_width=500))

    assert "-vf" in cmd and _arg_after(cmd, "-vf") == "scale='min(500,iw)':-2"
    assert "fps=" not in str(_arg_after(cmd, "-vf"))


async def test_convert_args_with_both_filters(hass: HomeAssistant, tmp_path: Path) -> None:
    """FPS and width caps are emitted as one comma-joined filter chain."""
    cmd = await _capture_convert_args(
        hass,
        tmp_path,
        VideoArtSettings(video_max_fps=15, video_max_width=500),
    )

    assert cmd.count("-vf") == 1
    assert _arg_after(cmd, "-vf") == "fps=15,scale='min(500,iw)':-2"


async def test_convert_args_ffmpeg_extra_args_overrides_codec_options(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """Raw ffmpeg args replace the generated codec options."""
    cmd = await _capture_convert_args(
        hass,
        tmp_path,
        VideoArtSettings(video_ffmpeg_extra_args="-c:v libwebp_anim -lossless 1 -compression_level 4"),
    )

    assert cmd[cmd.index("-c:v") : cmd.index("-f")] == [
        "-c:v",
        "libwebp_anim",
        "-lossless",
        "1",
        "-compression_level",
        "4",
    ]
    assert "-quality" not in cmd
    assert cmd.count("-compression_level") == 1
    assert _arg_after(cmd, "-compression_level") == "4"


async def test_convert_args_ffmpeg_extra_args_shlex_parses_quoted_strings(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """Raw ffmpeg args use shlex parsing so quoted filters stay intact."""
    cmd = await _capture_convert_args(
        hass,
        tmp_path,
        VideoArtSettings(video_ffmpeg_extra_args='-vf "fps=12,scale=100:100"'),
    )

    assert "-vf" in cmd and _arg_after(cmd, "-vf") == "fps=12,scale=100:100"


async def test_convert_args_ffmpeg_extra_args_preserves_io_boilerplate(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """Raw ffmpeg args cannot replace the integration-controlled input and output paths."""
    mp4_path = tmp_path / "test.mp4"
    webp_tmp_path = tmp_path / "cover.webp.tmp"
    cmd = await _capture_convert_args(
        hass,
        tmp_path,
        VideoArtSettings(video_ffmpeg_extra_args="-i /etc/passwd -f null ignored.webp -c:v libwebp_anim"),
    )

    assert cmd[:5] == ["/usr/bin/ffmpeg", "-y", "-hide_banner", "-i", str(mp4_path)]
    assert cmd[-3:] == ["-f", "webp", str(webp_tmp_path)]
    assert cmd.count(str(mp4_path)) == 1
    assert cmd.count(str(webp_tmp_path)) == 1


async def test_convert_mp4_to_webp_ffmpeg_failure(hass: HomeAssistant, tmp_path: Path) -> None:
    """Failed conversion returns False and cleans up tmp file."""
    mp4_path = tmp_path / "test.mp4"
    mp4_path.write_bytes(b"\x00" * 100)
    webp_path = tmp_path / "cover.webp"

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: codec not found"))
    mock_proc.returncode = 1
    mock_proc.kill = Mock()
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
    mock_proc.kill = Mock()
    mock_proc.wait = AsyncMock()

    async def fake_subprocess(*args: object, **kwargs: object) -> AsyncMock:
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        result = await convert_mp4_to_webp(hass, "/usr/bin/ffmpeg", mp4_path, webp_path)

    assert result is False
    mock_proc.kill.assert_called_once()
