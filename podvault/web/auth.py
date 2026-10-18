"""
Optional HTTP Basic authentication for the web interface (Fase 23).

Off by default, so a plain `podvault serve` on 127.0.0.1 keeps working
exactly as before. Setting both `PODVAULT_USERNAME` and `PODVAULT_PASSWORD`
turns it on for every route — pages, job/control endpoints, audio
streaming, export, static files — except `/healthz`, which Docker's
HEALTHCHECK polls without credentials.

Basic auth was chosen over a login page + session cookie because it needs
no session store, no login template, and no CSRF handling for this app's
plain HTML forms, and every browser (including the `<audio>` element's
Range requests) resends the credentials automatically once entered.
Credentials travel base64-encoded, not encrypted: put PodVault behind an
HTTPS reverse proxy if it's reachable beyond a trusted network.

Podcast apps can't do a browser login, so PodVault's own feeds (Fase 24,
`services/local_feed.py`) — `feed.xml`, the audio/cover URLs inside it,
and the OPML of all of them — also accept a `?token=` query parameter
(`feed_token()`): `PODVAULT_FEED_TOKEN` if set, else an HMAC of the
password, so changing the password revokes old feed URLs. The token only
opens those GET paths, never the rest of the UI.

Setting only one of the two variables is treated as a misconfiguration and
fails closed (every request gets a 500 explaining why) rather than silently
running without a login the operator clearly meant to have.

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
from typing import Optional
from urllib.parse import parse_qs

from podvault import config
from podvault.logging_config import setup_logging

logger = setup_logging(__name__)

PUBLIC_PATHS = frozenset({'/healthz'})
REALM = 'PodVault'

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
    # Compare both halves even if the first fails, so timing doesn't reveal which was wrong
    user_ok = secrets.compare_digest(username.encode('utf-8'), (config.WEB_USERNAME or '').encode('utf-8'))
    pass_ok = secrets.compare_digest(password.encode('utf-8'), (config.WEB_PASSWORD or '').encode('utf-8'))
    return user_ok and pass_ok


async def _send_plain(send, status: int, body: bytes, extra_headers=()) -> None:
    headers = [(b'content-type', b'text/plain; charset=utf-8'), (b'content-length', str(len(body)).encode())]
    headers.extend(extra_headers)
    await send({'type': 'http.response.start', 'status': status, 'headers': headers})
    await send({'type': 'http.response.body', 'body': body})


class BasicAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path') in PUBLIC_PATHS or not auth_configured():
            await self.app(scope, receive, send)
            return

        if not (config.WEB_USERNAME and config.WEB_PASSWORD):
            logger.error("Only one of PODVAULT_USERNAME / PODVAULT_PASSWORD is set; refusing all web requests")
            await _send_plain(send, 500, b'PodVault login is misconfigured: set both PODVAULT_USERNAME and '
                                         b'PODVAULT_PASSWORD, or neither.\n')
            return

        authorization = ''
        for name, value in scope.get('headers', []):
            if name == b'authorization':
                authorization = value.decode('latin-1')
                break

        if (authorization and credentials_valid(authorization)) or _feed_token_valid(scope):
            await self.app(scope, receive, send)
            return

        await _send_plain(send, 401, b'Authentication required.\n',
                          [(b'www-authenticate', f'Basic realm="{REALM}", charset="UTF-8"'.encode())])
