"""Tests for OPML import/export (`services/opml.py`, Fase 24) and the feeds-file fix that came with it."""

import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from podvault import config, services
from podvault.services import opml
from podvault.web.app import app

SAMPLE_OPML = b"""<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>My subscriptions</title></head>
  <body>
    <outline text="Tech">
      <outline type="rss" text="Show A" xmlUrl="https://example.com/a.xml"/>
      <outline type="rss" title="Show B" xmlurl="https://example.com/b.xml"/>
    </outline>
    <outline type="rss" text="Bad" xmlUrl="ftp://example.com/c.xml"/>
    <outline text="A folder with no feed"/>
  </body>
</opml>
"""


def _fake_collect(url):
    from tests.conftest import make_feed_dataframe
    name = Path(url).stem  # a.xml -> "a"
    df = make_feed_dataframe(podcast_name=f"show_{name}")
    df.attrs['rss_feed_url'] = url  # what collect_data records as the podcast's feed URL
    return df


def test_parse_finds_nested_outlines_case_insensitively():
    parsed = opml.parse_opml(SAMPLE_OPML)
    assert parsed.error is None
    assert [(o.title, o.xml_url) for o in parsed.outlines] == [
        ('Show A', 'https://example.com/a.xml'),
        ('Show B', 'https://example.com/b.xml'),
        ('Bad', 'ftp://example.com/c.xml'),
    ]


def test_parse_rejects_entities_and_non_opml():
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><opml><body/></opml>'
    assert 'DOCTYPE' in opml.parse_opml(evil).error
    assert opml.parse_opml(b'<rss><channel/></rss>').error
    assert opml.parse_opml(b'not xml at all <').error
    assert opml.parse_opml(b'').error
    assert opml.parse_opml(b' ' * (opml.MAX_OPML_BYTES + 1)).error


def test_import_adds_new_feeds_skips_invalid_and_is_idempotent(isolated_env):
    with patch('podvault.services.feeds.collect_data', side_effect=_fake_collect):
        result = opml.import_opml(SAMPLE_OPML)

    assert result.success
    assert result.found == 3
    assert result.added == ['https://example.com/a.xml', 'https://example.com/b.xml']
    assert result.synced == 2
    assert len(result.invalid) == 1 and result.invalid[0].startswith('Bad:')
    assert {p.name for p in services.list_podcasts().podcasts} == {'show_a', 'show_b'}
    assert Path(config.FEEDS_FILE).read_text().split() == ['https://example.com/a.xml', 'https://example.com/b.xml']

    with patch('podvault.services.feeds.collect_data', side_effect=_fake_collect) as collect:
        again = opml.import_opml(SAMPLE_OPML)
    assert again.added == []
    assert again.already_listed == 2
    collect.assert_not_called()
    assert 'already in the library' in str(again)


def test_export_lists_original_feed_urls(isolated_env):
    with patch('podvault.services.feeds.collect_data', side_effect=_fake_collect):
        opml.import_opml(SAMPLE_OPML)

    root = ET.fromstring(opml.export_opml())
    urls = sorted(o.get('xmlUrl') for o in root.iter('outline'))
    assert root.tag == 'opml' and root.get('version') == '2.0'
    assert urls == ['https://example.com/a.xml', 'https://example.com/b.xml']


def test_export_import_round_trip(isolated_env, tmp_path):
    with patch('podvault.services.feeds.collect_data', side_effect=_fake_collect):
        opml.import_opml(SAMPLE_OPML)
    exported = opml.export_opml().encode('utf-8')

    parsed = opml.parse_opml(exported)
    assert sorted(o.xml_url for o in parsed.outlines) == ['https://example.com/a.xml', 'https://example.com/b.xml']


def test_add_feed_uses_configured_feeds_file(isolated_env, feed_df_factory, monkeypatch):
    """Regression: PODVAULT_FEEDS_FILE used to be ignored (everything hardcoded 'feeds.txt' in the cwd)."""
    target = isolated_env / 'data' / 'my_feeds.txt'
    monkeypatch.setattr(config, 'FEEDS_FILE', str(target))
    with patch('podvault.services.feeds.collect_data', return_value=feed_df_factory()):
        services.add_feed('https://example.com/test_podcast.xml')

    assert target.read_text().strip() == 'https://example.com/test_podcast.xml'
    assert not (isolated_env / 'feeds.txt').exists()


def test_web_import_and_export(isolated_env):
    client = TestClient(app)
    with patch('podvault.services.feeds.collect_data', side_effect=_fake_collect):
        response = client.post('/import/opml', files={'opml_file': ('subs.opml', SAMPLE_OPML, 'text/x-opml')},
                               follow_redirects=False)
        assert response.status_code == 303 or response.status_code == 302
        from tests.test_web_routes import _job_id_from_redirect, _poll_job
        status = _poll_job(client, _job_id_from_redirect(response.headers['location']))
    assert status['status'] == 'success'
    assert '2 new podcast(s) added' in status['message']

    export = client.get('/export/opml')
    assert export.status_code == 200
    assert 'attachment' in export.headers['content-disposition']
    assert b'https://example.com/a.xml' in export.content


def test_web_import_rejects_bad_file_immediately(isolated_env):
    client = TestClient(app)
    response = client.post('/import/opml', files={'opml_file': ('x.opml', b'<rss/>', 'text/xml')},
                           follow_redirects=False)
    assert 'job_id' not in response.headers['location']
    assert 'ok=0' in response.headers['location']
