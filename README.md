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

| Option | Default | Description |
|--------|---------|-------------|
| Show Liked Songs | On | Display the Liked Songs folder |
| Show Recent | On | Display the Recent folder |
| Recent count | 20 | Number of songs in the Recent folder |
| Show Playlists | On | Display playlists |
| Cache refresh | 30 min | How often the library is refreshed from Suno |

## Privacy

This integration only reads library metadata needed for playback: song titles, audio URLs, cover art, tags, and duration.  It does not store or expose your email, username, or other personal information.  The session token is stored within Home Assistant's config entry system.

## Limitations

- Suno does not have an official public API.  This integration uses internal endpoints that could change without notice.
- Song generation is not supported.  This is a playback-only integration.
- The session token must be manually copied from your browser's Developer Tools.

## Licence

MIT
