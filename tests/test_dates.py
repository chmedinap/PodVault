"""
Tests for `podvault.dates` and the places that order episodes by it.

Feeds publish RFC 822 dates ("Wed, 01 Jan 2020 ..."), which SQL sorts
alphabetically by weekday name — so "newest first" picked the wrong
episode for Download latest, the bulk-download order, and every list.
"""

from unittest.mock import patch

from podvault import services
from podvault.dates import format_published, newest_first, parse_published

# Alphabetically by weekday ("Wed" > "Mon" > "Fri") this is the reverse of the real order
EPISODES = [
    {'id': 'ep-old', 'title': 'Oldest', 'published': 'Wed, 01 Jan 2020 10:00:00 +0000'},
    {'id': 'ep-mid', 'title': 'Middle', 'published': 'Mon, 10 Jun 2024 10:00:00 +0000'},
    {'id': 'ep-new', 'title': 'Newest', 'published': 'Fri, 03 Jan 2025 10:00:00 +0000'},
]


def _add_podcast(feed_df_factory):
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory(episodes=EPISODES)):
        services.add_feed('https://example.com/test_podcast.xml')


def test_parse_published_handles_rfc822_and_iso():
    assert parse_published('Fri, 03 Jan 2025 10:00:00 +0000').year == 2025
    assert parse_published('2024-01-08').month == 1
    assert parse_published('not a date') is None
    assert parse_published(None) is None


def test_newest_first_sorts_by_real_date_with_undated_last():
    items = [('a', 'Wed, 01 Jan 2020 10:00:00 +0000'), ('b', None), ('c', 'Fri, 03 Jan 2025 10:00:00 +0000')]
    assert [name for name, _ in newest_first(items, lambda item: item[1])] == ['c', 'a', 'b']


def test_format_published():
    assert format_published('Fri, 03 Jan 2025 10:00:00 +0000') == '2025-01-03'
    assert format_published('sometime') == 'sometime'
    assert format_published(None) == ''


def test_list_episodes_is_newest_first(isolated_env, feed_df_factory):
    _add_podcast(feed_df_factory)
    episodes = services.list_episodes('test_podcast').episodes
    assert [e.episode_id for e in episodes] == ['ep-new', 'ep-mid', 'ep-old']


def test_download_last_downloads_the_newest_episode(isolated_env, feed_df_factory, mock_download):
    _add_podcast(feed_df_factory)
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        assert services.download_last('test_podcast').success

    downloaded = [e.episode_id for e in services.list_episodes('test_podcast').episodes if e.status == 'downloaded']
    assert downloaded == ['ep-new']


def test_download_all_goes_newest_first(isolated_env, feed_df_factory, mock_download):
    _add_podcast(feed_df_factory)
    order = []

    def record(url, file_path, *args, **kwargs):
        order.append(url.rsplit('/', 1)[-1])
        mock_download(url, file_path, *args, **kwargs)

    with patch('podvault.download.downloader._download_with_retry', side_effect=record):
        services.download_all('test_podcast', require_confirmation=False)

    assert order == ['ep-new.mp3', 'ep-mid.mp3', 'ep-old.mp3']
