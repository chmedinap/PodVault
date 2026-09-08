"""
Tests for `services.downloads`. The `require_confirmation=False` path is
the highest-value thing to cover here: `download_all_episodes` normally
blocks on an interactive `input()` prompt, and a regression there would
hang a background job (or this test) forever instead of failing loudly.
"""

from pathlib import Path
from unittest.mock import patch

from podvault import services


def _add_podcast(feed_df_factory, episodes=None):
    df = feed_df_factory(episodes=episodes) if episodes else feed_df_factory()
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')


def test_download_all_no_confirmation_prompt(isolated_env, feed_df_factory, mock_download):
    _add_podcast(feed_df_factory, episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
    ])

    progress_calls = []
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        result = services.download_all(
            'test_podcast',
            require_confirmation=False,
            progress_callback=lambda cur, total, msg: progress_calls.append((cur, total, msg)),
        )

    assert result.success
    assert result.downloaded == 1
    assert progress_calls, "progress_callback should have been invoked at least once"

    episode = services.list_episodes('test_podcast').episodes[0]
    assert episode.status == 'downloaded'
    assert episode.saved_path is not None
    assert Path(episode.saved_path).exists()


def test_download_last(isolated_env, feed_df_factory, mock_download):
    _add_podcast(feed_df_factory, episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
    ])

    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        result = services.download_last('test_podcast')

    assert result.success
    assert result.downloaded == 1
    episode = services.list_episodes('test_podcast').episodes[0]
    assert episode.status == 'downloaded'


def test_download_all_rejects_malformed_podcast_name(isolated_env):
    result = services.download_all('bad name!!', require_confirmation=False)
    assert not result.success
    assert result.error


def test_download_all_reports_podcast_not_found(isolated_env, feed_df_factory):
    # DB exists (from adding a different podcast) but the requested one doesn't
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        services.add_feed('https://example.com/test_podcast.xml')

    result = services.download_all('some_other_podcast', require_confirmation=False)
    assert not result.success
    assert 'not found' in result.error.lower()


def test_download_episode_fetches_exactly_one(isolated_env, feed_df_factory, mock_download):
    _add_podcast(feed_df_factory, episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
        {'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-02'},
    ])

    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        result = services.download_episode('test_podcast', 'ep-1')

    assert result.success
    assert result.downloaded == 1
    assert result.total == 1

    episodes = {e.episode_id: e for e in services.list_episodes('test_podcast').episodes}
    assert episodes['ep-1'].status == 'downloaded'
    assert Path(episodes['ep-1'].saved_path).exists()
    # The other pending episode of the same podcast must be left untouched —
    # this is the whole point of a true single-episode download vs. re-running
    # the entire `download_all` backlog.
    assert episodes['ep-2'].status != 'downloaded'


def test_download_episode_redownloads_already_downloaded_episode(isolated_env, feed_df_factory, mock_download):
    _add_podcast(feed_df_factory, episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
    ])
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        first = services.download_episode('test_podcast', 'ep-1')
        second = services.download_episode('test_podcast', 'ep-1')

    assert first.success
    assert second.success


def test_download_episode_rejects_malformed_podcast_name(isolated_env):
    result = services.download_episode('bad name!!', 'ep-1')
    assert not result.success
    assert result.error


def test_download_episode_reports_podcast_not_found(isolated_env, feed_df_factory):
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        services.add_feed('https://example.com/test_podcast.xml')

    result = services.download_episode('some_other_podcast', 'ep-1')
    assert not result.success
    assert 'not found' in result.error.lower()


def test_download_episode_respects_cancelled_job_control(isolated_env, feed_df_factory):
    from podvault.jobs import JobControl

    _add_podcast(feed_df_factory, episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
    ])
    control = JobControl()
    control.cancel()

    result = services.download_episode('test_podcast', 'ep-1', job_control=control)
    assert not result.success
    assert 'cancel' in result.error.lower()


def test_download_all_cancel_between_episodes_keeps_finished_ones(isolated_env, feed_df_factory):
    """
    Cancelling a `download_all` job only stops the *next* episode from
    starting — an episode already in flight/finished when cancel() is called
    is kept, matching the UI's "Finished episodes are kept" copy (Fase 22).
    `download_all_episodes` processes episodes `published DESC`, so 'ep-2'
    (newer) goes first; cancelling from inside its download call should let
    'ep-2' finish but stop 'ep-1' from ever starting.
    """
    from podvault.jobs import JobControl

    _add_podcast(feed_df_factory, episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
        {'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-02'},
    ])
    control = JobControl()

    def fake_download_then_cancel(url, file_path, *args, **kwargs):
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        Path(file_path).write_bytes(b'fake mp3 content')
        control.cancel()

    with patch('podvault.download.downloader._download_with_retry', side_effect=fake_download_then_cancel):
        result = services.download_all('test_podcast', require_confirmation=False, job_control=control)

    assert control.is_cancelled()
    assert result.success  # one episode did finish before the cancel took effect
    assert result.downloaded == 1
    episodes = {e.episode_id: e for e in services.list_episodes('test_podcast').episodes}
    assert episodes['ep-2'].status == 'downloaded'
    assert episodes['ep-1'].status != 'downloaded'


def test_download_all_skip_removes_one_episode_from_the_run(isolated_env, feed_df_factory, mock_download):
    from podvault.jobs import JobControl

    _add_podcast(feed_df_factory, episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
        {'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-02'},
    ])
    control = JobControl()
    control.skip('ep-1')

    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all('test_podcast', require_confirmation=False, job_control=control)

    episodes = {e.episode_id: e for e in services.list_episodes('test_podcast').episodes}
    assert episodes['ep-2'].status == 'downloaded'
    assert episodes['ep-1'].status != 'downloaded'
