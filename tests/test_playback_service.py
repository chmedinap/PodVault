"""Tests for `services.playback` (the web player's backend, ROADMAP.md Fase 9)."""

from pathlib import Path
from unittest.mock import patch

from podvault import services


def _add_and_download(feed_df_factory, mock_download):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all('test_podcast', require_confirmation=False)


def test_get_playback_info_for_downloaded_episode(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download)

    info = services.get_playback_info('test_podcast', 'ep-1')
    assert info.success
    assert info.title == 'Episode One'
    assert info.podcast_name == 'test_podcast'
    assert Path(info.audio_path).exists()
    assert info.cover_path is None  # no image was ever downloaded for this fake episode


def test_get_playback_info_respects_custom_title(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download)
    services.rename_episode('test_podcast', 'ep-1', 'My Custom Name')

    info = services.get_playback_info('test_podcast', 'ep-1')
    assert info.success
    assert info.title == 'My Custom Name'


def test_get_playback_info_falls_back_to_podcast_cover(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download)

    episode = services.list_episodes('test_podcast').episodes[0]
    podcast_folder = Path(episode.saved_path).parent.parent.parent.parent
    (podcast_folder / 'podcast_image.jpg').write_bytes(b'fake image bytes')

    info = services.get_playback_info('test_podcast', 'ep-1')
    assert info.success
    assert info.cover_path == str(podcast_folder / 'podcast_image.jpg')


def test_get_playback_info_prefers_episode_cover_over_podcast_cover(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download)

    episode = services.list_episodes('test_podcast').episodes[0]
    episode_folder = Path(episode.saved_path).parent
    podcast_folder = episode_folder.parent.parent.parent
    (podcast_folder / 'podcast_image.jpg').write_bytes(b'podcast level image')

    episode_image = episode_folder / f"{Path(episode.saved_path).stem}.jpg"
    episode_image.write_bytes(b'episode level image')

    info = services.get_playback_info('test_podcast', 'ep-1')
    assert info.cover_path == str(episode_image)


def test_get_playback_info_rejects_not_yet_downloaded_episode(isolated_env, feed_df_factory):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    info = services.get_playback_info('test_podcast', 'ep-1')
    assert not info.success
    assert info.error


def test_get_playback_info_rejects_missing_file(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download)
    episode = services.list_episodes('test_podcast').episodes[0]
    Path(episode.saved_path).unlink()

    info = services.get_playback_info('test_podcast', 'ep-1')
    assert not info.success


def test_get_playback_info_unknown_episode(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download)
    info = services.get_playback_info('test_podcast', 'does-not-exist')
    assert not info.success


def test_get_playback_info_unknown_podcast(isolated_env):
    info = services.get_playback_info('does_not_exist', 'ep-1')
    assert not info.success
