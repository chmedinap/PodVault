"""
Tests for `services.migrate_layout`. Since the app only ever writes the
current year/month/label folder layout now, an "old layout" file is
simulated by manually moving a freshly-downloaded episode's folder up one
level (dropping the month component) — mirroring what real leftover
files from before Fase 1 look like.
"""

from pathlib import Path
from unittest.mock import patch

from podvault import config, services
from podvault.database.connection import get_db_connection


def _download_one(feed_df_factory, mock_download):
    df = feed_df_factory(episodes=[{'id': 'ep-1', 'title': 'Episode One', 'published': '2024-03-07'}])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all('test_podcast', require_confirmation=False)
    return services.list_episodes('test_podcast').episodes[0]


def _simulate_old_layout(episode):
    """Move `downloads/podcast/year/month/label/` to `downloads/podcast/year/label/`."""
    current_folder = Path(episode.saved_path).parent
    month_folder = current_folder.parent
    year_folder = month_folder.parent
    legacy_folder = year_folder / current_folder.name
    current_folder.rename(legacy_folder)

    legacy_saved_path = legacy_folder / Path(episode.saved_path).name
    with get_db_connection(config.DB_PATH) as conn:
        conn.execute("UPDATE test_podcast SET Saved_Path = ? WHERE id = 'ep-1'", (str(legacy_saved_path),))
        conn.commit()
    return legacy_folder


def test_migrate_layout_dry_run_does_not_touch_disk(isolated_env, feed_df_factory, mock_download):
    episode = _download_one(feed_df_factory, mock_download)
    legacy_folder = _simulate_old_layout(episode)

    result = services.migrate_layout(apply=False)
    assert result.success
    assert result.moves[0].status == 'planned'
    assert legacy_folder.exists(), "dry run must not move anything"


def test_migrate_layout_apply_moves_file_and_updates_db(isolated_env, feed_df_factory, mock_download):
    episode = _download_one(feed_df_factory, mock_download)
    legacy_folder = _simulate_old_layout(episode)

    result = services.migrate_layout(apply=True)
    assert result.success
    assert result.moves[0].status == 'moved'
    assert not legacy_folder.exists()

    new_episode = services.list_episodes('test_podcast').episodes[0]
    new_path = Path(new_episode.saved_path)
    assert new_path.exists()
    assert new_path.parent.parent.name == '03', "episode folder should be nested back under a month directory"


def test_migrate_layout_is_idempotent(isolated_env, feed_df_factory, mock_download):
    episode = _download_one(feed_df_factory, mock_download)
    _simulate_old_layout(episode)

    services.migrate_layout(apply=True)
    second_run = services.migrate_layout(apply=False)

    assert second_run.moves[0].status == 'skipped'
