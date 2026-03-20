# CHANGELOG


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
