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
CONF_SHOW_MY_SONGS = "show_my_songs"
CONF_SHOW_PLAYLISTS = "show_playlists"
CONF_CACHE_MAX_SIZE = "cache_max_size_mb"

# Download config
CONF_DOWNLOAD_PATH = "download_path"
CONF_ALL_PLAYLISTS = "all_playlists"
CONF_PLAYLISTS = "playlists"
CONF_MY_SONGS_COUNT = "my_songs_count"
CONF_MY_SONGS_DAYS = "my_songs_days"
CONF_MY_SONGS_MINIMUM = "my_songs_minimum"
CONF_CREATE_PLAYLISTS = "create_playlists"

# Per-source quality config keys
CONF_QUALITY_LIKED = "quality_liked"
CONF_QUALITY_PLAYLISTS = "quality_playlists"
CONF_QUALITY_MY_SONGS = "quality_my_songs"

# Per-source download mode config keys
CONF_DOWNLOAD_MODE_LIKED = "download_mode_liked"
CONF_DOWNLOAD_MODE_PLAYLISTS = "download_mode_playlists"
CONF_DOWNLOAD_MODE_MY_SONGS = "download_mode_my_songs"
CONF_DOWNLOAD_VIDEOS = "download_videos"

# Defaults
DEFAULT_SHOW_LIKED = True
DEFAULT_SHOW_MY_SONGS = True
DEFAULT_SHOW_PLAYLISTS = True
DEFAULT_CACHE_TTL = 30
DEFAULT_CACHE_MAX_SIZE = 500
DEFAULT_ALL_PLAYLISTS = True
DEFAULT_CREATE_PLAYLISTS = True
DEFAULT_MY_SONGS_COUNT = 20
DEFAULT_MY_SONGS_DAYS = 7
DEFAULT_MY_SONGS_MINIMUM = 0
DEFAULT_DOWNLOAD_MODE = "mirror"
DEFAULT_DOWNLOAD_MODE_MY_SONGS = "cache"

# Quality values
QUALITY_HIGH = "high"
QUALITY_STANDARD = "standard"

# Download modes
DOWNLOAD_MODE_MIRROR = "mirror"
DOWNLOAD_MODE_ARCHIVE = "archive"
DOWNLOAD_MODE_CACHE = "cache"

# Download operational constants
DOWNLOAD_FFMPEG_TIMEOUT = 60

JWT_REFRESH_BUFFER = 60
EXCLUDED_TASKS = frozenset({"infill", "fixed_infill"})
EXCLUDED_TYPES = frozenset({"rendered_context_window"})
