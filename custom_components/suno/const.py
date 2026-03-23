"""Constants for the Suno integration."""

DOMAIN = "suno"
DATA_VIEW_REGISTERED = f"{DOMAIN}_view_registered"

SUNO_API_BASE_URL = "https://studio-api-prod.suno.com"
CLERK_BASE_URL = "https://clerk.suno.com"
CLERK_JS_VERSION = "4.72.1"
CLERK_TOKEN_JS_VERSION = "4.72.0-snapshot.vc141245"

CDN_BASE_URL = "https://cdn1.suno.ai"
MAX_PAGES = 100

# Config keys
CONF_COOKIE = "cookie"
CONF_SHOW_LIKED = "show_liked"
CONF_SHOW_RECENT = "show_recent"
CONF_RECENT_COUNT = "recent_count"
CONF_SHOW_PLAYLISTS = "show_playlists"
CONF_CACHE_TTL = "cache_ttl_minutes"
CONF_AUDIO_QUALITY = "audio_quality"
CONF_CACHE_ENABLED = "cache_enabled"
CONF_CACHE_MAX_SIZE = "cache_max_size_mb"

# Defaults
DEFAULT_SHOW_LIKED = True
DEFAULT_SHOW_RECENT = True
DEFAULT_RECENT_COUNT = 20
DEFAULT_SHOW_PLAYLISTS = True
DEFAULT_CACHE_TTL = 30
DEFAULT_AUDIO_QUALITY = "standard"
DEFAULT_CACHE_ENABLED = False
DEFAULT_CACHE_MAX_SIZE = 500

# Sync config
CONF_SYNC_ENABLED = "sync_enabled"
CONF_SYNC_PATH = "sync_path"
CONF_SYNC_LIKED = "sync_liked"
CONF_SYNC_ALL_PLAYLISTS = "sync_all_playlists"
CONF_SYNC_PLAYLISTS = "sync_playlists"
CONF_SYNC_LATEST_COUNT = "sync_latest_count"
CONF_SYNC_LATEST_DAYS = "sync_latest_days"
CONF_SYNC_PLAYLISTS_M3U = "sync_playlists_m3u"

DEFAULT_SYNC_ENABLED = False
DEFAULT_SYNC_LIKED = True
DEFAULT_SYNC_ALL_PLAYLISTS = True
DEFAULT_SYNC_LATEST_COUNT = None
DEFAULT_SYNC_LATEST_DAYS = None
DEFAULT_SYNC_PLAYLISTS_M3U = False

SYNC_MAX_DOWNLOADS_PER_RUN = 10
SYNC_MAX_DOWNLOADS_BOOTSTRAP = 25
SYNC_DOWNLOAD_DELAY = 2
SYNC_FFMPEG_TIMEOUT = 60

QUALITY_HIGH = "high"
QUALITY_STANDARD = "standard"

# Sync modes
SYNC_MODE_SYNC = "sync"
SYNC_MODE_COPY = "copy"

# Per-source quality config keys
CONF_SYNC_QUALITY_LIKED = "sync_quality_liked"
CONF_SYNC_QUALITY_PLAYLISTS = "sync_quality_playlists"
CONF_SYNC_QUALITY_LATEST = "sync_quality_latest"

# Per-source quality config keys
CONF_SYNC_QUALITY_LIKED = "sync_quality_liked"
CONF_SYNC_QUALITY_PLAYLISTS = "sync_quality_playlists"
CONF_SYNC_QUALITY_LATEST = "sync_quality_latest"

# Per-source mode config keys
CONF_SYNC_MODE_LIKED = "sync_mode_liked"
CONF_SYNC_MODE_PLAYLISTS = "sync_mode_playlists"
CONF_SYNC_MODE_LATEST = "sync_mode_latest"

# Defaults
DEFAULT_SYNC_MODE = "sync"
JWT_REFRESH_BUFFER = 60
EXCLUDED_TASKS = frozenset({"infill", "fixed_infill"})
