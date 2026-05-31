"""Data models for Suno integration."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field, fields
from typing import Any

from .const import CDN_BASE_URL

_LOGGER = logging.getLogger(__name__)


def _fix_cdn_url(url: str | None) -> str:
    """Normalise CDN URLs to the primary endpoint.

    The Suno API sometimes returns cdn2.suno.ai URLs that are unreliable or
    return errors. We rewrite them to cdn1.suno.ai which is the stable CDN.
    """
    return url.replace("cdn2.suno.ai", "cdn1.suno.ai") if url else ""


@dataclass(slots=True)
class SunoUser:
    id: str
    display_name: str


@dataclass(slots=True)
class SunoClip:
    id: str
    title: str
    audio_url: str
    image_url: str
    image_large_url: str
    is_liked: bool
    status: str
    created_at: str
    tags: str
    duration: float
    clip_type: str
    has_vocal: bool
    lyrics: str = ""
    prompt: str = ""
    gpt_description_prompt: str = ""
    video_url: str = ""
    video_is_stale: bool | None = None
    video_cover_url: str = ""
    model_name: str = ""
    major_model_version: str = ""
    display_name: str = ""
    handle: str = ""
    edited_clip_id: str = ""
    is_remix: bool = False
    history: list[dict[str, Any]] | None = None
    root_ancestor_id: str = ""
    lineage_status: str = ""
    album_title: str = ""

    @classmethod
    def from_api_response(cls, raw: dict[str, Any]) -> SunoClip:
        """Construct a SunoClip from a Suno API response payload.

        The API returns a *nested* shape: clip-level fields like ``id``,
        ``title``, ``audio_url``, and the lineage fields
        (``root_ancestor_id``, ``lineage_status``, ``album_title``)
        live at the top of ``raw``, while content metadata like
        ``tags``, ``duration``, and ``prompt`` live under
        ``raw["metadata"]``. The lineage fields were added at top level
        in v6.3.1.

        This is distinct from :func:`_safe_clip`, which reads every
        field from a *flat* dict because that's the on-disk persistence
        shape. Any new clip field that needs to round-trip through both
        API parsing AND persistence must be added in both call sites.
        """
        metadata = raw.get("metadata") or {}
        clip_id = raw.get("id", "")
        audio_url = raw.get("audio_url", "")
        # Suno sometimes returns temporary "audiopipe" streaming URLs that
        # expire and break later playback. Replace with the deterministic
        # CDN URL which is permanent.
        if audio_url and "audiopipe" in audio_url and clip_id:
            audio_url = f"{CDN_BASE_URL}/{clip_id}.mp3"
        return cls(
            id=clip_id,
            title=raw.get("title", "Untitled"),
            audio_url=audio_url,
            image_url=_fix_cdn_url(raw.get("image_url")),
            image_large_url=_fix_cdn_url(raw.get("image_large_url")),
            is_liked=raw.get("is_liked", False),
            status=raw.get("status", "unknown"),
            created_at=raw.get("created_at", ""),
            tags=metadata.get("tags", ""),
            duration=metadata.get("duration") or 0.0,
            clip_type=metadata.get("type", ""),
            has_vocal=metadata.get("has_vocal", False),
            lyrics=raw.get("lyrics", ""),
            prompt=metadata.get("prompt", ""),
            gpt_description_prompt=metadata.get("gpt_description_prompt", ""),
            video_url=_fix_cdn_url(raw.get("video_url")),
            video_is_stale=metadata.get("video_is_stale"),
            video_cover_url=_fix_cdn_url(raw.get("video_cover_url")),
            model_name=raw.get("model_name", ""),
            major_model_version=raw.get("major_model_version", ""),
            display_name=raw.get("display_name", ""),
            handle=raw.get("handle", ""),
            edited_clip_id=metadata.get("edited_clip_id", ""),
            is_remix=bool(metadata.get("is_remix", False)),
            history=metadata.get("history"),
            root_ancestor_id=raw.get("root_ancestor_id", ""),
            lineage_status=raw.get("lineage_status", ""),
            album_title=raw.get("album_title", ""),
        )

    def to_track_metadata(
        self,
        title: str | None = None,
        artist: str | None = None,
        album: str | None = None,
    ) -> TrackMetadata:
        """Build a TrackMetadata from this clip's fields."""
        t = title or self.title
        return TrackMetadata(
            title=t,
            artist=artist or self.display_name or "Suno",
            album=album or self.album_title or t,
            album_artist=self.display_name or "Suno",
            date=self.created_at[:10] if self.created_at else "",
            lyrics=self.prompt,
            comment=self.gpt_description_prompt,
            suno_style=self.tags,
            suno_style_summary=self.gpt_description_prompt,
            suno_model=self.suno_model,
            suno_handle=self.handle,
            suno_parent=self.edited_clip_id,
            suno_lineage=self.suno_lineage,
        )

    @property
    def suno_model(self) -> str:
        """Combined model identifier for metadata tags."""
        if self.model_name and self.major_model_version:
            return f"{self.model_name} ({self.major_model_version})"
        return self.model_name

    @property
    def suno_lineage(self) -> str:
        """Formatted edit lineage for metadata tags."""
        parts: list[str] = []
        if self.is_remix and self.edited_clip_id:
            parts.append(f"Remix of {self.edited_clip_id[:8]}")
        elif self.edited_clip_id:
            parts.append(f"Derived from {self.edited_clip_id[:8]}")
        if self.lineage_status == "unavailable":
            parts.append("Lineage unavailable")
        elif self.root_ancestor_id and self.root_ancestor_id not in {self.id, self.edited_clip_id}:
            parts.append(f"Root {self.root_ancestor_id[:8]}")
        if self.history:
            for entry in self.history:
                parent = (entry.get("id") or "")[:8]
                start = entry.get("infill_start_s")
                end = entry.get("infill_end_s")
                if start is not None and end is not None:
                    mm_s, ss_s = divmod(int(start), 60)
                    mm_e, ss_e = divmod(int(end), 60)
                    desc = f"Edit {mm_s:02d}:{ss_s:02d}-{mm_e:02d}:{ss_e:02d}"
                else:
                    desc = "Edit"
                lyr = (entry.get("infill_lyrics") or "").strip()
                if lyr:
                    preview = lyr[:60].replace("\n", " ")
                    if len(lyr) > 60:
                        preview += "..."
                    desc += f': "{preview}"'
                if parent:
                    desc += f" (from {parent})"
                parts.append(desc)
        return "\n".join(parts)


_CLIP_FIELDS = {f.name for f in fields(SunoClip)}


def _safe_clip(raw: dict[str, Any]) -> SunoClip:
    """Construct a SunoClip, filtering unknown fields for schema compatibility."""
    return SunoClip(**{k: v for k, v in raw.items() if k in _CLIP_FIELDS})


def _safe_clips(raw_list: list[dict[str, Any]]) -> list[SunoClip]:
    """Construct a list of SunoClips, skipping corrupt entries."""
    result: list[SunoClip] = []
    for c in raw_list:
        try:
            result.append(_safe_clip(c))
        except Exception:
            _LOGGER.warning("Skipping corrupt clip entry: %s", c.get("id", "unknown"))
    return result


@dataclass(slots=True)
class SunoCredits:
    credits_left: int
    monthly_limit: int
    monthly_usage: int
    period: str | None

    @classmethod
    def from_api_response(cls, raw: dict[str, Any]) -> SunoCredits:
        return cls(
            credits_left=raw.get("total_credits_left", 0),
            monthly_limit=raw.get("monthly_limit", 0),
            monthly_usage=raw.get("monthly_usage", 0),
            period=raw.get("period"),
        )


@dataclass(slots=True)
class TrackMetadata:
    """Common metadata fields for audio file tagging."""

    title: str = ""
    artist: str = "Suno"
    album: str = ""
    album_artist: str = "Suno"
    date: str = ""
    lyrics: str = ""
    comment: str = ""
    image_data: bytes | None = None
    suno_style: str = ""
    suno_style_summary: str = ""
    suno_model: str = ""
    suno_handle: str = ""
    suno_parent: str = ""
    suno_lineage: str = ""

    def __post_init__(self) -> None:
        """Default album to title when album is empty but title is set.

        Centralises the "album = title" fallback that previously lived in
        four separate sites (proxy.py track construction, audio_stream
        download_as_mp3 / download_and_transcode_to_flac reconstructions,
        and TrackMetadata constructions inside the proxy). Callers that
        want album to stay empty must pass title="" too.
        """
        if not self.album and self.title:
            self.album = self.title


def selected_image_url(clip: SunoClip) -> str:
    """Pick the embedded album art URL for a clip.

    Single source of truth for "which CDN URL holds this clip's cover
    art". Every consumer (download path, ``cover.jpg`` sidecar sync,
    retag path, ``clip_meta_hash``, and the meta-hash that detects
    "needs retag") must go through this helper so an art URL change
    flows uniformly through all detection paths. Returns "" when no
    URL is available; callers needing ``None`` semantics can do
    ``selected_image_url(clip) or None``.
    """
    return clip.image_large_url or clip.image_url or clip.video_cover_url or ""


def image_url_hash(image_url: str) -> str:
    """Short stable digest of an art URL.

    Used as both the on-disk ``.cover_hash`` sentinel and the manifest
    ``embedded_art_hash`` sentinel so a single comparison tells us whether
    the bytes embedded in the audio file match the current Suno art URL.
    """
    return hashlib.md5(image_url.encode()).hexdigest()[:12] if image_url else ""  # noqa: S324


def video_url_hash(video_url: str) -> str:
    """Short stable digest of a video cover URL for staleness detection."""
    return hashlib.md5(video_url.encode()).hexdigest()[:12] if video_url else ""  # noqa: S324


def clip_meta_hash(clip: SunoClip) -> str:
    """Short hash of clip metadata for content change detection.

    Covers everything that affects file *content* (tags, lineage,
    LYRICS, comment, SUNO_STYLE_SUMMARY, SUNO_HANDLE) AND the art URL
    via :func:`selected_image_url`. The art URL is intentionally
    included here even though the engine also tracks it separately as
    ``embedded_art_hash`` — the two checks have different semantics:

    - ``meta_hash`` answers "do the clip's text/art *inputs* differ
      from what we used to generate the file?". A mismatch means we
      should re-tag.
    - ``embedded_art_hash`` answers "does the file *currently on disk*
      contain the art bytes Suno is now serving?". A mismatch means the
      file is stale even if its tags are correct (e.g. cover URL
      regenerated between two reconciles).

    Both checks can trigger a retag for the same root cause (the cover
    URL changed) but they're not redundant: they catch the regression
    at different points in the manifest lifecycle.

    Path-affecting fields like ``display_name`` are deliberately
    excluded; path changes are detected by comparing the rendered path
    against the stored ``path``. ``title`` *is* in the hash because we
    want a title change to trigger both a rename AND a retag — the
    rename is handled by ``_migrate_renamed_paths`` and the retag by
    the meta-hash mismatch here.
    """
    return hashlib.md5(  # noqa: S324
        (
            f"{clip.title}|{clip.tags}|{selected_image_url(clip)}|{clip.video_cover_url}|"
            f"{clip.root_ancestor_id}|{clip.lineage_status}|{clip.album_title}|{clip.prompt}|"
            f"{clip.gpt_description_prompt}|{clip.handle}"
        ).encode()
    ).hexdigest()[:12]


@dataclass(slots=True)
class SunoPlaylist:
    id: str
    name: str
    image_url: str
    num_clips: int

    @classmethod
    def from_api_response(cls, raw: dict[str, Any]) -> SunoPlaylist:
        return cls(
            id=raw.get("id", ""),
            name=raw.get("name", "Untitled"),
            image_url=_fix_cdn_url(raw.get("image_url")),
            num_clips=raw.get("num_total_results", 0),
        )


_PLAYLIST_FIELDS = {f.name for f in fields(SunoPlaylist)}


def _safe_playlist(raw: dict[str, Any]) -> SunoPlaylist:
    """Construct a SunoPlaylist, filtering unknown fields."""
    return SunoPlaylist(**{k: v for k, v in raw.items() if k in _PLAYLIST_FIELDS})


def _safe_playlists(raw_list: list[dict[str, Any]]) -> list[SunoPlaylist]:
    """Construct a list of SunoPlaylists, skipping corrupt entries."""
    result: list[SunoPlaylist] = []
    for p in raw_list:
        try:
            result.append(_safe_playlist(p))
        except Exception:
            _LOGGER.warning("Skipping corrupt playlist entry: %s", p.get("id", "unknown"))
    return result


@dataclass
class SunoData:
    """Aggregate Suno Library snapshot used across the integration."""

    clips: list[SunoClip] = field(default_factory=list)
    liked_clips: list[SunoClip] = field(default_factory=list)
    playlists: list[SunoPlaylist] = field(default_factory=list)
    playlist_clips: dict[str, list[SunoClip]] = field(default_factory=dict)
    credits: SunoCredits | None = None
    stale_sections: tuple[str, ...] = ()
    hidden_pending_remix_count: int = 0
    unavailable_lineage_count: int = 0
