"""
Shared pytest fixtures.

Tests never touch the real `podcast_data.db`/`downloads/` in the repo:
`isolated_env` points `config.DB_PATH`/`config.DOWNLOADS_FOLDER` at a
per-test temp directory and resets the process-wide JobRunner singleton
so it picks up that path too (it otherwise caches the DB path from
whichever test constructs it first).
"""

import pandas as pd
import pytest

import podvault.jobs as jobs_module
from podvault import config


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Point the app at a throwaway DB/downloads folder and cwd, per test."""
    monkeypatch.setattr(config, 'DB_PATH', str(tmp_path / 'test_podcast_data.db'))
    monkeypatch.setattr(config, 'DOWNLOADS_FOLDER', str(tmp_path / 'downloads'))
    monkeypatch.setattr(config, 'DEFAULT_DELAY_SECONDS', 0)
    # Don't inherit a developer's PODVAULT_USERNAME/PASSWORD/BACKUP_DIR from the environment
    monkeypatch.setattr(config, 'WEB_USERNAME', None)
    monkeypatch.setattr(config, 'WEB_PASSWORD', None)
    monkeypatch.setattr(config, 'BACKUP_DIR', None)
    monkeypatch.setattr(config, 'BACKUP_ENABLED', True)
    monkeypatch.chdir(tmp_path)

    if jobs_module._default_runner is not None:
        jobs_module._default_runner.shutdown(wait=False)
    monkeypatch.setattr(jobs_module, '_default_runner', None)

    return tmp_path


def make_feed_dataframe(podcast_name='test_podcast', episodes=None):
    """
    Build a DataFrame shaped like `collect_data()`'s output, for tests
    that mock out the network call feed parsing would otherwise need.
    """
    if episodes is None:
        episodes = [
            {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
            {'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-08'},
        ]

    rows = []
    for ep in episodes:
        rows.append({
            'title': ep['title'],
            'link': ep.get('link', ''),
            'link_direct': ep.get('link_direct', f"https://example.com/{ep['id']}.mp3"),
            'published': ep['published'],
            'published_parsed': ep['published'],
            'summary': ep.get('summary', ''),
            'id': ep['id'],
            'status': 'not downloaded',
            'author': podcast_name,
            'author_raw': ep.get('author_raw', podcast_name),
            'episode_number': ep.get('episode_number'),
            'season_number': ep.get('season_number'),
            'episode_image_url': ep.get('episode_image_url'),
            'Saved_Path': None,
            'Size': None,
            'File_name': None,
        })

    df = pd.DataFrame(rows)
    df.attrs['rss_feed_url'] = f'https://example.com/{podcast_name}.xml'
    df.attrs['podcast_image_url'] = None
    return df


@pytest.fixture
def feed_df_factory():
    """Factory fixture: feed_df_factory(podcast_name=..., episodes=[...]) -> DataFrame."""
    return make_feed_dataframe


def fake_download(url, file_path, *args, **kwargs):
    """Drop-in replacement for `downloader._download_with_retry`: writes a stub file instead of hitting the network."""
    from pathlib import Path
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'fake mp3 content')


@pytest.fixture
def mock_download():
    return fake_download
