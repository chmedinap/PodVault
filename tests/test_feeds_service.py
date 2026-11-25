"""
Tests for `services.feeds` — in particular a regression test for the
data-loss bug fixed in ROADMAP.md Fase 4: re-processing a feed used to
`to_sql(if_exists='replace')` the whole table, silently wiping every
episode's download status, saved path, and custom title.
"""

from unittest.mock import patch

from podvault import config, services
from podvault.database.connection import get_db_connection
from podvault.database.queries import validate_and_quote_table_name
from podvault.database.schema import add_download_columns_to_table


def _mark_downloaded(podcast_name, episode_id, saved_path, custom_title=None):
    with get_db_connection(config.DB_PATH) as conn:
        add_download_columns_to_table(conn, podcast_name)
        safe_table = validate_and_quote_table_name(podcast_name)
        conn.execute(
            f"UPDATE {safe_table} SET status='downloaded', Saved_Path=?, custom_title=? WHERE id=?",
            (saved_path, custom_title, episode_id),
        )
        conn.commit()


def test_add_feed_creates_podcast(isolated_env, feed_df_factory):
    df = feed_df_factory()
    with patch('podvault.services.feeds.collect_data', return_value=df):
        result = services.add_feed('https://example.com/test_podcast.xml')

    assert result.success
    assert len(result.feeds) == 1
    assert result.feeds[0].podcast_name == 'test_podcast'
    assert result.feeds[0].episodes_found == 2


def test_add_feed_rejects_invalid_url(isolated_env):
    result = services.add_feed('not-a-url')
    assert not result.success
    assert result.error


def test_resyncing_feed_preserves_download_state(isolated_env, feed_df_factory):
    df = feed_df_factory()
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    _mark_downloaded('test_podcast', 'ep-1', '/fake/path/ep1.mp3', custom_title='My Custom Name')

    # Re-sync the exact same feed content
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        result = services.add_feed('https://example.com/test_podcast.xml')

    assert result.success
    episodes = services.list_episodes('test_podcast').episodes
    ep1 = next(e for e in episodes if e.episode_id == 'ep-1')
    assert ep1.status == 'downloaded'
    assert ep1.saved_path == '/fake/path/ep1.mp3'
    assert ep1.custom_title == 'My Custom Name'


def test_resyncing_feed_preserves_missing_status(isolated_env, feed_df_factory):
    df = feed_df_factory()
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    with get_db_connection(config.DB_PATH) as conn:
        add_download_columns_to_table(conn, 'test_podcast')
        conn.execute("UPDATE test_podcast SET status='missing' WHERE id='ep-1'")
        conn.commit()

    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        services.add_feed('https://example.com/test_podcast.xml')

    episodes = services.list_episodes('test_podcast').episodes
    ep1 = next(e for e in episodes if e.episode_id == 'ep-1')
    assert ep1.status == 'missing'


def test_resyncing_feed_adds_new_episodes_without_disturbing_existing(isolated_env, feed_df_factory):
    df = feed_df_factory()
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    _mark_downloaded('test_podcast', 'ep-1', '/fake/ep1.mp3')

    df_with_new_episode = feed_df_factory(episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
        {'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-08'},
        {'id': 'ep-3', 'title': 'Episode Three (new)', 'published': '2024-01-15'},
    ])
    with patch('podvault.services.feeds.collect_data', return_value=df_with_new_episode):
        result = services.add_feed('https://example.com/test_podcast.xml')

    assert result.feeds[0].episodes_found == 3
    episodes = services.list_episodes('test_podcast').episodes
    assert len(episodes) == 3

    ep1 = next(e for e in episodes if e.episode_id == 'ep-1')
    assert ep1.status == 'downloaded'
    assert ep1.saved_path == '/fake/ep1.mp3'

    ep3 = next(e for e in episodes if e.episode_id == 'ep-3')
    assert ep3.status == 'not downloaded'


def test_episode_removed_from_feed_is_kept_and_flagged(isolated_env, feed_df_factory):
    """A podcast delisting an old episode (or a feed shrinking to its last N items)
    must not delete PodVault's record of it — see the 'vault' framing in ROADMAP.md."""
    df = feed_df_factory(episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
        {'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-08'},
    ])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    _mark_downloaded('test_podcast', 'ep-1', '/fake/ep1.mp3')

    shrunk_df = feed_df_factory(episodes=[
        {'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-08'},
    ])
    with patch('podvault.services.feeds.collect_data', return_value=shrunk_df):
        result = services.add_feed('https://example.com/test_podcast.xml')

    assert result.success
    # episodes_found reflects what the feed itself contained, not the merged total
    assert result.feeds[0].episodes_found == 1

    episodes = services.list_episodes('test_podcast').episodes
    assert len(episodes) == 2  # ep-1 is still on record, just no longer live

    ep1 = next(e for e in episodes if e.episode_id == 'ep-1')
    assert ep1.in_feed is False
    assert ep1.status == 'downloaded'  # download state untouched
    assert ep1.saved_path == '/fake/ep1.mp3'

    ep2 = next(e for e in episodes if e.episode_id == 'ep-2')
    assert ep2.in_feed is True


def test_episode_reappearing_in_feed_is_marked_in_feed_again(isolated_env, feed_df_factory):
    df = feed_df_factory(episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
        {'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-08'},
    ])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    shrunk_df = feed_df_factory(episodes=[{'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-08'}])
    with patch('podvault.services.feeds.collect_data', return_value=shrunk_df):
        services.add_feed('https://example.com/test_podcast.xml')

    ep1 = next(e for e in services.list_episodes('test_podcast').episodes if e.episode_id == 'ep-1')
    assert ep1.in_feed is False

    # ep-1 is back in a later fetch (e.g. the podcast un-deleted it)
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    ep1_again = next(e for e in services.list_episodes('test_podcast').episodes if e.episode_id == 'ep-1')
    assert ep1_again.in_feed is True


def test_resyncing_feed_preserves_checksum_for_still_present_episode(isolated_env, feed_df_factory):
    df = feed_df_factory()
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')

    with get_db_connection(config.DB_PATH) as conn:
        add_download_columns_to_table(conn, 'test_podcast')
        conn.execute(
            "UPDATE test_podcast SET status='downloaded', Saved_Path=?, Checksum=?, checksum_status='ok' WHERE id='ep-1'",
            ('/fake/ep1.mp3', 'deadbeef' * 8),
        )
        conn.commit()

    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        services.add_feed('https://example.com/test_podcast.xml')

    with get_db_connection(config.DB_PATH) as conn:
        row = conn.execute("SELECT Checksum, checksum_status FROM test_podcast WHERE id='ep-1'").fetchone()
    assert row[0] == 'deadbeef' * 8
    assert row[1] == 'ok'


def _sync(df, url):
    with patch('podvault.services.feeds.collect_data', return_value=df), get_db_connection(config.DB_PATH) as conn:
        return services.process_single_feed(conn, url)


def test_feed_that_changes_its_title_keeps_its_table(isolated_env, feed_df_factory):
    url = 'https://example.com/show.xml'
    first = feed_df_factory(podcast_name='my_show')
    first.attrs['rss_feed_url'] = url
    assert _sync(first, url).podcast_name == 'my_show'
    _mark_downloaded('my_show', 'ep-1', '/downloads/ep1.mp3')

    renamed = feed_df_factory(podcast_name='my_show_rebrand')
    renamed.attrs['rss_feed_url'] = url
    result = _sync(renamed, url)

    assert result.podcast_name == 'my_show'  # same feed URL -> same podcast, history intact
    with get_db_connection(config.DB_PATH) as conn:
        assert conn.execute("SELECT status FROM my_show WHERE id = 'ep-1'").fetchone()[0] == 'downloaded'
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'my_show_rebrand'").fetchone()


def test_different_feed_with_same_title_gets_its_own_table(isolated_env, feed_df_factory):
    a = feed_df_factory(podcast_name='daily_news')
    a.attrs['rss_feed_url'] = 'https://a.example.com/feed.xml'
    b = feed_df_factory(podcast_name='daily_news')
    b.attrs['rss_feed_url'] = 'https://b.example.com/feed.xml'

    assert _sync(a, 'https://a.example.com/feed.xml').podcast_name == 'daily_news'
    assert _sync(b, 'https://b.example.com/feed.xml').podcast_name == 'daily_news_2'


def test_podcast_named_like_an_internal_table_does_not_overwrite_it(isolated_env, feed_df_factory):
    with get_db_connection(config.DB_PATH) as conn:
        from podvault.database import jobs as jobs_db
        jobs_db.create_job(conn, 'job-1', 'add_feed')

    df = feed_df_factory(podcast_name='jobs')
    result = _sync(df, 'https://example.com/jobs.xml')

    assert result.podcast_name == 'jobs_podcast'
    with get_db_connection(config.DB_PATH) as conn:
        assert conn.execute("SELECT job_type FROM jobs WHERE id = 'job-1'").fetchone()[0] == 'add_feed'


def test_add_feed_result_reflects_only_the_new_feed(isolated_env, feed_df_factory):
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        assert services.add_feed('https://example.com/test_podcast.xml').success

    # A broken feed is not "successful" just because another feed in feeds.txt still syncs
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory().iloc[0:0]) as fetch:
        result = services.add_feed('https://example.com/broken.xml')

    assert not result.success
    assert [f.feed_url for f in result.feeds] == ['https://example.com/broken.xml']
    assert fetch.call_count == 1  # the rest of feeds.txt is not re-synced


def test_feed_split_before_url_matching_keeps_syncing_into_the_original(isolated_env, feed_df_factory):
    # Before 1.3.2 a renamed feed became a second podcast with the same feed URL in `summary`
    url = 'https://example.com/show.xml'
    for name in ('my_show', 'my_show_rebrand'):
        df = feed_df_factory(podcast_name=name)
        df.attrs['rss_feed_url'] = url
        with patch('podvault.services.feeds._table_name_for_feed', return_value=name):
            _sync(df, url)

    renamed_again = feed_df_factory(podcast_name='my_show_new_name')
    renamed_again.attrs['rss_feed_url'] = url
    assert _sync(renamed_again, url).podcast_name == 'my_show'
