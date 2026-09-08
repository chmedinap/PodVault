"""
Service for the web player (ROADMAP.md Fase 9): resolves the audio file and
cover image for an already-downloaded episode.

Only episodes with status='downloaded' are playable — there's no support
for streaming directly from the original feed URL for episodes that
haven't been downloaded yet; that's out of scope here.
"""

import os
from pathlib import Path

from podvault import config
from podvault.database.connection import get_db_connection
from podvault.database.queries import table_exists, validate_and_quote_table_name
from podvault.database.schema import add_download_columns_to_table
from podvault.download.id3_tags import find_cover_image_path
from podvault.download.utils import get_effective_title
from podvault.services.results import PlaybackInfo
from podvault.validation import validate_podcast_name


def get_playback_info(podcast_name: str, episode_id: str) -> PlaybackInfo:
    """Resolve the audio file (and cover image, if any) for a downloaded episode."""
    is_valid, error = validate_podcast_name(podcast_name)
    if not is_valid:
        return PlaybackInfo(error=f"Invalid podcast name '{podcast_name}': {error}")

    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return PlaybackInfo(error="Database not found. Process feeds first.")

    with get_db_connection(db_path) as conn:
        if not table_exists(conn, podcast_name):
            return PlaybackInfo(error=f"Podcast '{podcast_name}' not found in database.")

        add_download_columns_to_table(conn, podcast_name)
        safe_table_name = validate_and_quote_table_name(podcast_name)
        cursor = conn.execute(
            f"SELECT title, custom_title, status, Saved_Path, summary FROM {safe_table_name} WHERE id = ?",
            (episode_id,),
        )
        row = cursor.fetchone()

    if not row:
        return PlaybackInfo(error=f"Episode '{episode_id}' not found.")

    title, custom_title, status, saved_path, summary = row

    if status != 'downloaded' or not saved_path:
        return PlaybackInfo(error="This episode hasn't been downloaded yet.")

    audio_path = Path(saved_path)
    if not audio_path.exists():
        return PlaybackInfo(error="Audio file not found on disk — try 'verify' to check for missing files.")

    cover_path = find_cover_image_path(audio_path, audio_path.parent)

    return PlaybackInfo(
        episode_id=episode_id,
        podcast_name=podcast_name,
        title=get_effective_title(title or "", custom_title),
        summary=summary or '',
        audio_path=str(audio_path),
        cover_path=str(cover_path) if cover_path else None,
    )
