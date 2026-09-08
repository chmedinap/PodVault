"""
Tests for `services.integrity.run_integrity_check` — the background job
that re-hashes downloaded episodes and compares them against the baseline
checksum stamped at download time (see downloader.py / test_hashing.py).
"""

from pathlib import Path
from unittest.mock import patch

from podvault import config, services
from podvault.database.connection import get_db_connection
from podvault.database.queries import validate_and_quote_table_name


def _add_and_download(feed_df_factory, mock_download, episodes=None):
    df = feed_df_factory(episodes=episodes) if episodes else feed_df_factory()
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all('test_podcast', require_confirmation=False)


def test_run_integrity_check_verifies_unmodified_files(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download, episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
    ])

    message = services.run_integrity_check()

    assert '1 verified' in message
    assert '0 newly hashed' in message
    assert '0 corrupted' in message


def test_run_integrity_check_detects_corruption(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download, episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
    ])
    episode = services.list_episodes('test_podcast').episodes[0]
    original_detail = services.get_episode('test_podcast', 'ep-1')
    original_checksum = original_detail.checksum
    assert original_checksum is not None

    # Tamper with the file after it was downloaded and hashed.
    Path(episode.saved_path).write_bytes(b'corrupted bytes, not the original download')

    message = services.run_integrity_check()
    assert '1 corrupted' in message

    detail = services.get_episode('test_podcast', 'ep-1')
    assert detail.checksum_status == 'corrupt'
    # the original good checksum is preserved, not overwritten by the bad read
    assert detail.checksum == original_checksum


def test_run_integrity_check_backfills_missing_checksum(isolated_env, feed_df_factory, mock_download):
    """Episodes downloaded before this column existed have no Checksum yet — the
    job should hash them as a new baseline instead of treating them as corrupt."""
    _add_and_download(feed_df_factory, mock_download, episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
    ])

    with get_db_connection(config.DB_PATH) as conn:
        safe_table = validate_and_quote_table_name('test_podcast')
        conn.execute(f"UPDATE {safe_table} SET Checksum = NULL, checksum_status = NULL WHERE id = 'ep-1'")
        conn.commit()

    message = services.run_integrity_check()
    assert '1 newly hashed' in message

    detail = services.get_episode('test_podcast', 'ep-1')
    assert detail.checksum_status == 'ok'
    assert detail.checksum is not None


def test_run_integrity_check_empty_library(isolated_env):
    assert services.run_integrity_check() == '0 verified, 0 newly hashed, 0 corrupted (of 0)'
