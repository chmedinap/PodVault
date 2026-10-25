"""
Main download functions for podcast episodes.
"""

import sqlite3
import time
import logging
import traceback
import requests
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Tuple
from tqdm import tqdm
from podvault import config
from podvault.config import EPISODE_STATUS_NOT_DOWNLOADED, EPISODE_STATUS_DOWNLOADED, EPISODE_STATUS_MISSING
from podvault.dates import newest_first
from podvault.database.queries import table_exists, validate_and_quote_table_name
from podvault.database.schema import add_download_columns_to_table, update_download_info
from podvault.database.settings import get_app_settings
from podvault.database.transactions import (
    transaction,
    savepoint,
    safe_commit,
    safe_rollback,
    validate_transaction_state,
    TransactionError
)
from podvault.download.utils import (
    build_episode_base_filename,
    build_episode_folder_path,
    episode_audio_path,
    get_effective_title,
    sanitize_filename,
    parse_episode_date,
)
from podvault.download.metadata import update_summary
from podvault.data.collection import get_episode_xml_from_rss
from podvault.hashing import compute_checksum
from podvault.download.id3_tags import update_episode_id3_tags_from_db
from podvault.download.id3_tags import update_episode_id3_tags_from_db

# Set up logging
from podvault.logging_config import setup_logging
logger = setup_logging(__name__)


def _commit_batch_updates(
    conn: sqlite3.Connection,
    safe_table_name: str,
    pending_status_updates: list,
    pending_download_info_updates: list,
    batch_count: int
) -> None:
    """
    Commit batch updates using executemany() for better performance.
    
    Uses transaction context manager for safe transaction handling.
    
    Parameters:
    conn: SQLite connection object
    safe_table_name: Validated and quoted table name
    pending_status_updates: List of (status, episode_id) tuples
    pending_download_info_updates: List of (file_path_str, file_size, file_name, checksum, verified_at, checksum_status, episode_id)
        tuples — checksum/verified_at/checksum_status are None for episodes whose file was already on disk (COALESCE
        leaves whatever checksum they already had untouched instead of overwriting it with nothing)
    batch_count: Number of episodes in this batch (for logging)
    """
    try:
        with transaction(conn, autocommit=True) as cursor:
            # Batch update status using executemany()
            if pending_status_updates:
                cursor.executemany(
                    f"UPDATE {safe_table_name} SET status = ? WHERE id = ?",
                    pending_status_updates
                )
                logger.debug(f"Batch updated {len(pending_status_updates)} episode statuses using executemany()")

            # Batch update download info using executemany()
            if pending_download_info_updates:
                cursor.executemany(
                    f"""
                    UPDATE {safe_table_name}
                    SET Saved_Path = ?,
                        Size = ?,
                        File_name = ?,
                        Checksum = COALESCE(?, Checksum),
                        checksum_verified_at = COALESCE(?, checksum_verified_at),
                        checksum_status = COALESCE(?, checksum_status)
                    WHERE id = ?
                    """,
                    pending_download_info_updates
                )
                logger.debug(f"Batch updated {len(pending_download_info_updates)} download info records using executemany()")
            
            # Transaction automatically committed on exit
            print(f"  💾 Committed batch of {batch_count} updates to database (using executemany())")
        
    except TransactionError as e:
        logger.error(f"Transaction error in batch commit: {e}")
        logger.debug(traceback.format_exc())
        raise
    except sqlite3.Error as e:
        logger.error(f"Database error in batch commit: {e}")
        logger.debug(traceback.format_exc())
        raise


def _download_image(image_url: str, image_path: Path) -> bool:
    """
    Download an image from a URL and save it to a file path.
    
    Parameters:
    image_url: URL of the image to download
    image_path: Path where the image should be saved
    
    Returns:
    True if download successful, False otherwise
    """
    try:
        logger.debug(f"Downloading image from {image_url} to {image_path}")
        response = requests.get(image_url, stream=True, timeout=config.DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        
        # Determine file extension from URL or Content-Type
        content_type = response.headers.get('Content-Type', '')
        if 'image/jpeg' in content_type or 'image/jpg' in content_type:
            ext = '.jpg'
        elif 'image/png' in content_type:
            ext = '.png'
        elif 'image/gif' in content_type:
            ext = '.gif'
        elif 'image/webp' in content_type:
            ext = '.webp'
        else:
            # Try to get extension from URL
            from urllib.parse import urlparse
            parsed_url = urlparse(image_url)
            path = parsed_url.path.lower()
            if path.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                ext = path[path.rfind('.'):]
            else:
                ext = '.jpg'  # Default to jpg
        
        # Update path with correct extension
        if not str(image_path).lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
            image_path = image_path.with_suffix(ext)
        
        # Save the image
        with open(image_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=config.CHUNK_SIZE):
                f.write(chunk)
        
        logger.info(f"Successfully downloaded image to {image_path}")
        return True
        
    except requests.RequestException as e:
        logger.warning(f"Failed to download image from {image_url}: {e}")
        return False
    except OSError as e:
        logger.error(f"File system error saving image to {image_path}: {e}")
        logger.debug(traceback.format_exc())
        return False
    except Exception as e:
        logger.error(f"Unexpected error downloading image: {e}")
        logger.debug(traceback.format_exc())
        return False


def _download_with_retry(episode_url: str, file_path: Path) -> None:
    """
    Download a file with retry logic for transient failures.
    
    Parameters:
    episode_url: URL of the episode to download
    file_path: Path where the file should be saved
    
    Raises:
    requests.RequestException: For network-related errors
    OSError: For file system errors
    """
    max_retries = config.MAX_RETRIES
    retry_delay = config.RETRY_DELAY_BASE
    
    for attempt in range(max_retries):
        try:
            logger.debug(f"Download attempt {attempt + 1}/{max_retries} for {episode_url}")
            response = requests.get(episode_url, stream=True, timeout=config.DOWNLOAD_TIMEOUT)
            response.raise_for_status()
            
            # Get file size from Content-Length header if available
            total_size = int(response.headers.get('Content-Length', 0))
            
            # Save the file with progress bar
            with open(file_path, 'wb') as f:
                if total_size > 0:
                    # Use tqdm for file download progress if size is known
                    with tqdm(total=total_size, unit='B', unit_scale=True, unit_divisor=1024, 
                             desc="  Downloading", leave=False, ncols=80) as file_pbar:
                        for chunk in response.iter_content(chunk_size=config.CHUNK_SIZE):
                            if chunk:
                                f.write(chunk)
                                file_pbar.update(len(chunk))
                else:
                    # No size info, just download without progress bar
                    for chunk in response.iter_content(chunk_size=config.CHUNK_SIZE):
                        if chunk:
                            f.write(chunk)
            
            logger.info(f"Successfully downloaded {episode_url} to {file_path}")
            return
            
        except requests.Timeout as e:
            if attempt < max_retries - 1:
                logger.warning(
                    f"Timeout downloading {episode_url} (attempt {attempt + 1}/{max_retries}). "
                    f"Retrying in {retry_delay} seconds..."
                )
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, config.RETRY_DELAY_MAX)
            else:
                logger.error(f"Timeout downloading {episode_url} after {max_retries} attempts")
                raise
                
        except requests.ConnectionError as e:
            if attempt < max_retries - 1:
                logger.warning(
                    f"Connection error downloading {episode_url} (attempt {attempt + 1}/{max_retries}). "
                    f"Retrying in {retry_delay} seconds..."
                )
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, config.RETRY_DELAY_MAX)
            else:
                logger.error(f"Connection error downloading {episode_url} after {max_retries} attempts")
                raise
                
        except requests.HTTPError as e:
            # HTTP errors (4xx, 5xx) are usually not transient, don't retry
            logger.error(f"HTTP error downloading {episode_url}: {e}")
            raise
            
        except OSError as e:
            # File system errors are usually not transient, don't retry
            logger.error(f"File system error saving {file_path}: {e}")
            logger.debug(traceback.format_exc())
            raise
            
        except requests.RequestException as e:
            # Other request exceptions - retry if it might be transient
            if attempt < max_retries - 1:
                logger.warning(
                    f"Request error downloading {episode_url} (attempt {attempt + 1}/{max_retries}): {e}. "
                    f"Retrying in {retry_delay} seconds..."
                )
                logger.debug(traceback.format_exc())
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, config.RETRY_DELAY_MAX)
            else:
                logger.error(f"Request error downloading {episode_url} after {max_retries} attempts: {e}")
                logger.debug(traceback.format_exc())
                raise


def download_all_episodes(
    conn: sqlite3.Connection,
    podcast_name: str,
    downloads_folder: str = None,
    delay_seconds: int = None,
    require_confirmation: bool = True,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    job_control=None,
) -> Tuple[int, int]:
    """
    Download all episodes of a podcast that haven't been downloaded yet.
    Organizes files in: downloads/podcast_title/year/month/episode_number - episode_title/Episode_Title - podcast_name.<ext>
    (see build_episode_folder_path() for the exact folder-naming rules)
    Adds a delay between downloads to avoid overwhelming the server.

    Parameters:
    conn: SQLite connection object
    podcast_name: Name of the podcast (normalized table name)
    downloads_folder: Folder to save downloads (default: config.DOWNLOADS_FOLDER)
    delay_seconds: Delay in seconds between downloads (default: config.DEFAULT_DELAY_SECONDS)
    require_confirmation: If True (default), ask for interactive confirmation via input()
        before downloading. Set to False when calling from a non-interactive context
        (background job, web request) where there is no stdin to read from.
    progress_callback: Optional callable invoked as (current_index, total_episodes, episode_title)
        before each episode is processed, so a caller can report progress elsewhere.
    job_control: Optional `podvault.jobs.JobControl` (Fase 22) checked between
        episodes — never mid-download — so a separate request can cancel this
        run, pause it, or skip one specific episode. A cancel `break`s the loop
        (whatever's downloaded so far stays downloaded); a skip `continue`s
        past just that one episode, leaving it exactly as it was.

    Returns:
    Tuple of (successful_downloads, total_episodes)
    """
    if downloads_folder is None:
        downloads_folder = config.DOWNLOADS_FOLDER
    if delay_seconds is None:
        delay_seconds = config.DEFAULT_DELAY_SECONDS
    
    # Create downloads folder if it doesn't exist
    try:
        Path(downloads_folder).mkdir(exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to create downloads folder '{downloads_folder}': {e}")
        logger.debug(traceback.format_exc())
        print(f"Error: Failed to create downloads folder: {e}")
        return (0, 0)
    
    # Check if table exists
    if not table_exists(conn, podcast_name):
        print(f"Table '{podcast_name}' does not exist in database.")
        return (0, 0)
    
    # Ensure the download columns exist in the table
    add_download_columns_to_table(conn, podcast_name)

    # Naming templates configured via the Settings screen (services.settings);
    # fetched once here rather than per-episode since they don't change mid-run.
    naming_settings = get_app_settings(conn)
    folder_template = naming_settings['folder_template']
    filename_template = naming_settings['filename_template']

    # Get all episodes that haven't been downloaded (or were downloaded but the file
    # went missing from disk), ordered by published date (newest first)
    cursor = conn.cursor()
    safe_table_name = validate_and_quote_table_name(podcast_name)
    cursor.execute(f"""
        SELECT title, link_direct, published, id, episode_image_url, link, episode_number, custom_title, season_number
        FROM {safe_table_name}
        WHERE status IN (?, ?)
    """, (EPISODE_STATUS_NOT_DOWNLOADED, EPISODE_STATUS_MISSING))

    episodes = newest_first(cursor.fetchall(), lambda row: row[2])

    if not episodes:
        print(f"No episodes to download for '{podcast_name}'. All episodes may already be downloaded.")
        return (0, 0)
    
    total_episodes = len(episodes)
    
    # Show episode count and ask for confirmation
    print(f"\n{'='*60}")
    print(f"📊 Download Summary for '{podcast_name}'")
    print(f"{'='*60}")
    print(f"Number of episodes to download: {total_episodes}")
    print(f"Delay between downloads: {delay_seconds} seconds")
    print(f"{'='*60}\n")
    
    # Ask for user confirmation (skipped for non-interactive callers, e.g. background jobs)
    if require_confirmation:
        while True:
            user_input = input(f"Do you want to download {total_episodes} episode(s) for '{podcast_name}'? (yes/no): ").strip().lower()
            if user_input in ['yes', 'y']:
                print(f"\n✓ Confirmed. Starting downloads...\n")
                break
            elif user_input in ['no', 'n']:
                print(f"\n✗ Download cancelled by user.\n")
                return (0, total_episodes)
            else:
                print("Please enter 'yes' or 'no'.")
    
    successful_downloads = 0
    failed_downloads = 0
    
    # Batch transaction configuration
    batch_size = config.BATCH_COMMIT_SIZE
    pending_updates = []  # Track episodes that need database updates
    # Collect status updates for batch processing with executemany()
    pending_status_updates = []  # List of (status, episode_id) tuples
    pending_download_info_updates = []  # List of (file_path_str, file_size, file_name, episode_id) tuples
    
    print(f"Starting downloads with {delay_seconds} second delay between episodes...")
    print(f"Using batch transactions: committing every {batch_size} episodes\n")
    
    # Validate connection state before starting
    if not validate_transaction_state(conn):
        logger.error("Invalid database connection state, cannot proceed")
        return (0, 0)
    
    cursor = conn.cursor()
    
    # Download podcast image once (if available)
    podcast_base_folder = Path(downloads_folder) / podcast_name
    podcast_image_downloaded = False
    
    # Query RSS feed URL and podcast image URL once at the start (cache to avoid redundant queries)
    rss_feed_url = None
    podcast_image_url = None
    try:
        # Get both RSS feed URL and podcast image URL in a single query
        cursor.execute("""
            SELECT rss_feed_url, podcast_image_url FROM summary WHERE name = ?
        """, (podcast_name,))
        result = cursor.fetchone()
        if result:
            rss_feed_url = result[0] if result[0] else None
            podcast_image_url = result[1] if len(result) > 1 and result[1] else None
            
            if rss_feed_url:
                logger.info(f"Found RSS feed URL in summary: {rss_feed_url}")
            else:
                logger.debug(f"RSS feed URL not found in summary for {podcast_name}")
            
            if podcast_image_url:
                logger.debug(f"Found podcast image URL in summary: {podcast_image_url}")
        else:
            logger.debug(f"Summary entry not found for {podcast_name}")
    except sqlite3.Error as e:
        logger.debug(f"Could not get RSS feed URL or podcast image URL from summary (column may not exist): {e}")
    
    # Create progress bar for episode downloads
    pbar = tqdm(
        enumerate(episodes, 1),
        total=total_episodes,
        desc=f"Downloading {podcast_name}",
        unit="episode",
        ncols=100
    )
    
    for idx, episode in pbar:
        if job_control is not None:
            job_control.wait_while_paused()
            if job_control.is_cancelled():
                pbar.write("  ⏹️  Cancelled by user. Episodes already downloaded are kept.")
                break

        episode_title, episode_url, published_date, episode_id, episode_image_url, episode_link, episode_number, custom_title, season_number = episode

        # Update progress bar description with current episode
        short_title = episode_title[:50] + "..." if len(episode_title) > 50 else episode_title
        pbar.set_description(f"Downloading: {short_title}")

        if progress_callback:
            try:
                progress_callback(idx, total_episodes, episode_title)
            except Exception:
                logger.debug("progress_callback raised an exception", exc_info=True)

        if job_control is not None and job_control.should_skip(episode_id):
            pbar.write(f"  ⏭️  Removed from the queue by user: {episode_title}")
            continue

        if not episode_url:
            pbar.write(f"  ⚠️  No download URL found for episode '{episode_title}'. Skipping.")
            failed_downloads += 1
            continue
        
        # Parse and format the date using helper function
        episode_date = parse_episode_date(conn, podcast_name, episode_id, published_date)

        # Use the custom title (if the user set one) for folder/file naming and tags,
        # but keep the original episode_title for RSS metadata lookup and log messages.
        effective_title = get_effective_title(episode_title, custom_title)
        safe_episode_title = sanitize_filename(effective_title)

        # Create folder structure according to the configured naming template
        # (defaults to downloads/podcast_name/year/month/episode_number - episode_title/)
        try:
            episode_folder = build_episode_folder_path(
                downloads_folder, podcast_name, episode_date, episode_number, season_number,
                safe_episode_title, folder_template=folder_template,
            )
            episode_folder.mkdir(parents=True, exist_ok=True)
        except (ValueError, OSError) as e:
            logger.error(f"Failed to create folder for episode '{episode_title}': {e}")
            logger.debug(traceback.format_exc())
            pbar.write(f"  ✗ Error creating folder: {e}")
            failed_downloads += 1
            continue

        # Ensure podcast base folder exists for podcast image
        try:
            podcast_base_folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(f"Failed to create podcast base folder: {e}")

        # Base filename (without extension) for all episode files
        try:
            base_filename = build_episode_base_filename(
                podcast_name, episode_date, episode_number, season_number,
                safe_episode_title, filename_template=filename_template,
            )
        except ValueError as e:
            logger.error(f"Invalid filename template for episode '{episode_title}': {e}")
            pbar.write(f"  ✗ Error building filename: {e}")
            failed_downloads += 1
            continue
        
        # File will be saved as Episode_Title - podcast_name.<ext>, the extension
        # taken from the download URL (.mp3, .m4a, ...) unless a file already exists
        file_path = episode_audio_path(episode_folder, base_filename, episode_url)
        
        # Check if file already exists
        if file_path.exists():
            pbar.write(f"  ✓ File already exists: {file_path}")
            # Update ID3 tags even if file already exists (in case metadata changed)
            try:
                if update_episode_id3_tags_from_db(conn, podcast_name, episode_id, file_path, episode_folder):
                    pbar.write(f"  🏷️  ID3 tags updated")
            except Exception as e:
                logger.debug(f"Error updating ID3 tags for existing file: {e}")
            # Collect status update for batch processing (no commit yet - batch transaction)
            pending_status_updates.append((EPISODE_STATUS_DOWNLOADED, episode_id))
            
            # Collect download info update for batch processing. File was already on
            # disk (not freshly downloaded this run), so leave any existing checksum
            # alone rather than re-hashing an unchanged file on every re-sync — the
            # dedicated integrity-check job (services/integrity.py) is what backfills
            # a checksum for episodes downloaded before this column existed.
            file_path_str = str(file_path)
            file_size = file_path.stat().st_size
            file_name = file_path.name
            pending_download_info_updates.append((file_path_str, file_size, file_name, None, None, None, episode_id))

            successful_downloads += 1
            pending_updates.append(('success', idx))
            
            # Commit batch if we've reached the batch size
            if len(pending_updates) >= batch_size:
                try:
                    _commit_batch_updates(conn, safe_table_name, pending_status_updates, pending_download_info_updates, len(pending_updates))
                    pending_updates = []
                    pending_status_updates = []
                    pending_download_info_updates = []
                except (TransactionError, sqlite3.Error) as db_error:
                    logger.error(f"Database error committing batch: {db_error}")
                    safe_rollback(conn)
                    pending_updates = []
                    pending_status_updates = []
                    pending_download_info_updates = []
            
            # Add delay before next episode (except for the last one)
            if idx < total_episodes:
                pbar.write(f"  ⏳ Waiting {delay_seconds} seconds before next episode...")
                time.sleep(delay_seconds)
            continue
        
        try:
            pbar.write(f"  📥 Downloading from: {episode_url}")
            pbar.write(f"  💾 Saving to: {file_path}")
            
            # Download the file with retry logic
            _download_with_retry(episode_url, file_path)
            
            pbar.write(f"  ✓ Downloaded successfully!")
            
            # Download episode image FIRST (before updating ID3 tags, so the image is available)
            # Image will be saved with same base name as MP3 (extension added by _download_image)
            if episode_image_url and episode_image_url.strip():
                episode_image_path = episode_folder / base_filename
                if _download_image(episode_image_url, episode_image_path):
                    pbar.write(f"  🖼️  Episode image downloaded: {episode_image_path}")
                else:
                    logger.warning(f"Failed to download episode image from {episode_image_url}")
            
            # Update ID3 tags with episode metadata from database (after image is downloaded)
            try:
                if update_episode_id3_tags_from_db(conn, podcast_name, episode_id, file_path, episode_folder):
                    pbar.write(f"  🏷️  ID3 tags updated successfully")
                else:
                    logger.warning(f"Failed to update ID3 tags for {file_path}")
            except Exception as e:
                logger.warning(f"Error updating ID3 tags: {e}")
                logger.debug(traceback.format_exc())
                # Don't fail the download if ID3 tag update fails
            
            # Save episode metadata XML (original RSS entry) for archival
            # RSS feed URL is already cached from function start, no need to query again
            # If not available, try fallback construction from episode link
            if not rss_feed_url and episode_link:
                # Fallback: try to construct RSS URL from episode link domain
                from urllib.parse import urlparse
                parsed = urlparse(episode_link)
                # Common RSS feed patterns
                possible_rss_urls = [
                    f"{parsed.scheme}://{parsed.netloc}/feed",
                    f"{parsed.scheme}://{parsed.netloc}/rss",
                    f"{parsed.scheme}://{parsed.netloc}/podcast.xml",
                    f"{parsed.scheme}://{parsed.netloc}/feed.xml",
                ]
                # Try the first one as fallback
                rss_feed_url = possible_rss_urls[0]
                logger.warning(f"RSS feed URL not in summary, using fallback: {rss_feed_url}")
            
            # Extract and save episode metadata XML if RSS feed URL is available
            if rss_feed_url:
                try:
                    logger.debug(f"Attempting to extract XML metadata for episode {episode_id} from {rss_feed_url}")
                    episode_xml = get_episode_xml_from_rss(rss_feed_url, episode_id, episode_title, episode_link)
                    if episode_xml:
                        # XML file uses same base name as MP3
                        metadata_path = episode_folder / f"{base_filename}.xml"
                        with open(metadata_path, 'w', encoding='utf-8') as f:
                            # Write XML declaration and format the XML
                            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
                            f.write('<rss version="2.0">\n')
                            f.write('  <channel>\n')
                            # Indent the item XML properly
                            lines = episode_xml.split('\n')
                            for line in lines:
                                if line.strip():
                                    f.write('    ' + line + '\n')
                            f.write('  </channel>\n')
                            f.write('</rss>\n')
                        pbar.write(f"  📄 Episode metadata XML saved: {metadata_path}")
                        logger.info(f"Successfully saved episode metadata XML to {metadata_path}")
                    else:
                        logger.warning(f"Could not extract XML metadata for episode {episode_id} (title: {episode_title})")
                        pbar.write(f"  ⚠️  Could not extract XML metadata for this episode")
                except Exception as e:
                    logger.error(f"Failed to save episode metadata XML: {e}")
                    logger.debug(traceback.format_exc())
                    pbar.write(f"  ⚠️  Error saving episode metadata XML: {e}")
            else:
                logger.warning(f"RSS feed URL not available for {podcast_name}, skipping metadata XML save")
                pbar.write(f"  ⚠️  RSS feed URL not available, skipping metadata XML save")
            
            # Download podcast image once (if not already downloaded and URL available)
            # Podcast image URL is already cached from function start, no need to query again
            if not podcast_image_downloaded and podcast_image_url:
                podcast_image_path = podcast_base_folder / "podcast_image"
                if _download_image(podcast_image_url, podcast_image_path):
                    pbar.write(f"  🖼️  Podcast image downloaded: {podcast_image_path}")
                    podcast_image_downloaded = True
                else:
                    logger.warning(f"Failed to download podcast image from {podcast_image_url}")
                    podcast_image_downloaded = True  # Mark as attempted
            elif not podcast_image_url:
                # No image URL available, mark as attempted to avoid repeated checks
                podcast_image_downloaded = True
            
            # Collect status and download info updates for batch processing (no commit yet - batch transaction)
            try:
                # Collect status update
                pending_status_updates.append((EPISODE_STATUS_DOWNLOADED, episode_id))
                
                # Collect download info update. This file was just downloaded, so
                # hash it now (cheap relative to the network time already spent)
                # and stamp it as the integrity baseline.
                file_path_str = str(file_path)
                file_size = file_path.stat().st_size
                file_name = file_path.name
                checksum = compute_checksum(file_path)
                verified_at = datetime.now(timezone.utc).isoformat()
                pending_download_info_updates.append(
                    (file_path_str, file_size, file_name, checksum, verified_at, 'ok', episode_id)
                )
            except (sqlite3.Error, OSError) as db_error:
                logger.error(f"Error preparing update for episode {episode_id}: {db_error}")
                logger.debug(traceback.format_exc())
                # Use safe rollback if needed
                safe_rollback(conn)
                raise
            
            successful_downloads += 1
            pending_updates.append(('success', idx))
            
            # Commit batch if we've reached the batch size
            if len(pending_updates) >= batch_size:
                try:
                    _commit_batch_updates(conn, safe_table_name, pending_status_updates, pending_download_info_updates, len(pending_updates))
                    pending_updates = []
                    pending_status_updates = []
                    pending_download_info_updates = []
                except (TransactionError, sqlite3.Error) as db_error:
                    logger.error(f"Database error committing batch: {db_error}")
                    logger.debug(traceback.format_exc())
                    safe_rollback(conn)
                    pending_updates = []
                    pending_status_updates = []
                    pending_download_info_updates = []
            
            # Add delay before next episode (except for the last one)
            if idx < total_episodes:
                pbar.write(f"  ⏳ Waiting {delay_seconds} seconds before next episode...")
                time.sleep(delay_seconds)
            
        except requests.RequestException as e:
            pbar.write(f"  ✗ Network error downloading episode: {e}")
            logger.error(f"Network error downloading episode '{episode_title}': {e}")
            logger.debug(traceback.format_exc())
            failed_downloads += 1
            # Use safe rollback for transaction cleanup
            safe_rollback(conn)
            pending_updates = []
            pending_status_updates = []
            pending_download_info_updates = []
            # Still add delay even on failure
            if idx < total_episodes:
                pbar.write(f"  ⏳ Waiting {delay_seconds} seconds before next episode...")
                time.sleep(delay_seconds)
                
        except OSError as e:
            pbar.write(f"  ✗ File system error downloading episode: {e}")
            logger.error(f"File system error downloading episode '{episode_title}': {e}")
            logger.debug(traceback.format_exc())
            failed_downloads += 1
            # Use safe rollback for transaction cleanup
            safe_rollback(conn)
            pending_updates = []
            pending_status_updates = []
            pending_download_info_updates = []
            # Still add delay even on failure
            if idx < total_episodes:
                pbar.write(f"  ⏳ Waiting {delay_seconds} seconds before next episode...")
                time.sleep(delay_seconds)
                
        except sqlite3.Error as e:
            pbar.write(f"  ✗ Database error: {e}")
            logger.error(f"Database error processing episode '{episode_title}': {e}")
            logger.debug(traceback.format_exc())
            failed_downloads += 1
            # Use safe rollback for transaction cleanup
            safe_rollback(conn)
            pending_updates = []
            pending_status_updates = []
            pending_download_info_updates = []
            # Still add delay even on failure
            if idx < total_episodes:
                pbar.write(f"  ⏳ Waiting {delay_seconds} seconds before next episode...")
                time.sleep(delay_seconds)
    
    # Close progress bar
    pbar.close()
    
    # Commit any remaining pending updates
    if pending_updates:
        try:
            _commit_batch_updates(conn, safe_table_name, pending_status_updates, pending_download_info_updates, len(pending_updates))
        except (TransactionError, sqlite3.Error) as e:
            logger.error(f"Error committing final batch: {e}")
            safe_rollback(conn)
    
    print(f"\n{'='*60}")
    print(f"Download complete for '{podcast_name}':")
    print(f"  ✓ Successful: {successful_downloads}")
    print(f"  ✗ Failed: {failed_downloads}")
    print(f"  📊 Total: {total_episodes}")
    print(f"{'='*60}\n")
    
    # Update summary once at the end of all downloads (optimization: was called after each episode)
    if successful_downloads > 0 or failed_downloads > 0:
        print("📊 Updating summary statistics...")
        update_summary(conn, podcast_name)
    
    return (successful_downloads, total_episodes)


def download_last_episode(
    conn: sqlite3.Connection,
    podcast_name: str,
    downloads_folder: str = None
) -> bool:
    """
    Download the last (most recent) episode of a podcast that hasn't been downloaded yet.
    
    Parameters:
    conn: SQLite connection object
    podcast_name: Name of the podcast (normalized table name)
    downloads_folder: Folder to save downloads (default: config.DOWNLOADS_FOLDER)
    
    Returns:
    True if download successful, False otherwise
    """
    if downloads_folder is None:
        downloads_folder = config.DOWNLOADS_FOLDER
    
    # Get all episodes and download only the first one
    try:
        Path(downloads_folder).mkdir(exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to create downloads folder '{downloads_folder}': {e}")
        logger.debug(traceback.format_exc())
        print(f"Error: Failed to create downloads folder: {e}")
        return False
    
    if not table_exists(conn, podcast_name):
        print(f"Table '{podcast_name}' does not exist in database.")
        return False

    # Ensure the download columns (including custom_title) exist in the table
    add_download_columns_to_table(conn, podcast_name)

    naming_settings = get_app_settings(conn)
    folder_template = naming_settings['folder_template']
    filename_template = naming_settings['filename_template']

    cursor = conn.cursor()
    safe_table_name = validate_and_quote_table_name(podcast_name)
    cursor.execute(f"""
        SELECT title, link_direct, published, id, episode_number, custom_title, season_number
        FROM {safe_table_name}
        WHERE status IN (?, ?)
    """, (EPISODE_STATUS_NOT_DOWNLOADED, EPISODE_STATUS_MISSING))

    candidates = newest_first(cursor.fetchall(), lambda row: row[2])
    episode = candidates[0] if candidates else None

    if not episode:
        print(f"No episodes to download for '{podcast_name}'. All episodes may already be downloaded.")
        return False

    episode_title, episode_url, published_date, episode_id, episode_number, custom_title, season_number = episode

    if not episode_url:
        print(f"No download URL found for episode '{episode_title}'.")
        return False

    # Parse and format the date using helper function
    episode_date = parse_episode_date(conn, podcast_name, episode_id, published_date)

    effective_title = get_effective_title(episode_title, custom_title)
    safe_episode_title = sanitize_filename(effective_title)
    try:
        podcast_folder = build_episode_folder_path(
            downloads_folder, podcast_name, episode_date, episode_number, season_number,
            safe_episode_title, folder_template=folder_template,
        )
        podcast_folder.mkdir(parents=True, exist_ok=True)
    except (ValueError, OSError) as e:
        logger.error(f"Failed to create folder for episode '{episode_title}': {e}")
        logger.debug(traceback.format_exc())
        print(f"Error creating folder: {e}")
        return False

    try:
        base_filename = build_episode_base_filename(
            podcast_name, episode_date, episode_number, season_number,
            safe_episode_title, filename_template=filename_template,
        )
    except ValueError as e:
        logger.error(f"Invalid filename template for episode '{episode_title}': {e}")
        print(f"Error building filename: {e}")
        return False
    file_path = episode_audio_path(podcast_folder, base_filename, episode_url)
    
    if file_path.exists():
        print(f"File already exists: {file_path}")
        # Update ID3 tags even if file already exists (in case metadata changed)
        try:
            if update_episode_id3_tags_from_db(conn, podcast_name, episode_id, file_path, podcast_folder):
                print(f"  🏷️  ID3 tags updated")
        except Exception as e:
            logger.debug(f"Error updating ID3 tags for existing file: {e}")
        # Use transaction context manager for safe transaction handling
        try:
            with transaction(conn, autocommit=True) as cursor:
                cursor.executemany(
                    f"UPDATE {safe_table_name} SET status = ? WHERE id = ?",
                    [(EPISODE_STATUS_DOWNLOADED, episode_id)]
                )
            
            update_download_info(conn, podcast_name, episode_id, file_path)
            update_summary(conn, podcast_name)
            return True
        except (TransactionError, sqlite3.Error) as e:
            logger.error(f"Database error updating existing episode: {e}")
            safe_rollback(conn)
            return False
    
    try:
        print(f"Downloading: {episode_title}")
        _download_with_retry(episode_url, file_path)
        print(f"Downloaded successfully: {file_path}")
        
        # Update ID3 tags with episode metadata from database
        try:
            if update_episode_id3_tags_from_db(conn, podcast_name, episode_id, file_path, podcast_folder):
                print(f"  🏷️  ID3 tags updated successfully")
        except Exception as e:
            logger.warning(f"Error updating ID3 tags: {e}")
            logger.debug(traceback.format_exc())
        
        try:
            # Use transaction context manager for safe transaction handling
            with transaction(conn, autocommit=True) as cursor:
                # Use executemany() for consistency (even though it's just one update)
                cursor.executemany(
                    f"UPDATE {safe_table_name} SET status = ? WHERE id = ?",
                    [(EPISODE_STATUS_DOWNLOADED, episode_id)]
                )
            
            # Update download info and summary (these may have their own transactions).
            # This file was just downloaded, so hash it now for the integrity baseline.
            update_download_info(conn, podcast_name, episode_id, file_path, checksum=compute_checksum(file_path))
            update_summary(conn, podcast_name)
        except (TransactionError, sqlite3.Error) as db_error:
            logger.error(f"Database error updating episode {episode_id}: {db_error}")
            logger.debug(traceback.format_exc())
            safe_rollback(conn)
            return False
            
        return True
        
    except requests.RequestException as e:
        print(f"Network error downloading episode: {e}")
        logger.error(f"Network error downloading episode '{episode_title}': {e}")
        logger.debug(traceback.format_exc())
        return False
        
    except OSError as e:
        print(f"File system error downloading episode: {e}")
        logger.error(f"File system error downloading episode '{episode_title}': {e}")
        logger.debug(traceback.format_exc())
        return False
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        logger.error(f"Database error processing episode '{episode_title}': {e}")
        logger.debug(traceback.format_exc())
        safe_rollback(conn)
        return False


def download_one_episode(
    conn: sqlite3.Connection,
    podcast_name: str,
    episode_id: str,
    downloads_folder: str = None,
) -> bool:
    """
    Download (or re-download) exactly one episode by id — true single-episode
    fetch (Fase 22), independent of every other pending/missing episode of
    the podcast.

    Deliberately a standalone function rather than calling into
    `download_all_episodes` with a one-item list: that function's loop body
    is a heavily-exercised, carefully-tested hot path (batch commits, the
    tqdm progress bar, per-iteration job-control checks), and duplicating
    its per-episode steps here — folder/filename, download-or-reuse, image,
    ID3 tags, XML metadata, checksum — is a smaller change than reshaping it
    to also serve a single-episode caller cleanly.

    Returns True on success, False otherwise (episode not found, no
    download URL, network/filesystem/database error).
    """
    if downloads_folder is None:
        downloads_folder = config.DOWNLOADS_FOLDER

    if not table_exists(conn, podcast_name):
        print(f"Table '{podcast_name}' does not exist in database.")
        return False

    add_download_columns_to_table(conn, podcast_name)

    naming_settings = get_app_settings(conn)
    folder_template = naming_settings['folder_template']
    filename_template = naming_settings['filename_template']

    safe_table_name = validate_and_quote_table_name(podcast_name)
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT title, link_direct, published, episode_image_url, link, episode_number, custom_title, season_number
        FROM {safe_table_name}
        WHERE id = ?
    """, (episode_id,))
    row = cursor.fetchone()
    if not row:
        print(f"Episode '{episode_id}' not found.")
        return False

    episode_title, episode_url, published_date, episode_image_url, episode_link, episode_number, custom_title, season_number = row

    if not episode_url:
        print(f"No download URL found for episode '{episode_title}'.")
        return False

    rss_feed_url = None
    podcast_image_url = None
    try:
        cursor.execute("SELECT rss_feed_url, podcast_image_url FROM summary WHERE name = ?", (podcast_name,))
        result = cursor.fetchone()
        if result:
            rss_feed_url = result[0] or None
            podcast_image_url = result[1] if len(result) > 1 and result[1] else None
    except sqlite3.Error as e:
        logger.debug(f"Could not get RSS feed URL / podcast image URL from summary: {e}")

    try:
        Path(downloads_folder).mkdir(exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to create downloads folder '{downloads_folder}': {e}")
        print(f"Error: Failed to create downloads folder: {e}")
        return False

    episode_date = parse_episode_date(conn, podcast_name, episode_id, published_date)
    effective_title = get_effective_title(episode_title, custom_title)
    safe_episode_title = sanitize_filename(effective_title)

    try:
        episode_folder = build_episode_folder_path(
            downloads_folder, podcast_name, episode_date, episode_number, season_number,
            safe_episode_title, folder_template=folder_template,
        )
        episode_folder.mkdir(parents=True, exist_ok=True)
    except (ValueError, OSError) as e:
        logger.error(f"Failed to create folder for episode '{episode_title}': {e}")
        print(f"Error creating folder: {e}")
        return False

    podcast_base_folder = Path(downloads_folder) / podcast_name
    try:
        podcast_base_folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"Failed to create podcast base folder: {e}")

    try:
        base_filename = build_episode_base_filename(
            podcast_name, episode_date, episode_number, season_number,
            safe_episode_title, filename_template=filename_template,
        )
    except ValueError as e:
        logger.error(f"Invalid filename template for episode '{episode_title}': {e}")
        print(f"Error building filename: {e}")
        return False

    file_path = episode_audio_path(episode_folder, base_filename, episode_url)

    if file_path.exists():
        print(f"File already exists: {file_path}")
        try:
            if update_episode_id3_tags_from_db(conn, podcast_name, episode_id, file_path, episode_folder):
                print("  🏷️  ID3 tags updated")
        except Exception as e:
            logger.debug(f"Error updating ID3 tags for existing file: {e}")
        try:
            with transaction(conn, autocommit=True) as tx_cursor:
                tx_cursor.execute(f"UPDATE {safe_table_name} SET status = ? WHERE id = ?", (EPISODE_STATUS_DOWNLOADED, episode_id))
            # File was already on disk — leave any existing checksum untouched
            # (see the equivalent branch in download_all_episodes for why).
            update_download_info(conn, podcast_name, episode_id, file_path)
            update_summary(conn, podcast_name)
            return True
        except (TransactionError, sqlite3.Error) as e:
            logger.error(f"Database error updating existing episode: {e}")
            safe_rollback(conn)
            return False

    try:
        print(f"Downloading: {episode_title}")
        _download_with_retry(episode_url, file_path)
        print(f"Downloaded successfully: {file_path}")

        if episode_image_url and episode_image_url.strip():
            episode_image_path = episode_folder / base_filename
            if _download_image(episode_image_url, episode_image_path):
                print(f"  🖼️  Episode image downloaded: {episode_image_path}")
            else:
                logger.warning(f"Failed to download episode image from {episode_image_url}")

        try:
            if update_episode_id3_tags_from_db(conn, podcast_name, episode_id, file_path, episode_folder):
                print("  🏷️  ID3 tags updated successfully")
            else:
                logger.warning(f"Failed to update ID3 tags for {file_path}")
        except Exception as e:
            logger.warning(f"Error updating ID3 tags: {e}")

        if not rss_feed_url and episode_link:
            from urllib.parse import urlparse
            parsed = urlparse(episode_link)
            rss_feed_url = f"{parsed.scheme}://{parsed.netloc}/feed"
            logger.warning(f"RSS feed URL not in summary, using fallback: {rss_feed_url}")

        if rss_feed_url:
            try:
                episode_xml = get_episode_xml_from_rss(rss_feed_url, episode_id, episode_title, episode_link)
                if episode_xml:
                    metadata_path = episode_folder / f"{base_filename}.xml"
                    with open(metadata_path, 'w', encoding='utf-8') as f:
                        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0">\n  <channel>\n')
                        for line in episode_xml.split('\n'):
                            if line.strip():
                                f.write('    ' + line + '\n')
                        f.write('  </channel>\n</rss>\n')
                    print(f"  📄 Episode metadata XML saved: {metadata_path}")
                else:
                    logger.warning(f"Could not extract XML metadata for episode {episode_id}")
            except Exception as e:
                logger.error(f"Failed to save episode metadata XML: {e}")
        else:
            logger.warning(f"RSS feed URL not available for {podcast_name}, skipping metadata XML save")

        if podcast_image_url and not (podcast_base_folder / "podcast_image.jpg").exists() and not list(podcast_base_folder.glob("podcast_image.*")):
            podcast_image_path = podcast_base_folder / "podcast_image"
            if _download_image(podcast_image_url, podcast_image_path):
                print(f"  🖼️  Podcast image downloaded: {podcast_image_path}")

        try:
            with transaction(conn, autocommit=True) as tx_cursor:
                tx_cursor.execute(f"UPDATE {safe_table_name} SET status = ? WHERE id = ?", (EPISODE_STATUS_DOWNLOADED, episode_id))
            checksum = compute_checksum(file_path)
            update_download_info(conn, podcast_name, episode_id, file_path, checksum=checksum)
            update_summary(conn, podcast_name)
        except (TransactionError, sqlite3.Error) as db_error:
            logger.error(f"Database error updating episode {episode_id}: {db_error}")
            safe_rollback(conn)
            return False

        return True

    except requests.RequestException as e:
        print(f"Network error downloading episode: {e}")
        logger.error(f"Network error downloading episode '{episode_title}': {e}")
        return False
    except OSError as e:
        print(f"File system error downloading episode: {e}")
        logger.error(f"File system error downloading episode '{episode_title}': {e}")
        return False
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        logger.error(f"Database error processing episode '{episode_title}': {e}")
        safe_rollback(conn)
        return False


