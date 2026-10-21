"""Google sign-in: the authorize step, the state ↔ browser binding, and what a
failed callback shows the person.

── Why the authorize step is a navigation, not a fetch ─────────────────────────
fastapi-users signs the `state` token over an EMPTY payload, so it proves only
"this server issued a state" — never "*this* browser started the flow". An
attacker could run the flow with their own Google account, capture the callback
URL before fetching it, and hand it to a victim: the victim's browser completes a
login into the ATTACKER's tenant, and every extract they import afterwards lands
in the attacker's book. So the state is bound to the browser with a cookie only
we can set: a digest of the state, checked on the callback.

That cookie used to be issued on the response to a cross-site *fetch* of
/auth/google/authorize from the SPA (pages.dev → duckdns.org, two sites on the
public suffix list). That makes it a third-party cookie, and the browsers that
block those — Safari and everything on iOS, Firefox's partitioned jars, Brave,
any Incognito window — never sent it back: every Google login there ended on
INVALID_STATE_TOKEN, and clearing cookies could not help.

GET /auth/google/login is the same authorize step as a *top-level navigation*:
the browser lands on our host, we set the cookie first-party (SameSite=Lax, which
Google's redirect back to the callback — a top-level GET — still carries) and
302 it on to Google. The SPA no longer touches this cookie at all.

── Failures land on the login page ─────────────────────────────────────────────
Every way the callback can fail used to leave the browser on raw JSON at the API
host with no way back but Back. They now 302 to `{FRONTEND_URL}/login?erro=<code>`
and the login screen says what happened. The codes are a closed set, mirrored in
frontend/lib/loginErrors.ts.
"""

import hashlib
import hmac
import json
import logging

import jwt
from fastapi import APIRouter, Request
from fastapi_users.jwt import decode_jwt
from fastapi_users.router.oauth import STATE_TOKEN_AUDIENCE, generate_state_token
from starlette.responses import RedirectResponse, Response

from platform_app.config import settings
from platform_app.users import get_google_oauth_client, oauth_backend

log = logging.getLogger(__name__)

STATE_COOKIE = "oauth_state"
LOGIN_PATH = "/auth/google/login"
CALLBACK_PATH = "/auth/google/callback"
# The name fastapi-users gives the callback route it generates; url_for on it is
# what keeps redirect_uri byte-identical to the one registered with Google.
CALLBACK_ROUTE = f"oauth:google.{oauth_backend.name}.callback"
SCOPES = ["openid", "email", "profile"]
# One number for both halves. The cookie used to die at 10 minutes while the state
# it vouches for lived an hour, so a sign-in slowed down by a password reset or a
# 2FA prompt on another device came back to a callback that no longer knew it.
STATE_TTL_S = 3600

# The callback is the only place this cookie is ever read, so it is the only path
# it is ever sent to. Secure everywhere but dev, which is plain http on localhost.
_COOKIE_ATTRS = {
    "path": CALLBACK_PATH,
    "httponly": True,
    "samesite": "lax",
    "secure": not settings.is_dev,
}

# fastapi-users' error details → the login page's codes. Anything not listed is
# "falha": a reason we do not have a sentence for is still a failed login.
_DETAIL_CODES = {
    "OAUTH_NOT_AVAILABLE_EMAIL": "email",
    "LOGIN_BAD_CREDENTIALS": "conta",
    "OAUTH_USER_ALREADY_EXISTS": "conta",
}

router = APIRouter(tags=["auth"])


def state_digest(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


@router.get(LOGIN_PATH, status_code=302, response_class=RedirectResponse)
async def google_login(request: Request) -> RedirectResponse:
    """Start a Google sign-in: bind a fresh state to this browser, go to Google."""
    state = generate_state_token({}, settings.secret_key, STATE_TTL_S)
    url = await get_google_oauth_client().get_authorization_url(
        str(request.url_for(CALLBACK_ROUTE)), state, SCOPES
    )
    response = RedirectResponse(url, status_code=302)
    # no-store: a 302 that carries a one-time binding must never be replayed from
    # a cache, or two tabs would race over the same state.
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        STATE_COOKIE, state_digest(state), max_age=STATE_TTL_S, **_COOKIE_ATTRS
    )
    return response


def to_login(code: str) -> RedirectResponse:
    return RedirectResponse(f"{settings.frontend_url}/login?erro={code}", status_code=302)


def _bound_digests(request: Request) -> list[str]:
    """Every oauth_state value the browser sent, not just one.

    The cookie issued before this module existed lived at Path=/; for its last
    few minutes a browser can carry both, and Starlette's request.cookies keeps
    only the last — which would be the stale one, and a refused login that only
    clearing cookies would fix. Read the header ourselves and accept any match.
    """
    values = []
    for part in request.headers.get("cookie", "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == STATE_COOKIE and value:
            values.append(value)
    return values


def _state_is_ours(request: Request) -> bool:
    presented = request.query_params.get("state", "")
    digest = state_digest(presented)
    # compare_digest: the comparison is against a value the caller supplies.
    if not any(hmac.compare_digest(digest, v) for v in _bound_digests(request)):
        return False
    try:
        decode_jwt(presented, settings.secret_key, [STATE_TOKEN_AUDIENCE])
    except jwt.PyJWTError:
        # Expired, mostly. Upstream only catches DecodeError, so an expired state
        # would otherwise reach Google for a code exchange and then 500.
        return False
    return True


def _refusal(request: Request) -> str | None:
    """Why this callback must not reach the handler, or None to let it through."""
    error = request.query_params.get("error")
    if error:
        # Google's own redirect for "the person clicked Cancel". Checked before the
        # state because it logs nobody in, and "you cancelled" is the true answer.
        return "cancelado" if error == "access_denied" else "google"
    if not _state_is_ours(request):
        return "estado"
    return None


def failure_code(status: int, body: bytes) -> str:
    """The login page's code for a callback the handler refused."""
    if status >= 500:
        return "google"  # the code exchange with Google failed
    try:
        detail = json.loads(body).get("detail")
    except (ValueError, AttributeError):
        detail = None
    return _DETAIL_CODES.get(detail, "falha") if isinstance(detail, str) else "falha"


async def _complete(request: Request, call_next) -> Response:
    try:
        response = await call_next(request)
    except Exception:
        # get_id_email and friends raise straight through the router. The person
        # still gets a page that says what happened; we get the traceback.
        log.exception("google oauth callback failed")
        return to_login("google")
    if 300 <= response.status_code < 400:
        return response  # RedirectTransport: the token is on its way to the SPA
    body = b"".join([chunk async for chunk in response.body_iterator])
    return to_login(failure_code(response.status_code, body))


async def bind_oauth_state(request: Request, call_next):
    """HTTP middleware: refuse a callback whose state this browser did not start,
    and turn every failed callback into a trip back to the login page."""
    if request.url.path != CALLBACK_PATH:
        return await call_next(request)
    code = _refusal(request)
    response = to_login(code) if code else await _complete(request, call_next)
    # Spent on every outcome. A callback URL is not a secret — it lands in
    # history, in a referrer, in a screenshot — so the binding is single-use.
    response.delete_cookie(STATE_COOKIE, **_COOKIE_ATTRS)
    return response
