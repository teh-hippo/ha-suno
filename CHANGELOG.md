# CHANGELOG


## v6.0.25 (2026-05-05)

### Refactoring

- **models**: Lift SunoData dataclass from library_refresh to models
  ([`b3443bd`](https://github.com/teh-hippo/ha-suno/commit/b3443bd246f2563799446440cb97ef3959e45a37))


## v6.0.24 (2026-05-05)

### Refactoring

- **runtime**: Break runtime ↔ proxy cycle by moving view registration
  ([`5e79ed7`](https://github.com/teh-hippo/ha-suno/commit/5e79ed7f6916761b56a1d4713ef71d176ec7ccd8))


## v6.0.23 (2026-05-05)

### Refactoring

- **runtime**: Remove cache/downloaded_library/client property setters
  ([`4c724e6`](https://github.com/teh-hippo/ha-suno/commit/4c724e6bd4d3c0bfd6ce46be267da70eb074901f))


## v6.0.22 (2026-05-05)

### Chores

- **deps**: Sync uv.lock after v6.0.20 release
  ([`b04e14f`](https://github.com/teh-hippo/ha-suno/commit/b04e14f4952282372a73584d9e11694aaf0df26e))

### Refactoring

- **runtime**: Remove __getattr__ shim and dead property setters
  ([`63ea92f`](https://github.com/teh-hippo/ha-suno/commit/63ea92ffea273e7cd4816d2cabf53e12648224c1))

### Testing

- **coordinator**: Access SunoCoordinator via entry.runtime_data.coordinator
  ([`303b9f8`](https://github.com/teh-hippo/ha-suno/commit/303b9f8f49f47687ecff87ea3bc05c6108ea54c5))


## v6.0.21 (2026-05-05)

### Refactoring

- **downloaded_library**: Split _run_download into named phase methods
  ([`5348cc8`](https://github.com/teh-hippo/ha-suno/commit/5348cc8d85c59ec1a8a6b62d8463f2e301268319))


## v6.0.20 (2026-05-05)

### Chores

- **deps**: Sync uv.lock after v6.0.18 release
  ([`8706ed1`](https://github.com/teh-hippo/ha-suno/commit/8706ed13c5630043ea1cef7460625972c3e6eadd))

### Refactoring

- **downloaded_library**: Extract reconciliation passes to submodule
  ([`17ecc29`](https://github.com/teh-hippo/ha-suno/commit/17ecc29c71e454266c40b28bc9640a6c2be8094e))


## v6.0.19 (2026-05-05)

### Chores

- **deps**: Sync uv.lock after v6.0.17 release
  ([`5c565d2`](https://github.com/teh-hippo/ha-suno/commit/5c565d2f68c44f03978159234038078b567840d3))

### Refactoring

- **downloaded_library**: Extract planning to submodule
  ([`59e543b`](https://github.com/teh-hippo/ha-suno/commit/59e543b3d2121cf8e9b0a58b83c6f4012244147b))


## v6.0.18 (2026-05-05)

### Chores

- **deps**: Sync uv.lock after v6.0.16 release
  ([`9b738a4`](https://github.com/teh-hippo/ha-suno/commit/9b738a47312c825e2c65fd7166ed5c046e8a1299))

### Refactoring

- **downloaded_library**: Extract _update_cover_art to submodule
  ([`eb4143a`](https://github.com/teh-hippo/ha-suno/commit/eb4143a9b9a684ee1fad4a9b8956b3ae721f7091))

- **downloaded_library**: Extract HomeAssistantDownloadedLibraryAudio to submodule
  ([`821a32a`](https://github.com/teh-hippo/ha-suno/commit/821a32a20429449cb73d7cfb09c286e1cb3dfeaf))


## v6.0.17 (2026-05-05)

### Chores

- Sync uv.lock after v6.0.15 release
  ([`4498552`](https://github.com/teh-hippo/ha-suno/commit/44985520052cfbfb49a227cea26b78e1c75b0030))

### Refactoring

- Delete download.py wrapper, runtime owns engine directly
  ([`a19e8cd`](https://github.com/teh-hippo/ha-suno/commit/a19e8cdf99a25ad2e30ae1a3df73811c2312e8e0))


## v6.0.16 (2026-05-05)

### Refactoring

- **runtime**: Lift async_download orchestration onto runtime
  ([`8a1f928`](https://github.com/teh-hippo/ha-suno/commit/8a1f928ed02b606ced5cf60cd45cc2786b060fb0))


## v6.0.15 (2026-05-05)

### Chores

- Sync uv.lock after v6.0.13 release
  ([`8c677f6`](https://github.com/teh-hippo/ha-suno/commit/8c677f611aba449762562fd63036faa80bd53a60))

### Refactoring

- **download**: Delete _legacy_fetch_suno_library
  ([`80f494c`](https://github.com/teh-hippo/ha-suno/commit/80f494cd176ecfd39a93370890f6aaebe96e06ec))

- **download**: Slim wrapper to its public surface
  ([`db1b0cd`](https://github.com/teh-hippo/ha-suno/commit/db1b0cdbb9a8aa0fccde9c968bb6a70d24e2960b))

### Testing

- **downloaded_library**: Migrate build_desired and my_songs filtering tests
  ([`83c05ba`](https://github.com/teh-hippo/ha-suno/commit/83c05ba09da3d6f4603529559630d83167a4eab4))

- **downloaded_library**: Migrate cache mode + album from root ancestor tests
  ([`0ad281e`](https://github.com/teh-hippo/ha-suno/commit/0ad281e58f3ec7e2f4d2f59cf60125ee310f3ee2))

- **downloaded_library**: Migrate cover art handling tests
  ([`061a3e1`](https://github.com/teh-hippo/ha-suno/commit/061a3e177bd3ddea34e9c14a206b57b7c93bd3eb))

- **downloaded_library**: Migrate disk reconcile, get_path, library_size, my_songs_minimum
  ([`71234c9`](https://github.com/teh-hippo/ha-suno/commit/71234c91bc5f2698c59c5bd007fb2f7404bc9e3e))

- **downloaded_library**: Migrate download-clip rendering branch tests
  ([`c50ab55`](https://github.com/teh-hippo/ha-suno/commit/c50ab551ea8768ef1936e6a49d72ac3a98983c70))

- **downloaded_library**: Migrate hash-change + retag-failure tests
  ([`f6c95bc`](https://github.com/teh-hippo/ha-suno/commit/f6c95bc2c2dfd35905c8bde217b8518f366fd9e6))

- **downloaded_library**: Migrate hash-formula + manifest-OSError tests
  ([`1ea2568`](https://github.com/teh-hippo/ha-suno/commit/1ea25682b85d24e0be151a8b3283edcccbce016e))

- **downloaded_library**: Migrate last_result/bootstrap display tests
  ([`7733f76`](https://github.com/teh-hippo/ha-suno/commit/7733f763a63bb66f6c8018c4a161b773b2437a54))

- **downloaded_library**: Migrate path-migration / file-rename tests
  ([`ef22780`](https://github.com/teh-hippo/ha-suno/commit/ef22780a45a220b52e6c021854333ccb5361c525))

- **downloaded_library**: Migrate property/breakdown/cleanup/write helpers
  ([`f0c749a`](https://github.com/teh-hippo/ha-suno/commit/f0c749ae33ffe0ed8778cbcd21123184e7110455))

- **downloaded_library**: Migrate quality tracking tests
  ([`05e6e6b`](https://github.com/teh-hippo/ha-suno/commit/05e6e6b14f4429f579f36f2ebe8dc0ac2eb7f955))

- **downloaded_library**: Migrate reconcile / present-file / missing-file / zero-size
  ([`c289934`](https://github.com/teh-hippo/ha-suno/commit/c28993415f6df4433886b8fe40b572b9dbab5c75))

- **downloaded_library**: Migrate retag_clip / cover_art helpers / album / build_desired toggles
  ([`6e5e564`](https://github.com/teh-hippo/ha-suno/commit/6e5e56492ec04dec3bd517aa6e8f3065db914455))

- **downloaded_library**: Migrate sync downloads/orphan/manifest tests
  ([`3a6c3ac`](https://github.com/teh-hippo/ha-suno/commit/3a6c3ac10f4c4f0c3541a736cfed6896899de2ec))

- **downloaded_library**: Migrate sync-init and reconcile-guard tests
  ([`2c43b79`](https://github.com/teh-hippo/ha-suno/commit/2c43b79ab9eae00b1e2ce7f7e2db9f22ce673138))

- **downloaded_library**: Migrate username-change rename tests
  ([`326606e`](https://github.com/teh-hippo/ha-suno/commit/326606ea1d01cf953870f689f27dfc432b7fd492))

- **downloaded_library**: Migrate video download tests
  ([`6ae3a15`](https://github.com/teh-hippo/ha-suno/commit/6ae3a1516e409598c78c425651ac44600809d8a6))


## v6.0.14 (2026-05-04)

### Build System

- **renovate**: Align config with canonical baseline
  ([`7f2da4b`](https://github.com/teh-hippo/ha-suno/commit/7f2da4b5ff41a40875a41ccb59eb9cc44016b85f))


## v6.0.13 (2026-05-04)

### Chores

- Sync uv.lock after v6.0.12 release
  ([`bcef88e`](https://github.com/teh-hippo/ha-suno/commit/bcef88efca1c77390f6847bcdb098b08cb04c35d))

### Refactoring

- **runtime**: Lift HA lifecycle wiring out of SunoDownloadManager.async_setup
  ([`9d89c5e`](https://github.com/teh-hippo/ha-suno/commit/9d89c5e3c1e47197c69ebd6be6c24b136c646b02))

### Testing

- **download**: Import helpers from downloaded_library directly (1.6 slice 1)
  ([`e0f5c1e`](https://github.com/teh-hippo/ha-suno/commit/e0f5c1eb4319e6ea5ca6adb3c3712aef4659df85))

- **downloaded_library**: Relocate 9 pure-helper test classes (1.6 slice 2)
  ([`dbbe90e`](https://github.com/teh-hippo/ha-suno/commit/dbbe90e1b318e3ae36ac9766dd9074ad9ad914f7))


## v6.0.12 (2026-05-04)

### Chores

- Sync uv.lock after v6.0.11 release
  ([`6d290c6`](https://github.com/teh-hippo/ha-suno/commit/6d290c6c694ca24458cdcf6fba0528caa23e6c01))

### Refactoring

- **downloaded_library**: Convert flat module to subpackage (slice 1/9)
  ([`b1b193b`](https://github.com/teh-hippo/ha-suno/commit/b1b193b233b247b1f84dcd0990220b977edb13ba))

- **downloaded_library**: Extract contracts.py (slice 2/9)
  ([`1ca13f0`](https://github.com/teh-hippo/ha-suno/commit/1ca13f09ac50be051988648d52605094e008ac5b))

- **downloaded_library**: Extract filesystem.py (slice 4/9)
  ([`ee3a893`](https://github.com/teh-hippo/ha-suno/commit/ee3a893b1ec063611ed4a374c0bc12316f553f89))

- **downloaded_library**: Extract m3u8.py and metadata.py (slice 6/9)
  ([`d534eab`](https://github.com/teh-hippo/ha-suno/commit/d534eabfd9be0a1e0cd2809dd025c56dc2fc5689))

- **downloaded_library**: Extract paths.py (slice 3/9)
  ([`30b3cc2`](https://github.com/teh-hippo/ha-suno/commit/30b3cc27f4c1944829413f167c68b942e6b04716))

- **downloaded_library**: Extract source_modes.py (slice 5/9)
  ([`e830d95`](https://github.com/teh-hippo/ha-suno/commit/e830d95c8b262b175a47ff98b8920170cc21509b))

- **downloaded_library**: Extract storage.py and cache_adapter.py (slice 7/7)
  ([`fcf18a4`](https://github.com/teh-hippo/ha-suno/commit/fcf18a419249816bf333ca6120da942b23f21c0f))


## v6.0.11 (2026-05-04)

### Build System

- Drop redundant pytest/pytest-asyncio/coverage pins from dev
  ([`ebfc36d`](https://github.com/teh-hippo/ha-suno/commit/ebfc36d00cf4cc1618747b9f876226c5d9d96237))


## v6.0.10 (2026-05-03)

### Build System

- **deps**: Upgrade
  ([`0d7f479`](https://github.com/teh-hippo/ha-suno/commit/0d7f4798076b5f127bae645a3f1b74ca169054ca))


## v6.0.9 (2026-05-03)

### Build System

- **deps**: Upgrade
  ([`1e9a57d`](https://github.com/teh-hippo/ha-suno/commit/1e9a57da32ed7bcf0ac7b77467caf9e88161201d))

### Chores

- Address test warnings and dependency alerts
  ([`22bba6b`](https://github.com/teh-hippo/ha-suno/commit/22bba6babcae80d7a90807f69d69d9196e109e8f))

- Sync uv lock version
  ([`e3e4fec`](https://github.com/teh-hippo/ha-suno/commit/e3e4fecd557f646ec17cff06f65bfe51dc2d0103))


## v6.0.8 (2026-04-29)

### Build System

- Release refactors through HACS
  ([`f2b4ce5`](https://github.com/teh-hippo/ha-suno/commit/f2b4ce5e55534842a9ad09ae328e4428b7b99678))

### Chores

- Sync uv lock version
  ([`fe77fac`](https://github.com/teh-hippo/ha-suno/commit/fe77fac975cc301a8d65b21d81acf29f76dc1e2f))

### Refactoring

- Deepen downloaded library module
  ([`86a3653`](https://github.com/teh-hippo/ha-suno/commit/86a3653e3695ba35066bdf7e33c3cb627d52dc9b))

- Deepen home assistant runtime
  ([`1377801`](https://github.com/teh-hippo/ha-suno/commit/137780137d345a6b05ac134bee483e4aa84fa069))

- Deepen library refresh module
  ([`f7a95f7`](https://github.com/teh-hippo/ha-suno/commit/f7a95f79869785727e5888994289f671e29b03c7))


## v6.0.7 (2026-04-29)

### Build System

- Refresh lockfile version
  ([`f99a102`](https://github.com/teh-hippo/ha-suno/commit/f99a1025b9f87e5ef5b457e1f210d326922a8845))


## v6.0.6 (2026-04-26)

### Build System

- **deps**: Upgrade
  ([`6716dc1`](https://github.com/teh-hippo/ha-suno/commit/6716dc1fad3341e038445d9ec89bf1d805f05ad2))


## v6.0.5 (2026-04-26)

### Build System

- **deps**: Upgrade
  ([`71e6eef`](https://github.com/teh-hippo/ha-suno/commit/71e6eef169936a743c8dc35ce7bf1f2440dcabf9))


## v6.0.4 (2026-04-26)

### Build System

- **deps**: Upgrade
  ([`f398ddf`](https://github.com/teh-hippo/ha-suno/commit/f398ddf59de67d101047e9de50ffe1056493cc26))


## v6.0.3 (2026-04-26)

### Build System

- **deps**: Upgrade
  ([`911572a`](https://github.com/teh-hippo/ha-suno/commit/911572a1e5f6d2fa5583691708aa9fe4f3f313af))


## v6.0.2 (2026-04-26)

### Build System

- **deps**: Update astral-sh/setup-uv action to v8
  ([`0832f60`](https://github.com/teh-hippo/ha-suno/commit/0832f605007424588790c70e3b5ca86782f0bf1e))

- **deps**: Upgrade
  ([`2d44922`](https://github.com/teh-hippo/ha-suno/commit/2d44922da25bea6024c73d219b291915c18eabcd))


## v6.0.1 (2026-04-21)

### Build System

- **renovate**: Automerge major updates when CI passes
  ([`f66c279`](https://github.com/teh-hippo/ha-suno/commit/f66c279331f1f347f34b765b8fd7df5c72b711bd))

### Chores

- Ignore playwright-mcp session and recording dirs
  ([`2af90b7`](https://github.com/teh-hippo/ha-suno/commit/2af90b75ecfafff544c1a5c16d8abfcdc7777c87))


## v6.0.0 (2026-04-21)

### Refactoring

- Drop legacy total_songs cleanup, reset config flow VERSION to 1 (v6 fresh start)
  ([`19456c1`](https://github.com/teh-hippo/ha-suno/commit/19456c1c1662611f24eabd9ed819521c63906288))

- Drop migration tombstones (suno_cache dir, old cache schema, .trash dir)
  ([`64dfca6`](https://github.com/teh-hippo/ha-suno/commit/64dfca6d6f57de19325a1059fe325a096a463df7))

- **tests**: Drop sample_liked_clips, parametrize JWT/normalise tests, lift conftest helper
  ([`097749b`](https://github.com/teh-hippo/ha-suno/commit/097749bd5de413ffb94037baa1c3ea269c86bdce))


## v5.0.3 (2026-04-21)

### Bug Fixes

- Return cached data fast from coordinator update
  ([`a9b0c9c`](https://github.com/teh-hippo/ha-suno/commit/a9b0c9ca125b059ac0d309ecd061d3d47cc1912d))


## v5.0.2 (2026-04-21)

### Bug Fixes

- Drop music-videos/ subdir; mp4 lives next to its audio file
  ([`e7091be`](https://github.com/teh-hippo/ha-suno/commit/e7091bec3f70d6710f5413f8b9ee43afff347e68))

- Split coordinator update so slow ancestor lookups run in background
  ([`2adfde5`](https://github.com/teh-hippo/ha-suno/commit/2adfde5737c4a7857eb540918dc756cf295a643f))

### Code Style

- Apply ruff format to release-3 test files
  ([`2d3c821`](https://github.com/teh-hippo/ha-suno/commit/2d3c821ca49bf16a2d72ea585e0a330bbd1d58f2))


## v5.0.1 (2026-04-21)

### Bug Fixes

- Catch SunoConnectionError + verify reauth keeps the same account
  ([`91e5cb3`](https://github.com/teh-hippo/ha-suno/commit/91e5cb3b7ae4eb594c7877973f6526a9e63b5be9))

- Emit valid ID3v2.3 header instead of mislabeled v2.4
  ([`66a9426`](https://github.com/teh-hippo/ha-suno/commit/66a9426a44773cf4427d8468d19c92ed6aa7bbef))

- Pass user display_name as album_artist for richer media metadata
  ([`7d34c27`](https://github.com/teh-hippo/ha-suno/commit/7d34c27a66ae6a0298c2288001ef47985466d152))

- Re-download missing files instead of looping warnings
  ([`2f59bb5`](https://github.com/teh-hippo/ha-suno/commit/2f59bb54427c11edafcddd829b1ce82021020a46))

- Scope album inheritance to remixes only
  ([`f594a09`](https://github.com/teh-hippo/ha-suno/commit/f594a097ff0992e5e0b0a2b1df9e9a180a67f02d))

- Skip ancestor API resolution when remix set is unchanged
  ([`986f6a0`](https://github.com/teh-hippo/ha-suno/commit/986f6a0cfece24df0680db5215fc8d4be37fcf0f))

- Tighten cache eviction accounting and drop dead truthy fallback
  ([`618a29b`](https://github.com/teh-hippo/ha-suno/commit/618a29b5820ce1e9c0063cfdbb26396b00f1479b))

- Track coordinator updates with monotonic data_version counter
  ([`abee4b8`](https://github.com/teh-hippo/ha-suno/commit/abee4b8b03e47d59f1998c408a91a548dc66c115))

- Write per-track JPG sidecars so Jellyfin shows track-level art
  ([`16cc3c8`](https://github.com/teh-hippo/ha-suno/commit/16cc3c85d8b2018398493180fcafaf2f5e064e9f))

### Code Style

- Apply ruff format to test files
  ([`660ad73`](https://github.com/teh-hippo/ha-suno/commit/660ad7353aa6e3eefaf070f1dcb910b8c857d827))

### Testing

- Drop unused RetagResult import in reconciliation test
  ([`d0d4c71`](https://github.com/teh-hippo/ha-suno/commit/d0d4c714fa037a68f4788e730047459588f524dc))


## v5.0.0 (2026-04-20)

### Bug Fixes

- Declare correct HA minimum version in HACS manifest
  ([`5b5e747`](https://github.com/teh-hippo/ha-suno/commit/5b5e7473b996ae76a51320871d21b45fec1ed38e))

### Features

- Require auth on media proxy and sign media_source URLs
  ([`c6fce56`](https://github.com/teh-hippo/ha-suno/commit/c6fce56614432c8ab4a04a13ac4fedb89386e2fa))

### Breaking Changes

- Direct unauthenticated requests to `/api/suno/media/{clip_id}.{ext}` now return 401. External
  scripts must include `Authorization: Bearer <token>` or use a signed URL from
  `media_source.async_resolve_media`.


## v4.3.16 (2026-04-19)

### Build System

- **deps**: Upgrade
  ([`bb96666`](https://github.com/teh-hippo/ha-suno/commit/bb9666631ffaeb3fe2ba0b5d5e1610b9c34fa192))


## v4.3.15 (2026-04-19)

### Build System

- **deps**: Upgrade
  ([`16d242d`](https://github.com/teh-hippo/ha-suno/commit/16d242dbf330ac69693549b14671e5c7dd36638c))


## v4.3.14 (2026-04-19)

### Build System

- **deps**: Upgrade
  ([`c925e55`](https://github.com/teh-hippo/ha-suno/commit/c925e55744fc5aacfd3caec2e6d7bdab3712a222))


## v4.3.13 (2026-04-19)

### Build System

- **deps**: Upgrade
  ([`077c972`](https://github.com/teh-hippo/ha-suno/commit/077c9726908e082bb8976114ec789e83d480dbdc))


## v4.3.12 (2026-04-19)

### Build System

- **deps**: Upgrade
  ([`5a153f2`](https://github.com/teh-hippo/ha-suno/commit/5a153f20ce8e1fc7c685a0ac1c542ff512fe765e))


## v4.3.11 (2026-04-19)

### Build System

- **deps**: Upgrade
  ([`db9e04e`](https://github.com/teh-hippo/ha-suno/commit/db9e04e121119de203a05cb94dfab1aab427d207))


## v4.3.10 (2026-04-14)

### Build System

- **deps**: Update dependency pytest to v9.0.3 [SECURITY]
  ([`49be964`](https://github.com/teh-hippo/ha-suno/commit/49be964dfda9740acae1f84899dd2a613d67f039))


## v4.3.9 (2026-04-12)

### Build System

- **deps**: Update softprops/action-gh-release action to v3
  ([`b18f818`](https://github.com/teh-hippo/ha-suno/commit/b18f818bdab5b7250d40d4467b9f693be2cc16e2))

- **deps**: Upgrade
  ([`8edd586`](https://github.com/teh-hippo/ha-suno/commit/8edd58609c2e7ad70f1339f334614c17cf28be25))


## v4.3.8 (2026-04-05)

### Build System

- **deps**: Upgrade
  ([`8a959aa`](https://github.com/teh-hippo/ha-suno/commit/8a959aaa2952cd8cbd3fabd0e747f325e4505509))


## v4.3.7 (2026-04-05)

### Build System

- **deps**: Upgrade
  ([`012b00d`](https://github.com/teh-hippo/ha-suno/commit/012b00d06757c97e5da242928457b7ff91c5b60c))


## v4.3.6 (2026-04-05)

### Build System

- Update Renovate config for weekly grouped updates
  ([`abd8882`](https://github.com/teh-hippo/ha-suno/commit/abd88825e379c1dc02180640141f05fcbd068813))

### Chores

- Remove pre-commit hook, pre-push is sufficient
  ([`a8d60b2`](https://github.com/teh-hippo/ha-suno/commit/a8d60b21c4c3d875dd08e18a46c6c035d97efea7))

### Documentation

- Add AGENTS.md
  ([`ca37f6e`](https://github.com/teh-hippo/ha-suno/commit/ca37f6e276c865f261602496a4f400b20213745e))


## v4.3.5 (2026-03-30)

### Build System

- **deps**: Upgrade
  ([`3037358`](https://github.com/teh-hippo/ha-suno/commit/303735849d7077103fcd003760b9931c76614f5f))


## v4.3.4 (2026-03-30)

### Build System

- **deps**: Upgrade
  ([`4ea0b7a`](https://github.com/teh-hippo/ha-suno/commit/4ea0b7ad0924b38494d7a68b36e82b62a0e7d8a7))


## v4.3.3 (2026-03-30)

### Bug Fixes

- Pass relative path to _api_get in get_clip_parent
  ([`3a6accb`](https://github.com/teh-hippo/ha-suno/commit/3a6accb886f7b2a88704f7517a29c130bbcf4134))


## v4.3.2 (2026-03-30)

### Build System

- **deps**: Upgrade
  ([`1a11453`](https://github.com/teh-hippo/ha-suno/commit/1a114531d65f6db1c4d6b7acead57845ffbc52fc))


## v4.3.1 (2026-03-30)

### Build System

- **deps**: Upgrade
  ([`675d515`](https://github.com/teh-hippo/ha-suno/commit/675d51598486a2f804d0050a0179fdce2228fc2a))


## v4.3.0 (2026-03-29)

### Chores

- Add pre-commit hook for fast staged-file checks
  ([`eea6395`](https://github.com/teh-hippo/ha-suno/commit/eea639535b81b9367a16ade994f9b116abab3158))

- Sync uv.lock with v4.2.9
  ([`f0c13cc`](https://github.com/teh-hippo/ha-suno/commit/f0c13ccad41d3fe14abd15a3e4424b1441ab8285))

- Update uv.lock version
  ([`fe914be`](https://github.com/teh-hippo/ha-suno/commit/fe914bed3ec6a5b4339aecb2b83c4367f3c6a8db))

### Features

- Store music videos in dedicated directory
  ([`636f2ef`](https://github.com/teh-hippo/ha-suno/commit/636f2ef9d9bc468ae745c5b00870ae0217388203))


## v4.2.9 (2026-03-28)

### Bug Fixes

- Embed album art in MP3 proxy streams
  ([`8eeff51`](https://github.com/teh-hippo/ha-suno/commit/8eeff51a4fb48372b505231a1f9c873a20fbfe28))


## v4.2.8 (2026-03-27)

### Bug Fixes

- Use pathvalidate for readable filesystem-safe names
  ([`f279731`](https://github.com/teh-hippo/ha-suno/commit/f279731a7324fb64f47bdba51405462bd6543d1c))


## v4.2.7 (2026-03-27)

### Bug Fixes

- Trigger release for slugify path refactor
  ([`cc7662d`](https://github.com/teh-hippo/ha-suno/commit/cc7662d6741cf900b535da59db13f59ddcd9b3dc))

### Refactoring

- Use HA slugify for file path components
  ([`86bc3d9`](https://github.com/teh-hippo/ha-suno/commit/86bc3d9b35f11e7c2a8df18d1944396db44c4fde))


## v4.2.6 (2026-03-27)

### Bug Fixes

- Harden filename sanitisation against traversal
  ([`e2a0383`](https://github.com/teh-hippo/ha-suno/commit/e2a0383b2b1a3c21f81d3bff2d7bd02f23b1ec01))


## v4.2.5 (2026-03-27)

### Bug Fixes

- Revert Clerk auth priority, use API display_name as identity
  ([`ecc81f1`](https://github.com/teh-hippo/ha-suno/commit/ecc81f104203774cd9e5ab2e7a8dbcb314ff6b4f))


## v4.2.4 (2026-03-27)

### Bug Fixes

- Sync config entry title and avoid blocking I/O in retag
  ([`fbcf625`](https://github.com/teh-hippo/ha-suno/commit/fbcf625349cb4264ef6b22b5c69806c3dbf58932))


## v4.2.3 (2026-03-27)

### Bug Fixes

- Use Clerk auth as authoritative identity source
  ([`1b9495d`](https://github.com/teh-hippo/ha-suno/commit/1b9495dc6e0f5161136c44587e4733dd8925924c))


## v4.2.2 (2026-03-27)

### Bug Fixes

- Retag embedded metadata after file rename
  ([`b66dace`](https://github.com/teh-hippo/ha-suno/commit/b66dace316c261c328856c29d97b93f12de7ecab))

### Chores

- Sync uv.lock with v4.2.1
  ([`ea5f403`](https://github.com/teh-hippo/ha-suno/commit/ea5f4036c74ae7a3288c911dbe8384eebf720bd9))


## v4.2.1 (2026-03-26)

### Bug Fixes

- Address post-deploy review findings
  ([`d5a64bf`](https://github.com/teh-hippo/ha-suno/commit/d5a64bff025191c6bc4bdf628717edec2a3282c6))


## v4.2.0 (2026-03-26)

### Features

- Handle username changes with rename + re-tag instead of re-download
  ([`5ef796c`](https://github.com/teh-hippo/ha-suno/commit/5ef796cf3463aa7161cdfa8e57d86915b9b27895))


## v4.1.0 (2026-03-26)

### Features

- Root ancestor resolution and safe clip handling
  ([`2962f79`](https://github.com/teh-hippo/ha-suno/commit/2962f7992ec0e3ce9599b07c61755c4a8c831b3b))


## v4.0.0 (2026-03-25)

### Features

- Per-section download mode with Cache Only option
  ([`6acea9c`](https://github.com/teh-hippo/ha-suno/commit/6acea9ccf4c70c47734730eecc59d5630ddda580))

### Refactoring

- Consolidate codebase — TrackMetadata dataclass, config flow dedup, sensor/helper cleanup
  ([`b8426f1`](https://github.com/teh-hippo/ha-suno/commit/b8426f16a4f98dae7826caf7140c295214ad01f5))

### Breaking Changes

- Configuration keys renamed, no migration provided. Existing integrations must be removed and
  re-added.


## v3.8.0 (2026-03-25)

### Features

- Rename Download terminology to Library Sync
  ([`fd803c0`](https://github.com/teh-hippo/ha-suno/commit/fd803c0b12d0dbdbfc165b6409d581545c1ba820))


## v3.7.2 (2026-03-25)

### Bug Fixes

- Audit round 2 — dead code, logic fixes, performance, test coverage
  ([`61765c1`](https://github.com/teh-hippo/ha-suno/commit/61765c1bdb28aabdf9ebc657ee9bd1b1805a0cf0))


## v3.7.1 (2026-03-25)

### Bug Fixes

- Service lifecycle, media source multi-entry, proxy cache, and reconcile optimisation
  ([`7669e3a`](https://github.com/teh-hippo/ha-suno/commit/7669e3a366fac368afc09cb5ca12028f79ac7e37))

### Refactoring

- Codebase cleanup and test coverage improvements
  ([`923123c`](https://github.com/teh-hippo/ha-suno/commit/923123c7652a90353236dca0c2d4f2f2db0ad161))


## v3.7.0 (2026-03-25)

### Features

- Restructure paths, fix cover art, correct playlist ordering
  ([`9bc1859`](https://github.com/teh-hippo/ha-suno/commit/9bc1859bc4462efde790f8c77ea59431f764fc5b))


## v3.6.1 (2026-03-25)

### Bug Fixes

- Enable video downloads by default
  ([`b2dde31`](https://github.com/teh-hippo/ha-suno/commit/b2dde31c8fd21a19aa21ac8c4c365e85acfd8f4f))


## v3.6.0 (2026-03-25)

### Features

- Add video, model, artist, and lineage metadata from Suno API
  ([`b9d2688`](https://github.com/teh-hippo/ha-suno/commit/b9d2688c6cf4972743ce44508d7c10aa422a4611))


## v3.5.1 (2026-03-25)

### Bug Fixes

- Write cover.jpg for all directories, not just MP3
  ([`c49cf8a`](https://github.com/teh-hippo/ha-suno/commit/c49cf8a8012d6f13387575b3ba95d91e2d14382d))


## v3.5.0 (2026-03-24)

### Features

- Fix Jellyfin metadata and simplify downloads
  ([`4e1504e`](https://github.com/teh-hippo/ha-suno/commit/4e1504ec98f713bbc89d4ff58564ab1eb050d488))


## v3.4.2 (2026-03-24)

### Bug Fixes

- Force downloads skip bootstrap cap to avoid infinite loop
  ([`94a0aeb`](https://github.com/teh-hippo/ha-suno/commit/94a0aebf8ac08ec5bbad64d8ebd6148193076fe8))


## v3.4.1 (2026-03-24)

### Bug Fixes

- Propagate force flag through download continuation
  ([`7ee9439`](https://github.com/teh-hippo/ha-suno/commit/7ee94395acad16a9487772163f1b9b06ded7f102))


## v3.4.0 (2026-03-24)

### Bug Fixes

- FLAC album art not shown in Jellyfin (picture type 0→3)
  ([`e472617`](https://github.com/teh-hippo/ha-suno/commit/e4726176e0d57b861f50c05ac608d296caae3d98))

### Features

- Rich metadata for Jellyfin — album, lyrics, date, albumartist
  ([`6a554ee`](https://github.com/teh-hippo/ha-suno/commit/6a554ee0ac83393fc3542bde9d1367b537746def))


## v3.3.2 (2026-03-24)

### Bug Fixes

- Auto-continue downloads and skip delays for existing files
  ([`bc94f34`](https://github.com/teh-hippo/ha-suno/commit/bc94f34ef9209babb88f083327cf466139828fcd))

### Continuous Integration

- Enable global automerge and fix semantic-release patch_tags
  ([`b62ff3d`](https://github.com/teh-hippo/ha-suno/commit/b62ff3ddb624d0b7812a2d86c63e1b475c9ef778))


## v3.3.1 (2026-03-23)

### Bug Fixes

- Remove stale migration comment
  ([`c171eb5`](https://github.com/teh-hippo/ha-suno/commit/c171eb51eba303d97a6f87f36d338e2771abd6d1))

### Chores

- Align requires-python with HA core (drop upper bound)
  ([`db2ed6f`](https://github.com/teh-hippo/ha-suno/commit/db2ed6f842d221cef0350a6446b37f1554a71367))


## v3.3.0 (2026-03-23)

### Chores

- Update uv.lock for v3.2.1
  ([`89f6814`](https://github.com/teh-hippo/ha-suno/commit/89f6814b24aa2317fdaac52061a2632565b3609d))

### Features

- Add download enabled toggle and fix except syntax
  ([`2b7aa6f`](https://github.com/teh-hippo/ha-suno/commit/2b7aa6fd4eabf1da6ea32fef1a150d5585d76800))


## v3.2.2 (2026-03-23)

### Bug Fixes

- Resolve M3U8 playlist misattribution and duplicates
  ([`ae49d83`](https://github.com/teh-hippo/ha-suno/commit/ae49d83f82df53875ac9224b44cb76d9ed47a9b4))


## v3.2.1 (2026-03-23)

### Bug Fixes

- **ci**: Pass RELEASE_TOKEN to checkout for git push auth
  ([`ab037ec`](https://github.com/teh-hippo/ha-suno/commit/ab037ecdd377e886ff5da6cd617ef8bc98fd5080))


## v3.2.0 (2026-03-23)

### Bug Fixes

- **ci**: Use RELEASE_TOKEN for semantic-release push
  ([`de9c8bd`](https://github.com/teh-hippo/ha-suno/commit/de9c8bd53a629f95bd75bd3e14c03c5b84f31db3))

### Build System

- **deps**: Upgrade
  ([`2255a86`](https://github.com/teh-hippo/ha-suno/commit/2255a86475170dea3fc473d463d4571536b0b3cc))

### Features

- Include remaster, upload, concat and editor export tracks
  ([`003d663`](https://github.com/teh-hippo/ha-suno/commit/003d663b3ce189a6c13a18f0a50daa74d91e23a2))

- UX improvements, minimum songs filter, and cleanup
  ([`ca83185`](https://github.com/teh-hippo/ha-suno/commit/ca831859fd27c725de02962ad90947e925b918b4))


## v3.1.0 (2026-03-23)

### Bug Fixes

- Resolve correct coordinator per clip in multi-account proxy
  ([`a5f49b1`](https://github.com/teh-hippo/ha-suno/commit/a5f49b12ff1c6b95788add385ee07c5113a8a5d0))

### Chores

- Bump version to 3.1.0
  ([`47e3b19`](https://github.com/teh-hippo/ha-suno/commit/47e3b19f13ba533193741034022fe81446c5cfa0))

### Features

- Device naming, shared rate limiter, multi-account safety
  ([`4efd7d4`](https://github.com/teh-hippo/ha-suno/commit/4efd7d4502b72e3f1faf89668b1f2aaf2499df10))


## v3.0.1 (2026-03-23)

### Bug Fixes

- Check liked_clips for quality derivation in media source
  ([`7ee01da`](https://github.com/teh-hippo/ha-suno/commit/7ee01da3322ad0acd78043135f2f4236ac4d0218))


## v3.0.0 (2026-03-23)

### Build System

- **deps**: Update mcr.microsoft.com/devcontainers/python Docker tag to v3.14
  ([#2](https://github.com/teh-hippo/ha-suno/pull/2),
  [`e9f65a3`](https://github.com/teh-hippo/ha-suno/commit/e9f65a3392a7a344042b3a205bf4900f12cc7ae6))

- **deps**: Upgrade
  ([`98fa0ea`](https://github.com/teh-hippo/ha-suno/commit/98fa0ea191eb0d26f9ff9897a0056386ed93979f))

### Continuous Integration

- Fix automerge config for all update types
  ([`7d9f12f`](https://github.com/teh-hippo/ha-suno/commit/7d9f12f4b72d77973619fc9e1352361286ced861))

- Fix build_command, add dependabot, remove lockfile-update workflow
  ([`95efce3`](https://github.com/teh-hippo/ha-suno/commit/95efce3cc176ff395c68c6f6efd9c3bf1163680a))

- Migrate from Dependabot to Renovate
  ([`5b404d0`](https://github.com/teh-hippo/ha-suno/commit/5b404d0b12350d1c2cd82ff11aa519868ef05b43))

- Standardise renovate.json with forkProcessing
  ([`5ecdd8f`](https://github.com/teh-hippo/ha-suno/commit/5ecdd8f1508d10c0015561938ce4bb8e3bbeef78))

### Features

- Config flow restructure — per-content-type pages
  ([`2395fac`](https://github.com/teh-hippo/ha-suno/commit/2395fac95d05776271e46cd3ad3f65282df5beba))


## v1.13.1 (2026-03-22)

### Bug Fixes

- Broken sync sensors, infinite sync loop, remove trash feature
  ([`32c0ed9`](https://github.com/teh-hippo/ha-suno/commit/32c0ed99db0500d995ae5ed0656faa22917f8e04))


## v1.13.0 (2026-03-22)

### Features

- Improve sync sensors, reduce download delay, cache encapsulation
  ([`1a12b03`](https://github.com/teh-hippo/ha-suno/commit/1a12b03827b3474d332abd1e80c78bb72f048ac9))


## v1.12.1 (2026-03-22)

### Bug Fixes

- Use absolute paths in M3U8 playlists for Jellyfin compatibility
  ([`666ad23`](https://github.com/teh-hippo/ha-suno/commit/666ad238a1717205cf05f690078af5a3347637da))


## v1.12.0 (2026-03-22)

### Features

- Generate M3U8 playlist files for Jellyfin compatibility
  ([`7fb9d82`](https://github.com/teh-hippo/ha-suno/commit/7fb9d82911e6515c98c3052cbceef18597dd1235))

### Refactoring

- Aggressive code reduction across 7 files
  ([`7309c69`](https://github.com/teh-hippo/ha-suno/commit/7309c69b965f7b762a2db463620ed0fdea9540d6))

- Aggressive reduction — sync, proxy, cache, media, audio
  ([`be641c3`](https://github.com/teh-hippo/ha-suno/commit/be641c35874a0fb1bb30ab270b59cf26b31fed2e))

- Consolidate sensors, proxy helpers, auth HTTP wrapper
  ([`3d044ad`](https://github.com/teh-hippo/ha-suno/commit/3d044ad6c5c2faec8dd3656995f4fc6fa472f448))


## v1.11.0 (2026-03-22)

### Chores

- Update uv.lock
  ([`cbc722e`](https://github.com/teh-hippo/ha-suno/commit/cbc722e74f4e715c53c40b142d1b62a743e7f2c4))

### Features

- Fast startup with stored data and offline resilience
  ([`6c14e32`](https://github.com/teh-hippo/ha-suno/commit/6c14e32682a3eccb7187d31b93fd12c711834a12))


## v1.10.3 (2026-03-22)

### Bug Fixes

- Rate limiting, metadata tags, force resync, share coordinator data
  ([`2350a04`](https://github.com/teh-hippo/ha-suno/commit/2350a04edba55aa50c5ab5197c60f1fac7803af0))


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
