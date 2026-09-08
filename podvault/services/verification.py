"""
Service for checking that downloaded episodes still exist on disk.

Files can disappear for reasons the database never learns about (moved,
deleted, external cleanup, a drive unmounted). This reconciles rows with
status='downloaded' against the filesystem and marks the ones whose file
is gone as 'missing', so `download_all`/`download_last` pick them up for
re-download the next time they run instead of treating them as done
forever.
"""

import os
from typing import List, Optional

from podvault import config
from podvault.database.connection import get_db_connection
from podvault.database.queries import find_missing_downloaded_episodes, mark_episodes_missing, table_exists
from podvault.download.metadata import update_summary
from podvault.services.podcasts import list_podcast_table_names
from podvault.services.results import MissingEpisode, PodcastVerifyResult, VerifyResult
from podvault.validation import validate_podcast_name


def verify_files(podcast_name: Optional[str] = None, mark_missing: bool = True) -> VerifyResult:
    """
    Check downloaded episodes against disk for the given podcast, or all
    podcasts if `podcast_name` is None.

    When `mark_missing` is True (default), episodes whose file is gone are
    updated to status='missing' and the podcast's summary stats are
    refreshed to match.
    """
    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return VerifyResult(error="Database not found. Process feeds first.")

    with get_db_connection(db_path) as conn:
        if podcast_name:
            is_valid, error = validate_podcast_name(podcast_name)
            if not is_valid:
                return VerifyResult(error=f"Invalid podcast name '{podcast_name}': {error}")
            if not table_exists(conn, podcast_name):
                return VerifyResult(error=f"Podcast '{podcast_name}' not found in database.")
            names = [podcast_name]
        else:
            names = list_podcast_table_names(conn)
            if not names:
                return VerifyResult(error="No podcasts found in database.")

        podcasts: List[PodcastVerifyResult] = []
        for name in names:
            missing = find_missing_downloaded_episodes(conn, name)
            missing_episodes = [
                MissingEpisode(episode_id=m['id'], title=m['title'], saved_path=m['saved_path'])
                for m in missing
            ]

            if missing_episodes and mark_missing:
                mark_episodes_missing(conn, name, [m.episode_id for m in missing_episodes])
                update_summary(conn, name)

            podcasts.append(PodcastVerifyResult(podcast_name=name, missing_episodes=missing_episodes))

    return VerifyResult(podcasts=podcasts)
