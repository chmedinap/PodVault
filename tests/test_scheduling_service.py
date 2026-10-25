"""
Tests for `services.scheduling` (per-podcast auto-refresh, ROADMAP.md Fase 8).
"""

import time
from unittest.mock import patch

from podvault import config, services
from podvault.database.connection import get_db_connection
from podvault.database.jobs import get_job


def _add_podcast(feed_df_factory, episodes=None):
    df = feed_df_factory(episodes=episodes) if episodes else feed_df_factory()
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')


def _wait_for_job(runner_db_path, timeout=5):
    """Wait for at least one job to reach a terminal state, and return it."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with get_db_connection(runner_db_path) as conn:
            cursor = conn.execute("SELECT id FROM jobs ORDER BY created_at DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                job = get_job(conn, row[0])
                if job['status'] in ('success', 'failed'):
                    return job
        time.sleep(0.05)
    raise TimeoutError("no job reached a terminal state in time")


def test_get_schedule_defaults_to_disabled(isolated_env, feed_df_factory):
    _add_podcast(feed_df_factory)
    schedule = services.get_podcast_schedule('test_podcast')
    assert schedule.interval_hours is None
    assert schedule.auto_download is False
    assert schedule.last_checked_at is None


def test_list_schedules_includes_every_podcast(isolated_env, feed_df_factory):
    _add_podcast(feed_df_factory)
    services.set_podcast_schedule('test_podcast', interval_hours=12, auto_download=True)

    schedules = services.list_schedules()
    assert len(schedules) == 1
    assert schedules[0].podcast_name == 'test_podcast'
    assert schedules[0].interval_hours == 12
    assert schedules[0].auto_download is True
    assert schedules[0].total_episodes == 2


def test_list_schedules_empty_database(isolated_env):
    assert services.list_schedules() == []


def test_scheduler_is_running_reflects_thread_state(isolated_env):
    from podvault import scheduler
    assert scheduler.is_running() is False
    scheduler.start_scheduler()
    try:
        assert scheduler.is_running() is True
    finally:
        scheduler.stop_scheduler()


def test_set_schedule_persists(isolated_env, feed_df_factory):
    _add_podcast(feed_df_factory)
    result = services.set_podcast_schedule('test_podcast', interval_hours=12, auto_download=True)
    assert result.success

    schedule = services.get_podcast_schedule('test_podcast')
    assert schedule.interval_hours == 12
    assert schedule.auto_download is True


def test_set_schedule_disable(isolated_env, feed_df_factory):
    _add_podcast(feed_df_factory)
    services.set_podcast_schedule('test_podcast', interval_hours=12, auto_download=True)
    services.set_podcast_schedule('test_podcast', interval_hours=None, auto_download=False)

    schedule = services.get_podcast_schedule('test_podcast')
    assert schedule.interval_hours is None


def test_set_schedule_rejects_nonexistent_podcast(isolated_env):
    result = services.set_podcast_schedule('does_not_exist', interval_hours=12, auto_download=False)
    assert not result.success


def test_set_schedule_rejects_non_positive_interval(isolated_env, feed_df_factory):
    _add_podcast(feed_df_factory)
    result = services.set_podcast_schedule('test_podcast', interval_hours=0, auto_download=False)
    assert not result.success


def test_run_due_checks_skips_podcasts_without_a_schedule(isolated_env, feed_df_factory):
    _add_podcast(feed_df_factory)
    with patch('podvault.services.scheduling.process_single_feed') as mock_process:
        result = services.run_due_checks()
    mock_process.assert_not_called()
    assert result.checked == []


def test_run_due_checks_checks_never_checked_podcast(isolated_env, feed_df_factory):
    _add_podcast(feed_df_factory)
    services.set_podcast_schedule('test_podcast', interval_hours=6, auto_download=False)

    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        result = services.run_due_checks()

    assert len(result.checked) == 1
    assert result.checked[0].podcast_name == 'test_podcast'
    assert result.checked[0].success
    assert not result.checked[0].auto_download_triggered

    schedule = services.get_podcast_schedule('test_podcast')
    assert schedule.last_checked_at is not None


def test_run_due_checks_skips_recently_checked_podcast(isolated_env, feed_df_factory):
    _add_podcast(feed_df_factory)
    services.set_podcast_schedule('test_podcast', interval_hours=6, auto_download=False)

    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        services.run_due_checks()  # first pass: never checked -> due -> sets last_checked_at to now

    with patch('podvault.services.scheduling.process_single_feed') as mock_process:
        result = services.run_due_checks()  # second pass: just checked -> not due

    mock_process.assert_not_called()
    assert result.checked == []


def test_run_due_checks_triggers_auto_download_job(isolated_env, feed_df_factory, mock_download):
    _add_podcast(feed_df_factory)
    services.set_podcast_schedule('test_podcast', interval_hours=6, auto_download=True)

    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()), \
         patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        result = services.run_due_checks()
        job = _wait_for_job(config.DB_PATH)

    assert result.checked[0].auto_download_triggered
    assert job['job_type'] == 'download_all'
    assert job['podcast_name'] == 'test_podcast'
    assert job['status'] == 'success'


def test_list_schedules_includes_display_name(isolated_env, feed_df_factory):
    _add_podcast(feed_df_factory, episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01', 'author_raw': 'The Test Show'},
    ])
    assert services.list_schedules()[0].display_name == 'The Test Show'
