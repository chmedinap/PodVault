"""Shared Jinja2Templates instance, split out to avoid a routes<->app import cycle."""

import html
import re
from pathlib import Path

from fastapi.templating import Jinja2Templates

from podvault import __version__, config
from podvault.dates import format_published
from podvault.web.auth import login_enabled

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def cover_gradient(name: str) -> str:
    """
    A stable diagonal-stripe gradient derived from `name`'s hash, used as a
    cover-art placeholder for podcasts/episodes with no artwork — same
    podcast always gets the same color instead of a plain gray box.
    """
    hue = sum(ord(c) for c in (name or '')) % 360
    return (
        f"repeating-linear-gradient(135deg, "
        f"oklch(0.44 0.065 {hue}) 0 11px, oklch(0.31 0.055 {hue}) 11px 22px)"
    )


def format_size(num_bytes) -> str:
    """Human-readable byte count, e.g. 48,283,000 -> '46.1 MB'."""
    if not num_bytes:
        return '0 B'
    size = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f"{int(size)} {unit}" if unit == 'B' else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def format_duration(seconds) -> str:
    """Seconds -> 'h:mm:ss' or 'm:ss', matching how podcast apps usually show episode length."""
    if seconds is None:
        return '—'
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


_BLOCK_BREAK = re.compile(r'<\s*(br|/p|/div|/ul|/ol|/h[1-6]|/tr)\b[^>]*>', re.IGNORECASE)
_LIST_ITEM = re.compile(r'<\s*li\b[^>]*>', re.IGNORECASE)
_TAG = re.compile(r'<[^>]+>')


def html_to_text(value) -> str:
    """
    Feed descriptions are usually HTML. Reduce one to plain text (line breaks
    kept, tags dropped, entities decoded) for display; the template still
    autoescapes the result, so no feed markup reaches the page.
    """
    if not value:
        return ''
    text = _BLOCK_BREAK.sub('\n', str(value))
    text = _LIST_ITEM.sub('\n• ', text)
    text = html.unescape(_TAG.sub('', text)).replace('\xa0', ' ')
    lines = [' '.join(line.split()) for line in text.split('\n')]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


templates.env.globals['app_version'] = __version__
templates.env.globals['cover_gradient'] = cover_gradient
# Callables, not values: config is read per render so tests can monkeypatch it
templates.env.globals['login_enabled'] = login_enabled
templates.env.globals['login_username'] = lambda: config.WEB_USERNAME or ''
templates.env.filters['filesize'] = format_size
templates.env.filters['duration'] = format_duration
templates.env.filters['published_date'] = format_published
templates.env.filters['plain_text'] = html_to_text
