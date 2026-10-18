"""
Download utility functions.
"""

import sqlite3
import logging
import traceback
import pandas as pd
from pathlib import Path
from typing import List, Dict, Optional, Union
from podvault.database.queries import table_exists, validate_and_quote_table_name
from podvault import config
from podvault.config import EPISODE_STATUS_NOT_DOWNLOADED

# Set up logging
from podvault.logging_config import setup_logging
logger = setup_logging(__name__)


def sanitize_filename(filename: str) -> str:
    """
    Remove or replace invalid characters from filename.
    
    Parameters:
    filename: Original filename
    
    Returns:
    Sanitized filename safe for filesystem use
    """
    # Remove invalid characters for filenames
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    # Remove leading/trailing spaces and dots
    filename = filename.strip(' .')
    # Limit filename length
    if len(filename) > config.MAX_FILENAME_LENGTH:
        filename = filename[:config.MAX_FILENAME_LENGTH]
    return filename


# Audio formats an episode can be saved as (Fase 23). Podcasts aren't all MP3:
# AAC in an MP4 container (.m4a/.m4b) is common, and Ogg/Opus/FLAC show up too.
# Maps extension -> MIME type served to the browser player.
AUDIO_MIME_TYPES = {
    '.mp3': 'audio/mpeg',
    '.m4a': 'audio/mp4',
    '.m4b': 'audio/mp4',
    '.mp4': 'audio/mp4',
    '.aac': 'audio/aac',
    '.ogg': 'audio/ogg',
    '.oga': 'audio/ogg',
    '.opus': 'audio/ogg',
    '.flac': 'audio/flac',
    '.wav': 'audio/wav',
}
AUDIO_EXTENSIONS = frozenset(AUDIO_MIME_TYPES)
DEFAULT_AUDIO_EXTENSION = '.mp3'


def audio_extension_from_url(url: Optional[str]) -> str:
    """
    The file extension to save an episode under, taken from its download
    URL's path (query string ignored, so tracking redirects like
    `.../ep.m4a?src=feed` still work). Falls back to `.mp3` when the URL has
    no recognised audio extension — the historical behaviour, and still the
    most common podcast format.
    """
    if url:
        try:
            from urllib.parse import urlparse
            suffix = Path(urlparse(url).path).suffix.lower()
        except ValueError:
            suffix = ''
        if suffix in AUDIO_EXTENSIONS:
            return suffix
    return DEFAULT_AUDIO_EXTENSION


def find_existing_audio_file(episode_folder: Path, base_filename: str) -> Optional[Path]:
    """
    An already-downloaded audio file for this episode in `episode_folder`,
    whatever its audio extension — so a re-run recognises an existing
    `.m4a` (or a pre-Fase-23 `.mp3` saved for what is really an m4a feed)
    instead of downloading it again under a second name. Cover images and
    metadata XML share the base filename, so only audio extensions count.
    """
    for ext in AUDIO_MIME_TYPES:
        candidate = episode_folder / f"{base_filename}{ext}"
        if candidate.exists():
            return candidate
    return None


def episode_audio_path(episode_folder: Path, base_filename: str, episode_url: Optional[str]) -> Path:
    """Where an episode's audio lives: the existing file if any, else a new path with the URL's extension."""
    return (find_existing_audio_file(episode_folder, base_filename)
            or episode_folder / f"{base_filename}{audio_extension_from_url(episode_url)}")


def get_effective_title(title: str, custom_title: Optional[str]) -> str:
    """
    Return the title to actually use for display/file naming/ID3 tags.

    `custom_title` (a user override, stored per-episode) wins when set to a
    non-blank value; otherwise falls back to the feed's original `title`.
    """
    if custom_title and custom_title.strip():
        return custom_title.strip()
    return title


class _SafeFormatDict(dict):
    """Makes str.format_map raise KeyError for unknown keys instead of the
    default behavior of leaving `{missing}` untouched in the output."""

    def __missing__(self, key):
        raise KeyError(key)


# Variables available to folder/filename naming templates (see episode_template_variables()).
TEMPLATE_VARIABLES = (
    'podcast_name', 'episode_title', 'episode_number', 'season_number',
    'year', 'month', 'day', 'date', 'episode_label',
)


def render_naming_template(template: str, variables: Dict[str, object]) -> str:
    """
    Render a folder/filename template with `str.format` against `variables`.

    Raises ValueError — not KeyError/IndexError — for any problem with the
    template itself (unknown variable, bad format spec, unbalanced braces),
    so callers can treat "the template is broken" as a single case.
    """
    try:
        return template.format_map(_SafeFormatDict(variables))
    except KeyError as e:
        raise ValueError(f"Unknown template variable: {{{e.args[0]}}}")
    except (IndexError, ValueError) as e:
        raise ValueError(f"Invalid template '{template}': {e}")


def episode_template_variables(
    podcast_name: str,
    episode_date: pd.Timestamp,
    episode_number: Optional[Union[str, int]],
    season_number: Optional[Union[str, int]],
    safe_episode_title: str,
) -> Dict[str, object]:
    """
    Build the variable dict available to folder/filename naming templates.

    `episode_label` is a convenience variable: the episode number
    zero-padded to 3 digits, or the published date if the feed doesn't
    number episodes — the fallback this app always used before naming
    became configurable, kept as an easy default for templates that don't
    want to special-case episodes with no number.
    """
    numeric_label = None
    if episode_number not in (None, ''):
        try:
            numeric_label = f"{int(episode_number):03d}"
        except (TypeError, ValueError):
            numeric_label = None
    date_str = episode_date.strftime('%Y-%m-%d')

    return {
        'podcast_name': podcast_name,
        'episode_title': safe_episode_title,
        'episode_number': '' if episode_number in (None, '') else episode_number,
        'season_number': '' if season_number in (None, '') else season_number,
        'year': episode_date.year,
        'month': f"{episode_date.month:02d}",
        'day': f"{episode_date.day:02d}",
        'date': date_str,
        'episode_label': numeric_label or date_str,
    }


def build_episode_base_filename(
    podcast_name: str,
    episode_date: pd.Timestamp,
    episode_number: Optional[Union[str, int]],
    season_number: Optional[Union[str, int]],
    safe_episode_title: str,
    filename_template: Optional[str] = None,
) -> str:
    """
    Base filename (without extension) shared by an episode's mp3, cover
    image, and metadata XML, rendered from `filename_template` (defaults
    to config.DEFAULT_FILENAME_TEMPLATE — see episode_template_variables()
    for available variables).
    """
    filename_template = filename_template or config.DEFAULT_FILENAME_TEMPLATE
    variables = episode_template_variables(podcast_name, episode_date, episode_number, season_number, safe_episode_title)
    rendered = render_naming_template(filename_template, variables)
    return sanitize_filename(rendered)


def build_episode_folder_path(
    downloads_folder: Union[str, Path],
    podcast_name: str,
    episode_date: pd.Timestamp,
    episode_number: Optional[Union[str, int]],
    season_number: Optional[Union[str, int]],
    safe_episode_title: str,
    folder_template: Optional[str] = None,
) -> Path:
    """
    Build the per-episode download folder path, rendered from
    `folder_template` (defaults to config.DEFAULT_FOLDER_TEMPLATE — see
    episode_template_variables() for available variables). `/` in the
    rendered result is treated as a folder separator; every segment is
    sanitized independently and bare `.`/`..` segments are dropped, so a
    template can't escape `downloads_folder`.

    Parameters:
    downloads_folder: Base downloads folder
    podcast_name: Normalized podcast name (table name)
    episode_date: Parsed publication date of the episode
    episode_number: Episode number from the feed, if any (may be None, '', or non-numeric)
    season_number: Season number from the feed, if any (may be None, '', or non-numeric)
    safe_episode_title: Episode title already sanitized via sanitize_filename()
    folder_template: Template string; defaults to config.DEFAULT_FOLDER_TEMPLATE

    Returns:
    Path to the episode's folder (not created on disk by this function)
    """
    folder_template = folder_template or config.DEFAULT_FOLDER_TEMPLATE
    variables = episode_template_variables(podcast_name, episode_date, episode_number, season_number, safe_episode_title)
    rendered = render_naming_template(folder_template, variables)

    segments = [
        sanitize_filename(segment)
        for segment in rendered.split('/')
        if segment.strip() not in ('', '.', '..')
    ]
    if not segments:
        segments = ['untitled']

    return Path(downloads_folder).joinpath(*segments)


def show_podcast_summary(conn: sqlite3.Connection, podcast_names: List[str]) -> Dict[str, int]:
    """
    Show a summary of episodes to download for multiple podcasts.
    
    Parameters:
    conn: SQLite connection object
    podcast_names: List of podcast names (normalized table names)
    
    Returns:
    Dictionary with podcast names as keys and episode counts as values
    """
    summary = {}
    print(f"\n{'='*70}")
    print(f"📊 PODCAST DOWNLOAD SUMMARY")
    print(f"{'='*70}")
    
    total_episodes = 0
    for podcast_name in podcast_names:
        if not table_exists(conn, podcast_name):
            print(f"  ⚠️  '{podcast_name}': Table does not exist")
            summary[podcast_name] = 0
            continue
        
        count = 0
        try:
            cursor = conn.cursor()
            safe_table_name = validate_and_quote_table_name(podcast_name)
            cursor.execute(f"""
                SELECT COUNT(*)
                FROM {safe_table_name}
                WHERE status IN (?, ?)
            """, (EPISODE_STATUS_NOT_DOWNLOADED, config.EPISODE_STATUS_MISSING))
            count = cursor.fetchone()[0]
            summary[podcast_name] = count
            total_episodes += count
        except sqlite3.Error as e:
            logger.error(f"Database error getting episode count for '{podcast_name}': {e}")
            logger.debug(traceback.format_exc())
            print(f"  ⚠️  '{podcast_name}': Error reading database")
            summary[podcast_name] = 0
            count = 0
        
        if count > 0:
            print(f"  📻 '{podcast_name}': {count} episode(s) to download")
        else:
            print(f"  ✓ '{podcast_name}': All episodes already downloaded")
    
    print(f"{'='*70}")
    print(f"📊 TOTAL: {total_episodes} episode(s) across {len(podcast_names)} podcast(s)")
    print(f"{'='*70}\n")
    
    return summary


def parse_episode_date(conn: sqlite3.Connection, podcast_name: str, episode_id: str, published_date: str = None) -> pd.Timestamp:
    """
    Parse episode publication date from various formats.
    
    Parameters:
    conn: SQLite connection object
    podcast_name: Name of the podcast (table name)
    episode_id: ID of the episode
    published_date: Published date string (optional)
    
    Returns:
    pd.Timestamp object representing the episode date, or current timestamp if parsing fails
    """
    try:
        # Try to parse the published date
        if published_date:
            # Try parsing as ISO format string first
            try:
                episode_date = pd.to_datetime(published_date)
                return episode_date
            except (ValueError, TypeError) as e:
                # If that fails, try parsing the published_parsed if available
                logger.debug(f"Failed to parse published_date '{published_date}': {e}. Trying published_parsed.")
                try:
                    cursor = conn.cursor()
                    safe_table_name = validate_and_quote_table_name(podcast_name)
                    cursor.execute(f"SELECT published_parsed FROM {safe_table_name} WHERE id = ?", (episode_id,))
                    parsed_result = cursor.fetchone()
                    if parsed_result and parsed_result[0]:
                        try:
                            episode_date = pd.to_datetime(parsed_result[0])
                            return episode_date
                        except (ValueError, TypeError) as e2:
                            logger.debug(f"Failed to parse published_parsed '{parsed_result[0]}': {e2}")
                except sqlite3.Error as db_error:
                    logger.warning(f"Database error reading published_parsed for episode {episode_id}: {db_error}")
                    logger.debug(traceback.format_exc())
    except sqlite3.Error as e:
        logger.error(f"Database error in parse_episode_date for episode {episode_id}: {e}")
        logger.debug(traceback.format_exc())
    except Exception as e:
        logger.warning(f"Unexpected error in parse_episode_date for episode {episode_id}: {e}")
        logger.debug(traceback.format_exc())
    
    # Fallback to current timestamp
    return pd.Timestamp.now()


