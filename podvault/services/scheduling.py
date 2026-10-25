"""
Per-podcast auto-refresh: check a podcast's feed for new episodes on a
schedule, optionally downloading them automatically (see ROADMAP.md Fase 8).

`run_due_checks()` is the entry point the background scheduler thread
(`podvault.scheduler`) calls periodically — it re-syncs every podcast
whose interval has elapsed and, for the ones with auto_download on, submits
a `download_all` job. It's also safe to call directly (e.g. from a test, or
a one-off CLI invocation) since it just does one pass and returns.
"""

import os
import sqlite3
import uuid
from typing import List, Optional

from podvault import config
from podvault.database import jobs as jobs_db
from podvault.database.connection import get_db_connection
from podvault.database.queries import table_exists, validate_and_quote_table_name
from podvault.database.scheduling import get_all_schedules, get_due_podcasts, get_schedule, mark_checked, set_schedule
from podvault.jobs import get_job_runner
from podvault.logging_config import setup_logging
from podvault.services.downloads import download_all
from podvault.services.feeds import process_single_feed
from podvault.services.results import CheckOutcome, CheckRunResult, ScheduleInfo, ScheduleResult
from podvault.validation import validate_podcast_name

logger = setup_logging(__name__)

# Fixed choices offered by the web UI; the CLI/service accept any positive integer.
INTERVAL_CHOICES_HOURS = (6, 12, 24, 168)


def get_podcast_schedule(podcast_name: str) -> ScheduleInfo:
    """Return the current auto-refresh configuration for a podcast (disabled if none was ever set)."""
    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return ScheduleInfo()

    with get_db_connection(db_path) as conn:
        schedule = get_schedule(conn, podcast_name)
    return ScheduleInfo(
        interval_hours=schedule['interval_hours'],
        auto_download=schedule['auto_download'],
        last_checked_at=schedule['last_checked_at'],
    )


def set_podcast_schedule(
    podcast_name: str,
    interval_hours: Optional[int],
    auto_download: bool,
) -> ScheduleResult:
    """
    Configure (or, with interval_hours=None, disable) automatic feed checks
    for a podcast. When enabled, the background scheduler re-syncs the feed
    every `interval_hours`, downloading new episodes too if `auto_download`.
    """
    is_valid, error = validate_podcast_name(podcast_name)
    if not is_valid:
        return ScheduleResult(podcast_name, success=False, error=f"Invalid podcast name '{podcast_name}': {error}")

    if interval_hours is not None and interval_hours <= 0:
        return ScheduleResult(podcast_name, success=False, error="interval_hours must be a positive number")

    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return ScheduleResult(podcast_name, success=False, error="Database not found. Process feeds first.")

    with get_db_connection(db_path) as conn:
        if not table_exists(conn, podcast_name):
            return ScheduleResult(podcast_name, success=False, error=f"Podcast '{podcast_name}' not found in database.")
        set_schedule(conn, podcast_name, interval_hours, auto_download)

    return ScheduleResult(podcast_name, success=True)


def list_schedules() -> List[ScheduleInfo]:
    """Every podcast's auto-refresh configuration, in one pass (for the Schedule page and the Library 'Scheduled' filter)."""
    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return []

    with get_db_connection(db_path) as conn:
        rows = get_all_schedules(conn)
        display_names = {row['name']: _display_name(conn, row['name']) for row in rows}

    return [
        ScheduleInfo(
            podcast_name=row['name'],
            interval_hours=row['interval_hours'],
            auto_download=row['auto_download'],
            last_checked_at=row['last_checked_at'],
            total_episodes=row['total_episodes'],
            downloaded_episodes=row['downloaded_episodes'],
            podcast_image_url=row['podcast_image_url'],
            display_name=display_names[row['name']],
        )
        for row in rows
    ]


def _display_name(conn: sqlite3.Connection, podcast_name: str) -> str:
    """The feed's original title (`author_raw`), as the Library shows it; the table name if it has none."""
    try:
        safe_table_name = validate_and_quote_table_name(podcast_name)
        row = conn.execute(f"SELECT MAX(author_raw) FROM {safe_table_name}").fetchone()
    except (sqlite3.Error, ValueError):
        return podcast_name
    return (row[0] if row else None) or podcast_name


def run_due_checks() -> CheckRunResult:
    """
    Re-sync every podcast whose auto-refresh interval has elapsed, and
    submit a background download job for the ones with auto_download on.

    Meant to be called periodically (see podvault.scheduler), but is a
    plain function — call it directly for a one-off check outside the
    scheduler loop.
    """
    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return CheckRunResult()

    with get_db_connection(db_path) as conn:
        due = get_due_podcasts(conn)

    outcomes = []
    for entry in due:
        name = entry['name']

        # A plain jobs-table row, not a JobRunner submission: this already runs
        # on the scheduler thread, so it just needs an audit-trail entry for
        # the Activity page (Fase 15) to show scheduled syncs alongside
        # user-triggered jobs.
        job_id = str(uuid.uuid4())
        with get_db_connection(db_path) as conn:
            jobs_db.create_job(conn, job_id, 'scheduled_sync', podcast_name=name)
            jobs_db.mark_job_running(conn, job_id)

        with get_db_connection(db_path) as conn:
            result = process_single_feed(conn, entry['rss_feed_url'])
            mark_checked(conn, name)

        if not result.success:
            logger.warning(f"Scheduled check failed for '{name}': {result.error}")
            with get_db_connection(db_path) as conn:
                jobs_db.mark_job_failed(conn, job_id, result.error or 'unknown error')
            outcomes.append(CheckOutcome(podcast_name=name, success=False, error=result.error))
            continue

        auto_download_triggered = False
        if entry['auto_download']:
            get_job_runner().submit(
                "download_all", download_all, name,
                podcast_name=name, require_confirmation=False,
            )
            auto_download_triggered = True

        message = f"{result.episodes_found} episode(s) found"
        if auto_download_triggered:
            message += " · auto-download queued"
        with get_db_connection(db_path) as conn:
            jobs_db.mark_job_success(conn, job_id, message=message)

        outcomes.append(CheckOutcome(
            podcast_name=name, success=True, episodes_found=result.episodes_found,
            auto_download_triggered=auto_download_triggered,
        ))

    return CheckRunResult(checked=outcomes)
