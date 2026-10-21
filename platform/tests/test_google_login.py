"""GET /auth/google/login and what every callback outcome looks like to a person.

The CSRF half of the state binding is pinned in test_auth_hardening.py. This file
pins the two things that made "sometimes I have to clear cookies to log in": the
state cookie must be first-party (set on a top-level navigation, never on a
cross-site fetch), and a failed callback must land on the login page with a
reason instead of raw JSON on the API host.
"""
import os
import subprocess
import sys
import textwrap
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi_users.jwt import decode_jwt
from fastapi_users.router.oauth import STATE_TOKEN_AUDIENCE
from httpx_oauth.clients.google import GoogleOAuth2
from httpx_oauth.exceptions import GetIdEmailError
from httpx_oauth.oauth2 import OAuth2Token

from platform_app import oauth_login
from platform_app.config import settings


def _login_page(code: str) -> str:
    return f"{settings.frontend_url}/login?erro={code}"


async def _start(client) -> str:
    r = await client.get("/auth/google/login")
    assert r.status_code == 302, r.text
    return parse_qs(urlparse(r.headers["location"]).query)["state"][0]


def _google_answers(monkeypatch, email: str | None = "pessoa@example.com"):
    async def token(self, *a, **k):
        return OAuth2Token({"access_token": "google-at", "expires_at": time.time() + 600})

    async def id_email(self, *a, **k):
        return "google-sub-123", email

    monkeypatch.setattr(GoogleOAuth2, "get_access_token", token)
    monkeypatch.setattr(GoogleOAuth2, "get_id_email", id_email)


# ── the authorize step ────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_login_sends_the_browser_to_google_with_our_callback(client):
    r = await client.get("/auth/google/login")
    assert r.status_code == 302
    url = urlparse(r.headers["location"])
    q = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth")
    # The redirect_uri registered in Google Console is the callback route — the
    # same one the old /authorize sent — so nothing changes over there.
    assert q["redirect_uri"] == ["http://test/auth/google/callback"]
    assert q["scope"] == ["openid email profile"]
    # A cached 302 would replay one state into two flows.
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.asyncio(loop_scope="session")
async def test_the_state_cookie_lives_exactly_as_long_as_the_state(client):
    """The cookie used to expire at 10 minutes while its state lived an hour: a
    sign-in slowed by a password reset came back to a callback that had forgotten
    it. One number for both."""
    r = await client.get("/auth/google/login")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    exp = decode_jwt(state, settings.secret_key, [STATE_TOKEN_AUDIENCE])["exp"]
    assert abs(exp - time.time() - oauth_login.STATE_TTL_S) < 30
    assert f"Max-Age={oauth_login.STATE_TTL_S}" in r.headers["set-cookie"]


@pytest.mark.asyncio(loop_scope="session")
async def test_the_state_cookie_is_scoped_to_the_callback(client):
    cookie = (await client.get("/auth/google/login")).headers["set-cookie"]
    assert "Path=/auth/google/callback" in cookie
    assert "SameSite=lax" in cookie


def test_production_issues_the_state_cookie_first_party_and_secure():
    """Lax, not None: the cookie is now set on a top-level navigation to the API,
    so it is first-party, and Lax still rides Google's top-level GET back to the
    callback. None is what marked it third-party — the cookie Safari, iOS, Firefox,
    Brave and every Incognito window refuse, so their every login was refused.
    Secure because production is https and the value commits a login."""
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            from starlette.testclient import TestClient
            from platform_app.main import app
            c = TestClient(app, base_url="https://api.example")
            r = c.get("/auth/google/login", follow_redirects=False)
            print(r.status_code)
            print(r.headers["set-cookie"])
        """)],
        env={**os.environ, "ENVIRONMENT": "production", "SECRET_KEY": "x" * 64},
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    status, cookie = out.stdout.strip().splitlines()
    assert status == "302"
    assert "SameSite=lax" in cookie
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/auth/google/callback" in cookie


# ── the callback, by outcome ──────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_a_completed_sign_in_hands_the_token_to_the_spa(client, monkeypatch):
    _google_answers(monkeypatch)
    state = await _start(client)
    r = await client.get("/auth/google/callback", params={"code": "c", "state": state})
    assert r.status_code == 302, r.text
    assert r.headers["location"].startswith(
        f"{settings.frontend_url}/auth/callback#token=")
    assert "Max-Age=0" in r.headers["set-cookie"]  # spent


@pytest.mark.asyncio(loop_scope="session")
async def test_a_leftover_cookie_from_the_old_flow_does_not_refuse_the_new_one(
    client, monkeypatch
):
    """For a few minutes after the deploy a browser can carry the old Path=/ cookie
    next to the new one. Starlette keeps only the last of two same-named cookies,
    which would be the stale one: a refused login that only clearing cookies fixes."""
    _google_answers(monkeypatch)
    r = await client.get("/auth/google/login")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    good = oauth_login.state_digest(state)
    stale = oauth_login.state_digest("a-state-from-ten-minutes-ago")
    r = await client.get(
        "/auth/google/callback",
        params={"code": "c", "state": state},
        headers={"cookie": f"oauth_state={good}; oauth_state={stale}"},
    )
    assert r.headers["location"].startswith(f"{settings.frontend_url}/auth/callback#")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(("error", "code"), [
    ("access_denied", "cancelado"),
    ("server_error", "google"),
])
async def test_google_reporting_an_error_lands_on_the_login_page(client, error, code):
    state = await _start(client)
    r = await client.get(
        "/auth/google/callback", params={"error": error, "state": state})
    assert r.status_code == 302
    assert r.headers["location"] == _login_page(code)
    assert "Max-Age=0" in r.headers["set-cookie"]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_google_account_without_an_email_is_told_so(client, monkeypatch):
    _google_answers(monkeypatch, email=None)
    state = await _start(client)
    r = await client.get("/auth/google/callback", params={"code": "c", "state": state})
    assert r.headers["location"] == _login_page("email")


@pytest.mark.asyncio(loop_scope="session")
async def test_a_crash_inside_the_handler_still_lands_on_the_login_page(
    client, monkeypatch
):
    _google_answers(monkeypatch)

    async def broken(self, *a, **k):
        raise GetIdEmailError("people api down")

    monkeypatch.setattr(GoogleOAuth2, "get_id_email", broken)
    state = await _start(client)
    r = await client.get("/auth/google/callback", params={"code": "c", "state": state})
    assert r.status_code == 302
    assert r.headers["location"] == _login_page("google")


@pytest.mark.asyncio(loop_scope="session")
async def test_a_callback_without_a_code_is_a_plain_failure(client):
    state = await _start(client)
    r = await client.get("/auth/google/callback", params={"state": state})
    assert r.headers["location"] == _login_page("falha")


@pytest.mark.parametrize(("status", "body", "code"), [
    (400, b'{"detail": "LOGIN_BAD_CREDENTIALS"}', "conta"),
    (400, b'{"detail": "OAUTH_USER_ALREADY_EXISTS"}', "conta"),
    (400, b'{"detail": "OAUTH_NOT_AVAILABLE_EMAIL"}', "email"),
    (400, b'{"detail": null}', "falha"),
    (400, b'["not", "a", "dict"]', "falha"),
    (400, b"<html>", "falha"),
    (500, b'{"detail": "invalid_grant"}', "google"),
])
def test_every_handler_refusal_has_a_code(status, body, code):
    assert oauth_login.failure_code(status, body) == code
