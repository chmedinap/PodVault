"""
Service for listing episodes and customizing an episode's display name.

Feeds don't always give episodes a name worth keeping (bare numbering,
ads baked into the title, etc). `rename_episode` lets you override just
the display/file name via `custom_title`, independent of what the feed
says: it updates the ID3 TIT2 tag and, if the episode is already
downloaded, renames its folder and files to match — so the on-disk
layout reflects the name you chose, not just future downloads.
"""

import os
import sqlite3
from pathlib import Path
from typing import Optional

from podvault import config
from podvault.database.connection import get_db_connection
from podvault.database.queries import table_exists, validate_and_quote_table_name
from podvault.database.schema import add_download_columns_to_table
from podvault.database.settings import get_app_settings
from podvault.download.id3_tags import id3_tags_complete, update_episode_id3_tags_from_db
from podvault.download.utils import (
    build_episode_base_filename,
    build_episode_folder_path,
    get_effective_title,
    parse_episode_date,
    sanitize_filename,
)
from podvault.logging_config import setup_logging
from podvault.services.results import EpisodeDetail, EpisodeInfo, EpisodeListResult, FileOnDisk, RenameResult
from podvault.validation import validate_podcast_name

logger = setup_logging(__name__)

try:
    from mutagen.mp3 import MP3
except ImportError:
    MP3 = None


def list_episodes(podcast_name: str) -> EpisodeListResult:
    """List every episode of a podcast with its id, titles, status, and saved path."""
    is_valid, error = validate_podcast_name(podcast_name)
    if not is_valid:
        return EpisodeListResult(podcast_name=podcast_name, error=f"Invalid podcast name '{podcast_name}': {error}")

    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return EpisodeListResult(podcast_name=podcast_name, error="Database not found. Process feeds first.")

    with get_db_connection(db_path) as conn:
        if not table_exists(conn, podcast_name):
            return EpisodeListResult(podcast_name=podcast_name, error=f"Podcast '{podcast_name}' not found in database.")

        add_download_columns_to_table(conn, podcast_name)
        safe_table_name = validate_and_quote_table_name(podcast_name)
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT id, title, custom_title, status, Saved_Path, episode_number, published, Size, in_feed
            FROM {safe_table_name}
            ORDER BY published DESC
        """)
        episodes = [
            EpisodeInfo(
                episode_id=row[0], title=row[1], custom_title=row[2], status=row[3], saved_path=row[4],
                episode_number=row[5], published=row[6], size_bytes=row[7], in_feed=bool(row[8]),
            )
            for row in cursor.fetchall()
        ]

    return EpisodeListResult(podcast_name=podcast_name, episodes=episodes)


def get_episode(podcast_name: str, episode_id: str) -> EpisodeDetail:
    """
    Full detail for one episode: metadata, files on disk, and (for
    already-downloaded episodes only) duration and ID3-completeness, read
    on demand for this single file — not cached, not run across the library.
    """
    is_valid, error = validate_podcast_name(podcast_name)
    if not is_valid:
        return EpisodeDetail(podcast_name, episode_id, error=f"Invalid podcast name '{podcast_name}': {error}")

    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return EpisodeDetail(podcast_name, episode_id, error="Database not found. Process feeds first.")

    with get_db_connection(db_path) as conn:
        if not table_exists(conn, podcast_name):
            return EpisodeDetail(podcast_name, episode_id, error=f"Podcast '{podcast_name}' not found in database.")

        add_download_columns_to_table(conn, podcast_name)
        safe_table_name = validate_and_quote_table_name(podcast_name)
        cursor = conn.execute(f"""
            SELECT title, custom_title, status, Saved_Path, published, episode_number, season_number, Size, summary,
                   in_feed, Checksum, checksum_verified_at, checksum_status
            FROM {safe_table_name}
            WHERE id = ?
        """, (episode_id,))
        row = cursor.fetchone()

    if not row:
        return EpisodeDetail(podcast_name, episode_id, error=f"Episode '{episode_id}' not found.")

    (title, custom_title, status, saved_path, published, episode_number, season_number, size_bytes, summary,
     in_feed, checksum, checksum_verified_at, checksum_status) = row

    detail = EpisodeDetail(
        podcast_name=podcast_name, episode_id=episode_id, title=title or '',
        display_title=get_effective_title(title or '', custom_title),
        custom_title=custom_title, status=status, saved_path=saved_path, published=published,
        episode_number=episode_number, season_number=season_number, size_bytes=size_bytes, summary=summary or '',
        in_feed=bool(in_feed), checksum=checksum, checksum_verified_at=checksum_verified_at,
        checksum_status=checksum_status,
    )

    if saved_path and Path(saved_path).exists():
        audio_path = Path(saved_path)

        if MP3 is not None:
            try:
                detail.duration_seconds = MP3(str(audio_path)).info.length
            except Exception as e:
                logger.debug(f"Could not read duration for {audio_path}: {e}")

        detail.id3_complete = id3_tags_complete(audio_path)

        try:
            detail.files = sorted(
                (
                    FileOnDisk(name=item.name, size_bytes=item.stat().st_size)
                    for item in audio_path.parent.iterdir() if item.is_file()
                ),
                key=lambda f: f.name,
            )
        except OSError as e:
            logger.debug(f"Could not list files on disk for {audio_path.parent}: {e}")

    return detail


def rename_episode(
    podcast_name: str,
    episode_id: str,
    custom_title: Optional[str],
    downloads_folder: Optional[str] = None,
) -> RenameResult:
    """
    Set (or clear) the display name for one episode.

    Pass `custom_title=None` or `''` to clear the override and revert to
    the title from the feed. Updates the ID3 tag and, if the episode is
    already downloaded, renames its folder and files to match.
    """
    is_valid, error = validate_podcast_name(podcast_name)
    if not is_valid:
        return RenameResult(podcast_name, episode_id, success=False, error=f"Invalid podcast name '{podcast_name}': {error}")

    downloads_folder = downloads_folder or config.DOWNLOADS_FOLDER
    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return RenameResult(podcast_name, episode_id, success=False, error="Database not found. Process feeds first.")

    normalized_custom_title = custom_title.strip() if custom_title else None
    if normalized_custom_title == '':
        normalized_custom_title = None

    with get_db_connection(db_path) as conn:
        if not table_exists(conn, podcast_name):
            return RenameResult(podcast_name, episode_id, success=False, error=f"Podcast '{podcast_name}' not found in database.")

        add_download_columns_to_table(conn, podcast_name)
        naming_settings = get_app_settings(conn)
        safe_table_name = validate_and_quote_table_name(podcast_name)
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT title, custom_title, published, episode_number, season_number, Saved_Path
            FROM {safe_table_name}
            WHERE id = ?
        """, (episode_id,))
        row = cursor.fetchone()
        if not row:
            return RenameResult(podcast_name, episode_id, success=False, error=f"Episode '{episode_id}' not found.")

        title, old_custom_title, published, episode_number, season_number, saved_path = row

        old_effective_title = get_effective_title(title or "", old_custom_title)
        new_effective_title = get_effective_title(title or "", normalized_custom_title)

        if normalized_custom_title == old_custom_title:
            return RenameResult(
                podcast_name, episode_id, success=True,
                old_title=old_effective_title, new_title=new_effective_title,
                detail='no change',
            )

        cursor.execute(f"UPDATE {safe_table_name} SET custom_title = ? WHERE id = ?", (normalized_custom_title, episode_id))
        conn.commit()

        result = RenameResult(
            podcast_name, episode_id, success=True,
            old_title=old_effective_title, new_title=new_effective_title,
        )

        if not saved_path:
            result.detail = 'episode not downloaded yet; the new name will be used on the next download'
            return result

        old_file = Path(saved_path)
        old_folder = old_file.parent
        if not old_folder.exists():
            result.detail = "file not found on disk (episode marked missing?); only the stored name was updated"
            return result

        episode_date = parse_episode_date(conn, podcast_name, episode_id, published)
        old_safe_title = sanitize_filename(old_effective_title)
        new_safe_title = sanitize_filename(new_effective_title)
        try:
            new_folder = build_episode_folder_path(
                downloads_folder, podcast_name, episode_date, episode_number, season_number,
                new_safe_title, folder_template=naming_settings['folder_template'],
            )
        except ValueError as e:
            result.success = False
            result.error = f"Invalid folder template: {e}"
            return result

        if new_folder.resolve() == old_folder.resolve():
            result.detail = 'sanitized filename unchanged; nothing to rename on disk'
            return result

        if new_folder.exists():
            result.success = False
            result.error = f"Destination folder already exists: {new_folder}"
            return result

        try:
            old_base_filename = build_episode_base_filename(
                podcast_name, episode_date, episode_number, season_number,
                old_safe_title, filename_template=naming_settings['filename_template'],
            )
            new_base_filename = build_episode_base_filename(
                podcast_name, episode_date, episode_number, season_number,
                new_safe_title, filename_template=naming_settings['filename_template'],
            )

            new_folder.parent.mkdir(parents=True, exist_ok=True)
            old_folder.rename(new_folder)

            # Rename every file that shares the old base filename (mp3, cover image, metadata xml)
            for item in new_folder.iterdir():
                if item.stem == old_base_filename:
                    item.rename(new_folder / f"{new_base_filename}{item.suffix}")

            new_file_path = new_folder / f"{new_base_filename}.mp3"
            cursor.execute(
                f"UPDATE {safe_table_name} SET Saved_Path = ?, File_name = ? WHERE id = ?",
                (str(new_file_path), new_file_path.name, episode_id),
            )
            conn.commit()

            try:
                update_episode_id3_tags_from_db(conn, podcast_name, episode_id, new_file_path, new_folder)
            except Exception as e:
                logger.warning(f"Renamed files but failed to refresh ID3 tags for episode {episode_id}: {e}")

            result.renamed_on_disk = True
            result.old_folder = str(old_folder)
            result.new_folder = str(new_folder)
        except (OSError, sqlite3.Error) as e:
            logger.error(f"Failed to rename episode {episode_id} folder: {e}")
            result.success = False
            result.error = str(e)

    return result
