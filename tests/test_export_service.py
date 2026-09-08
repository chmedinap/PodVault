"""Tests for `services.export` (portable JSON manifests of the archive)."""

from unittest.mock import patch

from podvault import services


def _add_and_download(feed_df_factory, mock_download, podcast_name='test_podcast', episodes=None):
    df = feed_df_factory(podcast_name=podcast_name, episodes=episodes) if episodes else feed_df_factory(podcast_name=podcast_name)
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed(f'https://example.com/{podcast_name}.xml')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all(podcast_name, require_confirmation=False)


def test_export_podcast_manifest_includes_every_episode(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download, episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
        {'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-02'},
    ])

    manifest = services.export_podcast_manifest('test_podcast')

    assert manifest['podcast_name'] == 'test_podcast'
    assert manifest['episode_count'] == 2
    assert len(manifest['episodes']) == 2
    ep1 = next(e for e in manifest['episodes'] if e['id'] == 'ep-1')
    assert ep1['status'] == 'downloaded'
    assert ep1['checksum'] is not None
    assert ep1['in_feed'] is True
    assert 'podvault_version' in manifest
    assert 'exported_at' in manifest


def test_export_podcast_manifest_includes_removed_episodes(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download, episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01'},
        {'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-02'},
    ])
    shrunk_df = feed_df_factory(episodes=[{'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-02'}])
    with patch('podvault.services.feeds.collect_data', return_value=shrunk_df):
        services.add_feed('https://example.com/test_podcast.xml')

    manifest = services.export_podcast_manifest('test_podcast')
    assert manifest['episode_count'] == 2
    ep1 = next(e for e in manifest['episodes'] if e['id'] == 'ep-1')
    assert ep1['in_feed'] is False


def test_export_podcast_manifest_unknown_podcast_returns_none(isolated_env):
    assert services.export_podcast_manifest('does_not_exist') is None


def test_export_library_manifest_covers_every_podcast(isolated_env, feed_df_factory, mock_download):
    _add_and_download(feed_df_factory, mock_download, podcast_name='pod_a', episodes=[
        {'id': 'ep-1', 'title': 'Ep 1', 'published': '2024-01-01'},
    ])
    _add_and_download(feed_df_factory, mock_download, podcast_name='pod_b', episodes=[
        {'id': 'ep-1', 'title': 'Ep 1', 'published': '2024-01-01'},
    ])

    manifest = services.export_library_manifest()
    assert manifest['podcast_count'] == 2
    names = {p['podcast_name'] for p in manifest['podcasts']}
    assert names == {'pod_a', 'pod_b'}


def test_export_library_manifest_empty(isolated_env):
    manifest = services.export_library_manifest()
    assert manifest['podcast_count'] == 0
    assert manifest['podcasts'] == []
