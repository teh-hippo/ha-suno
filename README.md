# ha-suno

Suno.ai integration for Home Assistant.  Browse and play your Suno music library through any HA media player.

## What it does

- Browse your Suno library in the Home Assistant media browser
- Play songs on any media player (Sonos, Apple TV, Chromecast, etc.)
- Filter by liked songs, playlists, or recent creations
- Track your remaining Suno credits via a sensor

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Go to Integrations
3. Click the three-dot menu and select "Custom repositories"
4. Add `https://github.com/teh-hippo/ha-suno` as an Integration
5. Search for "Suno" and install it
6. Restart Home Assistant

### Manual

1. Copy the `custom_components/suno` folder into your HA `custom_components/` directory
2. Restart Home Assistant

## Setup

Suno does not offer a public API.  This integration uses a session token from your browser to access your library.  The token is valid for approximately 2 years.

### Getting your session token

1. Open [suno.com](https://suno.com) in your browser and log in
2. Press **F12** to open Developer Tools
3. Go to the **Application** tab
4. In the sidebar, expand **Cookies** and click **suno.com**
5. Find the row named `__client`
6. Double-click its **Value** cell (starts with `eyJ...`) and copy it

### Adding the integration

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for "Suno"
3. Paste the token value into the field
4. The integration will validate your token and set up

If the token expires, HA will show a "needs attention" badge on the integration card.  Click it to paste a fresh token.

## Media browser

Open any media player card, click "Browse Media", and select **Suno**.  You will see:

- **Liked Songs** -- songs you have liked on Suno
- **Recent** -- your most recently created songs (fetched live)
- **Playlists** -- your Suno playlists
- **All Songs** -- your complete library

These folders can be toggled on or off in the integration options.

## Credits sensor

The integration creates a `sensor.suno_credits` entity showing your remaining credits.  Monthly limit and usage are available as state attributes.

## Configuration options

Available under the integration's options menu:

**Media browser**

| Option | Default | Description |
|--------|---------|-------------|
| Show Liked Songs | On | Display the Liked Songs folder |
| Show Recent | On | Display the Recent folder |
| Recent count | 20 | Number of songs in the Recent folder |
| Show Playlists | On | Display playlists |
| Audio quality | Standard | Standard (MP3) or High (FLAC) |
| Cache refresh | 30 min | How often the library is refreshed from Suno |
| Cache enabled | Off | Cache audio files locally for faster playback |
| Cache max size | 500 MB | Maximum disk space for cached audio |

**Sync settings**

| Option | Default | Description |
|--------|---------|-------------|
| Sync enabled | Off | Download FLAC files to a local directory |
| Sync path | -- | Directory for downloaded files |
| Sync liked | On | Include liked songs in sync |
| Sync all playlists | On | Sync all playlists (or pick specific ones) |
| Recent count | -- | Limit sync to N most recent songs |
| Recent days | -- | Limit sync to songs from the last N days |
| Trash days | 7 | Days before removed files are permanently deleted |

## Removal

1. Go to **Settings > Devices & Services**
2. Click the three-dot menu on the Suno integration card
3. Select **Delete**
4. Restart Home Assistant

## Privacy

This integration only reads library metadata needed for playback: song titles, audio URLs, cover art, tags, and duration.  It does not store or expose your email, username, or other personal information.  The session token is stored within Home Assistant's config entry system.

## Limitations

- Suno does not have an official public API.  This integration uses internal endpoints that could change without notice.
- Song generation is not supported.  This is a playback-only integration.
- The session token must be manually copied from your browser's Developer Tools.

## How data is updated

The integration polls the Suno API at a configurable interval (default 30 minutes).  During each refresh, it fetches your complete library, liked songs, playlists, and credit balance.  The "Recent" folder fetches live data each time you browse it.

## Entities

| Entity | Type | Description |
|--------|------|-------------|
| `sensor.suno_credits` | Diagnostic | Remaining credits. Attrs: `monthly_limit`, `monthly_usage`, `period` |
| `sensor.suno_total_songs` | Diagnostic | Total songs in your library |
| `sensor.suno_liked_songs` | Diagnostic | Number of liked songs |
| `sensor.suno_cache_size` | Diagnostic | Local audio cache size in MB |
| `sensor.suno_sync_status`* | Diagnostic | Sync state: idle / syncing / error |
| `sensor.suno_sync_files`* | Diagnostic | Number of synced files |
| `sensor.suno_sync_pending`* | Diagnostic | Clips waiting to download |
| `sensor.suno_sync_size`* | Diagnostic | Total sync library size in MB |

*Sync sensors only appear when sync is enabled.

## Media source

The Suno media source provides these folders in the media browser:

- **Liked Songs** -- songs you have liked on Suno
- **Recent** -- your most recently created songs (live data)
- **Playlists** -- your Suno playlists and their songs
- **All Songs** -- your complete library (paginated into groups of 50)

## Development

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/teh-hippo/ha-suno.git
cd ha-suno
uv sync --all-groups
```

### Running checks locally

Run the same checks CI runs before pushing:

```bash
uv run ruff check .              # lint
uv run ruff format --check .     # format check
uv run mypy custom_components/suno  # type check
uv run pytest tests/ -x -q       # tests
```

Or all at once:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy custom_components/suno && uv run pytest tests/ -x -q
```

### Auto-fixing

```bash
uv run ruff check --fix .   # auto-fix lint issues
uv run ruff format .        # auto-format
```

### Project structure

```
custom_components/suno/
├── __init__.py       # Entry lifecycle (setup/unload/remove)
├── api.py            # Suno API client (feeds, playlists, credits)
├── auth.py           # Clerk authentication (cookie, JWT, session)
├── audio.py          # Audio processing (ffmpeg, ID3 tags, WAV/FLAC pipeline)
├── cache.py          # On-disk audio cache with LRU eviction
├── config_flow.py    # Config and options flows
├── const.py          # Constants and defaults
├── coordinator.py    # Data update coordinator
├── diagnostics.py    # Diagnostics export
├── exceptions.py     # Custom exceptions
├── media_source.py   # HA media browser integration
├── models.py         # Data models (SunoClip, SunoCredits, etc.)
├── proxy.py          # HTTP proxy for audio streaming with metadata
├── sensor.py         # Sensor entities
└── sync.py           # Background FLAC sync to local directory
```

## Troubleshooting

- **"Could not authenticate"** -- your session token may be expired.  Get a fresh one from your browser.
- **Songs not appearing** -- the library refreshes every 30 minutes.  Change the refresh interval in integration options or wait for the next update.
- **"Incompatible with selected player"** -- make sure you are using v1.2.0 or later which uses the correct audio content type.
- **Rate limiting (429 errors)** -- the integration includes delays between API requests.  If you see these in logs, try increasing the cache refresh interval.

## Examples

Play a random liked song on the kitchen speaker:

```yaml
service: media_player.play_media
target:
  entity_id: media_player.kitchen
data:
  media_content_id: media-source://suno/liked
  media_content_type: music
```

## Use cases

- Browse your Suno music library from any media player card
- Play generated songs on Sonos, Chromecast, or Apple TV
- Monitor your Suno credit usage via the credits sensor
- Create automations that play specific songs at certain times

## Licence

MIT
