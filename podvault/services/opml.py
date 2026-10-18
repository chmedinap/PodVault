"""
OPML import/export (Fase 24) — the standard way podcast apps move a
subscription list around (Apple Podcasts, Overcast, Pocket Casts,
AntennaPod, ... all read and write it).

- `export_opml()` lists every podcast's original RSS feed, so the library's
  subscriptions can be taken to another app.
- `export_opml(own_feeds=True, ...)` lists PodVault's *own* per-podcast
  feeds instead (services/local_feed.py), so a podcast app can subscribe to
  the whole local archive in one import.
- `parse_opml()` / `import_opml()` read a file exported elsewhere and add
  every feed not already listed, through the same feeds-file + sync path
  `add_feed` uses.

OPML files come from other apps or the internet, so parsing is defensive:
a size cap, and any DOCTYPE/ENTITY declaration is rejected outright (the
billion-laughs / external-entity tricks both need one; real OPML never
does), before handing the bytes to ElementTree.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import List, Optional, Tuple

from podvault import __version__
from podvault.database.connection import get_db_connection
from podvault.logging_config import setup_logging
from podvault.services.feeds import append_to_feeds_file, process_feed_urls, read_feeds_file
from podvault.services.podcasts import list_podcasts
from podvault.services.results import OpmlImportResult
from podvault.validation import validate_feed_url

logger = setup_logging(__name__)

MAX_OPML_BYTES = 5 * 1024 * 1024


@dataclass
class OpmlOutline:
    title: str
    xml_url: str


@dataclass
class _ParsedOpml:
    outlines: List[OpmlOutline] = field(default_factory=list)
    error: Optional[str] = None


def _subscriptions() -> List[Tuple[str, str, Optional[str]]]:
    """(podcast_name, display_name, original rss_feed_url) for every podcast in the library."""
    podcasts = list_podcasts()
    if not podcasts.success:
        return []
    with get_db_connection() as conn:
        urls = dict(conn.execute("SELECT name, rss_feed_url FROM summary").fetchall())
    return [(p.name, p.display_name or p.name, urls.get(p.name)) for p in podcasts.podcasts]


def export_opml(own_feeds: bool = False, feed_url_for=None) -> str:
    """
    An OPML 2.0 document of the library's subscriptions.

    own_feeds: list PodVault's own feed for each podcast instead of the
        original — `feed_url_for(podcast_name) -> str` must then build it
        (the web layer knows the public base URL and access token).
    """
    root = ET.Element('opml', version='2.0')
    head = ET.SubElement(root, 'head')
    ET.SubElement(head, 'title').text = 'PodVault archive' if own_feeds else 'PodVault subscriptions'
    ET.SubElement(head, 'dateCreated').text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(head, 'docs').text = 'http://opml.org/spec2.opml'
    ET.SubElement(head, 'ownerName').text = f'PodVault {__version__}'
    body = ET.SubElement(root, 'body')

    for name, display_name, rss_url in sorted(_subscriptions(), key=lambda s: s[1].lower()):
        url = feed_url_for(name) if own_feeds else rss_url
        if not url:
            continue
        ET.SubElement(body, 'outline', type='rss', text=display_name, title=display_name, xmlUrl=url)

    if hasattr(ET, 'indent'):  # Python 3.9+; output is still valid without it
        ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding='unicode') + '\n'


def parse_opml(content: bytes) -> _ParsedOpml:
    """Every `<outline xmlUrl=...>` in `content`, at any nesting depth (apps often group feeds in folders)."""
    if not content:
        return _ParsedOpml(error="The file is empty.")
    if len(content) > MAX_OPML_BYTES:
        return _ParsedOpml(error=f"The file is too large (limit {MAX_OPML_BYTES // (1024 * 1024)} MB).")
    head = content[:4096].lower()
    if b'<!doctype' in head or b'<!entity' in content.lower():
        return _ParsedOpml(error="OPML files with DOCTYPE/ENTITY declarations aren't accepted.")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        return _ParsedOpml(error=f"Not a valid OPML/XML file: {e}")
    if root.tag.lower() != 'opml':
        return _ParsedOpml(error="Not an OPML file (the root element isn't <opml>).")

    outlines = []
    for outline in root.iter('outline'):
        attrs = {k.lower(): v for k, v in outline.attrib.items()}
        xml_url = (attrs.get('xmlurl') or '').strip()
        if xml_url:
            title = (attrs.get('title') or attrs.get('text') or xml_url).strip()
            outlines.append(OpmlOutline(title=title, xml_url=xml_url))
    return _ParsedOpml(outlines=outlines)


def import_opml(content: bytes, progress_callback=None, job_control=None) -> OpmlImportResult:
    """
    Add every valid feed in an OPML file that isn't already in the feeds
    file, then sync just those new ones. Feeds already listed are left
    alone (they're synced by the normal refresh), so re-importing the same
    file is harmless. `job_control` is accepted for the JobRunner calling
    convention; feed syncs are short enough not to need it.
    """
    parsed = parse_opml(content)
    if parsed.error:
        return OpmlImportResult(error=parsed.error)

    result = OpmlImportResult(found=len(parsed.outlines))
    if not parsed.outlines:
        result.error = "No podcast feeds found in this OPML file."
        return result

    valid_urls = []
    for outline in parsed.outlines:
        is_valid, error = validate_feed_url(outline.xml_url)
        if is_valid:
            valid_urls.append(outline.xml_url)
        else:
            result.invalid.append(f"{outline.title}: {error}")

    already = set(read_feeds_file())
    new_urls = append_to_feeds_file(valid_urls)
    result.already_listed = len([u for u in set(valid_urls) if u in already])
    result.added = new_urls

    if new_urls:
        result.feeds = process_feed_urls(new_urls, progress_callback=progress_callback).feeds
    logger.info(f"OPML import: {result}")
    return result
