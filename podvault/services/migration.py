"""
Migrate already-downloaded episodes to the per-episode folder layout
(downloads/podcast/year/month/label/) introduced for Fase 1 of ROADMAP.md.

This moves real files on disk, so `migrate_layout()` defaults to a dry run
(`apply=False`) that only reports what it *would* do. Pass `apply=True`
to actually move folders and update `Saved_Path` in the database — do
that from the CLI after reviewing the dry-run report, not from a web
request, since a mistake here moves your whole downloaded library.
"""

import os
import shutil
import sqlite3
from pathlib import Path
from typing import List, Optional

from podvault import config
from podvault.database.connection import get_db_connection
from podvault.database.queries import validate_and_quote_table_name
from podvault.database.settings import get_app_settings
from podvault.download.utils import build_episode_folder_path, get_effective_title, parse_episode_date, sanitize_filename
from podvault.logging_config import setup_logging
from podvault.services.podcasts import list_podcast_table_names
from podvault.services.results import MigrationMove, MigrationResult

logger = setup_logging(__name__)


def _migrate_podcast(
    conn: sqlite3.Connection,
    podcast_name: str,
    downloads_folder: str,
    folder_template: str,
    apply: bool,
) -> List[MigrationMove]:
    safe_table_name = validate_and_quote_table_name(podcast_name)
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT id, title, custom_title, published, episode_number, season_number, Saved_Path
        FROM {safe_table_name}
        WHERE Saved_Path IS NOT NULL AND Saved_Path != ''
    """)
    rows = cursor.fetchall()

    moves = []
    for episode_id, title, custom_title, published, episode_number, season_number, saved_path in rows:
        old_file = Path(saved_path)
        old_folder = old_file.parent

        episode_date = parse_episode_date(conn, podcast_name, episode_id, published)
        safe_title = sanitize_filename(get_effective_title(title, custom_title))
        try:
            new_folder = build_episode_folder_path(
                downloads_folder, podcast_name, episode_date, episode_number, season_number,
                safe_title, folder_template=folder_template,
            )
        except ValueError as e:
            moves.append(MigrationMove(
                podcast_name, episode_id, title, str(old_folder), '',
                status='error', detail=f"invalid folder template: {e}",
            ))
            continue

        if old_folder.resolve() == new_folder.resolve():
            moves.append(MigrationMove(
                podcast_name, episode_id, title, str(old_folder), str(new_folder),
                status='skipped', detail='already in the new layout',
            ))
            continue

        if not old_folder.exists():
            moves.append(MigrationMove(
                podcast_name, episode_id, title, str(old_folder), str(new_folder),
                status='skipped', detail='source folder missing on disk',
            ))
            continue

        if new_folder.exists():
            moves.append(MigrationMove(
                podcast_name, episode_id, title, str(old_folder), str(new_folder),
                status='error', detail='destination folder already exists',
            ))
            continue

        if not apply:
            moves.append(MigrationMove(
                podcast_name, episode_id, title, str(old_folder), str(new_folder),
                status='planned',
            ))
            continue

        try:
            new_folder.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_folder), str(new_folder))
            new_saved_path = new_folder / old_file.name
            cursor.execute(
                f"UPDATE {safe_table_name} SET Saved_Path = ? WHERE id = ?",
                (str(new_saved_path), episode_id),
            )
            conn.commit()
            moves.append(MigrationMove(
                podcast_name, episode_id, title, str(old_folder), str(new_folder),
                status='moved',
            ))
        except (OSError, sqlite3.Error) as e:
            logger.error(f"Failed to migrate '{old_folder}' -> '{new_folder}': {e}")
            moves.append(MigrationMove(
                podcast_name, episode_id, title, str(old_folder), str(new_folder),
                status='error', detail=str(e),
            ))

    return moves


def migrate_layout(downloads_folder: Optional[str] = None, apply: bool = False) -> MigrationResult:
    """
    Migrate every downloaded episode to the year/month/label folder layout.

    With `apply=False` (default) nothing on disk or in the database changes;
    the returned `MigrationResult.moves` describes what each episode's move
    would look like. Re-run with `apply=True` once the dry-run report looks
    right.
    """
    downloads_folder = downloads_folder or config.DOWNLOADS_FOLDER
    db_path = config.DB_PATH

    if not os.path.exists(db_path):
        return MigrationResult(apply=apply, error="Database not found. Process feeds first.")

    with get_db_connection(db_path) as conn:
        try:
            podcast_names = list_podcast_table_names(conn)
        except sqlite3.Error as e:
            return MigrationResult(apply=apply, error=f"Database error: {e}")

        folder_template = get_app_settings(conn)['folder_template']

        moves = []
        for podcast_name in podcast_names:
            moves.extend(_migrate_podcast(conn, podcast_name, downloads_folder, folder_template, apply))

    return MigrationResult(apply=apply, moves=moves)
