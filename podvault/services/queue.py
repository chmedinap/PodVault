"""
Estimated (not live/byte-level) progress math for the Queue page (Fase 16).

Deliberately does not touch `download/downloader.py`'s hot streaming loop or
add any extra SQLite writes per chunk — everything here is derived after
the fact from data every other page already reads: the `jobs` table's
`created_at`/`progress`/`total` columns, and a podcast's average episode
size (`total_size_bytes / downloaded_episodes`, from `services.podcasts`).
Real byte-level speed/ETA would need a progress callback threaded through
the download loop itself; that's explicitly out of scope for v1 (see
ROADMAP.md Fase 16). Anywhere this shows up in the UI, label it "~estimated".
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class QueueEstimate:
    completed_count: int
    remaining_count: int
    elapsed_seconds: float
    seconds_per_episode: Optional[float] = None
    eta_seconds: Optional[float] = None
    bytes_per_second: Optional[float] = None
    bytes_done_estimate: Optional[int] = None
    bytes_total_estimate: Optional[int] = None


def estimate_progress(
    created_at: str,
    progress: int,
    total: int,
    avg_episode_size_bytes: Optional[float],
    now: Optional[datetime] = None,
) -> QueueEstimate:
    """
    Build a rough progress estimate for a running `download_all`/`download_last` job.

    `progress` is the 1-based index of the episode currently being processed
    (see `download_all_episodes`'s `progress_callback` calls), so
    `progress - 1` episodes are already finished. Both time-per-episode and
    bytes-per-second are simple elapsed/count averages, not sampled per-chunk.
    """
    now = now or datetime.now(timezone.utc)
    elapsed_seconds = 0.0
    try:
        started = datetime.fromisoformat(created_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed_seconds = max((now - started).total_seconds(), 0.0)
    except (TypeError, ValueError):
        pass

    completed_count = max(progress - 1, 0)
    remaining_count = max(total - completed_count, 0)

    estimate = QueueEstimate(completed_count=completed_count, remaining_count=remaining_count, elapsed_seconds=elapsed_seconds)

    if completed_count > 0 and elapsed_seconds > 0:
        estimate.seconds_per_episode = elapsed_seconds / completed_count
        estimate.eta_seconds = remaining_count * estimate.seconds_per_episode

    if avg_episode_size_bytes:
        estimate.bytes_done_estimate = int(completed_count * avg_episode_size_bytes)
        estimate.bytes_total_estimate = int(total * avg_episode_size_bytes)
        if estimate.seconds_per_episode:
            estimate.bytes_per_second = avg_episode_size_bytes / estimate.seconds_per_episode

    return estimate
