"""Tests for `services.rename_episode` and `services.list_episodes`."""

from pathlib import Path
from unittest.mock import patch

from podvault import services


def _add_podcast(feed_df_factory):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Original Title', 'published': '2024-01-01'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')


def test_rename_not_yet_downloaded_episode(isolated_env, feed_df_factory):
    _add_podcast(feed_df_factory)

    result = services.rename_episode('test_podcast', 'ep-1', 'Custom Name')
    assert result.success
    assert result.new_title == 'Custom Name'
    assert not result.renamed_on_disk

    episode = services.list_episodes('test_podcast').episodes[0]
    assert episode.custom_title == 'Custom Name'


def test_rename_downloaded_episode_moves_files(isolated_env, feed_df_factory, mock_download):
    _add_podcast(feed_df_factory)
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all('test_podcast', require_confirmation=False)

    before = services.list_episodes('test_podcast').episodes[0]
    assert Path(before.saved_path).exists()

    result = services.rename_episode('test_podcast', 'ep-1', 'Renamed Episode')
    assert result.success
    assert result.renamed_on_disk

    after = services.list_episodes('test_podcast').episodes[0]
    assert after.custom_title == 'Renamed Episode'
    assert Path(after.saved_path).exists()
    assert 'Renamed Episode' in after.saved_path
    assert not Path(before.saved_path).exists()


def test_clear_custom_title_reverts_name_and_files(isolated_env, feed_df_factory, mock_download):
    _add_podcast(feed_df_factory)
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all('test_podcast', require_confirmation=False)

    services.rename_episode('test_podcast', 'ep-1', 'Temp Name')
    result = services.rename_episode('test_podcast', 'ep-1', '')

    assert result.success
    assert result.new_title == 'Original Title'
    episode = services.list_episodes('test_podcast').episodes[0]
    assert episode.custom_title is None
    assert Path(episode.saved_path).exists()


def test_rename_unknown_episode(isolated_env, feed_df_factory):
    _add_podcast(feed_df_factory)
    result = services.rename_episode('test_podcast', 'does-not-exist', 'Whatever')
    assert not result.success
    assert result.error


def test_rename_same_title_is_a_no_op(isolated_env, feed_df_factory):
    _add_podcast(feed_df_factory)
    result = services.rename_episode('test_podcast', 'ep-1', '')
    assert result.success
    assert result.detail == 'no change'


def test_get_episode_not_downloaded(isolated_env, feed_df_factory):
    _add_podcast(feed_df_factory)
    detail = services.get_episode('test_podcast', 'ep-1')
    assert detail.success
    assert detail.display_title == 'Original Title'
    assert detail.status == 'not downloaded'
    assert detail.files == []
    assert detail.duration_seconds is None


def test_get_episode_downloaded_lists_files_on_disk(isolated_env, feed_df_factory, mock_download):
    _add_podcast(feed_df_factory)
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all('test_podcast', require_confirmation=False)

    detail = services.get_episode('test_podcast', 'ep-1')
    assert detail.success
    assert detail.status == 'downloaded'
    assert len(detail.files) == 1
    assert detail.files[0].size_bytes == len(b'fake mp3 content')


def test_get_episode_unknown_episode(isolated_env, feed_df_factory):
    _add_podcast(feed_df_factory)
    detail = services.get_episode('test_podcast', 'does-not-exist')
    assert not detail.success
    assert detail.error


def test_get_episode_unknown_podcast(isolated_env):
    detail = services.get_episode('does_not_exist', 'ep-1')
    assert not detail.success
    assert detail.error
