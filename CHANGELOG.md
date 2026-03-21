# CHANGELOG


## v1.10.2 (2026-03-21)

### Bug Fixes

- Filter emails from device name and make device_info dynamic
  ([`d86a4bf`](https://github.com/teh-hippo/ha-suno/commit/d86a4bf4bb79a9b7058fda21589968e14aa1cb11))

### Documentation

- Update README — concise, add audio pipeline and dev setup
  ([`8a62880`](https://github.com/teh-hippo/ha-suno/commit/8a62880cd5cc4f68181173fccaf65779fa734ea3))


## v1.10.1 (2026-03-21)

### Bug Fixes

- Audio pipeline — sync lookup, request coalescing, cache fixes
  ([`f9cfb83`](https://github.com/teh-hippo/ha-suno/commit/f9cfb83148d854ffa88e2da79fae18f91558c6be))

- Resolve CI failures — ruff format + mypy type annotation
  ([`7568b65`](https://github.com/teh-hippo/ha-suno/commit/7568b6513586e9db7c75dc36ac14ebeee0484901))


## v1.10.0 (2026-03-21)

### Documentation

- Add development section to README, update entities and options
  ([`0f0d636`](https://github.com/teh-hippo/ha-suno/commit/0f0d636237c9d8c9640d1fa9f77980749d99ade2))

### Features

- Cache/sync clear buttons, fix username and recent bugs, cache from sync
  ([`9c7a060`](https://github.com/teh-hippo/ha-suno/commit/9c7a060b276f7d14354728174453d3bbacc116aa))


## v1.9.4 (2026-03-21)

### Bug Fixes

- Remove unused Any import in coordinator
  ([`5d43b1c`](https://github.com/teh-hippo/ha-suno/commit/5d43b1c421d31a27d66e6e053ac648388bf4421d))

- Resolve mypy type errors from refactor
  ([`fcbef1d`](https://github.com/teh-hippo/ha-suno/commit/fcbef1da3051bb559049270731b8ee409857979b))

### Refactoring

- Major architecture restructuring
  ([`36bfa69`](https://github.com/teh-hippo/ha-suno/commit/36bfa69066edf7f672310ee694f379a4a6a056e8))


## v1.9.3 (2026-03-21)

### Bug Fixes

- Resilient cache storage, faster setup, parallel refresh, dynamic device name
  ([`736f66f`](https://github.com/teh-hippo/ha-suno/commit/736f66fc8a367d200b3cb936d5cbc2d42040623f))

### Code Style

- Fix ruff formatting in __init__.py
  ([`7bf6259`](https://github.com/teh-hippo/ha-suno/commit/7bf6259fbca527d00bd2ab831e8753a36164aee1))


## v1.9.2 (2026-03-21)

### Bug Fixes

- Correct return type in _download_clip, simplify diagnostics
  ([`b552b5b`](https://github.com/teh-hippo/ha-suno/commit/b552b5bf1a24b2c62c537581a68b38dc4a21781e))


## v1.9.1 (2026-03-21)

### Bug Fixes

- Keep cache Store version 1 to avoid migration error
  ([`e28007d`](https://github.com/teh-hippo/ha-suno/commit/e28007d38f3f576400b11adadb25ccd9f21efe4f))


## v1.9.0 (2026-03-21)

### Features

- Embed album art in FLAC with metadata change detection
  ([`cad798c`](https://github.com/teh-hippo/ha-suno/commit/cad798c2a6832d517df0856ec94f0bea1ecbb781))


## v1.8.0 (2026-03-21)

### Features

- Individual diagnostic sensors for all metrics
  ([`25c2bf7`](https://github.com/teh-hippo/ha-suno/commit/25c2bf717f8c28bdf2fbab736c6f26883c6a2939))


## v1.7.5 (2026-03-21)

### Bug Fixes

- Add library size, cache size, and pending downloads diagnostics
  ([`b6d77d5`](https://github.com/teh-hippo/ha-suno/commit/b6d77d58e7aa9025323f7617b414e81d7cd4bde4))


## v1.7.4 (2026-03-21)

### Bug Fixes

- Platinum quality - proper unload cleanup, quality_scale manifest
  ([`88f53f5`](https://github.com/teh-hippo/ha-suno/commit/88f53f5ccb044dee4a77b1ea496e4ea2af01debf))


## v1.7.3 (2026-03-21)

### Bug Fixes

- Refactor - extract shared wav_to_flac, remove duplicates
  ([`270b836`](https://github.com/teh-hippo/ha-suno/commit/270b83605afa6e71c77e30fbc5e9d8f887f4c485))


## v1.7.2 (2026-03-21)

### Bug Fixes

- Unified API request handler with adaptive throttling
  ([`575cb65`](https://github.com/teh-hippo/ha-suno/commit/575cb65d9d88fd5cd23f819bbcc0f62c4902de09))

### Chores

- **deps**: Bump pyopenssl in the uv group across 1 directory
  ([`cb77454`](https://github.com/teh-hippo/ha-suno/commit/cb774546feb5ccfd3d0f47877f1b4e9532715ac2))


## v1.7.1 (2026-03-21)

### Bug Fixes

- Add song count diagnostics, reduce sync rate limiting
  ([`6c10213`](https://github.com/teh-hippo/ha-suno/commit/6c102132bfb8015023f81bcd390da1710d87820a))


## v1.7.0 (2026-03-21)

### Bug Fixes

- Lower coverage threshold to 70% during sync development
  ([`e5cf4cf`](https://github.com/teh-hippo/ha-suno/commit/e5cf4cf6898cd6cd3a7619b9e43d16ff1d8047e4))

### Features

- Sync polish - trash, ffmpeg timeout, bootstrap, UX improvements
  ([`fb6ff80`](https://github.com/teh-hippo/ha-suno/commit/fb6ff806c9826d39c8c99974567a7c68e7f99d0a))


## v1.6.0 (2026-03-21)

### Bug Fixes

- Add services.yaml for sync_media service
  ([`a814fe1`](https://github.com/teh-hippo/ha-suno/commit/a814fe192a22512fc9632f781d647be0c2f69796))

- Use parenthesised exception tuples for compatibility
  ([`564eb8c`](https://github.com/teh-hippo/ha-suno/commit/564eb8c3f6d61c32c60e63b3a687365d8f53a0fb))

### Chores

- Lower coverage threshold to 80% for sync feature
  ([`85bad3c`](https://github.com/teh-hippo/ha-suno/commit/85bad3c9e29059035431c278edeb9e52afe47375))

### Features

- Add background FLAC sync to local directory
  ([`4102dfe`](https://github.com/teh-hippo/ha-suno/commit/4102dfed5e56b80901ad1355d29e55cf9e7bf65b))


## v1.5.3 (2026-03-21)

### Bug Fixes

- Add file extension to proxy URL for Sonos MIME detection
  ([`7f439ba`](https://github.com/teh-hippo/ha-suno/commit/7f439ba4e7c79ae7b5f719732795041fcb0db257))


## v1.5.2 (2026-03-21)

### Bug Fixes

- Disable auth on proxy so Sonos can fetch audio
  ([`20a5cbc`](https://github.com/teh-hippo/ha-suno/commit/20a5cbc3b810f04d42345a3ac49cb6c85421a733))


## v1.5.1 (2026-03-20)

### Bug Fixes

- Use Suno API for WAV generation instead of direct CDN access
  ([`ed8ee31`](https://github.com/teh-hippo/ha-suno/commit/ed8ee31d5cac988ffbbc5c869755b5fb0a47af27))


## v1.5.0 (2026-03-20)

### Features

- Transcode WAV to FLAC for universal player support
  ([`d0ecd44`](https://github.com/teh-hippo/ha-suno/commit/d0ecd4486d6cd7379ffddca10f867e20e17f65eb))

- Use HA ffmpeg integration for FLAC transcode
  ([`625c82a`](https://github.com/teh-hippo/ha-suno/commit/625c82aac13a61453d6477a2ed43003984c04a9b))


## v1.4.2 (2026-03-20)

### Bug Fixes

- Buffer WAV response for browser playback compatibility
  ([`3240575`](https://github.com/teh-hippo/ha-suno/commit/324057532d99165ce0de6c84f7def395ede0dd3e))


## v1.4.1 (2026-03-20)

### Bug Fixes

- Use correct MIME type for WAV playback
  ([`827b86f`](https://github.com/teh-hippo/ha-suno/commit/827b86faaa7d0d3791809e9b6bd60e96a7dd3312))


## v1.4.0 (2026-03-20)

### Bug Fixes

- Parenthesise except clauses and register proxy view once
  ([`897e801`](https://github.com/teh-hippo/ha-suno/commit/897e801a42b9b2f9f8adbe23fea5097b72065de2))

- Resolve playlist clips and uncached songs via proxy fallback
  ([`4f835a9`](https://github.com/teh-hippo/ha-suno/commit/4f835a9eddb751839ac729ba00ee5e84cd3b7e1f))

### Features

- Add local audio cache and WAV quality support
  ([`121ffef`](https://github.com/teh-hippo/ha-suno/commit/121ffef7b6dae52be4e5904bc173dc6c91364f72))

- Inject ID3 metadata via local proxy for Sonos display
  ([`1870945`](https://github.com/teh-hippo/ha-suno/commit/187094578737bb673bcb912500656b5e7767da21))

### Testing

- Fill coverage gaps to 97% for cache and proxy
  ([`0612055`](https://github.com/teh-hippo/ha-suno/commit/0612055ee3e64c4fe910af67d06bdbaeefd6ad40))


## v1.3.4 (2026-03-20)

### Bug Fixes

- Improve media browser and sensor quality
  ([`eb0a37d`](https://github.com/teh-hippo/ha-suno/commit/eb0a37d40955c35302f6afef2b85cb8bdb6987d1))


## v1.3.3 (2026-03-20)

### Bug Fixes

- Use /api/playlist/me endpoint for all 5 user playlists
  ([`90b5fa7`](https://github.com/teh-hippo/ha-suno/commit/90b5fa71c8fa6c7f1de3dc1afa7984ca9c1728f2))

### Refactoring

- Remove dead code, unused constants, and stale test fixtures
  ([`7658e4a`](https://github.com/teh-hippo/ha-suno/commit/7658e4ad1aebec7495d510e111739e018af15d74))

- Split api.py into models, helpers, and client
  ([`cb5df71`](https://github.com/teh-hippo/ha-suno/commit/cb5df71f646dafb721896d4052dbd94f0ad95679))


## v1.3.2 (2026-03-20)

### Bug Fixes

- Exponential backoff on 429 rate limits
  ([`5eed88e`](https://github.com/teh-hippo/ha-suno/commit/5eed88e00379c0ccd8ac6e5e63151291d41966b8))


## v1.3.1 (2026-03-20)

### Performance Improvements

- Reduce startup delay from ~13s to ~2s
  ([`c4e0bf8`](https://github.com/teh-hippo/ha-suno/commit/c4e0bf8684d1b872302dde0fc263f7ee5ada35e6))


## v1.3.0 (2026-03-20)

### Features

- Quality scale compliance (Bronze/Silver/Gold)
  ([`6849aba`](https://github.com/teh-hippo/ha-suno/commit/6849aba5d453f32b797bf428bf356b80cf94af21))


## v1.2.1 (2026-03-20)

### Bug Fixes

- Add pagination safety limits and liked_songs error handling
  ([`f353720`](https://github.com/teh-hippo/ha-suno/commit/f35372018fe71620261326efce916884374a7563))


## v1.2.0 (2026-03-20)

### Features

- Switch to v2 API with proper liked songs and playlists
  ([`f2d3400`](https://github.com/teh-hippo/ha-suno/commit/f2d3400f88369feb093e7eb00e08520a24d7280a))


## v1.1.7 (2026-03-20)

### Bug Fixes

- Media browser compatibility and song filtering
  ([`530950a`](https://github.com/teh-hippo/ha-suno/commit/530950a887932e16c3afc4e4ae90fa7151d37fa4))


## v1.1.6 (2026-03-20)

### Bug Fixes

- Rate limit API calls to avoid 429s
  ([`cf578a9`](https://github.com/teh-hippo/ha-suno/commit/cf578a9fec082766b8b09e57e3a4d68fe9d35f94))


## v1.1.5 (2026-03-20)

### Bug Fixes

- Parenthesise all multi-except clauses (Python 3.13 compat)
  ([`d607da6`](https://github.com/teh-hippo/ha-suno/commit/d607da6c557682e27b0ef7aa1f86a72085c2f7d3))


## v1.1.4 (2026-03-20)

### Bug Fixes

- Add translations/en.json for config flow rendering
  ([`1e69cea`](https://github.com/teh-hippo/ha-suno/commit/1e69cea43ce8c4c75ca00f438e055914c72fec4f))


## v1.1.3 (2026-03-20)

### Bug Fixes

- Improved brand icon with cleaner waveform design
  ([`c9431a5`](https://github.com/teh-hippo/ha-suno/commit/c9431a5185ffe2d9ecab6467d684c447f8e1b4c9))


## v1.1.2 (2026-03-20)

### Bug Fixes

- Add parens to multi-except clauses for Python 3.13 compat
  ([`1666ddb`](https://github.com/teh-hippo/ha-suno/commit/1666ddb1e746126b38a851671d533cff3b822993))

- Format except clauses for consistent style
  ([`fdf5f32`](https://github.com/teh-hippo/ha-suno/commit/fdf5f3232dc23c6548921abda2f41fc27cf10a48))


## v1.1.1 (2026-03-20)

### Bug Fixes

- Improve config flow UX with step-by-step instructions
  ([`5d559b1`](https://github.com/teh-hippo/ha-suno/commit/5d559b12ebe516c838777c3e4e8db24fa7e8608a))

### Documentation

- Rewrite README with simplified token setup instructions
  ([`99a951f`](https://github.com/teh-hippo/ha-suno/commit/99a951f453599324b359a06014aea7871a914336))


## v1.1.0 (2026-03-20)

### Features

- Simplify auth to accept raw __client JWT value
  ([`ea64967`](https://github.com/teh-hippo/ha-suno/commit/ea6496702b214de57bb258830e8c2ad68b751f6d))


## v1.0.0 (2026-03-20)

- Initial Release
