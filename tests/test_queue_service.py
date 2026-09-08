"""
Tests for `services.queue.estimate_progress` (Fase 16's estimated, non-live
speed/ETA math) and `services.preview_pending`. Fully deterministic against
fake timestamps/sizes — no real downloads, no mocked network.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from podvault import services
from podvault.services.queue import estimate_progress


def _iso(dt):
    return dt.isoformat()


def test_estimate_progress_before_any_tick():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    created_at = _iso(now)

    estimate = estimate_progress(created_at, progress=1, total=10, avg_episode_size_bytes=1_000_000, now=now)

    assert estimate.completed_count == 0
    assert estimate.remaining_count == 10
    assert estimate.seconds_per_episode is None
    assert estimate.eta_seconds is None
    assert estimate.bytes_per_second is None


def test_estimate_progress_midway():
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    now = started + timedelta(seconds=100)  # 100s elapsed
    created_at = _iso(started)

    # progress=6 -> 5 episodes completed in 100s -> 20s/episode
    estimate = estimate_progress(created_at, progress=6, total=10, avg_episode_size_bytes=2_000_000, now=now)

    assert estimate.completed_count == 5
    assert estimate.remaining_count == 5
    assert estimate.seconds_per_episode == 20.0
    assert estimate.eta_seconds == 100.0  # 5 remaining * 20s
    assert estimate.bytes_done_estimate == 10_000_000  # 5 * 2MB
    assert estimate.bytes_total_estimate == 20_000_000  # 10 * 2MB
    assert estimate.bytes_per_second == 100_000.0  # 2MB / 20s


def test_estimate_progress_without_size_data():
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    now = started + timedelta(seconds=60)
    estimate = estimate_progress(_iso(started), progress=3, total=5, avg_episode_size_bytes=None, now=now)

    assert estimate.seconds_per_episode == 30.0  # 2 completed in 60s
    assert estimate.eta_seconds == 90.0  # 3 remaining * 30s
    assert estimate.bytes_per_second is None
    assert estimate.bytes_done_estimate is None


def test_estimate_progress_handles_unparseable_timestamp():
    estimate = estimate_progress('not-a-timestamp', progress=3, total=5, avg_episode_size_bytes=1000)
    assert estimate.elapsed_seconds == 0.0
    assert estimate.completed_count == 2
    # zero elapsed time -> no rate can be derived
    assert estimate.seconds_per_episode is None


def test_estimate_progress_never_goes_negative():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    estimate = estimate_progress(_iso(now), progress=0, total=3, avg_episode_size_bytes=None, now=now)
    assert estimate.completed_count == 0
    assert estimate.remaining_count == 3


def test_preview_pending_excludes_downloaded_and_matches_publish_order(isolated_env, feed_df_factory, mock_download):
    episodes = [
        {'id': 'ep-1', 'title': 'Oldest', 'published': '2024-01-01'},
        {'id': 'ep-2', 'title': 'Middle', 'published': '2024-01-08'},
        {'id': 'ep-3', 'title': 'Newest', 'published': '2024-01-15'},
    ]
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory(episodes=episodes)):
        services.add_feed('https://example.com/test_podcast.xml')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_last('test_podcast')  # downloads 'Newest' (most recent published)

    pending = services.preview_pending('test_podcast')
    assert [e.title for e in pending] == ['Middle', 'Oldest']


def test_preview_pending_unknown_podcast_returns_empty(isolated_env):
    assert services.preview_pending('does_not_exist') == []
