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
CONF_SHOW_LATEST = "show_latest"
CONF_SHOW_PLAYLISTS = "show_playlists"
CONF_CACHE_MAX_SIZE = "cache_max_size_mb"

# Download config
CONF_DOWNLOAD_PATH = "download_path"
CONF_ALL_PLAYLISTS = "all_playlists"
CONF_PLAYLISTS = "playlists"
CONF_LATEST_COUNT = "latest_count"
CONF_LATEST_DAYS = "latest_days"
CONF_LATEST_MINIMUM = "latest_minimum"
CONF_CREATE_PLAYLISTS = "create_playlists"
CONF_DOWNLOAD_ENABLED = "download_enabled"

# Per-source quality config keys
CONF_QUALITY_LIKED = "quality_liked"
CONF_QUALITY_PLAYLISTS = "quality_playlists"
CONF_QUALITY_LATEST = "quality_latest"

# Per-source download mode config keys
CONF_DOWNLOAD_MODE_LIKED = "download_mode_liked"
CONF_DOWNLOAD_MODE_PLAYLISTS = "download_mode_playlists"
CONF_DOWNLOAD_MODE_LATEST = "download_mode_latest"

# Defaults
DEFAULT_SHOW_LIKED = True
DEFAULT_SHOW_LATEST = True
DEFAULT_SHOW_PLAYLISTS = True
DEFAULT_DOWNLOAD_ENABLED = True
DEFAULT_CACHE_TTL = 30
DEFAULT_CACHE_MAX_SIZE = 500
DEFAULT_ALL_PLAYLISTS = True
DEFAULT_CREATE_PLAYLISTS = True
DEFAULT_LATEST_COUNT = 20
DEFAULT_LATEST_DAYS = 7
DEFAULT_LATEST_MINIMUM = 0
DEFAULT_DOWNLOAD_MODE = "mirror"

# Quality values
QUALITY_HIGH = "high"
QUALITY_STANDARD = "standard"

# Download modes
DOWNLOAD_MODE_MIRROR = "mirror"
DOWNLOAD_MODE_COLLECT = "collect"

# Download operational constants
DOWNLOAD_MAX_PER_RUN = 10
DOWNLOAD_MAX_BOOTSTRAP = 25
DOWNLOAD_DELAY = 2
DOWNLOAD_FFMPEG_TIMEOUT = 60

JWT_REFRESH_BUFFER = 60
EXCLUDED_TASKS = frozenset({"infill", "fixed_infill"})
EXCLUDED_TYPES = frozenset({"rendered_context_window"})
