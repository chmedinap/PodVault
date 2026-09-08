"""
Database schema operations and migrations.
"""

import sqlite3
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from podvault import config
from podvault.database.queries import table_exists, validate_and_quote_table_name
from podvault.hashing import compute_checksum


def create_summary_table_if_not_exists(conn: sqlite3.Connection) -> None:
    """
    Create the summary table if it doesn't exist.
    
    Parameters:
    conn: SQLite connection object
    """
    cursor = conn.cursor()
    
    # Check if summary table exists
    if table_exists(conn, 'summary'):
        return  # Table already exists
    
    # Create the summary table with all required columns
    try:
        cursor.execute("""
            CREATE TABLE summary (
                name TEXT PRIMARY KEY,
                num_episodes INTEGER,
                num_episodes_downloaded INTEGER,
                num_episodes_not_downloaded INTEGER,
                pct_episodes_downloaded REAL,
                dataframe_name TEXT,
                last_episode_downloaded_date TEXT,
                podcast_image_url TEXT,
                rss_feed_url TEXT
            )
        """)
        conn.commit()
        print("  ✓ Created summary table")
    except sqlite3.Error as e:
        print(f"  ✗ Error creating summary table: {e}")
        raise


def add_podcast_image_url_to_summary(conn: sqlite3.Connection) -> None:
    """
    Add podcast_image_url and rss_feed_url columns to summary table if they don't exist.
    
    Parameters:
    conn: SQLite connection object
    """
    # Ensure summary table exists first
    create_summary_table_if_not_exists(conn)
    
    cursor = conn.cursor()
    
    # Get existing columns
    cursor.execute("PRAGMA table_info(summary)")
    existing_columns = [row[1] for row in cursor.fetchall()]
    
    # Add podcast_image_url column if it doesn't exist
    if 'podcast_image_url' not in existing_columns:
        try:
            cursor.execute("ALTER TABLE summary ADD COLUMN podcast_image_url TEXT")
            conn.commit()
            print("  ✓ Added column 'podcast_image_url' to summary table")
        except sqlite3.Error as e:
            print(f"  ✗ Error adding column 'podcast_image_url': {e}")
    else:
        print("  ✓ Column 'podcast_image_url' already exists in summary table")
    
    # Add rss_feed_url column if it doesn't exist
    if 'rss_feed_url' not in existing_columns:
        try:
            cursor.execute("ALTER TABLE summary ADD COLUMN rss_feed_url TEXT")
            conn.commit()
            print("  ✓ Added column 'rss_feed_url' to summary table")
        except sqlite3.Error as e:
            print(f"  ✗ Error adding column 'rss_feed_url': {e}")
    else:
        print("  ✓ Column 'rss_feed_url' already exists in summary table")


def add_schedule_columns_to_summary(conn: sqlite3.Connection) -> None:
    """
    Add check_interval_hours, auto_download, and last_checked_at columns to
    the summary table if they don't exist (Fase 8: per-podcast auto-refresh).

    Parameters:
    conn: SQLite connection object
    """
    create_summary_table_if_not_exists(conn)

    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(summary)")
    existing_columns = [row[1] for row in cursor.fetchall()]

    columns_to_add = {
        'check_interval_hours': 'INTEGER',  # NULL = scheduled checks disabled
        'auto_download': 'INTEGER DEFAULT 0',  # 0/1 boolean
        'last_checked_at': 'TEXT',  # ISO timestamp, NULL = never checked
    }

    for column_name, column_type in columns_to_add.items():
        if column_name not in existing_columns:
            try:
                cursor.execute(f"ALTER TABLE summary ADD COLUMN {column_name} {column_type}")
                conn.commit()
                print(f"  ✓ Added column '{column_name}' to summary table")
            except sqlite3.Error as e:
                print(f"  ✗ Error adding column '{column_name}': {e}")
        else:
            print(f"  ✓ Column '{column_name}' already exists in summary table")


def add_episode_metadata_columns_to_table(conn: sqlite3.Connection, table_name: str) -> None:
    """
    Add episode_number and season_number columns to an existing table if they don't exist.
    
    Parameters:
    conn: SQLite connection object
    table_name: Name of the table to update
    """
    if not table_exists(conn, table_name):
        print(f"Table '{table_name}' does not exist. Cannot add columns.")
        return
    
    cursor = conn.cursor()
    
    # Get existing columns
    safe_table_name = validate_and_quote_table_name(table_name)
    cursor.execute(f"PRAGMA table_info({safe_table_name})")
    existing_columns = [row[1] for row in cursor.fetchall()]
    
    # Add episode_number column if it doesn't exist
    if 'episode_number' not in existing_columns:
        try:
            cursor.execute(f"ALTER TABLE {safe_table_name} ADD COLUMN episode_number TEXT")
            conn.commit()
            print(f"  ✓ Added column 'episode_number' to table '{table_name}'")
        except sqlite3.Error as e:
            print(f"  ✗ Error adding column 'episode_number': {e}")
    else:
        print(f"  ✓ Column 'episode_number' already exists in table '{table_name}'")
    
    # Add season_number column if it doesn't exist
    if 'season_number' not in existing_columns:
        try:
            cursor.execute(f"ALTER TABLE {safe_table_name} ADD COLUMN season_number TEXT")
            conn.commit()
            print(f"  ✓ Added column 'season_number' to table '{table_name}'")
        except sqlite3.Error as e:
            print(f"  ✗ Error adding column 'season_number': {e}")
    else:
        print(f"  ✓ Column 'season_number' already exists in table '{table_name}'")


def add_author_raw_column_to_table(conn: sqlite3.Connection, table_name: str) -> None:
    """
    Add author_raw column to an existing table if it doesn't exist.
    
    Parameters:
    conn: SQLite connection object
    table_name: Name of the table to update
    """
    if not table_exists(conn, table_name):
        print(f"Table '{table_name}' does not exist. Cannot add column.")
        return
    
    cursor = conn.cursor()
    
    # Get existing columns
    safe_table_name = validate_and_quote_table_name(table_name)
    cursor.execute(f"PRAGMA table_info({safe_table_name})")
    existing_columns = [row[1] for row in cursor.fetchall()]
    
    # Add column if it doesn't exist
    if 'author_raw' not in existing_columns:
        try:
            cursor.execute(f"ALTER TABLE {safe_table_name} ADD COLUMN author_raw TEXT")
            conn.commit()
            print(f"  ✓ Added column 'author_raw' to table '{table_name}'")
        except sqlite3.Error as e:
            print(f"  ✗ Error adding column 'author_raw': {e}")
    else:
        print(f"  ✓ Column 'author_raw' already exists in table '{table_name}'")


def add_download_columns_to_table(conn: sqlite3.Connection, table_name: str) -> None:
    """
    Add Saved_Path, Size, File_name, custom_title, author_raw, episode_number,
    season_number, and the integrity/feed-tracking columns (Checksum,
    checksum_verified_at, checksum_status, in_feed) to an existing table if
    they don't exist.

    `in_feed` defaults to 1 (present) so existing rows — added before this
    column existed — aren't mistaken for episodes removed from the feed.

    Parameters:
    conn: SQLite connection object
    table_name: Name of the table to update
    """
    if not table_exists(conn, table_name):
        print(f"Table '{table_name}' does not exist. Cannot add columns.")
        return
    
    cursor = conn.cursor()
    
    # Get existing columns
    safe_table_name = validate_and_quote_table_name(table_name)
    cursor.execute(f"PRAGMA table_info({safe_table_name})")
    existing_columns = [row[1] for row in cursor.fetchall()]
    
    # Add author_raw column if it doesn't exist
    if 'author_raw' not in existing_columns:
        try:
            cursor.execute(f"ALTER TABLE {safe_table_name} ADD COLUMN author_raw TEXT")
            conn.commit()
            print(f"  ✓ Added column 'author_raw' to table '{table_name}'")
        except sqlite3.Error as e:
            print(f"  ✗ Error adding column 'author_raw': {e}")
    else:
        print(f"  ✓ Column 'author_raw' already exists in table '{table_name}'")
    
    # Add episode_number column if it doesn't exist
    if 'episode_number' not in existing_columns:
        try:
            cursor.execute(f"ALTER TABLE {safe_table_name} ADD COLUMN episode_number TEXT")
            conn.commit()
            print(f"  ✓ Added column 'episode_number' to table '{table_name}'")
        except sqlite3.Error as e:
            print(f"  ✗ Error adding column 'episode_number': {e}")
    else:
        print(f"  ✓ Column 'episode_number' already exists in table '{table_name}'")
    
    # Add season_number column if it doesn't exist
    if 'season_number' not in existing_columns:
        try:
            cursor.execute(f"ALTER TABLE {safe_table_name} ADD COLUMN season_number TEXT")
            conn.commit()
            print(f"  ✓ Added column 'season_number' to table '{table_name}'")
        except sqlite3.Error as e:
            print(f"  ✗ Error adding column 'season_number': {e}")
    else:
        print(f"  ✓ Column 'season_number' already exists in table '{table_name}'")
    
    # Add download columns if they don't exist
    columns_to_add = {
        'Saved_Path': 'TEXT',
        'Size': 'INTEGER',
        'File_name': 'TEXT',
        'custom_title': 'TEXT',
        'Checksum': 'TEXT',
        'checksum_verified_at': 'TEXT',
        'checksum_status': 'TEXT',
        'in_feed': 'INTEGER DEFAULT 1',
    }
    
    for column_name, column_type in columns_to_add.items():
        if column_name not in existing_columns:
            try:
                # Column names come from a fixed dict, so they're safe, but we quote them for consistency
                # SQLite supports case-sensitive identifiers when quoted
                safe_column_name = f'"{column_name}"'
                cursor.execute(f"ALTER TABLE {safe_table_name} ADD COLUMN {safe_column_name} {column_type}")
                conn.commit()
                print(f"  ✓ Added column '{column_name}' to table '{table_name}'")
            except sqlite3.Error as e:
                print(f"  ✗ Error adding column '{column_name}': {e}")
        else:
            print(f"  ✓ Column '{column_name}' already exists in table '{table_name}'")


def update_download_info(
    conn: sqlite3.Connection, podcast_name: str, episode_id: str, file_path: Path,
    auto_commit: bool = True, checksum: Optional[str] = None,
) -> bool:
    """
    Update Saved_Path, Size, and File_name fields in the database after a successful download.

    Pass `checksum` (the freshly-downloaded file's SHA-256, from
    `podvault.hashing.compute_checksum`) to stamp a baseline integrity hash at
    the same time — only relevant right after a real download; leave it None
    when this is called for a file that was already on disk (e.g. re-running
    `download_last` and finding it already downloaded), so an existing
    checksum from a previous run is never overwritten with nothing.

    Parameters:
    conn: SQLite connection object
    podcast_name: Name of the podcast (table name)
    episode_id: ID of the episode
    file_path: Path object or string of the downloaded file
    auto_commit: If True, commit immediately. If False, caller handles commit (for batch transactions)
    checksum: SHA-256 hex digest to stamp as the integrity baseline, or None to leave it untouched

    Returns:
    True if update successful, False otherwise
    """
    if not table_exists(conn, podcast_name):
        print(f"Table '{podcast_name}' does not exist. Cannot update download info.")
        return False

    # Convert Path to string if needed
    file_path_str = str(file_path) if isinstance(file_path, Path) else file_path
    file_path_obj = Path(file_path_str)

    # Check if file exists
    if not file_path_obj.exists():
        print(f"  ⚠️  File does not exist: {file_path_str}")
        return False

    # Get file size in bytes
    file_size = file_path_obj.stat().st_size

    # Get file name with extension
    file_name = file_path_obj.name

    verified_at = datetime.now(timezone.utc).isoformat() if checksum else None
    checksum_status = 'ok' if checksum else None

    # Update the database
    try:
        cursor = conn.cursor()
        safe_table_name = validate_and_quote_table_name(podcast_name)
        cursor.execute(f"""
            UPDATE {safe_table_name}
            SET Saved_Path = ?,
                Size = ?,
                File_name = ?,
                Checksum = COALESCE(?, Checksum),
                checksum_verified_at = COALESCE(?, checksum_verified_at),
                checksum_status = COALESCE(?, checksum_status)
            WHERE id = ?
        """, (file_path_str, file_size, file_name, checksum, verified_at, checksum_status, episode_id))
        if auto_commit:
            conn.commit()
        print(f"  ✓ Updated download info: {file_name} ({file_size:,} bytes)")
        return True
    except sqlite3.Error as e:
        print(f"  ✗ Error updating download info: {e}")
        return False


def update_all_tables_with_episode_metadata_columns(conn: sqlite3.Connection) -> None:
    """
    Add episode_number and season_number columns to all existing podcast tables.
    Run this once to update existing tables.
    
    Parameters:
    conn: SQLite connection object
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name != 'summary'
    """)
    
    tables = cursor.fetchall()
    
    if not tables:
        print("No tables found to update.")
        return
    
    print(f"Updating {len(tables)} table(s) with episode metadata columns...\n")
    
    for (table_name,) in tables:
        print(f"Updating table: {table_name}")
        add_episode_metadata_columns_to_table(conn, table_name)
        print()
    
    print("All tables updated with episode metadata columns!")


def update_all_tables_with_author_raw_column(conn: sqlite3.Connection) -> None:
    """
    Add author_raw column to all existing podcast tables.
    Run this once to update existing tables.
    
    Parameters:
    conn: SQLite connection object
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name != 'summary'
    """)
    
    tables = cursor.fetchall()
    
    if not tables:
        print("No tables found to update.")
        return
    
    print(f"Updating {len(tables)} table(s) with author_raw column...\n")
    
    for (table_name,) in tables:
        print(f"Updating table: {table_name}")
        add_author_raw_column_to_table(conn, table_name)
        print()
    
    print("All tables updated with author_raw column!")


def update_all_tables_with_download_columns(conn: sqlite3.Connection) -> None:
    """
    Add download columns (Saved_Path, Size, File_name) to all existing podcast tables.
    Run this once to update existing tables.
    
    Parameters:
    conn: SQLite connection object
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name != 'summary'
    """)
    
    tables = cursor.fetchall()
    
    if not tables:
        print("No tables found to update.")
        return
    
    print(f"Updating {len(tables)} table(s) with download columns...\n")
    
    for (table_name,) in tables:
        print(f"Updating table: {table_name}")
        add_download_columns_to_table(conn, table_name)
        print()
    
    print("All tables updated!")


def index_exists(conn: sqlite3.Connection, table_name: str, index_name: str) -> bool:
    """
    Check if an index exists for a table.
    
    Parameters:
    conn: SQLite connection object
    table_name: Name of the table
    index_name: Name of the index
    
    Returns:
    True if index exists, False otherwise
    """
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='index' AND name=?
        """, (index_name,))
        result = cursor.fetchone()
        return result is not None
    except sqlite3.Error as e:
        print(f"Error checking index existence: {e}")
        return False


def add_indexes_to_table(conn: sqlite3.Connection, table_name: str) -> None:
    """
    Add performance indexes to a podcast table.
    Creates indexes on: status, published, and id columns.
    
    Parameters:
    conn: SQLite connection object
    table_name: Name of the table to add indexes to
    """
    if not table_exists(conn, table_name):
        print(f"Table '{table_name}' does not exist. Cannot add indexes.")
        return
    
    cursor = conn.cursor()
    safe_table_name = validate_and_quote_table_name(table_name)
    
    # Indexes to create: (index_name, column, unique)
    # Index names are derived from table_name, so they're safe, but we validate anyway
    indexes_to_create = [
        (f"idx_{table_name}_status", "status", False),
        (f"idx_{table_name}_published", "published", False),
        (f"idx_{table_name}_id", "id", True),  # id should be unique
    ]
    
    for index_name, column, is_unique in indexes_to_create:
        if not index_exists(conn, table_name, index_name):
            try:
                unique_clause = "UNIQUE" if is_unique else ""
                # Index names are derived from validated table_name, so they're safe
                # But we still quote them for safety
                safe_index_name = f'"{index_name}"'
                cursor.execute(f"""
                    CREATE {unique_clause} INDEX IF NOT EXISTS {safe_index_name}
                    ON {safe_table_name}({column})
                """)
                conn.commit()
                print(f"  ✓ Created index '{index_name}' on column '{column}'")
            except sqlite3.Error as e:
                print(f"  ✗ Error creating index '{index_name}': {e}")
        else:
            print(f"  ✓ Index '{index_name}' already exists")


def update_all_tables_with_indexes(conn: sqlite3.Connection) -> None:
    """
    Add performance indexes to all existing podcast tables.
    Run this once to optimize existing tables.
    
    Parameters:
    conn: SQLite connection object
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name != 'summary'
    """)
    
    tables = cursor.fetchall()
    
    if not tables:
        print("No tables found to update.")
        return
    
    print(f"Adding indexes to {len(tables)} table(s)...\n")
    
    for (table_name,) in tables:
        print(f"Updating table: {table_name}")
        add_indexes_to_table(conn, table_name)
        print()
    
    print("All tables updated with indexes!")


def create_jobs_table_if_not_exists(conn: sqlite3.Connection) -> None:
    """
    Create the jobs table if it doesn't exist.

    Tracks background jobs (feed processing, downloads) so long-running
    operations can be triggered and polled from a non-interactive caller
    (e.g. a web UI) instead of blocking on the calling thread.

    Parameters:
    conn: SQLite connection object
    """
    cursor = conn.cursor()

    if table_exists(conn, 'jobs'):
        return

    try:
        cursor.execute("""
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                podcast_name TEXT,
                status TEXT NOT NULL,
                progress INTEGER DEFAULT 0,
                total INTEGER DEFAULT 0,
                message TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        print("  ✓ Created jobs table")
    except sqlite3.Error as e:
        print(f"  ✗ Error creating jobs table: {e}")
        raise


def create_app_settings_table_if_not_exists(conn: sqlite3.Connection) -> None:
    """
    Create the single-row app_settings table if it doesn't exist, seeded
    with the default naming templates (see config.py).

    Parameters:
    conn: SQLite connection object
    """
    if table_exists(conn, config.APP_SETTINGS_TABLE_NAME):
        return

    try:
        cursor = conn.cursor()
        cursor.execute(f"""
            CREATE TABLE {config.APP_SETTINGS_TABLE_NAME} (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                folder_template TEXT NOT NULL,
                filename_template TEXT NOT NULL
            )
        """)
        cursor.execute(
            f"INSERT INTO {config.APP_SETTINGS_TABLE_NAME} (id, folder_template, filename_template) VALUES (1, ?, ?)",
            (config.DEFAULT_FOLDER_TEMPLATE, config.DEFAULT_FILENAME_TEMPLATE),
        )
        conn.commit()
        print("  ✓ Created app_settings table")
    except sqlite3.Error as e:
        print(f"  ✗ Error creating app_settings table: {e}")
        raise


def explain_query_plan(conn: sqlite3.Connection, query: str, params: tuple = None) -> None:
    """
    Analyze query performance using EXPLAIN QUERY PLAN.
    
    Parameters:
    conn: SQLite connection object
    query: SQL query to analyze
    params: Query parameters (optional)
    """
    cursor = conn.cursor()
    explain_query = f"EXPLAIN QUERY PLAN {query}"
    
    try:
        if params:
            cursor.execute(explain_query, params)
        else:
            cursor.execute(explain_query)
        
        results = cursor.fetchall()
        print("Query Plan:")
        for row in results:
            print(f"  {row}")
    except sqlite3.Error as e:
        print(f"Error analyzing query: {e}")


