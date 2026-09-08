"""
Metadata update functions for download tracking and summaries.
"""

import sqlite3
import logging
import traceback
import pandas as pd
from podvault.database.queries import table_exists, summary_exists, validate_and_quote_table_name
from podvault.database.schema import create_summary_table_if_not_exists
from podvault import config
from podvault.config import EPISODE_STATUS_DOWNLOADED, EPISODE_STATUS_NOT_DOWNLOADED, EPISODE_STATUS_MISSING

# Set up logging
from podvault.logging_config import setup_logging
logger = setup_logging(__name__)


def update_summary(conn: sqlite3.Connection, podcast_name: str, podcast_image_url: str = None, rss_feed_url: str = None) -> None:
    """
    Update the summary table for a specific podcast after downloading an episode.
    Optimized version that uses SQL aggregation instead of reading entire table.
    
    Parameters:
    conn: SQLite connection object
    podcast_name: Name of the podcast (table name)
    """
    # Ensure summary table exists
    create_summary_table_if_not_exists(conn)
    
    if not table_exists(conn, podcast_name):
        return
    
    cursor = conn.cursor()
    
    try:
        # Use SQL aggregation to calculate statistics directly in database
        # This avoids reading the entire table into memory
        safe_table_name = validate_and_quote_table_name(podcast_name)
        cursor.execute(f"""
            SELECT
                COUNT(*) as num_episodes,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) as num_episodes_downloaded,
                SUM(CASE WHEN status IN (?, ?) THEN 1 ELSE 0 END) as num_episodes_not_downloaded
            FROM {safe_table_name}
        """, (EPISODE_STATUS_DOWNLOADED, EPISODE_STATUS_NOT_DOWNLOADED, EPISODE_STATUS_MISSING))
        
        stats = cursor.fetchone()
        num_episodes, num_episodes_downloaded, num_episodes_not_downloaded = stats
    except sqlite3.Error as e:
        logger.error(f"Database error calculating summary statistics for '{podcast_name}': {e}")
        logger.debug(traceback.format_exc())
        return
    
    # Calculate percentage
    pct_downloaded = round((num_episodes_downloaded / num_episodes * 100) if num_episodes > 0 else 0, 2)
    
    # Get the last downloaded episode date (only read this specific data)
    # This is the only part that needs to read actual row data
    try:
        cursor.execute(f"""
            SELECT published, published_parsed
            FROM {safe_table_name}
            WHERE status = ?
            ORDER BY published DESC
            LIMIT 1
        """, (EPISODE_STATUS_DOWNLOADED,))
        
        last_downloaded_result = cursor.fetchone()
        last_downloaded_date = None
        
        if last_downloaded_result:
            published, published_parsed = last_downloaded_result
            try:
                if published:
                    last_downloaded_date = pd.to_datetime(published)
                elif published_parsed:
                    last_downloaded_date = pd.to_datetime(published_parsed)
            except (ValueError, TypeError) as e:
                logger.debug(f"Failed to parse last downloaded date: {e}")
                last_downloaded_date = None
    except sqlite3.Error as e:
        logger.warning(f"Database error reading last downloaded date for '{podcast_name}': {e}")
        logger.debug(traceback.format_exc())
        last_downloaded_date = None
    
    # Get dataframe name
    dataframe_name = f"df_{podcast_name}"
    
    # Get existing podcast_image_url if updating (don't overwrite if not provided)
    existing_image_url = None
    if summary_exists(conn, podcast_name) and podcast_image_url is None:
        try:
            cursor.execute("SELECT podcast_image_url FROM summary WHERE name = ?", (podcast_name,))
            result = cursor.fetchone()
            if result:
                existing_image_url = result[0]
        except sqlite3.Error:
            pass  # Column might not exist yet
    
    # Use provided URL or keep existing one
    final_image_url = podcast_image_url if podcast_image_url is not None else existing_image_url
    
    # Get existing rss_feed_url if updating (don't overwrite if not provided)
    existing_rss_url = None
    if summary_exists(conn, podcast_name) and rss_feed_url is None:
        try:
            cursor.execute("SELECT rss_feed_url FROM summary WHERE name = ?", (podcast_name,))
            result = cursor.fetchone()
            if result:
                existing_rss_url = result[0]
        except sqlite3.Error:
            pass  # Column might not exist yet
    
    # Use provided RSS URL or keep existing one
    final_rss_url = rss_feed_url if rss_feed_url is not None else existing_rss_url
    
    # Check which columns exist
    cursor.execute("PRAGMA table_info(summary)")
    columns = [row[1] for row in cursor.fetchall()]
    has_image_column = 'podcast_image_url' in columns
    has_rss_column = 'rss_feed_url' in columns
    
    # Update or insert summary using UPSERT pattern
    try:
        if summary_exists(conn, podcast_name):
            # Update existing summary
            if has_image_column and has_rss_column:
                cursor.execute("""
                    UPDATE summary 
                    SET 
                        num_episodes = ?,
                        num_episodes_downloaded = ?,
                        num_episodes_not_downloaded = ?,
                        pct_episodes_downloaded = ?,
                        dataframe_name = ?,
                        last_episode_downloaded_date = ?,
                        podcast_image_url = COALESCE(?, podcast_image_url),
                        rss_feed_url = COALESCE(?, rss_feed_url)
                    WHERE name = ?
                """, (
                    num_episodes,
                    num_episodes_downloaded,
                    num_episodes_not_downloaded,
                    pct_downloaded,
                    dataframe_name,
                    str(last_downloaded_date) if last_downloaded_date else None,
                    final_image_url,
                    final_rss_url,
                    podcast_name
                ))
            elif has_image_column:
                cursor.execute("""
                    UPDATE summary 
                    SET 
                        num_episodes = ?,
                        num_episodes_downloaded = ?,
                        num_episodes_not_downloaded = ?,
                        pct_episodes_downloaded = ?,
                        dataframe_name = ?,
                        last_episode_downloaded_date = ?,
                        podcast_image_url = COALESCE(?, podcast_image_url)
                    WHERE name = ?
                """, (
                    num_episodes,
                    num_episodes_downloaded,
                    num_episodes_not_downloaded,
                    pct_downloaded,
                    dataframe_name,
                    str(last_downloaded_date) if last_downloaded_date else None,
                    final_image_url,
                    podcast_name
                ))
            else:
                cursor.execute("""
                    UPDATE summary 
                    SET 
                        num_episodes = ?,
                        num_episodes_downloaded = ?,
                        num_episodes_not_downloaded = ?,
                        pct_episodes_downloaded = ?,
                        dataframe_name = ?,
                        last_episode_downloaded_date = ?
                    WHERE name = ?
                """, (
                    num_episodes,
                    num_episodes_downloaded,
                    num_episodes_not_downloaded,
                    pct_downloaded,
                    dataframe_name,
                    str(last_downloaded_date) if last_downloaded_date else None,
                    podcast_name
                ))
        else:
            # Insert new summary
            if has_image_column and has_rss_column:
                cursor.execute("""
                    INSERT INTO summary (
                        name, num_episodes, num_episodes_downloaded, 
                        num_episodes_not_downloaded, pct_episodes_downloaded,
                        dataframe_name, last_episode_downloaded_date, podcast_image_url, rss_feed_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    podcast_name,
                    num_episodes,
                    num_episodes_downloaded,
                    num_episodes_not_downloaded,
                    pct_downloaded,
                    dataframe_name,
                    str(last_downloaded_date) if last_downloaded_date else None,
                    final_image_url,
                    final_rss_url
                ))
            elif has_image_column:
                cursor.execute("""
                    INSERT INTO summary (
                        name, num_episodes, num_episodes_downloaded, 
                        num_episodes_not_downloaded, pct_episodes_downloaded,
                        dataframe_name, last_episode_downloaded_date, podcast_image_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    podcast_name,
                    num_episodes,
                    num_episodes_downloaded,
                    num_episodes_not_downloaded,
                    pct_downloaded,
                    dataframe_name,
                    str(last_downloaded_date) if last_downloaded_date else None,
                    final_image_url
                ))
            else:
                cursor.execute("""
                    INSERT INTO summary (
                        name, num_episodes, num_episodes_downloaded, 
                        num_episodes_not_downloaded, pct_episodes_downloaded,
                        dataframe_name, last_episode_downloaded_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    podcast_name,
                    num_episodes,
                    num_episodes_downloaded,
                    num_episodes_not_downloaded,
                    pct_downloaded,
                    dataframe_name,
                    str(last_downloaded_date) if last_downloaded_date else None
                ))
        
        conn.commit()
        print(f"Summary updated for '{podcast_name}' (optimized SQL aggregation)")
    except sqlite3.Error as e:
        logger.error(f"Database error updating summary for '{podcast_name}': {e}")
        logger.debug(traceback.format_exc())
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        print(f"Error updating summary for '{podcast_name}': {e}")


