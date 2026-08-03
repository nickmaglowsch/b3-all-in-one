"""Regression tests for the auth-surface findings from the security review.

Each test is the attack, not the implementation: if someone re-mounts the
password routers in production or drops the OAuth state cookie, these fail.
"""
import hashlib
import importlib
import os
import subprocess
import sys
import textwrap

import pytest
from starlette.requests import Request


@pytest.mark.asyncio(loop_scope="session")
async def test_oauth_callback_rejects_state_from_another_browser(client):
    """Login CSRF: the state token alone must not be enough.

    The attacker runs the flow themselves and gets a validly-signed state, then
    feeds the victim the callback URL. Without a matching cookie — which the
    attacker cannot set on our origin — the callback must refuse, or the victim
    gets logged into the attacker's tenant and imports their extracts into it.
    """
    r = await client.get(
        "/auth/google/callback",
        params={"code": "attacker-code", "state": "a-validly-signed-state"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "INVALID_STATE_TOKEN"


@pytest.mark.asyncio(loop_scope="session")
async def test_oauth_callback_rejects_mismatched_cookie(client):
    """A cookie from a *different* flow must not authorise this state either."""
    r = await client.get(
        "/auth/google/callback",
        params={"code": "c", "state": "state-B"},
        cookies={"oauth_state": hashlib.sha256(b"state-A").hexdigest()},
    )
    assert r.status_code == 400


def test_state_cookie_is_storable_from_a_cross_site_fetch():
    """The SPA is on another site (pages.dev → duckdns.org), so /authorize is a
    cross-site fetch and a SameSite=Lax Set-Cookie is discarded on arrival — the
    callback then 400s on every single login. Production must issue the cookie
    as SameSite=None; Secure, or the flow is broken for everyone."""
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            from platform_app.main import _COOKIE_SITE
            print(_COOKIE_SITE)
        """)],
        env={**os.environ, "ENVIRONMENT": "production", "SECRET_KEY": "x" * 64},
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert "'samesite': 'none'" in out.stdout
    assert "'secure': True" in out.stdout  # None without Secure is rejected too


def test_password_and_dev_routes_are_not_mounted_in_production():
    """`/auth/register` + `associate_by_email` let anyone claim a stranger's email
    and inherit the account when that person later signs in with Google — with no
    password-reset router mounted to evict them. Neither route is used by the SPA,
    so production must not expose them (nor dev-login, nor dev-modules).

    A fresh interpreter, not importlib.reload: the dev-only routes are attached by
    module-level `if settings.is_dev` blocks that run exactly once per process, so
    a reload inside this session would report the development app's routes and
    pass whether or not the gate works.
    """
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            from platform_app.main import app
            print("\\n".join(app.openapi()["paths"]))
        """)],
        env={**os.environ, "ENVIRONMENT": "production", "SECRET_KEY": "x" * 64},
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    paths = set(out.stdout.split())
    for route in ("/auth/register", "/auth/jwt/login", "/auth/dev-login",
                  "/billing/dev-modules"):
        assert route not in paths, f"{route} is mounted in production"
    assert "/auth/google/authorize" in paths  # ...and Google login still is


@pytest.mark.parametrize("secret", ["", "change-me", "dev-secret-change-me"])
def test_production_refuses_an_unsafe_secret_key(monkeypatch, secret):
    """`SECRET_KEY: ${SECRET_KEY}` interpolates to "" when the var is missing, and
    PyJWT signs *and verifies* HS256 with a zero-length key — so the stack would
    boot green while anyone could mint a token for any tenant. It must not boot."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", secret)

    import platform_app.config as config

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        importlib.reload(config)

    monkeypatch.undo()
    importlib.reload(config)


@pytest.mark.asyncio(loop_scope="session")
async def test_the_authorize_response_commits_this_browser_to_that_state(client):
    """The other half of the CSRF binding: the cookie has to be issued, and it has
    to be issued over the state actually inside the URL the browser is sent to.
    A digest of anything else 400s every real login instead of only the forged one.
    """
    from urllib.parse import parse_qs, urlparse

    r = await client.get("/auth/google/authorize")
    state = parse_qs(urlparse(r.json()["authorization_url"]).query)["state"][0]
    cookie = r.headers["set-cookie"]

    assert f"oauth_state={hashlib.sha256(state.encode()).hexdigest()}" in cookie
    # The value is only ever compared server-side; script access buys nothing but
    # an XSS a step closer to a login.
    assert "HttpOnly" in cookie


@pytest.mark.asyncio(loop_scope="session")
async def test_a_matching_state_is_let_through_and_the_cookie_is_spent(
    client, monkeypatch
):
    """A callback URL is not a secret — it lands in history, in a referrer, in a
    screenshot. Clearing the cookie on the way out is what stops a second use of
    the same one from logging anyone in."""
    from urllib.parse import parse_qs, urlparse

    from httpx_oauth.clients.google import GoogleOAuth2
    from httpx_oauth.oauth2 import GetAccessTokenError

    async def refuse(self, *a, **k):
        raise GetAccessTokenError("google not reachable from a test")

    monkeypatch.setattr(GoogleOAuth2, "get_access_token", refuse)

    r = await client.get("/auth/google/authorize")
    state = parse_qs(urlparse(r.json()["authorization_url"]).query)["state"][0]

    first = await client.get(
        "/auth/google/callback", params={"code": "c", "state": state}
    )
    # It reached the real handler — which is where the (stubbed) exchange failed.
    assert first.json().get("detail") != "INVALID_STATE_TOKEN"

    replay = await client.get(
        "/auth/google/callback", params={"code": "c", "state": state}
    )
    assert replay.status_code == 400
    assert replay.json()["detail"] == "INVALID_STATE_TOKEN"


@pytest.mark.asyncio(loop_scope="session")
async def test_an_authorize_body_without_a_state_still_reaches_the_browser():
    """The state is read back out of a response this middleware does not own. If
    that shape ever changes, login must degrade to "no cookie" — not to a 500 on
    the only way into the product."""
    from starlette.responses import StreamingResponse

    from platform_app import main

    async def body():
        yield b'{"nothing_we_recognise": true}'

    async def call_next(_request):
        return StreamingResponse(body(), status_code=200, media_type="application/json")

    request = Request({
        "type": "http", "method": "GET", "path": main._AUTHORIZE_PATH,
        "query_string": b"", "headers": [], "scheme": "http", "server": ("test", 80),
    })
    out = await main.bind_oauth_state(request, call_next)

    assert out.status_code == 200
    assert out.body == b'{"nothing_we_recognise": true}'
    assert "oauth_state" not in out.headers.get("set-cookie", "")


@pytest.mark.asyncio(loop_scope="session")
async def test_the_session_token_is_handed_over_in_the_url_fragment():
    """A query string is written verbatim into Cloudflare's access log and every
    proxy in between, and this token is an hour of full account access that
    nothing can revoke. Fragments are never sent to a server at all."""
    from platform_app.users import RedirectTransport

    r = await RedirectTransport("https://app.example/auth/callback").get_login_response(
        "jwt-123"
    )
    assert r.status_code == 302
    assert r.headers["location"] == "https://app.example/auth/callback#token=jwt-123"
    assert "?" not in r.headers["location"]


@pytest.mark.asyncio(loop_scope="session")
async def test_there_is_no_server_side_logout_to_pretend_otherwise():
    """JWTStrategy cannot revoke a token, so a /logout route would tell the user
    the session is over while the token keeps working until it expires."""
    from fastapi_users.authentication.transport import TransportLogoutNotSupportedError

    from platform_app.users import RedirectTransport

    with pytest.raises(TransportLogoutNotSupportedError):
        await RedirectTransport("https://app.example").get_logout_response()
