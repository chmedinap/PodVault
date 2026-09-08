"""
CRUD helpers for the `jobs` table.

Backs the in-process background job runner (see `podvault.jobs`) so
long-running operations (feed processing, downloads) can be started without
blocking the caller and polled for status/progress afterwards.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from podvault.database.schema import create_jobs_table_if_not_exists

JOB_STATUS_PENDING = 'pending'
JOB_STATUS_RUNNING = 'running'
JOB_STATUS_SUCCESS = 'success'
JOB_STATUS_FAILED = 'failed'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: tuple) -> Dict[str, Any]:
    keys = [
        'id', 'job_type', 'podcast_name', 'status', 'progress', 'total',
        'message', 'error', 'created_at', 'updated_at',
    ]
    return dict(zip(keys, row))


def create_job(conn: sqlite3.Connection, job_id: str, job_type: str, podcast_name: Optional[str] = None) -> None:
    """Insert a new job row with status='pending'."""
    create_jobs_table_if_not_exists(conn)
    now = _now()
    conn.execute(
        """
        INSERT INTO jobs (id, job_type, podcast_name, status, progress, total, message, error, created_at, updated_at)
        VALUES (?, ?, ?, ?, 0, 0, NULL, NULL, ?, ?)
        """,
        (job_id, job_type, podcast_name, JOB_STATUS_PENDING, now, now),
    )
    conn.commit()


def mark_job_running(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
        (JOB_STATUS_RUNNING, _now(), job_id),
    )
    conn.commit()


def update_job_progress(conn: sqlite3.Connection, job_id: str, progress: int, total: int, message: Optional[str] = None) -> None:
    conn.execute(
        "UPDATE jobs SET progress = ?, total = ?, message = ?, updated_at = ? WHERE id = ?",
        (progress, total, message, _now(), job_id),
    )
    conn.commit()


def mark_job_success(conn: sqlite3.Connection, job_id: str, message: Optional[str] = None) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, message = ?, updated_at = ? WHERE id = ?",
        (JOB_STATUS_SUCCESS, message, _now(), job_id),
    )
    conn.commit()


def mark_job_failed(conn: sqlite3.Connection, job_id: str, error: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
        (JOB_STATUS_FAILED, error, _now(), job_id),
    )
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: str) -> Optional[Dict[str, Any]]:
    create_jobs_table_if_not_exists(conn)
    cursor = conn.execute(
        "SELECT id, job_type, podcast_name, status, progress, total, message, error, created_at, updated_at "
        "FROM jobs WHERE id = ?",
        (job_id,),
    )
    row = cursor.fetchone()
    return _row_to_dict(row) if row else None


def list_jobs(conn: sqlite3.Connection, limit: int = 50) -> List[Dict[str, Any]]:
    create_jobs_table_if_not_exists(conn)
    cursor = conn.execute(
        "SELECT id, job_type, podcast_name, status, progress, total, message, error, created_at, updated_at "
        "FROM jobs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return [_row_to_dict(row) for row in cursor.fetchall()]


def list_jobs_by_status(conn: sqlite3.Connection, statuses: List[str], limit: int = 50) -> List[Dict[str, Any]]:
    """Jobs in any of `statuses`, most recently created first."""
    create_jobs_table_if_not_exists(conn)
    placeholders = ', '.join('?' for _ in statuses)
    cursor = conn.execute(
        f"SELECT id, job_type, podcast_name, status, progress, total, message, error, created_at, updated_at "
        f"FROM jobs WHERE status IN ({placeholders}) ORDER BY created_at DESC LIMIT ?",
        (*statuses, limit),
    )
    return [_row_to_dict(row) for row in cursor.fetchall()]


def get_latest_job_by_type(conn: sqlite3.Connection, job_type: str) -> Optional[Dict[str, Any]]:
    """Most recently created job of `job_type` (e.g. the Storage page's cached 'id3_check' result)."""
    create_jobs_table_if_not_exists(conn)
    cursor = conn.execute(
        "SELECT id, job_type, podcast_name, status, progress, total, message, error, created_at, updated_at "
        "FROM jobs WHERE job_type = ? ORDER BY created_at DESC LIMIT 1",
        (job_type,),
    )
    row = cursor.fetchone()
    return _row_to_dict(row) if row else None


def get_queue_totals(conn: sqlite3.Connection) -> Dict[str, int]:
    """
    Rough (estimated) counts for the Library page's "In queue" stat card:
    episodes still pending across active jobs (total - progress, summed —
    pending jobs that haven't started iterating yet contribute 0 until
    their first progress tick) and how many jobs are currently running.
    """
    create_jobs_table_if_not_exists(conn)
    cursor = conn.execute("""
        SELECT COALESCE(SUM(total - progress), 0), COALESCE(SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END), 0)
        FROM jobs WHERE status IN ('pending', 'running')
    """)
    pending_episodes, running_jobs = cursor.fetchone()
    return {'pending_episodes': pending_episodes or 0, 'running_jobs': running_jobs or 0}
