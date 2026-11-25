"""A download that breaks off must never leave a file at the final path (it would later count as downloaded)."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from podvault.download import downloader


def test_interrupted_download_leaves_no_file(tmp_path):
    def broken_stream(chunk_size):
        yield b'first half'
        raise requests.exceptions.ChunkedEncodingError('connection dropped')

    response = MagicMock(headers={}, iter_content=broken_stream)
    target = tmp_path / 'episode.mp3'

    with patch('podvault.download.downloader.requests.get', return_value=response), \
         patch('podvault.download.downloader.time.sleep'):
        with pytest.raises(requests.RequestException):
            downloader._download_with_retry('https://example.com/ep.mp3', target)

    assert list(tmp_path.iterdir()) == []  # neither episode.mp3 nor a leftover episode.mp3.part


def test_complete_download_lands_at_final_path(tmp_path):
    response = MagicMock(headers={}, iter_content=lambda chunk_size: iter([b'whole file']))
    target = tmp_path / 'episode.mp3'

    with patch('podvault.download.downloader.requests.get', return_value=response):
        downloader._download_with_retry('https://example.com/ep.mp3', target)

    assert target.read_bytes() == b'whole file'
    assert list(tmp_path.iterdir()) == [target]


def test_failed_episode_keeps_the_batch_of_earlier_successes(isolated_env, feed_df_factory, mock_download):
    from podvault import services

    df = feed_df_factory(episodes=[
        {'id': 'ep-new', 'title': 'Newer', 'published': '2024-01-08'},
        {'id': 'ep-old', 'title': 'Older', 'published': '2024-01-01'},
    ])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    def first_ok_then_network_error(url, file_path):
        if 'ep-old' in url:
            raise requests.ConnectionError('network down')
        mock_download(url, file_path)

    with patch('podvault.download.downloader._download_with_retry', side_effect=first_ok_then_network_error), \
         patch('podvault.download.downloader.time.sleep'):
        services.download_all('test_podcast', require_confirmation=False)

    statuses = {e.episode_id: e.status for e in services.list_episodes('test_podcast').episodes}
    assert statuses == {'ep-new': 'downloaded', 'ep-old': 'not downloaded'}


def test_connection_dropped_mid_stream_is_retried(tmp_path):
    def broken_stream(chunk_size):
        yield b'first half'
        raise requests.exceptions.ChunkedEncodingError('connection dropped')

    broken = MagicMock(headers={}, iter_content=broken_stream)
    whole = MagicMock(headers={}, iter_content=lambda chunk_size: iter([b'whole file']))
    target = tmp_path / 'episode.mp3'

    with patch('podvault.download.downloader.requests.get', side_effect=[broken, whole]), \
         patch('podvault.download.downloader.time.sleep'):
        downloader._download_with_retry('https://example.com/ep.mp3', target)

    assert target.read_bytes() == b'whole file'
