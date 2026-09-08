"""
Service layer for downloading episodes.

Extracted from cli.py so the same logic can be triggered from a web
handler or background job. `download_all` exposes `require_confirmation`
because `download_all_episodes` normally blocks on an interactive `input()`
prompt, which would hang forever if called from a non-interactive context
(a background job has no stdin to read from).
"""

import os
from typing import Callable, List, Optional

from podvault import config
from podvault.database.connection import get_db_connection
from podvault.database.queries import table_exists
from podvault.download.downloader import download_all_episodes, download_last_episode, download_one_episode
from podvault.services.episodes import list_episodes
from podvault.services.results import DownloadResult, EpisodeInfo
from podvault.validation import validate_podcast_name

ProgressCallback = Callable[[int, int, str], None]


def download_all(
    podcast_name: str,
    require_confirmation: bool = True,
    progress_callback: Optional[ProgressCallback] = None,
    job_control=None,
) -> DownloadResult:
    """Download every not-yet-downloaded episode of `podcast_name`.

    Set `require_confirmation=False` when calling from a background job or
    web request — there's no terminal to answer the "yes/no" prompt.

    `job_control` (Fase 22), if given, lets a separate request cancel this
    job, pause it, or skip a specific episode — all checked between
    episodes, never mid-download (see `JobControl`'s docstring).
    """
    is_valid, error = validate_podcast_name(podcast_name)
    if not is_valid:
        return DownloadResult(success=False, error=f"Invalid podcast name '{podcast_name}': {error}")

    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return DownloadResult(success=False, error="Database not found. Process feeds first.")

    with get_db_connection(db_path) as conn:
        if not table_exists(conn, podcast_name):
            return DownloadResult(success=False, error=f"Podcast '{podcast_name}' not found in database.")

        successful, total = download_all_episodes(
            conn,
            podcast_name,
            require_confirmation=require_confirmation,
            progress_callback=progress_callback,
            job_control=job_control,
        )
        return DownloadResult(success=successful > 0, downloaded=successful, total=total)


def download_last(
    podcast_name: str, progress_callback: Optional[ProgressCallback] = None, job_control=None,
) -> DownloadResult:
    """Download only the most recent not-yet-downloaded episode of `podcast_name`."""
    is_valid, error = validate_podcast_name(podcast_name)
    if not is_valid:
        return DownloadResult(success=False, error=f"Invalid podcast name '{podcast_name}': {error}")

    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return DownloadResult(success=False, error="Database not found. Process feeds first.")

    if job_control is not None and job_control.is_cancelled():
        return DownloadResult(success=False, error="Cancelled before it started.")

    with get_db_connection(db_path) as conn:
        if not table_exists(conn, podcast_name):
            return DownloadResult(success=False, error=f"Podcast '{podcast_name}' not found in database.")

        if progress_callback:
            progress_callback(0, 1, f"Downloading last episode for '{podcast_name}'")
        success = download_last_episode(conn, podcast_name)
        if progress_callback:
            progress_callback(1, 1, "Done" if success else "Failed")

        return DownloadResult(success=success, downloaded=1 if success else 0, total=1)


def download_episode(
    podcast_name: str, episode_id: str, progress_callback: Optional[ProgressCallback] = None, job_control=None,
) -> DownloadResult:
    """
    Download (or re-download) exactly one episode — the true single-episode
    fetch Fase 22 adds, as opposed to `download_all`, which processes every
    pending/missing episode of a podcast.
    """
    is_valid, error = validate_podcast_name(podcast_name)
    if not is_valid:
        return DownloadResult(success=False, error=f"Invalid podcast name '{podcast_name}': {error}")

    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return DownloadResult(success=False, error="Database not found. Process feeds first.")

    if job_control is not None and job_control.is_cancelled():
        return DownloadResult(success=False, error="Cancelled before it started.")

    with get_db_connection(db_path) as conn:
        if not table_exists(conn, podcast_name):
            return DownloadResult(success=False, error=f"Podcast '{podcast_name}' not found in database.")

        if progress_callback:
            progress_callback(0, 1, f"Downloading episode {episode_id} of '{podcast_name}'")
        success = download_one_episode(conn, podcast_name, episode_id)
        if progress_callback:
            progress_callback(1, 1, "Done" if success else "Failed")

        return DownloadResult(success=success, downloaded=1 if success else 0, total=1)


def preview_pending(podcast_name: str) -> List[EpisodeInfo]:
    """
    Preview, for the Queue page, which episodes `download_all` would pick up
    right now — same rows, same `published DESC` order as
    `download_all_episodes`'s own query (status not-downloaded or missing),
    just filtered client-side from `list_episodes()` instead of a second SQL
    query. Display-only: may go slightly stale if state changes between this
    call and an actual download run, which is fine for a queue preview.
    """
    result = list_episodes(podcast_name)
    if result.error:
        return []
    return [e for e in result.episodes if e.status in (config.EPISODE_STATUS_NOT_DOWNLOADED, config.EPISODE_STATUS_MISSING)]
