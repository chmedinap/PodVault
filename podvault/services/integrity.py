"""
Service for the Storage page's "Integrity" health check: re-hashes every
downloaded episode and compares it against the SHA-256 baseline stamped at
download time (`hashing.compute_checksum`, wired into `download/downloader.py`),
to catch silent corruption (bit rot, a truncated write, a bad external edit)
that `verify_files()` can't see — that check only confirms a file still
*exists*, not that its bytes are still the ones PodVault actually downloaded.

Like the ID3-completeness check (services/storage.py), this runs as a
background job through the existing `JobRunner` rather than on every Storage
page load — hashing every downloaded file is far too expensive to redo per
request for a library of any real size.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from podvault.database.connection import get_db_connection
from podvault.database.queries import validate_and_quote_table_name
from podvault.database.schema import add_download_columns_to_table
from podvault.hashing import compute_checksum
from podvault.services.podcasts import list_podcast_table_names
from podvault.services.storage import list_downloaded_episodes

ProgressCallback = Callable[[int, int, Optional[str]], None]


def run_integrity_check(progress_callback: Optional[ProgressCallback] = None, job_control=None) -> str:
    """
    JobRunner target (job_type='integrity_check'): re-hash every downloaded
    episode still present on disk.

    - No stored checksum yet (downloaded before this feature existed): hash
      it now and store the result as the new baseline — counted as "hashed",
      not "verified", since there was nothing to compare against.
    - Stored checksum matches: refresh `checksum_verified_at`, status stays 'ok'.
    - Stored checksum differs: mark `checksum_status='corrupt'` and leave the
      original checksum and `checksum_verified_at` alone (so the record keeps
      saying when it was last known-good, instead of being silently updated
      as if this bad read were now the baseline).
    """
    targets = list_downloaded_episodes()
    total = len(targets)
    verified = 0
    hashed = 0
    corrupt = 0

    with get_db_connection() as conn:
        for index, (podcast_name, episode_id, saved_path) in enumerate(targets, 1):
            if job_control is not None and job_control.is_cancelled():
                break
            if progress_callback:
                progress_callback(index, total, f"checking {Path(saved_path).name}")

            safe_table_name = validate_and_quote_table_name(podcast_name)
            row = conn.execute(
                f"SELECT Checksum FROM {safe_table_name} WHERE id = ?", (episode_id,)
            ).fetchone()
            stored_checksum = row[0] if row else None

            try:
                current_checksum = compute_checksum(saved_path)
            except OSError:
                continue  # file vanished between listing and hashing; verify_files() covers missing files

            now = datetime.now(timezone.utc).isoformat()
            if not stored_checksum:
                conn.execute(
                    f"UPDATE {safe_table_name} SET Checksum = ?, checksum_verified_at = ?, checksum_status = 'ok' WHERE id = ?",
                    (current_checksum, now, episode_id),
                )
                hashed += 1
            elif current_checksum == stored_checksum:
                conn.execute(
                    f"UPDATE {safe_table_name} SET checksum_verified_at = ?, checksum_status = 'ok' WHERE id = ?",
                    (now, episode_id),
                )
                verified += 1
            else:
                conn.execute(
                    f"UPDATE {safe_table_name} SET checksum_status = 'corrupt' WHERE id = ?",
                    (episode_id,),
                )
                corrupt += 1
            conn.commit()

    return f"{verified} verified, {hashed} newly hashed, {corrupt} corrupted (of {total})"


def list_corrupt_episodes() -> List[Dict[str, Any]]:
    """
    Every episode currently flagged `checksum_status='corrupt'`, across every
    podcast — lets the Storage page name the specific files a failed
    integrity check found (Fase 22) instead of showing just a count.
    """
    results: List[Dict[str, Any]] = []
    with get_db_connection() as conn:
        for podcast_name in list_podcast_table_names(conn):
            add_download_columns_to_table(conn, podcast_name)
            safe_table_name = validate_and_quote_table_name(podcast_name)
            rows = conn.execute(f"""
                SELECT id, title, custom_title, Saved_Path, checksum_verified_at
                FROM {safe_table_name} WHERE checksum_status = 'corrupt'
            """).fetchall()
            for episode_id, title, custom_title, saved_path, verified_at in rows:
                results.append({
                    'podcast_name': podcast_name,
                    'episode_id': episode_id,
                    'title': custom_title or title,
                    'saved_path': saved_path,
                    'verified_at': verified_at,
                })
    return results
