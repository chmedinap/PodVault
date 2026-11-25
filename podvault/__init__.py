"""
PodVault - A podcast RSS feed parser and downloader.

This package provides functionality to:
- Parse RSS feeds and extract podcast episode information
- Store episode data in SQLite database
- Download podcast episodes with organized file structure
- Generate summary statistics
"""

__version__ = '1.3.2'

# Import config
from podvault import config

# Import data modules
from podvault.data.collection import (
    normalize, 
    collect_data, 
    get_rss_from_apple_podcast, 
    normalize_feed_url,
    get_podcast_title,
    get_apple_podcast_info,
    get_episode_xml_from_rss,
    get_cached_rss_content,
)

# Import database modules
from podvault.database.connection import (
    is_valid_database,
    get_db_connection,
    clean_dataframe_for_sqlite
)
from podvault.database.queries import (
    table_exists,
    summary_exists,
    has_downloaded_episodes,
    verify_downloaded_files_exist,
    validate_and_quote_table_name
)
from podvault.database.schema import (
    add_podcast_image_url_to_summary,
    add_download_columns_to_table,
    update_download_info,
    add_indexes_to_table,
)

# Import download modules
from podvault.download.utils import sanitize_filename, show_podcast_summary, parse_episode_date
from podvault.download.downloader import download_all_episodes, download_last_episode
from podvault.download.metadata import update_summary
from podvault.download.id3_tags import update_mp3_id3_tags, update_episode_id3_tags_from_db

# Import validation modules
from podvault.validation import (
    validate_feed_url,
    validate_podcast_name,
    validate_file_path,
    sanitize_podcast_name,
    ValidationError
)

# Import logging configuration (ensures logging is configured on import)
from podvault.logging_config import (
    setup_logging,
    get_logger,
    configure_logging
)

__all__ = [
    # Config
    'config',
    # Data functions
    'normalize',
    'collect_data',
    'get_rss_from_apple_podcast',
    'normalize_feed_url',
    'get_podcast_title',
    'get_apple_podcast_info',
    'get_episode_xml_from_rss',
    'get_cached_rss_content',
    # Database functions
    'is_valid_database',
    'get_db_connection',
    'clean_dataframe_for_sqlite',
    'table_exists',
    'summary_exists',
    'has_downloaded_episodes',
    'verify_downloaded_files_exist',
    'validate_and_quote_table_name',
    'add_podcast_image_url_to_summary',
    'add_download_columns_to_table',
    'update_download_info',
    'add_indexes_to_table',
    # Download functions
    'sanitize_filename',
    'show_podcast_summary',
    'parse_episode_date',
    'download_all_episodes',
    'download_last_episode',
    'update_summary',
    'update_mp3_id3_tags',
    'update_episode_id3_tags_from_db',
    # Validation functions
    'validate_feed_url',
    'validate_podcast_name',
    'validate_file_path',
    'sanitize_podcast_name',
    'ValidationError',
    # Logging functions
    'setup_logging',
    'get_logger',
    'configure_logging',
]

