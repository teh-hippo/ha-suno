# ha-suno

Suno.ai integration for Home Assistant. Browse and play your Suno music library through any HA media player.

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

## Features

- Browse your Suno library in the HA media browser (liked, playlists, recent, all songs)
- Play on any media player (Sonos, Chromecast, Apple TV, etc.)
- Standard (MP3) or high quality (FLAC) audio with embedded metadata and album art
- Optional sync to a media directory (FLAC or MP3, per source)
- Per-source retention mode: sync (managed mirror) or copy (download-only)
- Optional audio cache for faster replay
- Credit usage sensor

## Installation

### HACS (recommended)

1. Open HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/teh-hippo/ha-suno` as an Integration
3. Install "Suno" and restart Home Assistant

### Manual

Copy `custom_components/suno` into your HA `custom_components/` directory and restart.

## Setup

Suno doesn't have a public API. This integration uses a session token from your browser (valid ~2 years).

### Getting your session token

1. Log in to [suno.com](https://suno.com) and open Developer Tools (F12)
2. Go to **Application** → **Cookies** → **suno.com**
3. Copy the value of `__client` (starts with `eyJ...`)

### Adding the integration

1. **Settings → Devices & Services → Add Integration** → search "Suno"
2. Paste your token

If the token expires, HA shows a "needs attention" badge — click it to paste a fresh one.

## Configuration

Available under the integration's options menu.

### Media browser

| Option | Default | Description |
|--------|---------|-------------|
| Show Liked Songs | On | Display the Liked Songs folder |
| Show Recent | On | Display the Recent folder |
| Recent count | 20 | Number of songs in the Recent folder |
| Show Playlists | On | Display playlists |
| Audio quality | Standard | Standard (MP3) or High (FLAC via ffmpeg) |
| Cache refresh | 30 min | How often the library is refreshed from Suno |
| Cache enabled | Off | Cache audio files locally for faster playback |
| Cache max size | 500 MB | Maximum disk space for cached audio |

### Sync

Downloads songs to a local directory for offline access and instant playback. Each source can be configured independently with its own quality and retention mode.

#### General settings

| Option | Default | Description |
|--------|---------|-------------|
| Sync enabled | Off | Enable background sync to a local directory |
| Sync path | -- | Target directory for downloaded files |
| Playlist files | Off | Generate M3U8 playlist files |

#### Per-source settings

Each source (liked songs, playlists, latest songs) has independent quality and mode settings:

| Option | Default | Description |
|--------|---------|-------------|
| Quality | FLAC | FLAC (lossless, requires ffmpeg) or MP3 (direct CDN download with ID3 metadata) |
| Mode | Sync | **Sync**: managed mirror — removes local files when songs are removed from the source. **Copy**: download-only — never deletes files. |

#### Latest songs filters

| Option | Default | Description |
|--------|---------|-------------|
| Latest count | -- | Maximum number of latest songs to sync (0 or empty to disable) |
| Latest days | -- | Sync songs created within this many days (0 or empty to disable) |

When both count and days are set, AND logic applies — a song must satisfy both criteria to be synced.

#### File paths

Synced files use stable clip-ID-based paths (e.g., `liked/abcd1234.flac`). This prevents orphaned files when your library order changes. A disk reconciliation pass runs after each sync to clean up any orphaned files in sync mode.

## Entities

| Entity | Type | Description |
|--------|------|-------------|
| `sensor.suno_credits` | Diagnostic | Remaining credits (attrs: `monthly_limit`, `monthly_usage`, `period`) |
| `sensor.suno_liked_songs` | Diagnostic | Number of liked songs |
| `sensor.suno_cache_size` | Diagnostic | Local audio cache size in MB |
| `sensor.suno_sync_status`* | Diagnostic | Sync state: idle / syncing / error |
| `sensor.suno_synced_files`* | Diagnostic | Number of synced files |
| `sensor.suno_pending_downloads`* | Diagnostic | Clips waiting to download |
| `sensor.suno_sync_library_size`* | Diagnostic | Total sync library size in MB |

*Sync sensors only appear when sync is enabled.

## Audio pipeline

```
Play request
 ├─ Sync directory (FLAC or MP3) → instant
 ├─ Cache (MP3 or FLAC) → instant
 ├─ In-flight request for same clip → coalesced (wait for first to finish)
 └─ Pipeline:
     ├─ Standard: CDN MP3 stream with ID3 tag injection
     └─ HQ: WAV from Suno API → ffmpeg FLAC with metadata + album art
```

Concurrent requests for the same song are coalesced — only one download/transcode runs, the rest wait and serve the result.

## Troubleshooting

- **"Could not authenticate"** — session token expired. Get a fresh one from your browser.
- **Songs not appearing** — library refreshes every 30 min. Adjust in options or wait.
- **Slow HQ playback start** — first play of an unsynced song requires WAV generation on Suno's servers (can take 10–30s). Subsequent plays are cached/instant.
- **Rate limiting (429 errors)** — try increasing the cache refresh interval.

## Limitations

- Uses Suno's internal API which could change without notice
- Playback only — song generation is not supported
- Session token must be manually copied from browser DevTools

## Privacy

Only reads library metadata needed for playback (titles, audio URLs, cover art, tags, duration). No email, username, or personal data is stored. The session token is stored in HA's config entry system.

## Development

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/teh-hippo/ha-suno.git
cd ha-suno
uv sync --all-groups
```

### Running checks

Run the same checks CI runs:

```bash
uv run ruff check .                  # lint
uv run ruff format --check .         # format
uv run mypy custom_components/suno   # types
uv run pytest tests/ -x -q           # tests
```

### Auto-fixing

```bash
uv run ruff check --fix .   # auto-fix lint issues
uv run ruff format .        # auto-format
```

### Pre-push hook

Enable the pre-push hook to catch CI failures locally:

```bash
git config core.hooksPath .githooks
```

### Project structure

```
custom_components/suno/
├── __init__.py       # Entry lifecycle (setup/unload/remove)
├── api.py            # Suno API client (feeds, playlists, credits)
├── auth.py           # Clerk authentication (cookie → JWT)
├── audio.py          # Audio processing (ffmpeg, ID3, WAV→FLAC)
├── cache.py          # On-disk LRU audio cache
├── config_flow.py    # Config and options flows
├── const.py          # Constants and defaults
├── coordinator.py    # Data update coordinator
├── media_source.py   # HA media browser integration
├── models.py         # Data models (SunoClip, SunoPlaylist, etc.)
├── proxy.py          # HTTP proxy with metadata injection + coalescing
├── sensor.py         # Sensor entities
└── sync.py           # Background FLAC sync to local directory
```

## Licence

MIT
