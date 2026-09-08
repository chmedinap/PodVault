"""
Configuration constants for the PodVault application.

Deployment-relevant settings (paths, log level) can be overridden via
environment variables — this is what lets Docker map volumes onto
whatever paths a deployment wants without editing this file. Unset
env vars keep exactly the defaults this file has always had.
"""

import os


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value is not None else default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value is not None else default


# Database Configuration
DB_PATH = _env_str('PODVAULT_DB_PATH', 'podvault_data.db')
DB_TIMEOUT = _env_float('PODVAULT_DB_TIMEOUT', 30.0)
ENABLE_BACKUP = True

# Download Configuration
DOWNLOADS_FOLDER = _env_str('PODVAULT_DOWNLOADS_FOLDER', 'downloads')
DEFAULT_DELAY_SECONDS = _env_int('PODVAULT_DEFAULT_DELAY_SECONDS', 5)
DOWNLOAD_TIMEOUT = _env_int('PODVAULT_DOWNLOAD_TIMEOUT', 30)
CHUNK_SIZE = 8192
BATCH_COMMIT_SIZE = 10  # Number of episodes to process before committing transaction

# Scheduler Configuration (per-podcast auto-refresh, only active under `podvault serve`)
SCHEDULER_POLL_INTERVAL_SECONDS = _env_int('PODVAULT_SCHEDULER_POLL_INTERVAL_SECONDS', 300)

# Retry Configuration
MAX_RETRIES = 3  # Maximum number of retry attempts for transient failures
RETRY_DELAY_BASE = 1  # Base delay in seconds for exponential backoff
RETRY_DELAY_MAX = 60  # Maximum delay in seconds for exponential backoff

# Logging Configuration
LOG_LEVEL = _env_str('PODVAULT_LOG_LEVEL', 'INFO')  # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FILE = os.environ.get('PODVAULT_LOG_FILE')  # Set to a file path to enable file logging, None for console only

# RSS Feed Cache Configuration
RSS_CACHE_TTL_HOURS = 1  # Time-to-live for cached RSS feeds in hours
RSS_CACHE_MAX_ENTRIES = 50  # Maximum number of RSS feeds to cache
RSS_CACHE_MAX_SIZE_MB = 100  # Maximum total cache size in megabytes

# File Configuration
MAX_FILENAME_LENGTH = 200
FEEDS_FILE = _env_str('PODVAULT_FEEDS_FILE', 'feeds.txt')

# Episode Status Constants
EPISODE_STATUS_NOT_DOWNLOADED = 'not downloaded'
EPISODE_STATUS_DOWNLOADED = 'downloaded'
EPISODE_STATUS_MISSING = 'missing'  # was downloaded, but the file is gone from disk

# Database Table Names
SUMMARY_TABLE_NAME = 'summary'
APP_SETTINGS_TABLE_NAME = 'app_settings'

# Naming Template Defaults (user-configurable via services.settings / the web Settings screen)
# Available variables: podcast_name, episode_title, episode_number, season_number,
# year, month, day, date, episode_label (episode_number zero-padded, or date if absent)
DEFAULT_FOLDER_TEMPLATE = '{podcast_name}/{year}/{month}/{episode_label} - {episode_title}'
DEFAULT_FILENAME_TEMPLATE = '{episode_title} - {podcast_name}'

# Plotting Configuration
PLOTTING_STYLE = 'seaborn-v0_8'
SEABORN_PALETTE = "husl"
