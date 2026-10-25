"""
Parsing and ordering of feed publication dates.

Feeds store `published` as RFC 822 ("Mon, 01 Jan 2024 10:00:00 +0000") or ISO
strings, so SQL `ORDER BY published` sorts them alphabetically by weekday name.
Anything that needs "newest first" fetches the rows and sorts with
`newest_first()` instead.
"""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Iterable, List, Optional, TypeVar

import pandas as pd

T = TypeVar('T')

_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def parse_published(published: Optional[str]) -> Optional[datetime]:
    """Parse an RFC 822 or ISO date string into an aware datetime (UTC if no zone), or None."""
    if not published:
        return None
    try:
        parsed = parsedate_to_datetime(published)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        ts = pd.to_datetime(published, utc=True)
        return None if pd.isna(ts) else ts.to_pydatetime()
    except (ValueError, TypeError, OverflowError):
        return None


def newest_first(items: Iterable[T], published: Callable[[T], Optional[str]]) -> List[T]:
    """Sort `items` by their parsed publication date, newest first; undated items go last."""
    return sorted(items, key=lambda item: parse_published(published(item)) or _OLDEST, reverse=True)


def format_published(published: Optional[str]) -> str:
    """'2024-01-15' for display, falling back to the raw string when it can't be parsed."""
    parsed = parse_published(published)
    return parsed.strftime('%Y-%m-%d') if parsed else (published or '')
