"""
Optional web login (Fase 23, login page since Fase 25).

Off by default, so a plain `podvault serve` on 127.0.0.1 keeps working
exactly as before. Setting both `PODVAULT_USERNAME` and `PODVAULT_PASSWORD`
turns it on for every route — pages, job/control endpoints, audio
streaming, export — except `PUBLIC_PATHS` (`/healthz`, which Docker's
HEALTHCHECK polls without credentials, and the login/logout routes) and
`/static/fonts/`, which the login page itself needs.

Browsers sign in through `/login` (`web/login.py`), which sets a signed
`podvault_session` cookie: `"{expiry}.{kind}.{hmac}"`, keyed on the
password, so there's still no session store and changing the password
signs everyone out. Once a session is past half its lifetime, any request
renews it, so someone using the app daily (or listening when the cookie
would have expired) isn't cut off. The cookie is `SameSite=Lax`, which
keeps cross-site POSTs from carrying it — that's the CSRF protection for
the app's plain HTML forms.

An `Authorization: Basic` header is still accepted for curl/scripts, but
not from browsers (detected by the `Sec-Fetch-Site` header, or a
`Mozilla/` User-Agent where that header isn't sent): a browser still
holding Phase 23 Basic credentials would otherwise keep sending them,
and Sign out could never sign it out. Failed
Basic and form logins share a per-client-IP lockout (`LoginThrottle`).
Credentials travel in the clear: put PodVault behind an HTTPS reverse
proxy if it's reachable beyond a trusted network.

Without a valid login, a browser navigation (a page load, or a plain
form POST) is redirected to `/login?next=...` — for a POST, back to the
page the form was on; anything else (fetch/polling, audio, export) gets a
plain 401 with no `WWW-Authenticate` header, since that header is what
makes the browser pop up its own Basic-auth prompt.

Podcast apps can't do a browser login, so PodVault's own feeds (Fase 24,
`services/local_feed.py`) — `feed.xml`, the audio/cover URLs inside it,
and the OPML of all of them — also accept a `?token=` query parameter
(`feed_token()`): `PODVAULT_FEED_TOKEN` if set, else an HMAC of the
password, so changing the password revokes old feed URLs. The token only
opens those GET paths, never the rest of the UI.

Setting only one of the two variables is treated as a misconfiguration and
fails closed (the login page explains it with a 500, everything else gets
a plain 500) rather than silently running without a login the operator
clearly meant to have.

Implemented as plain ASGI middleware, not Starlette's BaseHTTPMiddleware,
so streamed responses (audio files) pass through untouched. Settings are
read from `config` on every request, so tests can monkeypatch them.
"""

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import threading
import time
from typing import NamedTuple, Optional, Tuple
from urllib.parse import parse_qs, quote, urlsplit

from podvault import config
from podvault.logging_config import setup_logging

logger = setup_logging(__name__)

PUBLIC_PATHS = frozenset({'/healthz', '/login', '/logout'})
PUBLIC_PREFIXES = ('/static/fonts/',)

SESSION_COOKIE = 'podvault_session'
SESSION_REMEMBER_SECONDS = 30 * 24 * 3600  # "Keep me signed in": persistent cookie
SESSION_BROWSER_SECONDS = 12 * 3600        # otherwise a browser-session cookie, still capped

# Failed logins (form or Basic header) per client IP: after MAX_FAILURES,
# refuse attempts for LOCKOUT_BASE_SECONDS, doubling per further failure up
# to LOCKOUT_MAX_SECONDS. Quiet for FAILURE_MEMORY_SECONDS → forgotten.
MAX_FAILURES = 5
LOCKOUT_BASE_SECONDS = 30
LOCKOUT_MAX_SECONDS = 15 * 60
FAILURE_MEMORY_SECONDS = 15 * 60

# Paths a valid feed token opens (GET only): each podcast's feed and the
# enclosure/artwork URLs it links to, plus the OPML of all local feeds.
_FEED_PATH = re.compile(r'^/podcasts/[^/]+/(feed\.xml|feed/audio|feed/cover)$|^/feeds\.opml$')


def feed_token() -> Optional[str]:
    """The token feed URLs must carry when a login is on, or None when there's no login."""
    if not (config.WEB_USERNAME and config.WEB_PASSWORD):
        return None
    if config.FEED_TOKEN:
        return config.FEED_TOKEN
    return hmac.new(config.WEB_PASSWORD.encode('utf-8'), b'podvault-feed-v1', hashlib.sha256).hexdigest()[:32]


def is_feed_path(path: str) -> bool:
    return bool(_FEED_PATH.match(path))


def _feed_token_valid(scope) -> bool:
    expected = feed_token()
    if not expected or scope.get('method') not in ('GET', 'HEAD') or not is_feed_path(scope.get('path', '')):
        return False
    query = parse_qs(scope.get('query_string', b'').decode('latin-1'))
    supplied = (query.get('token') or [''])[0]
    return secrets.compare_digest(supplied.encode('utf-8'), expected.encode('utf-8'))


def auth_configured() -> bool:
    """Whether a login is required (either variable set — see module docstring for the half-set case)."""
    return bool(config.WEB_USERNAME or config.WEB_PASSWORD)


def login_enabled() -> bool:
    """Whether a working login is on (both variables set)."""
    return bool(config.WEB_USERNAME and config.WEB_PASSWORD)


def login_misconfigured() -> bool:
    return auth_configured() and not login_enabled()


def check_login(username: str, password: str) -> bool:
    """Compare a username/password pair against the configured credentials."""
    # Compare both halves even if the first fails, so timing doesn't reveal which was wrong
    user_ok = secrets.compare_digest(username.encode('utf-8'), (config.WEB_USERNAME or '').encode('utf-8'))
    pass_ok = secrets.compare_digest(password.encode('utf-8'), (config.WEB_PASSWORD or '').encode('utf-8'))
    return user_ok and pass_ok and login_enabled()


def _session_signature(expiry: int, kind: str) -> str:
    message = f'podvault-session-v1:{config.WEB_USERNAME}:{expiry}:{kind}'.encode('utf-8')
    return hmac.new(config.WEB_PASSWORD.encode('utf-8'), message, hashlib.sha256).hexdigest()


class Session(NamedTuple):
    expiry: int
    remember: bool

    @property
    def lifetime(self) -> int:
        return SESSION_REMEMBER_SECONDS if self.remember else SESSION_BROWSER_SECONDS


def make_session_cookie(remember: bool, now: Optional[float] = None) -> Tuple[str, Optional[int]]:
    """A signed session value and its cookie Max-Age (None = until the browser closes)."""
    kind = 'r' if remember else 's'
    lifetime = SESSION_REMEMBER_SECONDS if remember else SESSION_BROWSER_SECONDS
    expiry = int(now if now is not None else time.time()) + lifetime
    return f'{expiry}.{kind}.{_session_signature(expiry, kind)}', (lifetime if remember else None)


def parse_session(value: str, now: Optional[float] = None) -> Optional[Session]:
    """The session a cookie value carries, or None if it's missing, expired, or not ours."""
    if not value or not login_enabled():
        return None
    expiry_text, _, rest = value.partition('.')
    kind, _, signature = rest.partition('.')
    if not expiry_text.isdigit() or kind not in ('r', 's'):
        return None
    expiry = int(expiry_text)
    if expiry < (now if now is not None else time.time()):
        return None
    if not secrets.compare_digest(signature.encode('utf-8'), _session_signature(expiry, kind).encode('utf-8')):
        return None
    return Session(expiry, kind == 'r')


def session_valid(value: str, now: Optional[float] = None) -> bool:
    return parse_session(value, now) is not None


def session_needs_renewal(session: Session, now: Optional[float] = None) -> bool:
    remaining = session.expiry - (now if now is not None else time.time())
    return remaining < session.lifetime / 2


def session_cookie_header(value: str, max_age: Optional[int], secure: bool) -> str:
    """A `Set-Cookie` value for the session cookie (`max_age=0` deletes it)."""
    parts = [f'{SESSION_COOKIE}={value}', 'Path=/', 'HttpOnly', 'SameSite=Lax']
    if max_age is not None:
        parts.append(f'Max-Age={max_age}')
        if max_age == 0:
            parts.append('expires=Thu, 01 Jan 1970 00:00:00 GMT')
    if secure:
        parts.append('Secure')
    return '; '.join(parts)


def scope_is_https(scope) -> bool:
    """Whether the request arrived over HTTPS, directly or through a reverse proxy."""
    forwarded = _header(scope, b'x-forwarded-proto').split(',')[0].strip().lower()
    return forwarded == 'https' or scope.get('scheme') == 'https'


def client_key(scope) -> str:
    client = scope.get('client')
    return client[0] if client else 'unknown'


class LoginThrottle:
    """In-memory failed-login counter per client IP; resets with the process, by design."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries = {}  # key -> [failures, locked_until, last_failure]

    def retry_after(self, key: str, now: Optional[float] = None) -> int:
        """Seconds until `key` may try again (0 = not locked)."""
        now = now if now is not None else time.time()
        with self._lock:
            entry = self._entries.get(key)
            return max(0, int(entry[1] - now + 0.999)) if entry else 0

    def record_failure(self, key: str, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            if len(self._entries) > 1000:
                stale = [k for k, e in self._entries.items() if now - e[2] > FAILURE_MEMORY_SECONDS and e[1] < now]
                for k in stale:
                    del self._entries[k]
            entry = self._entries.get(key)
            if entry is None or (now - entry[2] > FAILURE_MEMORY_SECONDS and entry[1] < now):
                entry = self._entries[key] = [0, 0.0, now]
            entry[0] += 1
            entry[2] = now
            if entry[0] >= MAX_FAILURES:
                lockout = min(LOCKOUT_BASE_SECONDS * 2 ** (entry[0] - MAX_FAILURES), LOCKOUT_MAX_SECONDS)
                entry[1] = now + lockout

    def record_success(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()


throttle = LoginThrottle()


def safe_next(target: Optional[str]) -> str:
    """Where to go after signing in: only a local path, never another site."""
    if not target or not target.startswith('/') or target.startswith('//') or '\\' in target:
        return '/'
    return target


def credentials_valid(authorization: str) -> bool:
    """Check an `Authorization: Basic ...` header value against the configured credentials."""
    scheme, _, encoded = authorization.partition(' ')
    if scheme.lower() != 'basic' or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode('utf-8')
    except (binascii.Error, UnicodeDecodeError):
        return False
    username, sep, password = decoded.partition(':')
    if not sep:
        return False
    return check_login(username, password)


def _header(scope, name: bytes) -> str:
    for key, value in scope.get('headers', []):
        if key == name:
            return value.decode('latin-1')
    return ''


def _session_cookie(scope) -> str:
    for part in _header(scope, b'cookie').split(';'):
        name, _, value = part.strip().partition('=')
        if name == SESSION_COOKIE:
            return value
    return ''


def _from_browser(scope) -> bool:
    # Fetch-metadata headers are only sent to secure origins (HTTPS, localhost); over plain HTTP on a
    # LAN address, fall back to the User-Agent — every browser's starts with "Mozilla/", curl's,
    # requests' and httpx's don't.
    return bool(_header(scope, b'sec-fetch-site')) or _header(scope, b'user-agent').startswith('Mozilla/')


def _is_navigation(scope) -> bool:
    """A page load or plain form submit — something a sign-in page can answer — as opposed to fetch/audio."""
    mode = _header(scope, b'sec-fetch-mode')
    if mode:
        return mode == 'navigate'
    return 'text/html' in _header(scope, b'accept')


def _return_path(scope) -> str:
    """Where to come back after signing in: this page, or for a form POST the page the form was on."""
    if scope.get('method') in ('GET', 'HEAD'):
        target = scope.get('path', '/')
        query = scope.get('query_string', b'').decode('latin-1')
        return target + ('?' + query if query else '')
    referer = urlsplit(_header(scope, b'referer'))
    if referer.netloc and referer.netloc == _header(scope, b'host'):
        return safe_next(referer.path + ('?' + referer.query if referer.query else ''))
    return '/'


async def _redirect_to_login(send, scope) -> None:
    target = _return_path(scope)
    location = '/login' if target == '/' else '/login?next=' + quote(target, safe='')
    await send({'type': 'http.response.start', 'status': 303,
                'headers': [(b'location', location.encode('latin-1')), (b'content-length', b'0')]})
    await send({'type': 'http.response.body', 'body': b''})


async def _send_plain(send, status: int, body: bytes, extra_headers=()) -> None:
    headers = [(b'content-type', b'text/plain; charset=utf-8'), (b'content-length', str(len(body)).encode())]
    headers.extend(extra_headers)
    await send({'type': 'http.response.start', 'status': status, 'headers': headers})
    await send({'type': 'http.response.body', 'body': body})


_misconfiguration_logged = False


class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        global _misconfiguration_logged
        path = scope.get('path', '')
        if (scope['type'] != 'http' or not auth_configured()
                or path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)):
            await self.app(scope, receive, send)
            return

        if login_misconfigured():
            if not _misconfiguration_logged:
                logger.error("Only one of PODVAULT_USERNAME / PODVAULT_PASSWORD is set; refusing all web requests")
                _misconfiguration_logged = True
            if _is_navigation(scope):
                await _redirect_to_login(send, scope)  # the login page explains it
                return
            await _send_plain(send, 500, b'PodVault login is misconfigured: set both PODVAULT_USERNAME and '
                                         b'PODVAULT_PASSWORD, or neither.\n')
            return

        session = parse_session(_session_cookie(scope))
        if session:
            if session_needs_renewal(session):
                await self.app(scope, receive, self._with_renewed_cookie(scope, send, session))
            else:
                await self.app(scope, receive, send)
            return

        if _feed_token_valid(scope):
            await self.app(scope, receive, send)
            return

        authorization = _header(scope, b'authorization')
        if authorization and not _from_browser(scope):
            key = client_key(scope)
            wait = throttle.retry_after(key)
            if wait:
                await _send_plain(send, 429, b'Too many failed logins. Try again later.\n',
                                  [(b'retry-after', str(wait).encode())])
                return
            if credentials_valid(authorization):
                throttle.record_success(key)
                await self.app(scope, receive, send)
                return
            throttle.record_failure(key)

        if _is_navigation(scope):
            await _redirect_to_login(send, scope)
            return
        await _send_plain(send, 401, b'Authentication required.\n')

    @staticmethod
    def _with_renewed_cookie(scope, send, session: Session):
        value, max_age = make_session_cookie(session.remember)
        header = (b'set-cookie', session_cookie_header(value, max_age, scope_is_https(scope)).encode('latin-1'))

        async def send_wrapper(message):
            if message['type'] == 'http.response.start':
                message = {**message, 'headers': [*message.get('headers', []), header]}
            await send(message)
        return send_wrapper
