"""Tests for the optional HTTP Basic login on the web interface (`web/auth.py`, Fase 23)."""

import pytest
from fastapi.testclient import TestClient

from podvault import config
from podvault.web.app import app


@pytest.fixture
def client(isolated_env):
    return TestClient(app)


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(config, 'WEB_USERNAME', 'admin')
    monkeypatch.setattr(config, 'WEB_PASSWORD', 's3cret:with-colon')


def test_no_login_required_by_default(client, monkeypatch):
    monkeypatch.setattr(config, 'WEB_USERNAME', None)
    monkeypatch.setattr(config, 'WEB_PASSWORD', None)
    assert client.get('/').status_code == 200


def test_requests_without_credentials_are_challenged(client, auth_on):
    response = client.get('/')
    assert response.status_code == 401
    assert response.headers['www-authenticate'].startswith('Basic realm="PodVault"')


@pytest.mark.parametrize('path', ['/', '/queue', '/settings', '/jobs/active/status', '/export',
                                  '/static/fonts/IBMPlexSans-400.woff2'])
def test_every_route_is_protected(client, auth_on, path):
    assert client.get(path).status_code == 401
    assert client.get(path, auth=('admin', 's3cret:with-colon')).status_code == 200


def test_post_routes_are_protected(client, auth_on):
    response = client.post('/queue/cancel-all', follow_redirects=False)
    assert response.status_code == 401


def test_wrong_credentials_rejected(client, auth_on):
    assert client.get('/', auth=('admin', 'wrong')).status_code == 401
    assert client.get('/', auth=('someone', 's3cret:with-colon')).status_code == 401
    assert client.get('/', headers={'Authorization': 'Basic not-base64!!'}).status_code == 401
    assert client.get('/', headers={'Authorization': 'Bearer abc'}).status_code == 401


def test_healthz_is_always_public(client, auth_on):
    response = client.get('/healthz')
    assert response.status_code == 200
    assert response.text == 'ok'


def test_half_configured_login_fails_closed(client, monkeypatch):
    monkeypatch.setattr(config, 'WEB_USERNAME', 'admin')
    monkeypatch.setattr(config, 'WEB_PASSWORD', None)
    response = client.get('/', auth=('admin', ''))
    assert response.status_code == 500
    assert 'PODVAULT_PASSWORD' in response.text
    assert client.get('/healthz').status_code == 200


def test_settings_page_never_shows_password(client, auth_on):
    html = client.get('/settings', auth=('admin', 's3cret:with-colon')).text
    assert 's3cret' not in html
    assert 'PODVAULT_USERNAME' in html
