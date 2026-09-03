"""Reader for Massive's (formerly Polygon.io) free grouped-daily endpoint.

This exists because of the same arithmetic `stooq_archive`'s header note set
out, arrived at from the other end. The US plane holds roughly thirteen
thousand symbols; no free per-symbol feed can refresh that daily, and the bulk
file that seeded the history cannot be fetched by a machine — the vendor put a
browser proof-of-work in front of it, so the dump arrives by hand or not at all.

What a daily refresh actually needs is not a symbol, it is a *day*. This
endpoint serves exactly that: one request returns the open/high/low/close of
every ticker that traded on a given session, which costs one call against a
free tier that allows five a minute. The history comes from the manual dump;
this keeps it current.

Two properties are load-bearing.

**The bars are split-adjusted as of the request.** `adjusted=true` asks for the
vendor's adjusted series, which is the basis the stored history already carries
(`split_only`). That match is not a convenience — appending a raw close onto a
split-adjusted history invents a return at the seam, which is the failure
`market_storage.delete_market_prices_from_other_sources` exists to prevent.

**Adjustment drifts, and that is why `splits` is here too.** The vendor
re-adjusts *its* history when a split executes; the stored history does not
move on its own. So a symbol that splits tomorrow has a correct vendor series
and a stale local one, joined at the split date by a split-sized jump — exactly
the artefact `MARKETS["US"].glitch_down` is built to mask, which is what makes
it dangerous rather than merely wrong. `fetch_splits` reports what executed so
`us_daily` can re-scale the history that predates it.

**"Too early" is not an error.** The free tier refuses a session that has not
closed, by name (`NOT_AUTHORIZED`, "before end of day"). A cron that runs at
03:00 UTC is asking about a New York session that is still the same calendar
day, so this is the *expected* answer near the boundary and is raised as
`NotSettled` for the caller to treat as "come back later", not as a failure.
"""

import datetime
import logging
import os
import time

import pandas as pd
import requests

logger = logging.getLogger(__name__)

SOURCE = "massive-daily"
ADJUST_STATE = "split_only"
SOURCE_PREFIX = SOURCE
MARKET = "US"
CURRENCY = "USD"

# The brand is Massive; the API host is still the polygon.io one it rebranded
# from, and https://polygon.io 301s to https://massive.com. Pinned to the host
# that actually answers rather than the one on the marketing site.
_HOST = "https://api.polygon.io"
GROUPED_URL = _HOST + "/v2/aggs/grouped/locale/us/market/stocks/{date}"
SPLITS_URL = _HOST + "/v3/reference/splits"

_TIMEOUT = 120

# Free tier is five requests a minute. Only the splits walk can page, so only it
# can hit this; the grouped call is one request a day.
_SLEEP_BETWEEN = float(os.environ.get("MASSIVE_DELAY_SECONDS") or 13.0)

# A US session prints five figures of tickers. A response with *some* results
# but far fewer is a truncated or partial answer, not a quiet market, and is
# refused for the reason `stooq_archive.MIN_PLAUSIBLE_SYMBOLS` refuses a small
# archive. Zero results is different and legitimate — see `parse_grouped`.
MIN_PLAUSIBLE_TICKERS = 3_000

# Response keys, pinned. Read by name and never by position, and a payload
# missing one raises rather than yielding a column of NaN.
_FIELDS = {"T": "symbol", "o": "open", "h": "high", "l": "low",
           "c": "close", "v": "share_volume"}


class NotSettled(RuntimeError):
    """The session asked for has not closed yet on this tier."""


class Throttled(RuntimeError):
    """The vendor's rate or request ceiling is spent."""


def api_key() -> str:
    key = os.environ.get("MASSIVE_API_KEY") or ""
    if not key:
        raise RuntimeError(
            "MASSIVE_API_KEY is unset — the US daily refresh cannot run. A free "
            "key is issued at https://massive.com and belongs in .env.prod, "
            "which is gitignored and survives a deploy.")
    return key


def _get(url: str, params: dict) -> dict:
    params = dict(params, apiKey=api_key())
    r = requests.get(url, params=params, timeout=_TIMEOUT)
    if r.status_code == 429:
        raise Throttled(f"{SOURCE}: rate limited")
    if r.status_code == 403:
        body = r.text or ""
        if "before end of day" in body or "NOT_AUTHORIZED" in body:
            raise NotSettled(body.strip()[:300])
        raise Throttled(f"{SOURCE}: forbidden — {body.strip()[:300]}")
    r.raise_for_status()
    return r.json()


def fetch_grouped(date, adjusted: bool = True) -> dict:
    """The whole market's bars for one session. One request."""
    day = pd.Timestamp(date).date() if not isinstance(date, datetime.date) else date
    logger.info(f"{SOURCE}: grouped daily for {day}")
    return _get(GROUPED_URL.format(date=day),
                {"adjusted": "true" if adjusted else "false"})


def parse_grouped(payload: dict, date) -> pd.DataFrame:
    """Grouped payload -> `[symbol, date, open, high, low, close, share_volume]`.

    An empty result set is returned empty rather than raised: a weekend, a
    holiday or a date before the market existed all answer that way, and a
    refresh that treats "no session" as a failure would page an operator every
    Saturday. A *small* result set is raised on — see `MIN_PLAUSIBLE_TICKERS`.
    """
    day = pd.Timestamp(date).date() if not isinstance(date, datetime.date) else date
    if "results" not in payload:
        raise ValueError(
            f"{SOURCE}({day}): payload has no 'results' — the response shape "
            f"changed. Keys seen: {sorted(payload)[:12]}")
    rows = payload["results"] or []
    if not rows:
        logger.info(f"{SOURCE}: no session on {day}")
        return pd.DataFrame(
            columns=["symbol", "date", "open", "high", "low", "close",
                     "share_volume"])
    if len(rows) < MIN_PLAUSIBLE_TICKERS:
        raise ValueError(
            f"{SOURCE}({day}): only {len(rows):,} tickers, under the "
            f"{MIN_PLAUSIBLE_TICKERS:,} floor — a truncated or partial "
            "response, not a market. Nothing was written.")

    missing = [k for k in _FIELDS if k not in rows[0]]
    if missing:
        raise ValueError(
            f"{SOURCE}({day}): rows are missing {missing} — the response shape "
            f"changed. Keys seen: {sorted(rows[0])}")

    df = pd.DataFrame(rows)[list(_FIELDS)].rename(columns=_FIELDS)
    for col in ("open", "high", "low", "close", "share_volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df["date"] = day
    df = df.dropna(subset=["symbol", "close"])
    return df.reset_index(drop=True)


def fetch_splits(since, until=None, limit: int = 1000) -> list:
    """Splits executing in `[since, until]`, newest first, following pages.

    The window is on `execution_date` rather than on an announcement timestamp
    because execution is when the price series actually moves; an announcement
    two months ahead must not re-scale anything yet.
    """
    params = {"execution_date.gte": str(since), "limit": limit,
              "order": "asc", "sort": "execution_date"}
    if until is not None:
        params["execution_date.lte"] = str(until)
    out, url, page = [], SPLITS_URL, 0
    while url:
        payload = _get(url, params if page == 0 else {})
        if "results" not in payload:
            raise ValueError(
                f"{SOURCE}: splits payload has no 'results' — shape changed. "
                f"Keys seen: {sorted(payload)[:12]}")
        for r in payload["results"] or []:
            missing = [k for k in ("id", "ticker", "execution_date",
                                   "split_from", "split_to") if k not in r]
            if missing:
                raise ValueError(
                    f"{SOURCE}: split record missing {missing} — shape "
                    f"changed. Keys seen: {sorted(r)}")
            out.append(r)
        url, page = payload.get("next_url"), page + 1
        if url:
            time.sleep(_SLEEP_BETWEEN)
    logger.info(f"{SOURCE}: {len(out):,} splits since {since}")
    return out


def split_factor(split: dict) -> float:
    """`split_from / split_to` — what pre-execution prices must be scaled by.

    A 4-for-1 arrives as from=1, to=4 and scales old prices by 0.25; a 1-for-50
    reverse as from=50, to=1 and scales by 50. Raised on rather than defaulted:
    a zero or absent leg would silently zero a price history.
    """
    a, b = float(split["split_from"]), float(split["split_to"])
    if a <= 0 or b <= 0:
        raise ValueError(
            f"{SOURCE}: {split.get('ticker')} split {split.get('id')} has "
            f"split_from={a}, split_to={b} — refusing to scale a history by it.")
    return a / b
