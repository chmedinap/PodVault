"""
Service layer for listing podcasts and their download status.

Extracted from cli.py so the same logic can back a web dashboard, not
just the argparse `list`/`status` commands.
"""

import os
import sqlite3
from typing import List, Optional

from podvault import config
from podvault.database.connection import get_db_connection, is_valid_database
from podvault.database.queries import table_exists, validate_and_quote_table_name
from podvault.database.schema import add_download_columns_to_table, create_summary_table_if_not_exists
from podvault.download.utils import show_podcast_summary
from podvault.services.results import PodcastInfo, PodcastListResult, StatusResult
from podvault.validation import validate_podcast_name

# Tables that exist alongside podcast tables but aren't themselves podcasts.
_NON_PODCAST_TABLES = (config.SUMMARY_TABLE_NAME, 'jobs', config.APP_SETTINGS_TABLE_NAME)


def list_podcast_table_names(conn: sqlite3.Connection) -> List[str]:
    placeholders = ', '.join('?' for _ in _NON_PODCAST_TABLES)
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT IN ({placeholders})
        ORDER BY name
        """,
        _NON_PODCAST_TABLES,
    )
    return [row[0] for row in cursor.fetchall()]


def _build_podcast_info(conn: sqlite3.Connection, name: str) -> PodcastInfo:
    """Assemble one podcast's dashboard/header stats: episode counts and cover
    image from `summary` (cheap, already-aggregated columns), missing count,
    on-disk size and display name from the podcast's own table (needs a
    per-podcast scan — there's no aggregate table for it)."""
    info = PodcastInfo(name=name)
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT num_episodes, num_episodes_downloaded, podcast_image_url, check_interval_hours "
            "FROM summary WHERE name = ?",
            (name,),
        )
        row = cursor.fetchone()
        if row:
            info.total_episodes, info.downloaded_episodes, info.podcast_image_url = row[0], row[1], row[2]
            info.scheduled = row[3] is not None
    except sqlite3.Error:
        pass

    try:
        add_download_columns_to_table(conn, name)
        safe_table_name = validate_and_quote_table_name(name)
        cursor.execute(f"""
            SELECT MAX(author_raw),
                   SUM(CASE WHEN status = ? THEN 1 ELSE 0 END),
                   SUM(CASE WHEN status = ? THEN Size ELSE 0 END),
                   SUM(CASE WHEN checksum_status = 'corrupt' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN in_feed = 0 THEN 1 ELSE 0 END)
            FROM {safe_table_name}
        """, (config.EPISODE_STATUS_MISSING, config.EPISODE_STATUS_DOWNLOADED))
        author_raw, missing_count, total_size, corrupt_count, local_only_count = cursor.fetchone()
        info.display_name = author_raw or name
        info.missing_episodes = missing_count or 0
        info.total_size_bytes = total_size or 0
        info.corrupt_episodes = corrupt_count or 0
        info.local_only_episodes = local_only_count or 0
    except sqlite3.Error:
        info.display_name = info.display_name or name

    return info


def list_podcasts() -> PodcastListResult:
    """List all podcasts in the database with their download stats."""
    db_path = config.DB_PATH

    if not os.path.exists(db_path):
        return PodcastListResult(error="Database not found. Process feeds first.")
    if not is_valid_database(db_path):
        return PodcastListResult(error="Database is corrupted.")

    with get_db_connection(db_path) as conn:
        try:
            create_summary_table_if_not_exists(conn)
            names = list_podcast_table_names(conn)
            podcasts = [_build_podcast_info(conn, name) for name in names]
            return PodcastListResult(podcasts=podcasts)
        except sqlite3.Error as e:
            return PodcastListResult(error=f"Database error: {e}")


def get_podcast_info(podcast_name: str) -> Optional[PodcastInfo]:
    """Header/stat info for a single podcast (the podcast detail page), or None if it doesn't exist."""
    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return None

    with get_db_connection(db_path) as conn:
        if not table_exists(conn, podcast_name):
            return None
        create_summary_table_if_not_exists(conn)
        return _build_podcast_info(conn, podcast_name)


def get_status(podcast_name: Optional[str] = None) -> StatusResult:
    """Get the number of pending (not downloaded) episodes per podcast."""
    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return StatusResult(error="Database not found. Process feeds first.")

    with get_db_connection(db_path) as conn:
        try:
            if podcast_name:
                is_valid, error = validate_podcast_name(podcast_name)
                if not is_valid:
                    return StatusResult(error=f"Invalid podcast name '{podcast_name}': {error}")
                if not table_exists(conn, podcast_name):
                    return StatusResult(error=f"Podcast '{podcast_name}' not found in database.")
                names = [podcast_name]
            else:
                names = list_podcast_table_names(conn)
                if not names:
                    return StatusResult(error="No podcasts found in database.")

            summary = show_podcast_summary(conn, names)
            return StatusResult(pending_by_podcast=summary or {})
        except Exception as e:
            return StatusResult(error=str(e))
