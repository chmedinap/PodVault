"""
Tests for `services.storage`: disk-usage bucketing, largest-podcasts
aggregate (via `services.list_podcasts`), and the ID3-completeness job
targets. The `fake_download` fixture writes non-MP3 bytes, so real tag
parsing isn't meaningfully testable without a real fixture file — mocking
`id3_tags_complete` directly here is consistent with how download tests
already mock the network layer instead of hitting it.
"""

from pathlib import Path
from unittest.mock import patch

from podvault import config, services


def _add_and_download(feed_df_factory, mock_download, podcast_name='test_podcast', episodes=None):
    df = feed_df_factory(podcast_name=podcast_name, episodes=episodes) if episodes else feed_df_factory(podcast_name=podcast_name)
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed(f'https://example.com/{podcast_name}.xml')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all(podcast_name, require_confirmation=False)


def test_get_disk_usage_buckets_by_extension(isolated_env):
    downloads = Path(config.DOWNLOADS_FOLDER)
    episode_folder = downloads / 'some_podcast' / '2024' / '01'
    episode_folder.mkdir(parents=True)

    (episode_folder / 'episode.mp3').write_bytes(b'x' * 1000)
    (episode_folder / 'episode.jpg').write_bytes(b'x' * 200)
    (episode_folder / 'episode_metadata.xml').write_bytes(b'x' * 50)
    (episode_folder / 'episode.txt').write_bytes(b'x' * 10)

    usage = services.get_disk_usage(str(downloads))

    assert usage.error is None
    assert usage.audio_bytes == 1000
    assert usage.art_metadata_bytes == 250
    assert usage.orphaned_bytes == 10
    assert usage.library_bytes == 1260


def test_get_disk_usage_missing_folder(isolated_env):
    usage = services.get_disk_usage('/no/such/folder/anywhere')
    assert usage.error is not None
    assert usage.library_bytes == 0


def test_largest_podcasts_sorted_by_size(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download, podcast_name='small_pod', episodes=[
        {'id': 'ep-1', 'title': 'Only Episode', 'published': '2024-01-01'},
    ])
    _add_and_download(feed_df_factory, mock_download, podcast_name='big_pod', episodes=[
        {'id': 'ep-1', 'title': 'Ep 1', 'published': '2024-01-01'},
        {'id': 'ep-2', 'title': 'Ep 2', 'published': '2024-01-02'},
    ])

    podcasts = services.list_podcasts().podcasts
    by_size = sorted(podcasts, key=lambda p: p.total_size_bytes or 0, reverse=True)
    assert by_size[0].name == 'big_pod'
    assert by_size[0].total_size_bytes > by_size[1].total_size_bytes


def test_run_id3_check_counts_complete_files(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download, episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
        {'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-02'},
    ])

    with patch('podvault.services.storage.id3_tags_complete', side_effect=[True, False]):
        message = services.run_id3_check()

    assert message == '1 of 2 complete'


def test_run_id3_check_empty_library(isolated_env):
    assert services.run_id3_check() == '0 of 0 complete'


def test_run_id3_retag_only_touches_incomplete_files(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download, episodes=[
        {'id': 'ep-1', 'title': 'Complete Episode', 'published': '2024-01-01'},
        {'id': 'ep-2', 'title': 'Incomplete Episode', 'published': '2024-01-02'},
    ])

    with patch('podvault.services.storage.id3_tags_complete', side_effect=[True, False]), \
         patch('podvault.services.storage.update_episode_id3_tags_from_db', return_value=True) as mock_update:
        message = services.run_id3_retag()

    assert mock_update.call_count == 1
    assert message == 'retagged 1 of 1'
