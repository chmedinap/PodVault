"""
PodVault's own RSS feed per podcast (Fase 24): an RSS 2.0 + iTunes-namespace
feed listing every *downloaded* episode, with enclosures pointing back at
PodVault's own copies. Any podcast app can subscribe to it and play the
archive — including episodes the publisher has since deleted (`in_feed=0`,
Fase 20), which is the point of keeping them.

URLs are built by the web layer (it knows the public base URL and, when a
login is on, the feed access token), passed in as `urls`. Episode ids are
carried in a query parameter rather than the path, because feed GUIDs are
often URLs themselves and a `/` in a path segment can't be routed.

Only episodes whose file is actually on disk are listed — an enclosure that
404s makes many apps mark the whole feed broken. Durations aren't included:
reading every file's header on each feed fetch doesn't scale, and apps
compute it on download anyway.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Callable, Optional

from podvault import __version__, config
from podvault.database.connection import get_db_connection
from podvault.database.queries import table_exists, validate_and_quote_table_name
from podvault.database.schema import add_download_columns_to_table
from podvault.dates import newest_first, parse_published
from podvault.download.id3_tags import find_cover_image_path
from podvault.download.utils import AUDIO_MIME_TYPES, get_effective_title
from podvault.services.podcasts import get_podcast_info
from podvault.validation import validate_podcast_name

ITUNES_NS = 'http://www.itunes.com/dtds/podcast-1.0.dtd'
ATOM_NS = 'http://www.w3.org/2005/Atom'
ET.register_namespace('itunes', ITUNES_NS)
ET.register_namespace('atom', ATOM_NS)


@dataclass
class FeedUrls:
    """URL builders supplied by the web layer (absolute, token included when needed)."""
    feed: Callable[[str], str]                    # podcast_name -> this feed's own URL
    page: Callable[[str], str]                    # podcast_name -> the podcast's page in PodVault
    audio: Callable[[str, str], str]              # (podcast_name, episode_id) -> audio enclosure
    episode_cover: Callable[[str, str], str]      # (podcast_name, episode_id) -> episode image
    podcast_cover: Callable[[str], str]           # podcast_name -> podcast image


def podcast_cover_path(podcast_name: str) -> Optional[Path]:
    """The locally downloaded `podcast_image.*` for a podcast, if any."""
    folder = Path(config.DOWNLOADS_FOLDER) / podcast_name
    if not folder.is_dir():
        return None
    images = sorted(folder.glob('podcast_image.*'))
    return images[0] if images else None


def _sub(parent, tag: str, text=None, ns: Optional[str] = None, **attrs):
    element = ET.SubElement(parent, f'{{{ns}}}{tag}' if ns else tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        element.text = str(text)
    return element


def build_podcast_feed(podcast_name: str, urls: FeedUrls) -> Optional[str]:
    """The RSS XML for one podcast's downloaded episodes, or None if the podcast doesn't exist."""
    is_valid, _error = validate_podcast_name(podcast_name)
    if not is_valid:
        return None

    with get_db_connection() as conn:
        if not table_exists(conn, podcast_name):
            return None
        add_download_columns_to_table(conn, podcast_name)
        safe_table_name = validate_and_quote_table_name(podcast_name)
        rows = conn.execute(f"""
            SELECT id, title, custom_title, summary, published, episode_number, season_number,
                   Saved_Path, Size, link
            FROM {safe_table_name}
            WHERE status = ? AND Saved_Path IS NOT NULL AND Saved_Path != ''
        """, (config.EPISODE_STATUS_DOWNLOADED,)).fetchall()
        summary_row = conn.execute(
            "SELECT podcast_image_url, rss_feed_url FROM summary WHERE name = ?", (podcast_name,)
        ).fetchone()

    info = get_podcast_info(podcast_name)
    display_name = (info.display_name if info else None) or podcast_name
    remote_image = summary_row[0] if summary_row else None
    image_url = urls.podcast_cover(podcast_name) if podcast_cover_path(podcast_name) else remote_image

    rss = ET.Element('rss', version='2.0')
    channel = _sub(rss, 'channel')
    _sub(channel, 'title', display_name)
    _sub(channel, 'link', urls.page(podcast_name))
    _sub(channel, 'description', f"{display_name} — archived copy served by PodVault.")
    _sub(channel, 'generator', f'PodVault {__version__}')
    _sub(channel, 'lastBuildDate', format_datetime(datetime.now(timezone.utc)))
    _sub(channel, 'link', ns=ATOM_NS, href=urls.feed(podcast_name), rel='self', type='application/rss+xml')
    _sub(channel, 'author', display_name, ns=ITUNES_NS)
    _sub(channel, 'block', 'Yes', ns=ITUNES_NS)  # private archive: ask directories not to list it
    if image_url:
        image = _sub(channel, 'image')
        _sub(image, 'url', image_url)
        _sub(image, 'title', display_name)
        _sub(image, 'link', urls.page(podcast_name))
        _sub(channel, 'image', ns=ITUNES_NS, href=image_url)

    for (episode_id, title, custom_title, summary, published, episode_number, season_number,
         saved_path, size, link) in newest_first(rows, lambda row: row[4]):
        published_at = parse_published(published)
        audio_path = Path(saved_path)
        if not audio_path.exists():
            continue
        item = _sub(channel, 'item')
        _sub(item, 'title', get_effective_title(title or '', custom_title))
        _sub(item, 'guid', episode_id, isPermaLink='false')
        if published_at:
            _sub(item, 'pubDate', format_datetime(published_at))
        if summary:
            _sub(item, 'description', summary)
        if link:
            _sub(item, 'link', link)
        try:
            length = size or audio_path.stat().st_size
        except OSError:
            length = 0
        _sub(item, 'enclosure', url=urls.audio(podcast_name, episode_id), length=int(length or 0),
             type=AUDIO_MIME_TYPES.get(audio_path.suffix.lower(), 'audio/mpeg'))
        if episode_number is not None:
            try:
                _sub(item, 'episode', int(float(episode_number)), ns=ITUNES_NS)
            except (TypeError, ValueError):
                pass
        if season_number is not None:
            try:
                _sub(item, 'season', int(float(season_number)), ns=ITUNES_NS)
            except (TypeError, ValueError):
                pass
        cover = find_cover_image_path(audio_path, audio_path.parent)
        if cover and not cover.name.startswith('podcast_image.'):  # only episode-specific art
            _sub(item, 'image', ns=ITUNES_NS, href=urls.episode_cover(podcast_name, episode_id))

    if hasattr(ET, 'indent'):
        ET.indent(rss)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding='unicode') + '\n'
