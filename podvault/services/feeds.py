"""
Service layer for feed ingestion (add a feed / process the feeds file).

The feeds file defaults to `config.FEEDS_FILE` (`PODVAULT_FEEDS_FILE`) —
before Fase 24 every caller hardcoded `'feeds.txt'` relative to the
working directory, so in Docker the web UI's "add feed" wrote outside the
`/app/data` volume and the list was lost on every image rebuild.

Extracted from cli.py so the same logic can be triggered from a web
handler or a background job, not just the argparse entry point.
"""

import os
from pathlib import Path
from typing import Callable, List, Optional

import pandas as pd

from podvault import config
from podvault.data.collection import collect_data, normalize_feed_url
from podvault.database.backup import maybe_backup_database, preserve_corrupt_database
from podvault.database.connection import clean_dataframe_for_sqlite, get_db_connection, is_valid_database
from podvault.database.queries import table_exists, validate_and_quote_table_name
from podvault.database.schema import add_download_columns_to_table, add_indexes_to_table, add_podcast_image_url_to_summary
from podvault.download.metadata import update_summary
from podvault.logging_config import setup_logging
from podvault.services.results import FeedResult, ProcessFeedsResult
from podvault.validation import (
    sanitize_podcast_name,
    validate_feed_url,
    validate_file_path,
    validate_podcast_name,
)

logger = setup_logging(__name__)

ProgressCallback = Callable[[int, int, str], None]

# Columns that only ever hold local app state (never RSS data): re-processing a
# feed must not stomp on these for episodes we already know about.
_TRACKING_COLUMNS = [
    'status', 'Saved_Path', 'Size', 'File_name', 'custom_title',
    'Checksum', 'checksum_verified_at', 'checksum_status',
]


def _preserve_download_state(conn, table_name: str, df: pd.DataFrame) -> pd.DataFrame:
    """
    Carry download-tracking state over from the existing table into a
    freshly-parsed feed DataFrame, matched by episode id.

    `collect_data()` always returns a brand-new DataFrame where every
    episode is 'not downloaded' with no saved path — that's correct for a
    podcast's first sync, but re-processing a feed you've already
    downloaded episodes from used to write this fresh DataFrame straight
    over the table (`to_sql(if_exists='replace')`), silently wiping every
    episode's download status, saved file path, and custom title. This
    restores that state for any episode id that already existed before
    the write happens, so re-syncing a feed only adds new episodes.
    """
    if not table_exists(conn, table_name):
        return df

    safe_table_name = validate_and_quote_table_name(table_name)
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({safe_table_name})")
    existing_columns = {row[1] for row in cursor.fetchall()}

    columns_to_restore = [col for col in _TRACKING_COLUMNS if col in existing_columns]
    if not columns_to_restore:
        return df

    select_columns = ', '.join(f'"{col}"' for col in ['id'] + columns_to_restore)
    existing = pd.read_sql_query(f"SELECT {select_columns} FROM {safe_table_name}", conn)
    if existing.empty:
        return df

    merged = df.merge(existing, on='id', how='left', suffixes=('', '_existing'))
    for col in columns_to_restore:
        existing_col = f'{col}_existing'
        if existing_col in merged.columns:
            # Prefer the value already on record; episodes new to this sync have
            # no match in `existing`, so they fall back to df's fresh default.
            merged[col] = merged[existing_col].combine_first(merged[col])
            merged = merged.drop(columns=[existing_col])
    return merged


def _combine_with_removed_episodes(conn, table_name: str, df: pd.DataFrame) -> pd.DataFrame:
    """
    Mark every episode in `df` as `in_feed=1` (it's in this fetch), and union
    in — verbatim, with `in_feed=0` — any episode already stored under
    `table_name` that this fetch's feed no longer lists.

    `process_single_feed` writes with `to_sql(if_exists='replace')`: without
    this, an episode a podcast delists (or a feed that shrinks to only its
    last N items) would simply vanish from the table on the next sync,
    exactly the kind of loss a vault exists to prevent. Once marked
    `in_feed=0` a row is never touched again by a future sync unless the
    episode's id reappears in a live fetch, at which point it naturally goes
    back to `in_feed=1` and picks up any RSS-derived field changes as usual.
    """
    df = df.copy()
    df['in_feed'] = 1

    if not table_exists(conn, table_name):
        return df

    # Ensure the old table has `in_feed` (and friends) before reading it back —
    # relevant the first time this runs against a table created before this
    # column existed; existing rows default to 1 (assumed present) via the
    # column's DEFAULT, which is safe since it's about to be overridden to 0
    # for whichever ones this fetch shows are actually gone.
    add_download_columns_to_table(conn, table_name)

    safe_table_name = validate_and_quote_table_name(table_name)
    existing_ids = pd.read_sql_query(f"SELECT id FROM {safe_table_name}", conn)['id']
    removed_ids = set(existing_ids) - set(df['id'])
    if not removed_ids:
        return df

    placeholders = ', '.join('?' for _ in removed_ids)
    removed_df = pd.read_sql_query(
        f"SELECT * FROM {safe_table_name} WHERE id IN ({placeholders})", conn, params=list(removed_ids),
    )
    removed_df['in_feed'] = 0
    return pd.concat([df, removed_df], ignore_index=True, sort=False)


def process_single_feed(conn, feed_url: str) -> FeedResult:
    is_valid, error = validate_feed_url(feed_url)
    if not is_valid:
        return FeedResult(feed_url=feed_url, success=False, skipped=True, error=error)

    try:
        normalized_url = normalize_feed_url(feed_url)
        df = collect_data(normalized_url)

        if df.empty:
            return FeedResult(feed_url=feed_url, success=False, error="No episodes found in feed")

        title = df['author'].iloc[0]
        is_valid, error = validate_podcast_name(title)
        if not is_valid:
            title = sanitize_podcast_name(title)

        rss_feed_url = df.attrs.get('rss_feed_url') if hasattr(df, 'attrs') else None
        podcast_image_url = df.attrs.get('podcast_image_url') if hasattr(df, 'attrs') else None
        if not rss_feed_url:
            rss_feed_url = normalized_url

        episodes_found = len(df)
        df = _preserve_download_state(conn, title, df)
        df = _combine_with_removed_episodes(conn, title, df)
        df_clean = clean_dataframe_for_sqlite(df)
        df_clean.to_sql(title, conn, if_exists='replace', index=False)
        add_indexes_to_table(conn, title)
        update_summary(conn, title, podcast_image_url=podcast_image_url, rss_feed_url=rss_feed_url)

        return FeedResult(feed_url=feed_url, success=True, podcast_name=title, episodes_found=episodes_found)

    except Exception as e:
        logger.error(f"Error processing feed '{feed_url}': {e}")
        return FeedResult(feed_url=feed_url, success=False, error=str(e))


def read_feeds_file(feeds_file: Optional[str] = None) -> List[str]:
    """Feed URLs listed in `feeds_file` (default `config.FEEDS_FILE`), skipping blanks and `#` comments."""
    feeds_path = Path(feeds_file or config.FEEDS_FILE)
    if not feeds_path.exists():
        return []
    with open(feeds_path, 'r', encoding='utf-8') as f:
        lines = [line.strip() for line in f.readlines()]
    return [line for line in lines if line and not line.startswith('#')]


def append_to_feeds_file(feed_urls: List[str], feeds_file: Optional[str] = None) -> List[str]:
    """Append whichever of `feed_urls` aren't already listed; returns the ones actually added."""
    feeds_path = Path(feeds_file or config.FEEDS_FILE)
    existing = set(read_feeds_file(str(feeds_path)))
    new_urls = []
    for url in feed_urls:
        if url not in existing and url not in new_urls:
            new_urls.append(url)
    if not new_urls:
        return []
    feeds_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = '\n' if feeds_path.exists() and feeds_path.stat().st_size > 0 else ''
    with open(feeds_path, 'a', encoding='utf-8') as f:
        f.write(prefix + ''.join(f"{url}\n" for url in new_urls))
    return new_urls


def process_feed_urls(feed_urls: List[str], progress_callback: Optional[ProgressCallback] = None) -> ProcessFeedsResult:
    """Sync each of `feed_urls` into the database, one at a time (backing the database up first)."""
    db_path = config.DB_PATH
    if os.path.exists(db_path) and not is_valid_database(db_path):
        logger.warning(f"Database {db_path} is corrupted. Recreating it.")
        preserve_corrupt_database(db_path)
        os.remove(db_path)
    else:
        # Each sync rewrites podcast tables wholesale; keep a recent copy first.
        maybe_backup_database(db_path)

    total = len(feed_urls)
    results = []
    with get_db_connection(db_path) as conn:
        add_podcast_image_url_to_summary(conn)

        for idx, feed_url in enumerate(feed_urls, 1):
            if progress_callback:
                try:
                    progress_callback(idx, total, feed_url)
                except Exception:
                    logger.debug("progress_callback raised an exception", exc_info=True)
            results.append(process_single_feed(conn, feed_url))

    return ProcessFeedsResult(feeds=results)


def process_feeds_file(feeds_file: Optional[str] = None, progress_callback: Optional[ProgressCallback] = None) -> ProcessFeedsResult:
    """Process every feed URL listed in `feeds_file` (default `config.FEEDS_FILE`), one at a time."""
    feeds_file = feeds_file or config.FEEDS_FILE
    is_valid, error = validate_file_path(feeds_file)
    if not is_valid:
        return ProcessFeedsResult(error=f"Invalid file path '{feeds_file}': {error}")

    feeds_path = Path(feeds_file)
    if not feeds_path.exists():
        return ProcessFeedsResult(error=f"{feeds_file} not found")

    feed_urls = read_feeds_file(feeds_file)
    if not feed_urls:
        return ProcessFeedsResult(error=f"No feeds found in {feeds_file}")

    return process_feed_urls(feed_urls, progress_callback=progress_callback)


def add_feed(
    feed_url: str,
    feeds_file: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
    job_control=None,
) -> ProcessFeedsResult:
    """Append `feed_url` to `feeds_file` (creating it if needed) and process it.

    `job_control` isn't used here — parsing one feed is fast enough that
    cancelling mid-flight isn't worth the complexity — but every JobRunner
    target accepts it for a consistent calling convention (see jobs.py).
    """
    is_valid, error = validate_feed_url(feed_url)
    if not is_valid:
        return ProcessFeedsResult(error=f"Invalid feed URL: {error}")

    feeds_file = feeds_file or config.FEEDS_FILE
    append_to_feeds_file([feed_url], feeds_file)

    return process_feeds_file(feeds_file, progress_callback=progress_callback)
