"""Constants for the Suno integration."""

DOMAIN = "suno"

# Suno API
SUNO_API_BASE_URL = "https://studio-api-prod.suno.com"
CLERK_BASE_URL = "https://clerk.suno.com"
CLERK_JS_VERSION = "4.72.1"
CLERK_TOKEN_JS_VERSION = "4.72.0-snapshot.vc141245"

# CDN (cdn1 works publicly; cdn2 returns 403)
CDN_BASE_URL = "https://cdn1.suno.ai"
CDN_AUDIO_URL = f"{CDN_BASE_URL}/{{clip_id}}.mp3"
CDN_IMAGE_URL = f"{CDN_BASE_URL}/image_{{clip_id}}.jpeg"
CDN_IMAGE_LARGE_URL = f"{CDN_BASE_URL}/image_large_{{clip_id}}.jpeg"

# API pagination
FEED_PAGE_SIZE = 20
MAX_PAGES = 100

# Config keys
CONF_COOKIE = "cookie"
CONF_SHOW_LIKED = "show_liked"
CONF_SHOW_RECENT = "show_recent"
CONF_RECENT_COUNT = "recent_count"
CONF_SHOW_PLAYLISTS = "show_playlists"
CONF_CACHE_TTL = "cache_ttl_minutes"

# Defaults
DEFAULT_SHOW_LIKED = True
DEFAULT_SHOW_RECENT = True
DEFAULT_RECENT_COUNT = 20
DEFAULT_SHOW_PLAYLISTS = True
DEFAULT_CACHE_TTL = 30

# Media source identifiers
MEDIA_SOURCE_PREFIX = f"media-source://{DOMAIN}"

# JWT refresh buffer (seconds before expiry to trigger refresh)
JWT_REFRESH_BUFFER = 60

# Metadata task values that indicate edit fragments (not standalone songs)
EXCLUDED_TASKS = frozenset({"infill", "fixed_infill"})
