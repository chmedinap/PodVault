"""
Service for the Storage page (Fase 16b): disk usage breakdown and library
health checks (missing files, files outside the naming template, ID3 tag
completeness).

The "downloaded but missing" and "outside naming template" checks reuse
`verify_files()`/`migrate_layout()` unchanged, both already safe to call
read-only (`mark_missing=False`, `apply=False`). ID3 completeness is the one
check expensive enough (opens every downloaded MP3) to need caching: it runs
as a background job (job_type='id3_check'/'id3_retag') through the existing
JobRunner, and the Storage page just reads back the most recent finished
job of that type rather than re-scanning on every page load.
"""

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from podvault import config
from podvault.database.connection import get_db_connection
from podvault.database.queries import validate_and_quote_table_name
from podvault.database.schema import add_download_columns_to_table
from podvault.download.id3_tags import id3_tags_complete, update_episode_id3_tags_from_db
from podvault.download.utils import AUDIO_EXTENSIONS
from podvault.services.podcasts import list_podcast_table_names
from podvault.services.results import DiskUsage

_AUDIO_EXTENSIONS = AUDIO_EXTENSIONS
_ART_METADATA_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.xml'}

ProgressCallback = Callable[[int, int, Optional[str]], None]


def get_disk_usage(downloads_folder: Optional[str] = None) -> DiskUsage:
    """
    A single walk over `downloads_folder`, bucketed by extension, plus
    `shutil.disk_usage()` for the volume's free space. Not cached in v1 — if
    this turns out slow on a very large or network-mounted library, add a
    cache with a manual refresh then; don't guess at that cost up front.
    """
    downloads_folder = downloads_folder or config.DOWNLOADS_FOLDER
    usage = DiskUsage()

    if not os.path.isdir(downloads_folder):
        usage.error = "Downloads folder not found."
        return usage

    for root, _dirs, files in os.walk(downloads_folder):
        for name in files:
            path = Path(root) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            ext = path.suffix.lower()
            if ext in _AUDIO_EXTENSIONS:
                usage.audio_bytes += size
            elif ext in _ART_METADATA_EXTENSIONS:
                usage.art_metadata_bytes += size
            else:
                usage.orphaned_bytes += size

    try:
        disk = shutil.disk_usage(downloads_folder)
        usage.total_bytes, usage.used_bytes, usage.free_bytes = disk.total, disk.used, disk.free
    except OSError:
        pass

    return usage


def _iter_downloaded_episodes(conn: sqlite3.Connection, podcast_name: str) -> List[Tuple[str, str, str]]:
    """Every downloaded episode of one podcast whose file still exists on disk, as (podcast_name, episode_id, saved_path)."""
    add_download_columns_to_table(conn, podcast_name)
    safe_table_name = validate_and_quote_table_name(podcast_name)
    cursor = conn.execute(
        f"SELECT id, Saved_Path FROM {safe_table_name} WHERE status = ? AND Saved_Path IS NOT NULL",
        (config.EPISODE_STATUS_DOWNLOADED,),
    )
    return [
        (podcast_name, episode_id, saved_path)
        for episode_id, saved_path in cursor.fetchall()
        if saved_path and Path(saved_path).exists()
    ]


def list_downloaded_episodes() -> List[Tuple[str, str, str]]:
    """Every downloaded episode across every podcast, as (podcast_name, episode_id, saved_path).
    Shared by both the ID3-completeness job here and the integrity-check job (services/integrity.py)."""
    with get_db_connection() as conn:
        podcast_names = list_podcast_table_names(conn)
        episodes = []
        for name in podcast_names:
            episodes.extend(_iter_downloaded_episodes(conn, name))
    return episodes


def list_local_only_episodes() -> List[Tuple[str, str, str]]:
    """
    Every downloaded episode currently flagged `in_feed=0` (removed from its
    podcast's feed, kept because PodVault never deletes on re-sync) — powers
    the Storage page's "kept after leaving their feed" review list (Fase 22),
    as (podcast_name, episode_id, title).
    """
    results: List[Tuple[str, str, str]] = []
    with get_db_connection() as conn:
        for podcast_name in list_podcast_table_names(conn):
            add_download_columns_to_table(conn, podcast_name)
            safe_table_name = validate_and_quote_table_name(podcast_name)
            rows = conn.execute(
                f"SELECT id, title, custom_title FROM {safe_table_name} WHERE in_feed = 0 AND status = ?",
                (config.EPISODE_STATUS_DOWNLOADED,),
            ).fetchall()
            for episode_id, title, custom_title in rows:
                results.append((podcast_name, episode_id, custom_title or title))
    return results


def run_id3_check(progress_callback: Optional[ProgressCallback] = None, job_control=None) -> str:
    """JobRunner target (job_type='id3_check'): scan every downloaded episode's ID3 tags; the Storage page reads the result back from this job's `message`."""
    targets = list_downloaded_episodes()
    total = len(targets)
    complete = 0
    for index, (_podcast_name, _episode_id, saved_path) in enumerate(targets, 1):
        if job_control is not None and job_control.is_cancelled():
            break
        if progress_callback:
            progress_callback(index, total, f"checking {Path(saved_path).name}")
        if id3_tags_complete(Path(saved_path)):
            complete += 1
    return f"{complete} of {total} complete"


def run_id3_retag(progress_callback: Optional[ProgressCallback] = None, job_control=None) -> str:
    """JobRunner target (job_type='id3_retag'): re-write ID3 tags for every downloaded episode currently missing one of the three tracked tags."""
    incomplete = [
        entry for entry in list_downloaded_episodes()
        if id3_tags_complete(Path(entry[2])) is False
    ]
    total = len(incomplete)
    retagged = 0
    with get_db_connection() as conn:
        for index, (podcast_name, episode_id, saved_path) in enumerate(incomplete, 1):
            if job_control is not None and job_control.is_cancelled():
                break
            if progress_callback:
                progress_callback(index, total, f"retagging {Path(saved_path).name}")
            path = Path(saved_path)
            if update_episode_id3_tags_from_db(conn, podcast_name, episode_id, path, path.parent):
                retagged += 1
    return f"retagged {retagged} of {total}"
