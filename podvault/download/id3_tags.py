"""
Tag management for podcast episode audio files.

MP3 files get ID3 tags; MP4-container files (.m4a/.m4b/.mp4) get the
equivalent MP4 atoms (Fase 23). Other formats (.ogg, .opus, .flac, ...)
are downloaded and archived like any other episode, but tag writing is
skipped for them. The function names keep their historical "id3"/"mp3"
wording since they're part of the package's public API.
"""

import logging
import traceback
from pathlib import Path
from typing import Optional
from podvault import config
from podvault.database.queries import validate_and_quote_table_name
from podvault.download.utils import get_effective_title

# Set up logging
from podvault.logging_config import setup_logging
logger = setup_logging(__name__)

try:
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TRCK, TPOS, TCON, COMM, APIC
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen import File
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False
    logger.warning("mutagen library not available. ID3 tag updates will be skipped.")
    logger.warning("Install with: pip install mutagen")

ID3_EXTENSIONS = frozenset({'.mp3'})
MP4_EXTENSIONS = frozenset({'.m4a', '.m4b', '.mp4'})


def tags_supported(file_path: Path) -> bool:
    """Whether this app knows how to write tags for `file_path`'s format."""
    suffix = Path(file_path).suffix.lower()
    return suffix in ID3_EXTENSIONS or suffix in MP4_EXTENSIONS


def _image_mime_type(image_path: Path) -> str:
    ext = image_path.suffix.lower()
    return {'.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'}.get(ext, 'image/jpeg')


def _update_mp4_tags(
    file_path: Path,
    title: str,
    artist: str,
    album: str,
    year: Optional[int],
    track: Optional[int],
    disc_number: Optional[int],
    comment: Optional[str],
    cover_image_path: Optional[Path],
    genre: str,
) -> bool:
    """MP4-atom equivalent of the ID3 frames `update_mp3_id3_tags` writes."""
    audio_file = MP4(str(file_path))
    if audio_file.tags is None:
        audio_file.add_tags()
    tags = audio_file.tags

    if title:
        tags['\xa9nam'] = [title]
    if artist:
        tags['\xa9ART'] = [artist]
    if album:
        tags['\xa9alb'] = [album]
    if year:
        tags['\xa9day'] = [str(year)]
    if track is not None:
        tags['trkn'] = [(track, 0)]
    if disc_number is not None:
        tags['disk'] = [(disc_number, 0)]
    if genre:
        tags['\xa9gen'] = [genre]
    if comment:
        tags['\xa9cmt'] = [comment]

    if cover_image_path and cover_image_path.exists():
        image_format = MP4Cover.FORMAT_PNG if _image_mime_type(cover_image_path) == 'image/png' else MP4Cover.FORMAT_JPEG
        with open(cover_image_path, 'rb') as img_file:
            tags['covr'] = [MP4Cover(img_file.read(), imageformat=image_format)]

    audio_file.save()
    logger.info(f"Successfully updated MP4 tags for {file_path}")
    return True


def update_mp3_id3_tags(
    file_path: Path,
    title: str,
    artist: str,
    album: str,
    year: Optional[int] = None,
    track: Optional[int] = None,
    disc_number: Optional[int] = None,
    comment: Optional[str] = None,
    cover_image_path: Optional[Path] = None,
    genre: str = "Podcast"
) -> bool:
    """
    Update tags for an episode audio file: ID3 for MP3, MP4 atoms for
    .m4a/.m4b/.mp4. Returns False (without touching the file) for formats
    tags aren't written for.
    
    Parameters:
    file_path: Path to the audio file
    title: Episode title
    artist: Podcast name (author_raw)
    album: Podcast name (author_raw)
    year: Year of release
    track: Episode number
    disc_number: Season number
    comment: Episode summary
    cover_image_path: Path to cover image (podcast or episode image)
    genre: Genre (default: "Podcast")
    
    Returns:
    bool: True if successful, False otherwise
    """
    if not MUTAGEN_AVAILABLE:
        logger.warning("mutagen not available, skipping ID3 tag update")
        return False
    
    if not file_path.exists():
        logger.error(f"File does not exist: {file_path}")
        return False

    suffix = file_path.suffix.lower()
    if suffix in MP4_EXTENSIONS:
        try:
            return _update_mp4_tags(file_path, title, artist, album, year, track,
                                    disc_number, comment, cover_image_path, genre)
        except Exception as e:
            logger.error(f"Failed to update MP4 tags for {file_path}: {e}")
            logger.debug(traceback.format_exc())
            return False
    if suffix not in ID3_EXTENSIONS:
        logger.info(f"Tag writing isn't supported for '{suffix}' files; skipping {file_path}")
        return False
    
    try:
        # Load the MP3 file
        audio_file = File(str(file_path))
        
        # If the file doesn't have ID3 tags, add them
        if audio_file.tags is None:
            audio_file.add_tags()
        
        # Update title
        if title:
            audio_file.tags['TIT2'] = TIT2(encoding=3, text=title)
        
        # Update artist (author_raw)
        if artist:
            audio_file.tags['TPE1'] = TPE1(encoding=3, text=artist)
        
        # Update album (author_raw)
        if album:
            audio_file.tags['TALB'] = TALB(encoding=3, text=album)
        
        # Update year
        if year:
            audio_file.tags['TDRC'] = TDRC(encoding=3, text=str(year))
        
        # Update track (episode number)
        if track is not None:
            audio_file.tags['TRCK'] = TRCK(encoding=3, text=str(track))
        
        # Update disc number (season number)
        if disc_number is not None:
            audio_file.tags['TPOS'] = TPOS(encoding=3, text=str(disc_number))
        
        # Update genre
        if genre:
            audio_file.tags['TCON'] = TCON(encoding=3, text=genre)
        
        # Update comment (summary)
        if comment:
            # Remove existing COMM frames and add new one
            audio_file.tags.delall('COMM')
            audio_file.tags['COMM'] = COMM(encoding=3, lang='eng', desc='', text=comment)
        
        # Update cover image
        if cover_image_path and cover_image_path.exists():
            try:
                # Remove existing APIC frames
                audio_file.tags.delall('APIC')
                
                # Read the image file
                with open(cover_image_path, 'rb') as img_file:
                    image_data = img_file.read()
                
                # Determine MIME type from file extension
                mime_type = _image_mime_type(cover_image_path)
                
                # Add cover image
                audio_file.tags['APIC'] = APIC(
                    encoding=3,
                    mime=mime_type,
                    type=3,  # Cover (front)
                    desc='Cover',
                    data=image_data
                )
                logger.info(f"Successfully added cover image to ID3 tags: {cover_image_path} (MIME: {mime_type})")
            except Exception as e:
                logger.warning(f"Failed to add cover image to ID3 tags: {e}")
                logger.debug(traceback.format_exc())
        else:
            if cover_image_path:
                logger.warning(f"Cover image path specified but file does not exist: {cover_image_path}")
            else:
                logger.debug("No cover image found for this episode")
        
        # Save the changes
        audio_file.save()
        logger.info(f"Successfully updated ID3 tags for {file_path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to update ID3 tags for {file_path}: {e}")
        logger.debug(traceback.format_exc())
        return False


def id3_tags_complete(file_path: Path) -> Optional[bool]:
    """
    Whether `file_path` has the three tags most visibly missing in a podcast
    player: title (TIT2), artist (TPE1), and cover art (APIC) — or their MP4
    equivalents (\xa9nam, \xa9ART, covr) for .m4a/.m4b/.mp4 files. Shared by the
    episode detail page (checks one file at render time) and the Storage
    page's cached "ID3 tags complete" background job (Fase 16b) so both
    agree on what "complete" means.

    Returns None if the file can't be read at all (missing, corrupt, or
    mutagen unavailable) — distinct from False, which means the file *was*
    read and is missing one of the three tags. Also None for formats this
    app doesn't write tags for, so they aren't reported as "incomplete".
    """
    if not MUTAGEN_AVAILABLE or not file_path.exists() or not tags_supported(file_path):
        return None
    try:
        if file_path.suffix.lower() in MP4_EXTENSIONS:
            tags = MP4(str(file_path)).tags
            if tags is None:
                return False
            return bool(tags.get('\xa9nam')) and bool(tags.get('\xa9ART')) and bool(tags.get('covr'))
        audio_file = File(str(file_path))
        if audio_file is None or audio_file.tags is None:
            return False
        tags = audio_file.tags
        has_title = bool(tags.get('TIT2'))
        has_artist = bool(tags.get('TPE1'))
        has_cover = any(key.startswith('APIC') for key in tags.keys())
        return has_title and has_artist and has_cover
    except Exception as e:
        logger.debug(f"Could not read ID3 tags from {file_path}: {e}")
        return None


def find_cover_image_path(file_path: Path, episode_folder: Path) -> Optional[Path]:
    """
    Find the cover image for an episode: prefer an episode-specific image
    (same base name as the audio file, any common extension) in `episode_folder`,
    falling back to the podcast-level image three levels up
    (episode_folder -> month -> year -> podcast).

    Shared by ID3 tag writing and the web player's cover art route, so both
    agree on which image an episode's cover is.

    Parameters:
    file_path: Path to the episode's audio file (used to derive the image base name)
    episode_folder: Path to the episode's folder

    Returns:
    Path to the cover image, or None if none was found
    """
    try:
        mp3_file = file_path.stem
        for ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
            image_path = episode_folder / f"{mp3_file}{ext}"
            if image_path.exists():
                return image_path
    except Exception as e:
        logger.debug(f"Error searching for episode image: {e}")

    try:
        podcast_base_folder = episode_folder.parent.parent.parent
        podcast_image_files = list(podcast_base_folder.glob("podcast_image.*"))
        if podcast_image_files:
            return podcast_image_files[0]
    except Exception as e:
        logger.debug(f"Error searching for podcast image: {e}")

    return None


def update_episode_id3_tags_from_db(
    conn,
    podcast_name: str,
    episode_id: str,
    file_path: Path,
    episode_folder: Path
) -> bool:
    """
    Update tags for an episode audio file using data from the database.
    
    Parameters:
    conn: SQLite connection
    podcast_name: Name of the podcast (table name)
    episode_id: Episode ID
    file_path: Path to the audio file
    episode_folder: Path to the episode folder (for finding images)
    
    Returns:
    bool: True if successful, False otherwise
    """
    try:
        cursor = conn.cursor()
        
        # Get episode data from database
        safe_table_name = validate_and_quote_table_name(podcast_name)
        cursor.execute(f"""
            SELECT
                title,
                custom_title,
                author_raw,
                episode_number,
                season_number,
                summary,
                published,
                episode_image_url
            FROM {safe_table_name}
            WHERE id = ?
        """, (episode_id,))

        result = cursor.fetchone()
        if not result:
            logger.warning(f"Episode {episode_id} not found in database")
            return False

        title, custom_title, author_raw, episode_number, season_number, summary, published, episode_image_url = result
        effective_title = get_effective_title(title or "", custom_title)
        
        # Extract year from published date
        year = None
        if published:
            try:
                from datetime import datetime
                # Try to parse the published date
                if isinstance(published, str):
                    # Try common date formats
                    for fmt in ['%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%a, %d %b %Y %H:%M:%S %z']:
                        try:
                            dt = datetime.strptime(published, fmt)
                            year = dt.year
                            break
                        except ValueError:
                            continue
                    # If no format worked, try to extract year from string
                    if year is None:
                        import re
                        year_match = re.search(r'\d{4}', published)
                        if year_match:
                            year = int(year_match.group())
                elif hasattr(published, 'year'):
                    year = published.year
            except Exception as e:
                logger.debug(f"Could not extract year from published date: {e}")
        
        # Find cover image (prefer episode image, fallback to podcast image)
        cover_image_path = find_cover_image_path(file_path, episode_folder)

        # Convert episode_number and season_number to integers if they're strings
        track = None
        if episode_number is not None:
            try:
                track = int(float(str(episode_number)))
            except (ValueError, TypeError):
                pass
        
        disc_number = None
        if season_number is not None:
            try:
                disc_number = int(float(str(season_number)))
            except (ValueError, TypeError):
                pass
        
        # Update ID3 tags
        return update_mp3_id3_tags(
            file_path=file_path,
            title=effective_title,
            artist=author_raw or "",
            album=author_raw or "",
            year=year,
            track=track,
            disc_number=disc_number,
            comment=summary or "",
            cover_image_path=cover_image_path,
            genre="Podcast"
        )
        
    except Exception as e:
        logger.error(f"Failed to update ID3 tags from database: {e}")
        logger.debug(traceback.format_exc())
        return False

