"""The metered client the US plane buys its prices and its universe through.

Two things about this vendor shape the whole module.

**A 200 is not a payload.** Alpha Vantage answers HTTP 200 with a small JSON
envelope when it throttles you, when the key is wrong, when the endpoint is
premium-only and when the call itself is malformed — including when the request
asked for `datatype=csv`. Handed straight to pandas, `{"Note": "..."}` parses as
a one-column frame and lands in the database as a row. So every response goes
through `_guard`, which is this module's version of the check
`sec_downloader.download_bulk_facts` already makes with `zipfile.is_zipfile`
because EDGAR answers 200 with an HTML error page. Same lesson, different
vendor: a status code is not evidence of a payload.

**The free tier is 25 requests a day.** Not a rate to smooth out — a hard daily
ceiling, against a universe of roughly eleven thousand symbols. That makes the
budget a first-class object rather than a `time.sleep`: it lives in the database
(`api_call_budget`), because an in-memory counter double-spends the moment the
nightly job and a hand-run ingest land on the same day, and at 25 a day that is
the difference between progress and none.

Both tiers run this code unchanged. Free is the default; a paid key is
`ALPHAVANTAGE_RATE_PER_MIN=75` and `ALPHAVANTAGE_DAILY_BUDGET=0`, and nothing
else in the repository knows which one it is on.
"""

import datetime
import json
import logging
import os
import re
import time

import requests
from b3_pipeline import us_storage

logger = logging.getLogger(__name__)

BASE_URL = "https://www.alphavantage.co/query"
SOURCE = "alphavantage"

_TIMEOUT = 120

# Free tier: 25 requests/day. Premium starts at 75/min with no daily cap.
_DEFAULT_RATE_PER_MIN = 5.0
_DEFAULT_DAILY_BUDGET = 25

# The refusal keys, in the order they are checked. Which condition arrives under
# which key is the single most important thing for the fixture capture to pin
# down (scripts/capture_us_fixtures.py writes `refusal_*.json`): if a bad key
# were to arrive as "Information" it would be classified retryable and burn the
# budget forever. Until a real sample says otherwise, "Error Message" is the
# vendor's documented key for a malformed call and the other two are its
# throttle and quota envelopes.
_REFUSAL_KEYS = ("Error Message", "Note", "Information")

# Substrings that mark an "Information" envelope as a permanent refusal rather
# than a temporary one. Deliberately narrow: misreading a throttle as permanent
# would abandon a symbol that was only busy.
_PREMIUM_MARKERS = ("premium", "subscribe")


class AlphaVantageError(RuntimeError):
    """The vendor did not return the payload that was asked for."""


class BadRequest(AlphaVantageError):
    """The call itself was refused. Retrying it unchanged cannot help."""


class PremiumOnly(AlphaVantageError):
    """The endpoint exists but this key cannot reach it. Not a failure of the
    run — a fact about coverage, which the caller records rather than retries."""


class RateLimited(AlphaVantageError):
    """Throttled or out of quota. The symbol is fine; come back later."""


class BudgetExhausted(AlphaVantageError):
    """Local, not remote: today's allowance is spent.

    Never an error in a run's ledger. Under the free tier it is the *expected*
    end of a day's work, and a pipeline that reported it as a failure would mark
    every single night red.
    """


class MissingApiKey(RuntimeError):
    """No key configured.

    Unlike the SEC's User-Agent there is no sensible default to fall back to:
    a blank key does not degrade, it 200s with a refusal envelope on every call.
    """


def api_key() -> str:
    """The configured key, read at call time so tests can set it per-case.

    `.strip()` for the same reason `sec_downloader.user_agent` strips: docker
    compose interpolates an unset variable to the empty string, and a key of
    `""` is not a key.
    """
    key = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
    if not key:
        raise MissingApiKey(
            "ALPHAVANTAGE_API_KEY is unset. The US price and listing ingests "
            "need one; a free key is issued at alphavantage.co/support/#api-key "
            "and goes in .env next to SEC_USER_AGENT.")
    return key


def rate_per_min() -> float:
    return float(os.environ.get("ALPHAVANTAGE_RATE_PER_MIN") or _DEFAULT_RATE_PER_MIN)


def daily_budget() -> int:
    """Requests allowed per day. 0 means no local ceiling (a paid key)."""
    raw = os.environ.get("ALPHAVANTAGE_DAILY_BUDGET")
    return _DEFAULT_DAILY_BUDGET if raw is None or raw == "" else int(raw)


def _classify(body: str, what: str) -> AlphaVantageError:
    """A refusal envelope -> the exception that says what to do about it."""
    try:
        payload = json.loads(body)
    except ValueError:
        return AlphaVantageError(f"{what}: unparseable body {body[:160]!r}")
    if not isinstance(payload, dict):
        return AlphaVantageError(f"{what}: expected an object, got {type(payload).__name__}")
    for key in _REFUSAL_KEYS:
        if key not in payload:
            continue
        msg = str(payload[key])
        if key == "Error Message":
            return BadRequest(f"{what}: {msg}")
        if any(m in msg.lower() for m in _PREMIUM_MARKERS):
            return PremiumOnly(f"{what}: {msg}")
        return RateLimited(f"{what}: {msg}")
    return AlphaVantageError(
        f"{what}: unrecognised JSON envelope with keys {sorted(payload)[:6]}")


def _guard_csv(body: str, what: str) -> str:
    """The CSV body, or a typed refusal.

    A CSV never begins with `{`, `[` or `<`. That is the whole heuristic, and it
    is enough because the three things that can arrive instead of CSV are a JSON
    refusal envelope, a JSON payload from a misrouted call, and an HTML page from
    something in front of the API.
    """
    head = body.lstrip()[:1]
    if head in ("{", "["):
        raise _classify(body, what)
    if head in ("<", ""):
        raise AlphaVantageError(
            f"{what}: the response is neither CSV nor JSON ({body[:120]!r}). "
            "Most likely a proxy or WAF page; nothing was parsed.")
    return body


def _guard_json(body: str, what: str, expect: str) -> dict:
    """The JSON payload, or a typed refusal.

    A JSON endpoint cannot use the brace heuristic — a refusal and a payload are
    both objects — so this needs the positive check instead: the key the endpoint
    is documented to return has to be there.
    """
    try:
        payload = json.loads(body)
    except ValueError:
        raise AlphaVantageError(
            f"{what}: expected JSON, got {body[:120]!r}") from None
    if isinstance(payload, dict) and expect in payload:
        return payload
    raise _classify(body, what)


class Client:
    """One metered conversation with the vendor.

    Holds the database connection because the budget is durable, and paces
    itself between requests. Construct one per run and pass it down; two
    clients on the same connection share the same budget correctly, which is
    the point of keeping it out of the instance.

    `conn=None` is for a one-off tool that runs nowhere near a plane — the
    fixture capture script. It means there is nothing to count against, so
    there is no local ceiling either, and the vendor's own refusal becomes the
    only limit. Never the right thing for a pipeline.
    """

    def __init__(self, conn, *, key=None, per_min=None, budget=None,
                 today=None, reserve: int = 0):
        self._conn = conn
        self._key = key or api_key()
        self._per_min = float(per_min if per_min is not None else rate_per_min())
        self._budget = 0 if conn is None else int(
            budget if budget is not None else daily_budget())
        self._today = today or datetime.date.today()
        # Headroom left for the other jobs sharing this key. The price backfill
        # would otherwise spend the whole day's allowance before the listing and
        # benchmark jobs ever run.
        self._reserve = int(reserve)
        self._last = 0.0
        self._untracked = 0

    @property
    def spent(self) -> int:
        if self._conn is None:
            return self._untracked
        return us_storage.calls_spent(self._conn, SOURCE, self._today)

    @property
    def remaining(self) -> int:
        """Requests this client may still make. -1 when there is no ceiling."""
        if self._budget <= 0:
            return -1
        return max(0, self._budget - self._reserve - self.spent)

    def _charge(self, what: str) -> None:
        if self._budget > 0 and self.remaining <= 0:
            raise BudgetExhausted(
                f"{what}: {self.spent}/{self._budget} requests spent today"
                f"{f' (reserving {self._reserve})' if self._reserve else ''}. "
                "Not a failure — the backfill resumes tomorrow from the cursor.")
        if self._conn is None:
            self._untracked += 1
            return
        us_storage.charge_call(self._conn, SOURCE, self._today)

    def _pace(self) -> None:
        gap = 60.0 / self._per_min if self._per_min > 0 else 0.0
        wait = gap - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _fetch(self, function: str, params: dict) -> str:
        what = f"{function}({params.get('symbol') or params.get('state') or ''})"
        self._charge(what)
        self._pace()
        query = {"function": function, **params, "apikey": self._key}
        try:
            r = requests.get(BASE_URL, params=query, timeout=_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as exc:
            # The URL carries the key; never let it reach a log or a stored
            # ingest error.
            raise AlphaVantageError(f"{what}: {_scrub(str(exc))}") from None
        return r.text

    def get_csv(self, function: str, **params) -> str:
        return _guard_csv(self._fetch(function, params),
                          f"{function}({params.get('symbol', '')})")

    def get_json(self, function: str, *, expect: str = "data", **params) -> dict:
        return _guard_json(self._fetch(function, params),
                           f"{function}({params.get('symbol', '')})", expect)


def _scrub(text: str) -> str:
    """Remove an apikey= value from anything about to be logged or stored."""
    return re.sub(r"(apikey=)[^&\s]+", r"\1***", text)
