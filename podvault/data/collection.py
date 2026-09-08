"""
Data collection functions for parsing RSS feeds and extracting podcast information.
"""

import re
import logging
import traceback
import unicodedata
import requests
import json
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, parse_qs
from typing import Tuple, Dict, Optional
from datetime import datetime, timedelta
from collections import OrderedDict
import pandas as pd
import feedparser
from podvault import config
from podvault.config import EPISODE_STATUS_NOT_DOWNLOADED
from podvault.validation import validate_feed_url

# Set up logging
from podvault.logging_config import setup_logging
logger = setup_logging(__name__)

# RSS Feed Cache
# Structure: OrderedDict for LRU eviction: {rss_url: (content_bytes, timestamp, size_bytes)}
# Using OrderedDict to maintain insertion order for LRU eviction
_rss_cache: OrderedDict[str, Tuple[bytes, datetime, int]] = OrderedDict()
_rss_cache_ttl = timedelta(hours=config.RSS_CACHE_TTL_HOURS)
_rss_cache_max_entries = config.RSS_CACHE_MAX_ENTRIES
_rss_cache_max_size_bytes = config.RSS_CACHE_MAX_SIZE_MB * 1024 * 1024  # Convert MB to bytes
_rss_cache_current_size = 0  # Track total cache size in bytes


def _evict_cache_entries() -> None:
    """
    Evict cache entries using LRU (Least Recently Used) strategy.
    
    Evicts entries until cache is within size and entry limits.
    """
    global _rss_cache_current_size
    
    # Remove expired entries first
    current_time = datetime.now()
    expired_urls = [
        url for url, (_, timestamp, _) in _rss_cache.items()
        if current_time - timestamp >= _rss_cache_ttl
    ]
    
    for url in expired_urls:
        _, _, size = _rss_cache.pop(url)
        _rss_cache_current_size -= size
        logger.debug(f"Evicted expired cache entry: {url}")
    
    # Evict oldest entries (LRU) if still over limits
    while len(_rss_cache) >= _rss_cache_max_entries or _rss_cache_current_size >= _rss_cache_max_size_bytes:
        if not _rss_cache:
            break
        
        # OrderedDict maintains insertion order, so popitem(last=False) removes oldest
        url, (_, _, size) = _rss_cache.popitem(last=False)
        _rss_cache_current_size -= size
        logger.debug(f"Evicted LRU cache entry: {url} ({size:,} bytes)")


def get_cached_rss_content(rss_url: str) -> Optional[bytes]:
    """
    Get RSS feed content from cache or download if not cached or expired.
    
    This function implements an in-memory cache with TTL (time-to-live),
    size limits, and LRU (Least Recently Used) eviction to avoid redundant
    RSS feed downloads. The cache is shared across all functions that need
    RSS feed content.
    
    Parameters:
    rss_url: URL of the RSS feed
    
    Returns:
    bytes: RSS feed content, or None if download fails
    
    Example:
        >>> content = get_cached_rss_content("https://feeds.example.com/podcast.rss")
        >>> # First call downloads and caches
        >>> content = get_cached_rss_content("https://feeds.example.com/podcast.rss")
        >>> # Second call uses cache (if within TTL)
    """
    global _rss_cache_current_size
    
    # Validate URL
    is_valid, error = validate_feed_url(rss_url)
    if not is_valid:
        logger.error(f"Invalid RSS feed URL '{rss_url}': {error}")
        return None
    
    current_time = datetime.now()
    
    # Check if we have a valid cached version
    if rss_url in _rss_cache:
        # Move to end (most recently used) for LRU
        content, timestamp, size = _rss_cache.pop(rss_url)
        _rss_cache[rss_url] = (content, timestamp, size)
        
        if current_time - timestamp < _rss_cache_ttl:
            logger.debug(f"Using cached RSS feed: {rss_url} (age: {current_time - timestamp})")
            return content
        else:
            # Cache expired, remove it
            logger.debug(f"RSS cache expired for {rss_url}, will re-download")
            _rss_cache_current_size -= size
    
    # Evict entries if needed before adding new one
    _evict_cache_entries()
    
    # Download and cache
    try:
        logger.debug(f"Downloading RSS feed: {rss_url}")
        response = requests.get(rss_url, timeout=config.DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        content = response.content
        content_size = len(content)
        
        # Check if content is too large for cache
        if content_size > _rss_cache_max_size_bytes:
            logger.warning(
                f"RSS feed too large to cache ({content_size:,} bytes > {_rss_cache_max_size_bytes:,} bytes): {rss_url}"
            )
            return content
        
        # Cache the content (add to end for LRU)
        _rss_cache[rss_url] = (content, current_time, content_size)
        _rss_cache_current_size += content_size
        logger.info(f"Cached RSS feed: {rss_url} ({content_size:,} bytes)")
        
        # Evict if we went over limits
        _evict_cache_entries()
        
        return content
    except requests.RequestException as e:
        logger.error(f"Failed to download RSS feed {rss_url}: {e}")
        logger.debug(traceback.format_exc())
        return None


def clear_rss_cache():
    """
    Clear the RSS feed cache.
    
    Useful for testing or forcing fresh downloads.
    """
    global _rss_cache, _rss_cache_current_size
    cache_size = len(_rss_cache)
    cache_bytes = _rss_cache_current_size
    _rss_cache.clear()
    _rss_cache_current_size = 0
    logger.debug(f"RSS feed cache cleared ({cache_size} entries, {cache_bytes:,} bytes)")


def invalidate_rss_cache_entry(rss_url: str) -> bool:
    """
    Invalidate a specific RSS feed cache entry.
    
    Parameters:
    rss_url: URL of the RSS feed to invalidate
    
    Returns:
    bool: True if entry was found and removed, False otherwise
    
    Example:
        >>> invalidate_rss_cache_entry("https://feeds.example.com/podcast.rss")
        True
    """
    global _rss_cache_current_size
    
    if rss_url in _rss_cache:
        _, _, size = _rss_cache.pop(rss_url)
        _rss_cache_current_size -= size
        logger.debug(f"Invalidated cache entry: {rss_url}")
        return True
    return False


def get_rss_cache_stats() -> Dict[str, any]:
    """
    Get statistics about the RSS feed cache.
    
    Returns:
    dict: Cache statistics including:
        - entries: Number of cached entries
        - size_bytes: Total cache size in bytes
        - size_mb: Total cache size in megabytes
        - max_entries: Maximum number of entries allowed
        - max_size_mb: Maximum cache size in megabytes
        - ttl_hours: Time-to-live in hours
        - utilization_entries: Percentage of entry limit used
        - utilization_size: Percentage of size limit used
    
    Example:
        >>> stats = get_rss_cache_stats()
        >>> print(f"Cache has {stats['entries']} entries using {stats['size_mb']:.2f} MB")
    """
    current_time = datetime.now()
    expired_count = sum(
        1 for _, (_, timestamp, _) in _rss_cache.items()
        if current_time - timestamp >= _rss_cache_ttl
    )
    
    return {
        'entries': len(_rss_cache),
        'expired_entries': expired_count,
        'size_bytes': _rss_cache_current_size,
        'size_mb': _rss_cache_current_size / (1024 * 1024),
        'max_entries': _rss_cache_max_entries,
        'max_size_mb': config.RSS_CACHE_MAX_SIZE_MB,
        'ttl_hours': config.RSS_CACHE_TTL_HOURS,
        'utilization_entries': (len(_rss_cache) / _rss_cache_max_entries * 100) if _rss_cache_max_entries > 0 else 0,
        'utilization_size': (_rss_cache_current_size / _rss_cache_max_size_bytes * 100) if _rss_cache_max_size_bytes > 0 else 0,
    }


def get_podcast_title(rss_url: str) -> str:
    """
    Get the podcast title from an RSS feed URL.
    
    Parameters:
    rss_url: str - RSS feed URL
    
    Returns:
    str - Podcast title, or "Unknown Podcast" if title cannot be determined
    
    Example:
        >>> rss_url = "https://feeds.example.com/podcast.rss"
        >>> title = get_podcast_title(rss_url)
        >>> print(title)
        "Example Podcast"
    """
    # Validate URL
    is_valid, error = validate_feed_url(rss_url)
    if not is_valid:
        logger.warning(f"Invalid RSS feed URL '{rss_url}': {error}")
        return "Unknown Podcast"
    
    try:
        # Get RSS feed content from cache or download
        rss_content = get_cached_rss_content(rss_url)
        if not rss_content:
            return "Unknown Podcast"
        
        # Parse the feed to get the title
        feed = feedparser.parse(rss_content)
        
        # Try to get the title from various locations
        if hasattr(feed, 'channel') and hasattr(feed.channel, 'title'):
            return feed.channel.title
        elif hasattr(feed, 'feed') and hasattr(feed.feed, 'title'):
            return feed.feed.title
        elif hasattr(feed, 'feed') and 'title' in feed.feed:
            return feed.feed['title']
        elif feed.entries and len(feed.entries) > 0:
            # Try to get from first entry
            first_entry = feed.entries[0]
            if hasattr(first_entry, 'author'):
                return first_entry.author
            elif 'author' in first_entry:
                return first_entry['author']
        
        return "Unknown Podcast"
        
    except Exception as e:
        logger.warning(f"Could not get podcast title from {rss_url}: {e}")
        return "Unknown Podcast"


def get_rss_from_apple_podcast(apple_url: str) -> str:
    """
    Extract the RSS feed URL from an Apple Podcast link.
    
    This function takes an Apple Podcast URL (e.g., https://podcasts.apple.com/us/podcast/.../id123456789)
    and uses the iTunes Search API to retrieve the corresponding RSS feed URL.
    
    Parameters:
    apple_url: str - Apple Podcast URL
    
    Returns:
    str - RSS feed URL
    
    Raises:
    ValueError: If the URL is not a valid Apple Podcast URL or if the podcast ID cannot be extracted
    requests.RequestException: If there's an error making the API request
    KeyError: If the RSS feed URL is not found in the API response
    
    Example:
        >>> apple_url = "https://podcasts.apple.com/us/podcast/example/id123456789"
        >>> rss_url = get_rss_from_apple_podcast(apple_url)
        >>> print(rss_url)
        "https://feeds.example.com/podcast.rss"
    """
    try:
        # Parse the URL to extract the podcast ID
        parsed_url = urlparse(apple_url)
        
        # Apple Podcast URLs typically have the format:
        # https://podcasts.apple.com/{country}/podcast/{name}/id{ID}
        # or
        # https://podcasts.apple.com/podcast/{name}/id{ID}
        
        # Extract ID from path (look for /id{number})
        path_parts = parsed_url.path.split('/')
        podcast_id = None
        
        for part in path_parts:
            if part.startswith('id') and part[2:].isdigit():
                podcast_id = part[2:]  # Remove 'id' prefix
                break
        
        # Alternative: try to extract from query parameters
        if not podcast_id:
            query_params = parse_qs(parsed_url.query)
            if 'id' in query_params:
                podcast_id = query_params['id'][0]
        
        if not podcast_id:
            raise ValueError(
                f"Could not extract podcast ID from Apple Podcast URL: {apple_url}. "
                f"Expected format: https://podcasts.apple.com/.../id{'{number}'}"
            )
        
        logger.info(f"Extracted podcast ID: {podcast_id} from Apple Podcast URL")
        
        # Use iTunes Search API to get RSS feed URL
        itunes_api_url = f"https://itunes.apple.com/lookup?id={podcast_id}"
        
        try:
            response = requests.get(itunes_api_url, timeout=config.DOWNLOAD_TIMEOUT)
            response.raise_for_status()
            
            data = response.json()
            
            # Check if results were found
            if not data.get('results') or len(data['results']) == 0:
                raise ValueError(
                    f"No podcast found with ID {podcast_id}. "
                    f"The podcast may not be available in the iTunes Store."
                )
            
            # Get the first result (should be the podcast)
            podcast_info = data['results'][0]
            
            # Extract RSS feed URL
            if 'feedUrl' not in podcast_info:
                raise KeyError(
                    f"RSS feed URL not found in iTunes API response for podcast ID {podcast_id}. "
                    f"Response keys: {list(podcast_info.keys())}"
                )
            
            rss_url = podcast_info['feedUrl']
            podcast_name = podcast_info.get('collectionName', 'Unknown Podcast')
            logger.info(f"Successfully retrieved RSS feed URL: {rss_url} (Podcast: {podcast_name})")
            
            return rss_url
            
        except requests.Timeout as e:
            logger.error(f"Timeout while fetching RSS feed from iTunes API: {e}")
            raise requests.RequestException(f"Timeout fetching RSS feed for podcast ID {podcast_id}") from e
            
        except requests.HTTPError as e:
            logger.error(f"HTTP error while fetching RSS feed from iTunes API: {e}")
            raise requests.RequestException(f"HTTP error fetching RSS feed for podcast ID {podcast_id}") from e
            
        except requests.ConnectionError as e:
            logger.error(f"Connection error while fetching RSS feed from iTunes API: {e}")
            raise requests.RequestException(f"Connection error fetching RSS feed for podcast ID {podcast_id}") from e
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from iTunes API: {e}")
            raise ValueError(f"Invalid response from iTunes API for podcast ID {podcast_id}") from e
            
    except ValueError as e:
        # Re-raise ValueError as-is
        logger.error(f"Error extracting RSS feed from Apple Podcast URL: {e}")
        raise
        
    except Exception as e:
        logger.error(f"Unexpected error extracting RSS feed from Apple Podcast URL: {e}")
        logger.debug(traceback.format_exc())
        raise ValueError(f"Failed to extract RSS feed from Apple Podcast URL: {e}") from e


def get_apple_podcast_info(apple_url: str) -> Tuple[str, str]:
    """
    Get both RSS feed URL and podcast title from an Apple Podcast link.
    
    Parameters:
    apple_url: str - Apple Podcast URL
    
    Returns:
    tuple[str, str] - (RSS feed URL, Podcast title)
    
    Raises:
    ValueError: If the URL is not a valid Apple Podcast URL or if the podcast ID cannot be extracted
    requests.RequestException: If there's an error making the API request
    """
    try:
        # Parse the URL to extract the podcast ID
        parsed_url = urlparse(apple_url)
        
        # Extract ID from path (look for /id{number})
        path_parts = parsed_url.path.split('/')
        podcast_id = None
        
        for part in path_parts:
            if part.startswith('id') and part[2:].isdigit():
                podcast_id = part[2:]  # Remove 'id' prefix
                break
        
        # Alternative: try to extract from query parameters
        if not podcast_id:
            query_params = parse_qs(parsed_url.query)
            if 'id' in query_params:
                podcast_id = query_params['id'][0]
        
        if not podcast_id:
            raise ValueError(
                f"Could not extract podcast ID from Apple Podcast URL: {apple_url}. "
                f"Expected format: https://podcasts.apple.com/.../id{'{number}'}"
            )
        
        # Use iTunes Search API to get RSS feed URL and title
        itunes_api_url = f"https://itunes.apple.com/lookup?id={podcast_id}"
        
        response = requests.get(itunes_api_url, timeout=config.DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        
        data = response.json()
        
        # Check if results were found
        if not data.get('results') or len(data['results']) == 0:
            raise ValueError(
                f"No podcast found with ID {podcast_id}. "
                f"The podcast may not be available in the iTunes Store."
            )
        
        # Get the first result (should be the podcast)
        podcast_info = data['results'][0]
        
        # Extract RSS feed URL and title
        if 'feedUrl' not in podcast_info:
            raise KeyError(
                f"RSS feed URL not found in iTunes API response for podcast ID {podcast_id}. "
                f"Response keys: {list(podcast_info.keys())}"
            )
        
        rss_url = podcast_info['feedUrl']
        podcast_title = podcast_info.get('collectionName', 'Unknown Podcast')
        
        return (rss_url, podcast_title)
        
    except requests.RequestException as e:
        logger.error(f"Request error getting Apple Podcast info: {e}")
        raise
    except Exception as e:
        logger.error(f"Error getting Apple Podcast info: {e}")
        logger.debug(traceback.format_exc())
        raise ValueError(f"Failed to get Apple Podcast info: {e}") from e


def normalize_feed_url(feed_url: str) -> str:
    """
    Normalize a feed URL by converting Apple Podcast links to RSS feed URLs.
    
    This function automatically detects whether the input is:
    - An Apple Podcast link (podcasts.apple.com or itunes.apple.com) → converts to RSS feed
    - An RSS feed URL (already in correct format) → returns as-is
    
    Parameters:
    feed_url: str - Feed URL (can be Apple Podcast link or RSS feed URL)
    
    Returns:
    str - RSS feed URL
    
    Raises:
    ValueError: If the URL cannot be processed
    requests.RequestException: If there's an error fetching the RSS feed from Apple Podcast
    
    Example:
        >>> # Apple Podcast link - will be converted
        >>> apple_url = "https://podcasts.apple.com/us/podcast/example/id123456789"
        >>> rss_url = normalize_feed_url(apple_url)
        >>> print(rss_url)
        "https://feeds.example.com/podcast.rss"
        
        >>> # RSS feed URL - returned as-is
        >>> rss_url = "https://feeds.example.com/podcast.rss"
        >>> normalized = normalize_feed_url(rss_url)
        >>> print(normalized)
        "https://feeds.example.com/podcast.rss"
    """
    if not feed_url or not isinstance(feed_url, str):
        raise ValueError(f"Invalid feed URL: {feed_url}")
    
    feed_url = feed_url.strip()
    
    # Check if it's an Apple Podcast URL
    if 'podcasts.apple.com' in feed_url or 'itunes.apple.com' in feed_url:
        logger.info(f"Detected Apple Podcast URL, converting to RSS feed: {feed_url}")
        try:
            rss_url = get_rss_from_apple_podcast(feed_url)
            # Validate the converted RSS URL
            is_valid, error = validate_feed_url(rss_url)
            if not is_valid:
                raise ValueError(f"Converted RSS feed URL is invalid: {error}")
            logger.info(f"Successfully converted Apple Podcast link to RSS feed")
            return rss_url
        except Exception as e:
            logger.error(f"Failed to convert Apple Podcast URL to RSS feed: {e}")
            raise
    
    # Check if it looks like an RSS feed URL (common patterns)
    rss_indicators = ['.rss', '/rss', '/feed', 'feed.xml', 'rss.xml', 'atom.xml', 'podcast']
    is_likely_rss = any(indicator in feed_url.lower() for indicator in rss_indicators)
    
    if is_likely_rss:
        logger.debug(f"URL appears to be an RSS feed already: {feed_url}")
    else:
        logger.debug(f"URL does not match common RSS patterns, using as-is: {feed_url}")
    
    # Validate the RSS feed URL before returning
    is_valid, error = validate_feed_url(feed_url)
    if not is_valid:
        raise ValueError(f"Invalid RSS feed URL: {error}")
    
    # Already an RSS feed URL (or assumed to be), return as-is
    return feed_url


def normalize(name):
    """
    Normalize a name by removing accents and converting to a safe format.
    
    Parameters:
    name: str - The name to normalize
    
    Returns:
    str - Normalized name (lowercase, underscores, no special chars)
    """
    # Remove accents / diacritics
    name = unicodedata.normalize("NFD", name)
    name = "".join(
        c for c in name
        if unicodedata.category(c) != "Mn"
    )

    # Original normalization
    name = name.lower()
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^\w_]", "", name)

    return name


def get_episode_xml_from_rss(rss_url: str, episode_id: str, episode_title: str = None, episode_link: str = None) -> Optional[str]:
    """
    Download the RSS feed and extract the XML for a specific episode entry.
    
    This function downloads the RSS feed, parses it as XML, and extracts
    the complete <item> element for the specified episode, preserving
    all original metadata exactly as it appears in the RSS feed.
    
    Parameters:
    rss_url: str - URL of the RSS feed
    episode_id: str - ID of the episode to extract (matches RSS <guid> or <id>)
    episode_title: str - Optional episode title for matching if ID doesn't match
    episode_link: str - Optional episode link for matching if ID doesn't match
    
    Returns:
    str - XML string of the episode entry, or None if not found
    
    Example:
        >>> rss_url = "https://feeds.example.com/podcast.rss"
        >>> episode_id = "https://example.com/episode/123"
        >>> xml = get_episode_xml_from_rss(rss_url, episode_id)
        >>> print(xml)
        "<item>...</item>"
    """
    # Validate URL
    is_valid, error = validate_feed_url(rss_url)
    if not is_valid:
        logger.error(f"Invalid RSS feed URL '{rss_url}': {error}")
        return None
    
    try:
        # Get RSS feed content from cache or download
        rss_content = get_cached_rss_content(rss_url)
        if not rss_content:
            logger.warning(f"Could not retrieve RSS feed content from {rss_url}")
            return None
        
        # Parse the XML
        root = ET.fromstring(rss_content)
        
        # Handle namespaces (RSS feeds often use namespaces)
        namespaces = {
            'atom': 'http://www.w3.org/2005/Atom',
            'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd',
            'content': 'http://purl.org/rss/1.0/modules/content/',
        }
        
        # Find all <item> elements
        items = root.findall('.//item')
        if not items:
            # Try without namespace
            items = root.findall('item')
        
        # Search for the episode by ID (guid or id), then by title, then by link
        for item in items:
            # Try to find guid or id
            guid_elem = item.find('guid')
            if guid_elem is not None:
                guid_text = guid_elem.text if guid_elem.text else ''
                if guid_text == episode_id or episode_id in guid_text:
                    # Found the episode, return its XML
                    return ET.tostring(item, encoding='unicode')
            
            # Try id attribute or other identifiers
            id_attr = item.get('id')
            if id_attr == episode_id:
                return ET.tostring(item, encoding='unicode')
            
            # Try link as identifier
            link_elem = item.find('link')
            link_text = link_elem.text if link_elem is not None and link_elem.text else ''
            if link_text == episode_id or episode_id in link_text:
                return ET.tostring(item, encoding='unicode')
            
            # If episode_link provided, try matching by link
            if episode_link and link_text:
                if link_text == episode_link or episode_link in link_text:
                    return ET.tostring(item, encoding='unicode')
            
            # If episode_title provided, try matching by title
            if episode_title:
                title_elem = item.find('title')
                title_text = title_elem.text if title_elem is not None and title_elem.text else ''
                if title_text == episode_title or episode_title in title_text:
                    return ET.tostring(item, encoding='unicode')
        
        logger.warning(f"Episode with ID '{episode_id}' not found in RSS feed {rss_url}")
        logger.debug(f"Searched {len(items)} items in RSS feed")
        if episode_title:
            logger.debug(f"Episode title: {episode_title}")
        if episode_link:
            logger.debug(f"Episode link: {episode_link}")
        return None
        
    except requests.RequestException as e:
        logger.error(f"Failed to download RSS feed for episode metadata from {rss_url}: {e}")
        logger.debug(traceback.format_exc())
        return None
    except ET.ParseError as e:
        logger.error(f"Failed to parse RSS XML from {rss_url}: {e}")
        logger.debug(traceback.format_exc())
        return None
    except Exception as e:
        logger.error(f"Unexpected error extracting episode XML from {rss_url}: {e}")
        logger.debug(traceback.format_exc())
        return None


def collect_data(feed):
    """
    Collect data from an RSS feed and return a cleaned DataFrame.
    
    Parameters:
    feed: str - URL or path to RSS feed
    
    Returns:
    pd.DataFrame - DataFrame containing episode information with columns:
        - title: Episode title
        - link: Episode link
        - published: Publication date string
        - summary: Episode summary
        - id: Episode ID
        - link_direct: Direct audio download link
        - published_parsed: Parsed publication date (ISO format)
        - status: Download status (default: 'not downloaded')
        - author: Podcast name (normalized)
        - author_raw: Podcast name (original, before normalization)
        - episode_number: Episode number (may be None/empty)
        - season_number: Season number (may be None/empty)
        - Saved_Path: File path (None initially)
        - Size: File size in bytes (None initially)
        - File_name: Filename (None initially)
    """
    # Store the original feed URL for later metadata extraction
    feed_url = feed if isinstance(feed, str) else None
    
    # Use cached RSS content if feed is a URL string
    if isinstance(feed, str) and (feed.startswith('http://') or feed.startswith('https://')):
        # Validate URL before processing
        is_valid, error = validate_feed_url(feed)
        if not is_valid:
            logger.error(f"Invalid feed URL '{feed}': {error}")
            return pd.DataFrame()
        
        rss_content = get_cached_rss_content(feed)
        if rss_content:
            # Parse from cached content
            feed = feedparser.parse(rss_content)
        else:
            # Fallback to direct parsing if cache fails
            feed = feedparser.parse(feed)
    else:
        # Parse the feed (file path or other)
        feed = feedparser.parse(feed)
    
    # Check if feed parsing was successful
    if feed.bozo and feed.bozo_exception:
        print(f"⚠️  Warning: Feed parsing encountered an error: {feed.bozo_exception}")
    
    # Get the title of the podcast with error handling
    try:
        if hasattr(feed, 'channel') and hasattr(feed.channel, 'title'):
            title_raw = feed.channel.title
        elif hasattr(feed, 'feed') and hasattr(feed.feed, 'title'):
            title_raw = feed.feed.title
        elif hasattr(feed, 'feed') and 'title' in feed.feed:
            title_raw = feed.feed['title']
        else:
            # Try to get title from entries or use default
            if feed.entries and len(feed.entries) > 0:
                # Try to extract from first entry's author or use feed URL
                title_raw = feed.entries[0].get('author', '') or feed.entries[0].get('publisher', '')
                if not title_raw:
                    # Use feed URL as fallback
                    from urllib.parse import urlparse
                    parsed_url = urlparse(feed.get('href', feed) if isinstance(feed, str) else str(feed))
                    title_raw = parsed_url.netloc or parsed_url.path or 'unknown_podcast'
            else:
                # Last resort: use feed URL or default
                title_raw = str(feed) if isinstance(feed, str) else 'unknown_podcast'
            
            print(f"⚠️  Warning: Could not find podcast title in feed. Using: '{title_raw}'")
    except (AttributeError, KeyError) as e:
        print(f"⚠️  Error extracting podcast title: {e}")
        # Use feed URL or default as fallback
        from urllib.parse import urlparse
        if isinstance(feed, str):
            parsed_url = urlparse(feed)
            title_raw = parsed_url.netloc or parsed_url.path or 'unknown_podcast'
        else:
            title_raw = 'unknown_podcast'
        print(f"⚠️  Using fallback title: '{title_raw}'")
    
    # Save the raw title before normalizing
    author_raw = title_raw
    
    # Extract podcast image URL
    podcast_image_url = None
    try:
        if hasattr(feed, 'channel') and hasattr(feed.channel, 'image'):
            if hasattr(feed.channel.image, 'href'):
                podcast_image_url = feed.channel.image.href
            elif hasattr(feed.channel.image, 'url'):
                podcast_image_url = feed.channel.image.url
        elif hasattr(feed, 'feed') and hasattr(feed.feed, 'image'):
            if hasattr(feed.feed.image, 'href'):
                podcast_image_url = feed.feed.image.href
            elif hasattr(feed.feed.image, 'url'):
                podcast_image_url = feed.feed.image.url
        elif 'image' in feed.feed:
            image_data = feed.feed['image']
            if isinstance(image_data, dict):
                podcast_image_url = image_data.get('href', '') or image_data.get('url', '')
            elif isinstance(image_data, str):
                podcast_image_url = image_data
        # Try iTunes image
        if not podcast_image_url:
            if hasattr(feed, 'channel') and hasattr(feed.channel, 'itunes_image'):
                podcast_image_url = feed.channel.itunes_image
            elif hasattr(feed, 'feed') and 'itunes_image' in feed.feed:
                image_data = feed.feed['itunes_image']
                if isinstance(image_data, dict):
                    podcast_image_url = image_data.get('href', '') or image_data.get('url', '')
                elif isinstance(image_data, str):
                    podcast_image_url = image_data
    except Exception as e:
        logger.debug(f"Error extracting podcast image URL: {e}")
    
    # Normalize the title
    title = normalize(title_raw)
    
    # Check if feed has entries
    if not feed.entries or len(feed.entries) == 0:
        print(f"⚠️  Warning: Feed has no entries. Creating empty DataFrame for '{title}'")
        # Return empty DataFrame with expected columns
        df = pd.DataFrame(columns=['title', 'link', 'published', 'summary', 'id', 'link_direct', 
                                   'published_parsed', 'status', 'author', 'author_raw', 'episode_number', 
                                   'season_number', 'Saved_Path', 'Size', 'File_name'])
        df['status'] = EPISODE_STATUS_NOT_DOWNLOADED
        df['author'] = title
        df['author_raw'] = author_raw
        df['episode_number'] = None
        df['season_number'] = None
        df['Saved_Path'] = None
        df['Size'] = None
        df['File_name'] = None
        return df
    
    # Extract only scalar values from feed entries to avoid FeedParserDict issues
    entries_data = []
    for entry in feed.get('entries', []):
        entry_dict = {}
        # Extract common fields as strings/scalars
        entry_dict['title'] = entry.get('title', '')
        entry_dict['link'] = entry.get('link', '')
        entry_dict['published'] = entry.get('published', '')
        entry_dict['summary'] = entry.get('summary', '')
        entry_dict['id'] = entry.get('id', '')
        
        # Handle links - get the direct audio link if available
        if 'links' in entry and entry['links']:
            # Try to get the audio link (usually the second link or one with type='audio')
            audio_link = None
            for link in entry['links']:
                if link.get('type', '').startswith('audio'):
                    audio_link = link.get('href', '')
                    break
            if not audio_link and len(entry['links']) > 1:
                audio_link = entry['links'][1].get('href', '')
            entry_dict['link_direct'] = audio_link if audio_link else (entry['links'][0].get('href', '') if entry['links'] else None)
        else:
            entry_dict['link_direct'] = None
        
        # Handle published_parsed if available (convert to string)
        if 'published_parsed' in entry and entry['published_parsed']:
            try:
                entry_dict['published_parsed'] = pd.Timestamp(*entry['published_parsed']).isoformat()
            except (ValueError, TypeError, IndexError) as e:
                logger.debug(f"Failed to parse published_parsed for entry: {e}")
                entry_dict['published_parsed'] = None
        else:
            entry_dict['published_parsed'] = None
        
        # Extract episode number and season number (iTunes tags)
        # These fields may be empty or null, so we handle them carefully
        episode_number = None
        season_number = None
        
        # Try to get episode number from various possible locations
        if 'itunes_episode' in entry:
            try:
                episode_value = entry.get('itunes_episode', '')
                if episode_value and str(episode_value).strip():
                    # Try to convert to integer, but keep as string if it fails
                    try:
                        episode_number = int(float(str(episode_value).strip()))
                    except (ValueError, TypeError):
                        episode_number = str(episode_value).strip()
            except Exception as e:
                logger.debug(f"Error extracting episode number: {e}")
        
        # Try alternative field names for episode number
        if episode_number is None:
            for field_name in ['episode', 'episode_number', 'episodenumber']:
                if field_name in entry:
                    try:
                        episode_value = entry.get(field_name, '')
                        if episode_value and str(episode_value).strip():
                            try:
                                episode_number = int(float(str(episode_value).strip()))
                            except (ValueError, TypeError):
                                episode_number = str(episode_value).strip()
                            break
                    except Exception:
                        continue
        
        # Try to get season number from various possible locations
        if 'itunes_season' in entry:
            try:
                season_value = entry.get('itunes_season', '')
                if season_value and str(season_value).strip():
                    # Try to convert to integer, but keep as string if it fails
                    try:
                        season_number = int(float(str(season_value).strip()))
                    except (ValueError, TypeError):
                        season_number = str(season_value).strip()
            except Exception as e:
                logger.debug(f"Error extracting season number: {e}")
        
        # Try alternative field names for season number
        if season_number is None:
            for field_name in ['season', 'season_number', 'seasonnumber']:
                if field_name in entry:
                    try:
                        season_value = entry.get(field_name, '')
                        if season_value and str(season_value).strip():
                            try:
                                season_number = int(float(str(season_value).strip()))
                            except (ValueError, TypeError):
                                season_number = str(season_value).strip()
                            break
                    except Exception:
                        continue
        
        entry_dict['episode_number'] = episode_number
        entry_dict['season_number'] = season_number
        
        # Extract episode image URL if available
        episode_image_url = None
        # Try various locations for episode image
        if 'image' in entry and entry['image']:
            if isinstance(entry['image'], dict):
                episode_image_url = entry['image'].get('href', '') or entry['image'].get('url', '')
            elif isinstance(entry['image'], str):
                episode_image_url = entry['image']
        elif 'itunes_image' in entry:
            if isinstance(entry['itunes_image'], dict):
                episode_image_url = entry['itunes_image'].get('href', '') or entry['itunes_image'].get('url', '')
            elif isinstance(entry['itunes_image'], str):
                episode_image_url = entry['itunes_image']
        elif 'media_thumbnail' in entry:
            if isinstance(entry['media_thumbnail'], list) and len(entry['media_thumbnail']) > 0:
                episode_image_url = entry['media_thumbnail'][0].get('url', '')
            elif isinstance(entry['media_thumbnail'], dict):
                episode_image_url = entry['media_thumbnail'].get('url', '')
        
        entry_dict['episode_image_url'] = episode_image_url if episode_image_url else None
        
        entries_data.append(entry_dict)
    
    # Create a dataframe from the cleaned entries
    df = pd.DataFrame(entries_data)
    # Add status and author columns
    df['status'] = EPISODE_STATUS_NOT_DOWNLOADED
    df['author'] = title  # Normalized name
    df['author_raw'] = author_raw  # Original name before normalization
    
    # Ensure episode_number and season_number columns exist (they should already be in entries_data)
    # But set None for any missing values
    if 'episode_number' not in df.columns:
        df['episode_number'] = None
    if 'season_number' not in df.columns:
        df['season_number'] = None
    if 'episode_image_url' not in df.columns:
        df['episode_image_url'] = None
    
    # Replace empty strings with None for episode_number and season_number
    df['episode_number'] = df['episode_number'].replace('', None)
    df['season_number'] = df['season_number'].replace('', None)
    df['episode_image_url'] = df['episode_image_url'].replace('', None)
    
    # Add new fields for download information (will be filled after download)
    df['Saved_Path'] = None
    df['Size'] = None
    df['File_name'] = None
    
    # Store podcast image URL and RSS feed URL as metadata in DataFrame attributes
    if podcast_image_url:
        df.attrs['podcast_image_url'] = podcast_image_url
    if feed_url:
        df.attrs['rss_feed_url'] = feed_url
    
    return df

