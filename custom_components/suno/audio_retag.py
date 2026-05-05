"""Re-tag flows for existing audio files."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from .audio_metadata import build_id3_header, extract_apic, fix_flac_cover_type, skip_existing_id3
from .const import DOWNLOAD_FFMPEG_TIMEOUT
from .models import TrackMetadata

_LOGGER = logging.getLogger(__name__)


def retag_mp3(path: os.PathLike[str], meta: TrackMetadata) -> bool:
    """Update embedded ID3 metadata in an existing MP3 file.

    Reads album art from the existing ID3 APIC frame if ``meta.image_data``
    is not provided. Falls back to ``cover.jpg`` in the same directory.
    Uses atomic write (tmp + replace) to avoid corruption.
    """
    file_path = Path(path) if not isinstance(path, Path) else path
    try:
        raw = file_path.read_bytes()
    except OSError:
        _LOGGER.warning("Cannot read MP3 for re-tagging: %s", file_path)
        return False

    if meta.image_data is None:
        existing_art = extract_apic(raw)
        if existing_art:
            meta = TrackMetadata(
                title=meta.title,
                artist=meta.artist,
                album=meta.album,
                album_artist=meta.album_artist,
                date=meta.date,
                lyrics=meta.lyrics,
                comment=meta.comment,
                image_data=existing_art,
                suno_style=meta.suno_style,
                suno_style_summary=meta.suno_style_summary,
                suno_model=meta.suno_model,
                suno_handle=meta.suno_handle,
                suno_parent=meta.suno_parent,
                suno_lineage=meta.suno_lineage,
            )
        else:
            cover = file_path.parent / "cover.jpg"
            if cover.is_file():
                try:
                    meta = TrackMetadata(
                        title=meta.title,
                        artist=meta.artist,
                        album=meta.album,
                        album_artist=meta.album_artist,
                        date=meta.date,
                        lyrics=meta.lyrics,
                        comment=meta.comment,
                        image_data=cover.read_bytes(),
                        suno_style=meta.suno_style,
                        suno_style_summary=meta.suno_style_summary,
                        suno_model=meta.suno_model,
                        suno_handle=meta.suno_handle,
                        suno_parent=meta.suno_parent,
                        suno_lineage=meta.suno_lineage,
                    )
                except OSError:
                    pass

    header = build_id3_header(meta)
    body = skip_existing_id3(raw)
    tmp = file_path.with_suffix(".tmp")
    try:
        tmp.write_bytes(header + body)
        os.replace(str(tmp), str(file_path))
        return True
    except OSError:
        _LOGGER.warning("Failed to re-tag MP3: %s", file_path)
        tmp.unlink(missing_ok=True)
        return False


async def retag_flac(
    ffmpeg_binary: str,
    path: os.PathLike[str],
    meta: TrackMetadata,
) -> bool:
    """Update embedded metadata in an existing FLAC file via ffmpeg remux.

    Uses ``-c copy`` (no transcoding) to rewrite metadata tags without
    re-encoding audio. Re-applies the cover-type fix after remux.
    Uses atomic write (tmp + replace) to avoid corruption.
    """
    file_path = Path(path) if not isinstance(path, Path) else path
    if not file_path.is_file():
        _LOGGER.debug("Cannot re-tag FLAC, file missing: %s", file_path)
        return False

    tmp_img_path: str | None = None
    proc: asyncio.subprocess.Process | None = None
    try:
        args = [ffmpeg_binary, "-y", "-i", str(file_path)]

        if meta.image_data:
            fd, tmp_img_path = tempfile.mkstemp(suffix=".jpg")
            os.write(fd, meta.image_data)
            os.close(fd)
            args.extend(["-i", tmp_img_path])
            args.extend(["-map", "0:a:0", "-map", "1:v:0", "-c:v", "copy", "-disposition:v:0", "attached_pic"])
        else:
            args.extend(["-map", "0:a:0"])
            args.extend(["-map", "0:v?", "-c:v", "copy"])

        args.extend(["-c:a", "copy"])

        cmd = [
            "-metadata",
            f"title={meta.title}",
            "-metadata",
            f"artist={meta.artist}",
            "-metadata",
            f"album={meta.album}",
        ]
        optional = [
            ("albumartist", meta.album_artist),
            ("date", meta.date),
            ("LYRICS", meta.lyrics),
            ("comment", meta.comment),
            ("SUNO_STYLE", meta.suno_style),
            ("SUNO_STYLE_SUMMARY", meta.suno_style_summary),
            ("SUNO_MODEL", meta.suno_model),
            ("SUNO_HANDLE", meta.suno_handle),
            ("SUNO_PARENT", meta.suno_parent),
            ("SUNO_LINEAGE", meta.suno_lineage),
        ]
        for key, val in optional:
            cmd.extend(["-metadata", f"{key}={val}"])

        tmp_out = file_path.with_suffix(".retag.tmp")
        args.extend(cmd + ["-f", "flac", str(tmp_out)])

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=DOWNLOAD_FFMPEG_TIMEOUT)
        if proc.returncode != 0:
            _LOGGER.warning("ffmpeg re-tag failed: %s", stderr.decode()[:200])
            tmp_out.unlink(missing_ok=True)
            return False

        def _finalise() -> None:
            result = tmp_out.read_bytes()
            if meta.image_data:
                result = fix_flac_cover_type(result)
                tmp_out.write_bytes(result)
            os.replace(str(tmp_out), str(file_path))

        await asyncio.to_thread(_finalise)
        return True
    except TimeoutError:
        _LOGGER.error("ffmpeg re-tag timed out for %s", file_path)
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
        return False
    except FileNotFoundError:
        _LOGGER.error("ffmpeg not found for FLAC re-tagging")
        return False
    except Exception:
        _LOGGER.exception("FLAC re-tag error for %s", file_path)
        return False
    finally:
        if tmp_img_path:
            try:
                os.unlink(tmp_img_path)
            except OSError:
                pass
        tmp_out_path = file_path.with_suffix(".retag.tmp")
        if tmp_out_path.exists():
            tmp_out_path.unlink(missing_ok=True)
