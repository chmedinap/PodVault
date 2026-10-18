"""Tests for PodVault's own per-podcast RSS feeds (`services/local_feed.py` + routes, Fase 24)."""

import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from podvault import config, services
from podvault.services.local_feed import ITUNES_NS
from podvault.web.app import app
from podvault.web.auth import feed_token

EPISODES = [
    {'id': 'https://example.com/?p=1', 'title': 'Episode One', 'published': 'Mon, 01 Jan 2024 10:00:00 +0000',
     'link_direct': 'https://cdn.example.com/one.m4a', 'episode_number': 1, 'summary': 'First'},
    {'id': 'ep-2', 'title': 'Episode Two', 'published': '2024-01-08',
     'link_direct': 'https://cdn.example.com/two.mp3'},
    {'id': 'ep-3', 'title': 'Not Downloaded', 'published': '2024-01-15'},
]


@pytest.fixture
def library(isolated_env, feed_df_factory, mock_download):
    df = feed_df_factory(episodes=EPISODES)
    with patch('podvault.services.feeds.collect_data', return_value=df):
        services.add_feed('https://example.com/test_podcast.xml')
    with patch('podvault.download.downloader._download_with_retry', side_effect=mock_download):
        services.download_episode('test_podcast', 'https://example.com/?p=1')
        services.download_episode('test_podcast', 'ep-2')
    return isolated_env


def _items(xml_text):
    root = ET.fromstring(xml_text)
    return root, root.find('channel').findall('item')


def test_feed_lists_only_downloaded_episodes(library):
    client = TestClient(app)
    response = client.get('/podcasts/test_podcast/feed.xml')

    assert response.status_code == 200
    assert response.headers['content-type'].startswith('application/rss+xml')
    root, items = _items(response.text)
    assert root.tag == 'rss'
    assert [i.findtext('title') for i in items] == ['Episode Two', 'Episode One']
    first = items[1]
    assert first.findtext('guid') == 'https://example.com/?p=1'
    assert first.findtext('pubDate') == 'Mon, 01 Jan 2024 10:00:00 +0000'
    assert first.findtext(f'{{{ITUNES_NS}}}episode') == '1'
    assert first.find('enclosure').get('type') == 'audio/mp4'
    assert int(first.find('enclosure').get('length')) > 0
    assert items[0].findtext('pubDate', '').startswith('Mon, 08 Jan 2024')


def test_enclosures_resolve_to_the_audio_even_for_url_guids(library):
    client = TestClient(app)
    _root, items = _items(client.get('/podcasts/test_podcast/feed.xml').text)

    for item in items:
        enclosure = urlparse(item.find('enclosure').get('url'))
        audio = client.get(f"{enclosure.path}?{enclosure.query}")
        assert audio.status_code == 200
        assert audio.content == b'fake mp3 content'


def test_feed_skips_episodes_whose_file_is_gone(library):
    episode = next(e for e in services.list_episodes('test_podcast').episodes if e.episode_id == 'ep-2')
    Path(episode.saved_path).unlink()

    _root, items = _items(TestClient(app).get('/podcasts/test_podcast/feed.xml').text)
    assert [i.findtext('title') for i in items] == ['Episode One']


def test_feed_uses_base_url_override(library, monkeypatch):
    monkeypatch.setattr(config, 'BASE_URL', 'https://vault.example.org/')
    root, items = _items(TestClient(app).get('/podcasts/test_podcast/feed.xml').text)
    assert items[0].find('enclosure').get('url').startswith('https://vault.example.org/podcasts/test_podcast/feed/audio?')
    assert root.find('channel').findtext('link') == 'https://vault.example.org/podcasts/test_podcast'


def test_podcast_cover_served_from_local_image(library):
    client = TestClient(app)
    assert client.get('/podcasts/test_podcast/feed/cover').status_code == 404

    (Path(config.DOWNLOADS_FOLDER) / 'test_podcast' / 'podcast_image.jpg').write_bytes(b'jpg')
    root, _items_ = _items(client.get('/podcasts/test_podcast/feed.xml').text)
    href = root.find('channel').find(f'{{{ITUNES_NS}}}image').get('href')
    assert '/podcasts/test_podcast/feed/cover' in href
    assert client.get('/podcasts/test_podcast/feed/cover').content == b'jpg'


def test_unknown_podcast_feed_is_404(isolated_env):
    assert TestClient(app).get('/podcasts/nope/feed.xml').status_code == 404


def test_feed_token_with_login(library, monkeypatch):
    monkeypatch.setattr(config, 'WEB_USERNAME', 'admin')
    monkeypatch.setattr(config, 'WEB_PASSWORD', 'pw')
    client = TestClient(app)
    token = feed_token()

    assert client.get('/podcasts/test_podcast/feed.xml').status_code == 401
    assert client.get('/podcasts/test_podcast/feed.xml?token=wrong').status_code == 401
    response = client.get(f'/podcasts/test_podcast/feed.xml?token={token}')
    assert response.status_code == 200

    # Every URL inside the feed carries the token and works without the login
    _root, items = _items(response.text)
    enclosure = urlparse(items[0].find('enclosure').get('url'))
    assert parse_qs(enclosure.query)['token'] == [token]
    assert client.get(f"{enclosure.path}?{enclosure.query}").status_code == 200

    # The token opens only feed paths, never the rest of the UI
    assert client.get(f'/?token={token}').status_code == 401
    assert client.get(f'/podcasts/test_podcast?token={token}').status_code == 401
    assert client.get(f'/export?token={token}').status_code == 401
    assert client.post(f'/queue/cancel-all?token={token}').status_code == 401


def test_feed_token_changes_with_password_or_override(monkeypatch):
    monkeypatch.setattr(config, 'WEB_USERNAME', 'admin')
    monkeypatch.setattr(config, 'FEED_TOKEN', None)
    monkeypatch.setattr(config, 'WEB_PASSWORD', 'one')
    first = feed_token()
    monkeypatch.setattr(config, 'WEB_PASSWORD', 'two')
    assert feed_token() != first
    monkeypatch.setattr(config, 'FEED_TOKEN', 'fixed-token')
    assert feed_token() == 'fixed-token'
    monkeypatch.setattr(config, 'WEB_PASSWORD', None)
    monkeypatch.setattr(config, 'WEB_USERNAME', None)
    assert feed_token() is None


def test_archive_opml_lists_own_feeds_with_token(library, monkeypatch):
    monkeypatch.setattr(config, 'WEB_USERNAME', 'admin')
    monkeypatch.setattr(config, 'WEB_PASSWORD', 'pw')
    client = TestClient(app)

    assert client.get('/feeds.opml').status_code == 401
    response = client.get(f'/feeds.opml?token={feed_token()}')
    assert response.status_code == 200
    urls = [o.get('xmlUrl') for o in ET.fromstring(response.text).iter('outline')]
    assert len(urls) == 1
    assert '/podcasts/test_podcast/feed.xml?token=' in urls[0]


def test_podcast_page_shows_feed_url(library):
    html = TestClient(app).get('/podcasts/test_podcast').text
    assert 'http://testserver/podcasts/test_podcast/feed.xml' in html
