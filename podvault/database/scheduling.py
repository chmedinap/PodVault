"""
Per-podcast auto-refresh schedule, stored on the `summary` table
(check_interval_hours, auto_download, last_checked_at — see
add_schedule_columns_to_summary() in schema.py).
"""

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from podvault.database.schema import add_schedule_columns_to_summary


def get_schedule(conn: sqlite3.Connection, podcast_name: str) -> Dict[str, Any]:
    """Return {'interval_hours': int|None, 'auto_download': bool, 'last_checked_at': str|None}."""
    add_schedule_columns_to_summary(conn)
    cursor = conn.execute(
        "SELECT check_interval_hours, auto_download, last_checked_at FROM summary WHERE name = ?",
        (podcast_name,),
    )
    row = cursor.fetchone()
    if row is None:
        return {'interval_hours': None, 'auto_download': False, 'last_checked_at': None}
    return {'interval_hours': row[0], 'auto_download': bool(row[1]), 'last_checked_at': row[2]}


def set_schedule(conn: sqlite3.Connection, podcast_name: str, interval_hours: Optional[int], auto_download: bool) -> None:
    """Set (or, with interval_hours=None, disable) the auto-refresh schedule for one podcast."""
    add_schedule_columns_to_summary(conn)
    conn.execute(
        "UPDATE summary SET check_interval_hours = ?, auto_download = ? WHERE name = ?",
        (interval_hours, int(bool(auto_download)), podcast_name),
    )
    conn.commit()


def mark_checked(conn: sqlite3.Connection, podcast_name: str) -> None:
    """Record that a podcast's feed was just checked (resets the due timer)."""
    conn.execute(
        "UPDATE summary SET last_checked_at = ? WHERE name = ?",
        (datetime.now(timezone.utc).isoformat(), podcast_name),
    )
    conn.commit()


def get_all_schedules(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Return every podcast's auto-refresh config in a single query (one row
    per podcast with a summary row, whether or not a schedule was ever set),
    plus the episode counts and cover image already stored on `summary` so
    callers (the Schedule page, the Library page's "Scheduled" filter) don't
    need a second per-podcast query.
    """
    add_schedule_columns_to_summary(conn)
    cursor = conn.execute("""
        SELECT name, check_interval_hours, auto_download, last_checked_at,
               num_episodes, num_episodes_downloaded, podcast_image_url
        FROM summary
        ORDER BY name
    """)
    return [
        {
            'name': row[0],
            'interval_hours': row[1],
            'auto_download': bool(row[2]),
            'last_checked_at': row[3],
            'total_episodes': row[4],
            'downloaded_episodes': row[5],
            'podcast_image_url': row[6],
        }
        for row in cursor.fetchall()
    ]


def get_due_podcasts(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Return every podcast with a configured check interval whose feed is due
    to be re-checked (never checked, or `now - last_checked_at >= interval`).

    Each item: {'name', 'rss_feed_url', 'auto_download'}.
    """
    add_schedule_columns_to_summary(conn)
    cursor = conn.execute("""
        SELECT name, rss_feed_url, auto_download, last_checked_at, check_interval_hours
        FROM summary
        WHERE check_interval_hours IS NOT NULL AND rss_feed_url IS NOT NULL
    """)

    now = datetime.now(timezone.utc)
    due = []
    for name, rss_feed_url, auto_download, last_checked_at, interval_hours in cursor.fetchall():
        if last_checked_at:
            try:
                last_checked = datetime.fromisoformat(last_checked_at)
                if last_checked.tzinfo is None:
                    last_checked = last_checked.replace(tzinfo=timezone.utc)
                elapsed_hours = (now - last_checked).total_seconds() / 3600
                if elapsed_hours < interval_hours:
                    continue
            except ValueError:
                pass  # unparseable timestamp: treat as never checked, so it's due

        due.append({'name': name, 'rss_feed_url': rss_feed_url, 'auto_download': bool(auto_download)})

    return due
