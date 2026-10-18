"""
Login page and sign-out for the optional web login (Fase 25, see `web/auth.py`).

A plain POST form that works without JavaScript. A successful sign-in sets
the signed `podvault_session` cookie and redirects (303) to `next`, which is
only ever a local path. A failed one re-renders the page with a 401, keeping
the username and clearing the password. Repeated failures from one client
IP lock it out for a while (`auth.throttle`, shared with the Basic header
path), answered with a 429 without even checking the password.
"""

from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from podvault.web import auth
from podvault.web.templating import templates

router = APIRouter()


def _render(request: Request, status_code: int = 200, **context):
    misconfigured = auth.login_misconfigured()
    context.setdefault('next', '/')
    context.setdefault('username', '')
    context.setdefault('error', False)
    context.setdefault('signed_out', False)
    context.setdefault('retry_after', 0)
    return templates.TemplateResponse(request, "login.html", {**context, 'misconfigured': misconfigured},
                                      status_code=500 if misconfigured else status_code)


def _locked_out(request: Request, wait: int, **context):
    response = _render(request, status_code=429, retry_after=wait, **context)
    response.headers['Retry-After'] = str(wait)
    return response


@router.get("/login", include_in_schema=False)
def login_page(request: Request, next: Optional[str] = None, signed_out: bool = False):
    target = auth.safe_next(next)
    if not auth.auth_configured():
        return RedirectResponse(url=target, status_code=303)
    if auth.session_valid(request.cookies.get(auth.SESSION_COOKIE, '')):
        return RedirectResponse(url=target, status_code=303)
    wait = auth.throttle.retry_after(auth.client_key(request.scope))
    if wait and not auth.login_misconfigured():
        return _locked_out(request, wait, next=target)
    return _render(request, next=target, signed_out=signed_out)


@router.post("/login", include_in_schema=False)
def login_submit(
    request: Request,
    username: str = Form(''), password: str = Form(''),
    remember: bool = Form(False), next: Optional[str] = Form(None),
):
    target = auth.safe_next(next)
    if not auth.auth_configured():
        return RedirectResponse(url=target, status_code=303)
    if auth.login_misconfigured():
        return _render(request, next=target, username=username)

    key = auth.client_key(request.scope)
    wait = auth.throttle.retry_after(key)
    if wait:
        return _locked_out(request, wait, next=target, username=username)
    if not auth.check_login(username, password):
        auth.throttle.record_failure(key)
        wait = auth.throttle.retry_after(key)
        if wait:
            return _locked_out(request, wait, next=target, username=username)
        return _render(request, status_code=401, next=target, username=username, error=True)

    auth.throttle.record_success(key)
    value, max_age = auth.make_session_cookie(remember)
    response = RedirectResponse(url=target, status_code=303)
    response.headers.append('set-cookie', auth.session_cookie_header(value, max_age, auth.scope_is_https(request.scope)))
    return response


@router.post("/logout", include_in_schema=False)
def logout(request: Request):
    response = RedirectResponse(url='/login?signed_out=1' if auth.auth_configured() else '/', status_code=303)
    response.headers.append('set-cookie', auth.session_cookie_header('', 0, auth.scope_is_https(request.scope)))
    return response
