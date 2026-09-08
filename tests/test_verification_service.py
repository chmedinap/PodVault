"""Tests for `services.verify_files`."""

from pathlib import Path
from unittest.mock import patch

from podvault import services


def _add_and_download(feed_df_factory, mock_download):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all('test_podcast', require_confirmation=False)


def test_verify_files_clean_state(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download)

    result = services.verify_files('test_podcast')
    assert result.success
    assert result.total_missing == 0


def test_verify_files_marks_missing_when_file_deleted(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download)

    episode = services.list_episodes('test_podcast').episodes[0]
    assert episode.status == 'downloaded'
    Path(episode.saved_path).unlink()

    result = services.verify_files('test_podcast')
    assert result.success
    assert result.total_missing == 1

    episode_after = services.list_episodes('test_podcast').episodes[0]
    assert episode_after.status == 'missing'


def test_verify_files_unknown_podcast(isolated_env):
    result = services.verify_files('does_not_exist')
    assert not result.success
    assert result.error
