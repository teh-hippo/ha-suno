"""Animated WebP conversion helpers for Suno video artwork."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shlex
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from homeassistant.core import HomeAssistant

from ..const import (
    DEFAULT_VIDEO_FFMPEG_EXTRA_ARGS,
    DEFAULT_VIDEO_LOSSLESS,
    DEFAULT_VIDEO_MAX_FPS,
    DEFAULT_VIDEO_MAX_WIDTH,
    DEFAULT_VIDEO_QUALITY,
    DOWNLOAD_FFMPEG_TIMEOUT,
    VIDEO_FFMPEG_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

_WEBP_MIN_BYTES = 12


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _unlink_missing_ok(path: Path) -> None:
    path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class VideoArtSettings:
    """Settings controlling MP4 to animated WebP conversion."""

    video_quality: int = DEFAULT_VIDEO_QUALITY
    video_lossless: bool = DEFAULT_VIDEO_LOSSLESS
    video_max_fps: int = DEFAULT_VIDEO_MAX_FPS
    video_max_width: int = DEFAULT_VIDEO_MAX_WIDTH
    video_ffmpeg_extra_args: str = DEFAULT_VIDEO_FFMPEG_EXTRA_ARGS

    def __post_init__(self) -> None:
        """Validate user-configurable conversion bounds."""
        if not 0 <= self.video_quality <= 100:
            raise ValueError("video_quality must be between 0 and 100")
        if not isinstance(self.video_lossless, bool):
            raise ValueError("video_lossless must be a boolean")
        if not 0 <= self.video_max_fps <= 60:
            raise ValueError("video_max_fps must be between 0 and 60")
        if not 0 <= self.video_max_width <= 4000:
            raise ValueError("video_max_width must be between 0 and 4000")
        if not isinstance(self.video_ffmpeg_extra_args, str):
            raise ValueError("video_ffmpeg_extra_args must be a string")
        object.__setattr__(self, "video_ffmpeg_extra_args", self.video_ffmpeg_extra_args.strip())

    def as_storage(self) -> dict[str, bool | int | str]:
        """Return a stable serialisable representation for conversion state."""
        return asdict(self)


def _build_webp_options(settings: VideoArtSettings) -> list[str]:
    extra_args = settings.video_ffmpeg_extra_args.strip()
    if extra_args:
        return shlex.split(extra_args)

    args = ["-c:v", "libwebp_anim"]

    if settings.video_lossless:
        args.extend(["-lossless", "1", "-compression_level", "0"])
    else:
        args.extend(["-quality", str(settings.video_quality), "-compression_level", "0"])

    filters: list[str] = []
    if settings.video_max_fps > 0:
        filters.append(f"fps={settings.video_max_fps}")
    if settings.video_max_width > 0:
        filters.append(f"scale='min({settings.video_max_width},iw)':-2")
    if filters:
        args.extend(["-vf", ",".join(filters)])

    args.extend(["-loop", "0", "-an"])
    return args


def _has_valid_webp(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(_WEBP_MIN_BYTES)
            return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    except OSError:
        return False


async def probe_libwebp_anim(hass: HomeAssistant, ffmpeg_binary: str) -> bool:
    """Return whether the configured ffmpeg binary exposes libwebp_anim."""

    def _probe() -> bool:
        try:
            result = subprocess.run(  # noqa: S603 - fixed arg list, no shell.
                [ffmpeg_binary, "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=DOWNLOAD_FFMPEG_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as err:
            _LOGGER.debug("Unable to probe ffmpeg animated WebP support: %s", err)
            return False

        stdout = result.stdout if isinstance(result.stdout, str) else ""
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        return "libwebp_anim" in stdout + stderr

    return await hass.async_add_executor_job(_probe)


async def convert_mp4_to_webp(
    hass: HomeAssistant,
    ffmpeg_binary: str,
    mp4_path: Path,
    webp_path: Path,
    settings: VideoArtSettings | None = None,
) -> bool:
    """Convert an MP4 artwork video into animated WebP.

    ``video_ffmpeg_extra_args`` is an advanced escape hatch. When set, the
    shlex-parsed tokens replace the generated codec and filter options, while
    the integration still controls the primary input and final output path. The
    tokens are passed directly to ``asyncio.create_subprocess_exec`` without a
    shell, so shell metacharacters are not expanded. A user who sets this option
    still controls ffmpeg flags and may ask ffmpeg to read or write other paths,
    so treat it as trusted power-user configuration.
    """
    active_settings = settings or VideoArtSettings()
    tmp_path = webp_path.with_suffix(f"{webp_path.suffix}.tmp")

    if not await hass.async_add_executor_job(mp4_path.exists):
        _LOGGER.warning("Video artwork source does not exist: %s", mp4_path)
        return False
    await hass.async_add_executor_job(_ensure_parent, tmp_path)

    try:
        webp_options = _build_webp_options(active_settings)
    except ValueError as err:
        _LOGGER.warning("Invalid ffmpeg extra args for video artwork: %s", err)
        await hass.async_add_executor_job(_unlink_missing_ok, tmp_path)
        return False

    args = [
        ffmpeg_binary,
        "-y",
        "-hide_banner",
        "-i",
        str(mp4_path),
        *webp_options,
        "-f",
        "webp",
        str(tmp_path),
    ]

    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=VIDEO_FFMPEG_TIMEOUT)
    except TimeoutError:
        _LOGGER.warning("Timed out converting video artwork %s", mp4_path.name)
        if proc is not None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
        await hass.async_add_executor_job(_unlink_missing_ok, tmp_path)
        return False
    except OSError as err:
        _LOGGER.warning("Unable to launch ffmpeg for video artwork %s: %s", mp4_path.name, err)
        await hass.async_add_executor_job(_unlink_missing_ok, tmp_path)
        return False

    if proc.returncode != 0:
        _LOGGER.warning(
            "ffmpeg failed converting video artwork %s: %s",
            mp4_path.name,
            (stderr or stdout).decode(errors="ignore").strip(),
        )
        await hass.async_add_executor_job(_unlink_missing_ok, tmp_path)
        return False

    if not await hass.async_add_executor_job(_has_valid_webp, tmp_path):
        _LOGGER.warning("ffmpeg produced an invalid WebP for %s", mp4_path.name)
        await hass.async_add_executor_job(_unlink_missing_ok, tmp_path)
        return False

    await hass.async_add_executor_job(os.replace, tmp_path, webp_path)
    return True
