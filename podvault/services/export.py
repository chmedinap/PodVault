"""
Service for exporting a portable inventory of the archive: a JSON manifest
per podcast (or the whole library) capturing every episode's metadata,
on-disk path, and integrity/feed-tracking state — independent of PodVault's
own sqlite schema.

Deliberately manifest-only in v1, not a zip of the audio files themselves:
the files already live in an organized folder layout on disk (see the naming
templates in services/settings.py), and duplicating potentially many GB of
audio into an archive on every export isn't something to do by default. The
manifest is what makes that existing folder tree self-describing and
portable on its own — a listing you can keep independent of the database,
or hand off to another tool, without re-copying the files it describes.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from podvault import __version__
from podvault.database.connection import get_db_connection
from podvault.database.queries import validate_and_quote_table_name, table_exists
from podvault.database.schema import add_download_columns_to_table
from podvault.dates import newest_first
from podvault.services.podcasts import get_podcast_info, list_podcast_table_names
from podvault.validation import validate_podcast_name


def _episode_rows(conn, podcast_name: str) -> List[Dict[str, Any]]:
    add_download_columns_to_table(conn, podcast_name)
    safe_table_name = validate_and_quote_table_name(podcast_name)
    cursor = conn.execute(f"""
        SELECT id, title, custom_title, status, published, episode_number, season_number,
               Saved_Path, Size, Checksum, checksum_verified_at, checksum_status, in_feed
        FROM {safe_table_name}
    """)
    rows = newest_first(cursor.fetchall(), lambda row: row[4])
    return [
        {
            'id': row[0],
            'title': row[1],
            'custom_title': row[2],
            'status': row[3],
            'published': row[4],
            'episode_number': row[5],
            'season_number': row[6],
            'saved_path': row[7],
            'size_bytes': row[8],
            'checksum': row[9],
            'checksum_verified_at': row[10],
            'checksum_status': row[11],
            'in_feed': bool(row[12]),
        }
        for row in rows
    ]


def export_podcast_manifest(podcast_name: str) -> Optional[Dict[str, Any]]:
    """A JSON-serializable manifest of one podcast's full episode inventory (every episode ever seen, including ones removed from the live feed), or None if the podcast doesn't exist."""
    is_valid, _error = validate_podcast_name(podcast_name)
    if not is_valid:
        return None

    with get_db_connection() as conn:
        if not table_exists(conn, podcast_name):
            return None
        episodes = _episode_rows(conn, podcast_name)

    podcast_info = get_podcast_info(podcast_name)
    return {
        'podvault_version': __version__,
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'podcast_name': podcast_name,
        'display_name': podcast_info.display_name if podcast_info else podcast_name,
        'episode_count': len(episodes),
        'episodes': episodes,
    }


def export_library_manifest() -> Dict[str, Any]:
    """A JSON-serializable manifest of every podcast's full episode inventory."""
    with get_db_connection() as conn:
        podcast_names = list_podcast_table_names(conn)

    podcasts = [export_podcast_manifest(name) for name in podcast_names]
    podcasts = [p for p in podcasts if p is not None]
    return {
        'podvault_version': __version__,
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'podcast_count': len(podcasts),
        'podcasts': podcasts,
    }
