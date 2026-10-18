"""
Tests for non-MP3 audio support (Fase 23): the saved file's extension
follows the download URL, an existing file is recognised whatever its
audio extension, renames keep it, the player serves the right MIME type,
and tag writing is dispatched by format instead of assuming ID3.
"""

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from podvault import services
from podvault.download.id3_tags import id3_tags_complete, update_mp3_id3_tags
from podvault.download.utils import audio_extension_from_url, find_existing_audio_file
from podvault.web.app import app


def _add_podcast(feed_df_factory, link_direct):
    df = feed_df_factory(episodes=[
        {'id': 'ep-1', 'title': 'Episode One', 'published': '2024-01-01', 'link_direct': link_direct},
    ])
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')


def test_audio_extension_from_url():
    assert audio_extension_from_url('https://cdn.example.com/ep1.m4a') == '.m4a'
    assert audio_extension_from_url('https://cdn.example.com/ep1.M4B?token=abc&x=.mp3') == '.m4b'
    assert audio_extension_from_url('https://cdn.example.com/ep1.opus') == '.opus'
    assert audio_extension_from_url('https://cdn.example.com/ep1.mp3') == '.mp3'
    # No recognisable audio extension -> the historical default
    assert audio_extension_from_url('https://cdn.example.com/stream/12345') == '.mp3'
    assert audio_extension_from_url('https://cdn.example.com/page.html') == '.mp3'
    assert audio_extension_from_url(None) == '.mp3'


def test_find_existing_audio_file_ignores_non_audio_siblings(tmp_path):
    (tmp_path / 'Episode.jpg').write_bytes(b'img')
    (tmp_path / 'Episode.xml').write_text('<rss/>')
    assert find_existing_audio_file(tmp_path, 'Episode') is None

    (tmp_path / 'Episode.m4a').write_bytes(b'audio')
    assert find_existing_audio_file(tmp_path, 'Episode') == tmp_path / 'Episode.m4a'


def test_download_saves_m4a_with_m4a_extension(isolated_env, feed_df_factory, mock_download):
    _add_podcast(feed_df_factory, 'https://cdn.example.com/ep1.m4a?src=feed')

    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        result = services.download_all('test_podcast', require_confirmation=False)

    assert result.success
    episode = services.list_episodes('test_podcast').episodes[0]
    assert episode.saved_path.endswith('.m4a')
    assert Path(episode.saved_path).exists()


def test_single_episode_download_uses_url_extension(isolated_env, feed_df_factory, mock_download):
    _add_podcast(feed_df_factory, 'https://cdn.example.com/ep1.ogg')

    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        result = services.download_episode('test_podcast', 'ep-1')

    assert result.success
    episode = services.list_episodes('test_podcast').episodes[0]
    assert episode.saved_path.endswith('.ogg')


def test_existing_file_with_other_audio_extension_is_reused(isolated_env, feed_df_factory, mock_download):
    """A pre-Fase-23 `.mp3` saved for an m4a URL is recognised, not downloaded again as `.m4a`."""
    _add_podcast(feed_df_factory, 'https://cdn.example.com/ep1.m4a')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all('test_podcast', require_confirmation=False)

    original = Path(services.list_episodes('test_podcast').episodes[0].saved_path)
    legacy = original.with_suffix('.mp3')
    original.rename(legacy)
    services.verify_files('test_podcast')  # marks it missing (Saved_Path no longer exists)

    calls = []
    with patch('podvault.download.downloader._download_with_retry', side_effect=lambda *a, **k: calls.append(a)):
        services.download_all('test_podcast', require_confirmation=False)

    assert calls == []
    episode = services.list_episodes('test_podcast').episodes[0]
    assert episode.status == 'downloaded'
    assert episode.saved_path == str(legacy)


def test_rename_keeps_audio_extension(isolated_env, feed_df_factory, mock_download):
    _add_podcast(feed_df_factory, 'https://cdn.example.com/ep1.m4a')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all('test_podcast', require_confirmation=False)

    result = services.rename_episode('test_podcast', 'ep-1', 'Renamed Episode')
    assert result.success and result.renamed_on_disk

    episode = services.list_episodes('test_podcast').episodes[0]
    assert episode.saved_path.endswith('.m4a')
    assert 'Renamed Episode' in episode.saved_path
    assert Path(episode.saved_path).exists()


def test_audio_route_serves_format_specific_mime_type(isolated_env, feed_df_factory, mock_download):
    _add_podcast(feed_df_factory, 'https://cdn.example.com/ep1.m4a')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_all('test_podcast', require_confirmation=False)

    response = TestClient(app).get('/podcasts/test_podcast/episodes/ep-1/audio')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'audio/mp4'


def test_tag_writing_dispatches_by_format(tmp_path):
    m4a = tmp_path / 'ep.m4a'
    m4a.write_bytes(b'not really audio')
    with patch('podvault.download.id3_tags._update_mp4_tags', return_value=True) as mp4_writer:
        assert update_mp3_id3_tags(m4a, title='T', artist='A', album='A') is True
    mp4_writer.assert_called_once()

    ogg = tmp_path / 'ep.ogg'
    ogg.write_bytes(b'not really audio')
    with patch('podvault.download.id3_tags._update_mp4_tags') as mp4_writer:
        assert update_mp3_id3_tags(ogg, title='T', artist='A', album='A') is False
    mp4_writer.assert_not_called()
    assert ogg.read_bytes() == b'not really audio'


def test_tags_complete_is_unknown_for_untagged_formats(tmp_path):
    """Formats tags aren't written for shouldn't be reported as "incomplete" on the Storage page."""
    ogg = tmp_path / 'ep.ogg'
    ogg.write_bytes(b'x')
    assert id3_tags_complete(ogg) is None
