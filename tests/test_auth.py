"""Tests for the optional web login (`web/auth.py` Fase 23, login page `web/login.py` Fase 25)."""

import time

import pytest
from fastapi.testclient import TestClient

from podvault import config
from podvault.web import auth
from podvault.web.app import app

HTML = {'Accept': 'text/html,application/xhtml+xml'}


@pytest.fixture
def client(isolated_env):
    auth.throttle.reset()
    yield TestClient(app)
    auth.throttle.reset()


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(config, 'WEB_USERNAME', 'admin')
    monkeypatch.setattr(config, 'WEB_PASSWORD', 's3cret:with-colon')


def test_no_login_required_by_default(client, monkeypatch):
    monkeypatch.setattr(config, 'WEB_USERNAME', None)
    monkeypatch.setattr(config, 'WEB_PASSWORD', None)
    assert client.get('/').status_code == 200


def test_requests_without_credentials_get_401_without_browser_popup(client, auth_on):
    response = client.get('/')
    assert response.status_code == 401
    # The WWW-Authenticate header is what makes browsers show their own Basic-auth prompt
    assert 'www-authenticate' not in response.headers


@pytest.mark.parametrize('path', ['/', '/queue', '/settings', '/jobs/active/status', '/export'])
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


def test_only_fonts_are_public_under_static(client, auth_on):
    assert client.get('/static/fonts/IBMPlexSans-400.woff2').status_code == 200
    # Anything added to /static later stays behind the login unless it's a font
    assert client.get('/static/other.css').status_code == 401


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


# ---- Fase 25: login page + session cookie ----

def _sign_in(client, remember=True, next_path='/', password='s3cret:with-colon'):
    data = {'username': 'admin', 'password': password, 'next': next_path}
    if remember:
        data['remember'] = 'on'
    return client.post('/login', data=data, follow_redirects=False)


def test_browser_page_load_redirects_to_login_with_next(client, auth_on):
    response = client.get('/podcasts/foo?page=2', headers=HTML, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/login?next=%2Fpodcasts%2Ffoo%3Fpage%3D2'
    assert client.get('/', headers=HTML, follow_redirects=False).headers['location'] == '/login'


def test_non_page_requests_still_get_plain_401(client, auth_on):
    assert client.get('/jobs/active/status', headers={'Accept': 'application/json'}).status_code == 401
    assert client.post('/queue/cancel-all', headers={'Accept': '*/*'}, follow_redirects=False).status_code == 401


def test_login_page_renders(client, auth_on):
    response = client.get('/login?next=/queue')
    assert response.status_code == 200
    assert 'action="/login"' in response.text
    assert 'name="next" value="/queue"' in response.text
    assert 'misconfigured' not in response.text.lower()


def test_login_page_without_login_configured_redirects_home(client):
    response = client.get('/login', follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/'


def test_successful_login_sets_session_and_redirects(client, auth_on):
    response = _sign_in(client, next_path='/queue')
    assert response.status_code == 303
    assert response.headers['location'] == '/queue'
    cookie = response.headers['set-cookie']
    assert cookie.startswith(f'{auth.SESSION_COOKIE}=')
    assert 'HttpOnly' in cookie
    assert 'samesite=lax' in cookie.lower()
    assert f'Max-Age={auth.SESSION_REMEMBER_SECONDS}' in cookie
    assert 'Secure' not in cookie
    # The client now carries the cookie: pages, POSTs and polling all pass
    assert client.get('/queue').status_code == 200
    assert client.get('/jobs/active/status').status_code == 200


def test_login_without_remember_is_a_browser_session_cookie(client, auth_on):
    cookie = _sign_in(client, remember=False).headers['set-cookie']
    assert 'Max-Age' not in cookie
    assert 'expires' not in cookie.lower()


def test_login_over_https_proxy_sets_secure_cookie(client, auth_on):
    response = client.post('/login', data={'username': 'admin', 'password': 's3cret:with-colon'},
                           headers={'X-Forwarded-Proto': 'https'}, follow_redirects=False)
    assert 'Secure' in response.headers['set-cookie']


def test_wrong_login_rerenders_with_error(client, auth_on):
    response = _sign_in(client, password='nope')
    assert response.status_code == 401
    assert 'Wrong username or password' in response.text
    assert 'value="admin"' in response.text
    assert 'nope' not in response.text
    assert 'set-cookie' not in response.headers


@pytest.mark.parametrize('target', ['https://evil.example/', '//evil.example/', '/\\evil.example', 'javascript:alert(1)'])
def test_next_only_accepts_local_paths(client, auth_on, target):
    response = _sign_in(client, next_path=target)
    assert response.headers['location'] == '/'


def test_already_signed_in_login_page_redirects(client, auth_on):
    _sign_in(client)
    response = client.get('/login?next=/settings', follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/settings'


def test_logout_clears_cookie(client, auth_on):
    _sign_in(client)
    response = client.post('/logout', follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/login?signed_out=1'
    assert client.get('/queue').status_code == 401
    assert "You've signed out" in client.get('/login?signed_out=1').text


def test_sidebar_shows_sign_out_only_with_login(client, auth_on, monkeypatch):
    _sign_in(client)
    html = client.get('/').text
    assert 'action="/logout"' in html
    assert 'admin · v' in html
    monkeypatch.setattr(config, 'WEB_USERNAME', None)
    monkeypatch.setattr(config, 'WEB_PASSWORD', None)
    assert 'action="/logout"' not in client.get('/').text


def test_changing_password_invalidates_sessions(client, auth_on, monkeypatch):
    _sign_in(client)
    assert client.get('/queue').status_code == 200
    monkeypatch.setattr(config, 'WEB_PASSWORD', 'rotated')
    assert client.get('/queue').status_code == 401


def test_session_value_checks(auth_on):
    value, max_age = auth.make_session_cookie(remember=False, now=1000)
    assert max_age is None
    assert auth.session_valid(value, now=1000 + auth.SESSION_BROWSER_SECONDS - 1)
    assert not auth.session_valid(value, now=1000 + auth.SESSION_BROWSER_SECONDS + 1)
    expiry, _, signature = value.partition('.')
    assert not auth.session_valid(f'{int(expiry) + 999999}.{signature}', now=1000)
    assert not auth.session_valid('garbage', now=1000)
    assert not auth.session_valid('', now=1000)


def test_half_configured_login_page_explains(client, monkeypatch):
    monkeypatch.setattr(config, 'WEB_USERNAME', 'admin')
    monkeypatch.setattr(config, 'WEB_PASSWORD', None)
    redirect = client.get('/queue', headers=HTML, follow_redirects=False)
    assert redirect.status_code == 303
    page = client.get(redirect.headers['location'])
    assert page.status_code == 500
    assert 'Login is misconfigured' in page.text
    posted = client.post('/login', data={'username': 'admin', 'password': ''}, follow_redirects=False)
    assert posted.status_code == 500
    assert 'set-cookie' not in posted.headers


# ---- Fase 25 follow-up: browser vs. script, expired sessions, renewal, lockout ----

BROWSER_UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15'}


@pytest.mark.parametrize('browser_headers', [{'Sec-Fetch-Site': 'same-origin'}, BROWSER_UA])
def test_browsers_cannot_use_basic_auth(client, auth_on, browser_headers):
    # A browser still holding Phase 23 Basic credentials must go through /login, or Sign out can't work
    response = client.get('/queue', auth=('admin', 's3cret:with-colon'),
                          headers={**browser_headers, **HTML}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'].startswith('/login')
    assert client.get('/queue', auth=('admin', 's3cret:with-colon')).status_code == 200


def test_browser_form_post_without_session_returns_to_the_form_page(client, auth_on):
    response = client.post('/queue/cancel-all', follow_redirects=False, headers={
        **HTML, 'Referer': 'http://testserver/podcasts/foo?page=2', 'Sec-Fetch-Mode': 'navigate'})
    assert response.status_code == 303
    assert response.headers['location'] == '/login?next=%2Fpodcasts%2Ffoo%3Fpage%3D2'


def test_form_post_with_foreign_referer_goes_to_plain_login(client, auth_on):
    response = client.post('/queue/cancel-all', follow_redirects=False,
                           headers={**HTML, 'Referer': 'https://evil.example/podcasts/foo'})
    assert response.headers['location'] == '/login'


def test_fetch_without_session_gets_401_not_redirect(client, auth_on):
    response = client.post('/settings/preview', headers={'Sec-Fetch-Mode': 'cors', 'Accept': '*/*'},
                           follow_redirects=False)
    assert response.status_code == 401


def _cookie_issued_ago(seconds_ago, remember=True):
    value, _ = auth.make_session_cookie(remember, now=time.time() - seconds_ago)
    return value


def test_session_past_half_life_is_renewed(client, auth_on):
    old = _cookie_issued_ago(auth.SESSION_REMEMBER_SECONDS * 0.6)
    client.cookies.set(auth.SESSION_COOKIE, old)
    response = client.get('/queue')
    assert response.status_code == 200
    cookie = response.headers['set-cookie']
    assert f'Max-Age={auth.SESSION_REMEMBER_SECONDS}' in cookie
    renewed = auth.parse_session(cookie.split(';')[0].split('=', 1)[1])
    assert renewed.remember and renewed.expiry > auth.parse_session(old).expiry


def test_browser_session_renewal_stays_a_browser_session(client, auth_on):
    old = _cookie_issued_ago(auth.SESSION_BROWSER_SECONDS * 0.6, remember=False)
    client.cookies.set(auth.SESSION_COOKIE, old)
    cookie = client.get('/queue').headers['set-cookie']
    assert 'Max-Age' not in cookie


def test_fresh_session_is_not_rewritten(client, auth_on):
    fresh = _cookie_issued_ago(60)
    client.cookies.set(auth.SESSION_COOKIE, fresh)
    assert 'set-cookie' not in client.get('/queue').headers


def test_repeated_failed_logins_lock_the_client_out(client, auth_on):
    for _ in range(auth.MAX_FAILURES - 1):
        assert _sign_in(client, password='nope').status_code == 401
    locked = _sign_in(client, password='nope')
    assert locked.status_code == 429
    assert int(locked.headers['retry-after']) == auth.LOCKOUT_BASE_SECONDS
    assert 'Too many failed attempts' in locked.text
    # While locked, even the right password isn't checked
    assert _sign_in(client).status_code == 429
    assert client.get('/login').status_code == 429


def test_lockout_expires_and_doubles(auth_on):
    t = auth.LoginThrottle()
    for _ in range(auth.MAX_FAILURES):
        t.record_failure('ip', now=1000)
    assert t.retry_after('ip', now=1000) == auth.LOCKOUT_BASE_SECONDS
    assert t.retry_after('ip', now=1000 + auth.LOCKOUT_BASE_SECONDS) == 0
    t.record_failure('ip', now=1000 + auth.LOCKOUT_BASE_SECONDS)
    assert t.retry_after('ip', now=1000 + auth.LOCKOUT_BASE_SECONDS) == auth.LOCKOUT_BASE_SECONDS * 2
    t.record_success('ip')
    assert t.retry_after('ip', now=1000 + auth.LOCKOUT_BASE_SECONDS) == 0


def test_lockout_is_capped_and_forgotten(auth_on):
    t = auth.LoginThrottle()
    for _ in range(auth.MAX_FAILURES + 20):
        t.record_failure('ip', now=1000)
    assert t.retry_after('ip', now=1000) == auth.LOCKOUT_MAX_SECONDS
    later = 1000 + auth.LOCKOUT_MAX_SECONDS + auth.FAILURE_MEMORY_SECONDS + 1
    t.record_failure('ip', now=later)
    assert t.retry_after('ip', now=later) == 0  # a fresh count, not one more on top of 25


def test_basic_auth_failures_share_the_lockout(client, auth_on):
    for _ in range(auth.MAX_FAILURES):
        client.get('/queue', auth=('admin', 'wrong'))
    response = client.get('/queue', auth=('admin', 's3cret:with-colon'))
    assert response.status_code == 429
    assert 'retry-after' in response.headers
    assert _sign_in(client).status_code == 429
