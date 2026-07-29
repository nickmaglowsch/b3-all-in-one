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
    so production must not expose them (nor dev-login, nor dev-plan).

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
                  "/billing/dev-plan"):
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
