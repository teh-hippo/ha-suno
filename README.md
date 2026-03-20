# ha-suno

Suno.ai integration for Home Assistant.  Browse and play your Suno music library through any HA media player.

## What it does

- Browse your Suno library in the Home Assistant media browser
- Play songs on any media player (Sonos, Apple TV, Chromecast, etc.)
- Filter by liked songs, playlists, or recent renders
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

The integration requires a session cookie from your Suno account.  Suno does not offer a public API, so this is the only way to access your library programmatically.

### Getting your cookie

1. Open [suno.com](https://suno.com) in your browser and log in
2. Open Developer Tools (F12 or right-click and select Inspect)
3. Go to the **Network** tab
4. Click on any request to `suno.com`
5. In the **Headers** section, find the **Cookie** header
6. Copy the entire value

### Adding the integration

1. Go to Settings > Devices & Services > Add Integration
2. Search for "Suno"
3. Paste your cookie into the field
4. The integration will validate and set up

The cookie contains a long-lived refresh token (~1 year expiry).  If it expires, HA will prompt you to re-authenticate.

## Media browser

The media browser shows:

- **Liked Songs** -- songs you have liked on Suno (default view)
- **Recent** -- the most recently created songs (fetched live)
- **Playlists** -- your Suno playlists
- **All Songs** -- your complete library

These can be toggled on or off in the integration options.

## Sensor

The integration creates a `sensor.suno_credits` entity showing your remaining credits with monthly limit and usage as attributes.

## Configuration options

| Option | Default | Description |
|--------|---------|-------------|
| Show Liked Songs | On | Display the Liked Songs folder |
| Show Recent | On | Display the Recent folder |
| Recent count | 20 | Number of songs in the Recent folder |
| Show Playlists | On | Display playlists |
| Cache refresh | 30 min | How often the library is refreshed |

## Privacy

This integration only reads your library metadata (titles, audio URLs, cover art).  It does not store or expose your email, username, or other personal information.  The cookie is stored securely within Home Assistant's config entry system.

## Limitations

- Suno does not have an official public API.  This integration uses reverse-engineered internal endpoints that could change without notice.
- Song generation is not supported.  This is a playback-only integration.
- The cookie must be manually extracted from your browser.

## Licence

MIT
