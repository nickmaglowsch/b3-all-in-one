"""Reader for Stooq's free per-symbol daily quotes.

The second attempt at this vendor, and the first one that can actually be
fetched. `us_backfill` was originally written against the bulk dump
`d_us_txt.zip`; that URL 404s, the real one is `/db/d/?b=d_us_txt`, and behind
it sits a JavaScript proof-of-work challenge and then "Unauthorized" — every
file under `/db/` needs an account, the small ones included. The US plane came
out of that with 13,181 instruments and zero bars.

The quote-download endpoint is a different service on the same host and is not
gated: `/q/d/l/?s=aapl.us&i=d` returns one symbol's whole daily history as CSV,
no key, no account. It is what `pandas-datareader`'s Stooq reader has used for
years. The cost is one request per symbol instead of one for the market, which
makes this a walk rather than a download — the same shape `crypto_pipeline`
already has with the Binance bucket, and for the same reason: a free source that
charges in wall-clock instead of money.

**What it publishes, and the two limits that follow.**

*The bars are split-adjusted.* Stooq back-adjusts to its own last day, so the
`close` here is not the raw close the metered feed returns and not the raw close
WIKI ships. Bars land `split_only`, and `us_prices` must never re-price a symbol
this module covered — `covered_symbols` is what enforces it, matched on
`SOURCE_PREFIX` so rows written by the old bulk path fence identically.

*It carries no distributions.* `split_only` is therefore the ceiling here, and
the upgrade to `total_return` is the metered feed's two-request corporate-actions
call. That is exactly what the free tier's budget is for once it has stopped
buying bars: `demote_bulk_priced` routes it.

**Two answers that are not CSV, and both must be caught.** Anonymous callers
have a daily hit ceiling; past it the body is the plain sentence "Exceeded the
daily hits limit", served with a 200. An unknown symbol answers "No data". Both
would otherwise reach `read_csv` and parse as a symbol with no history — a
throttled run would silently record eleven thousand empty symbols and look like
a success. `classify_response` names which of the three a body is, and
`run_pipeline` treats the throttle as the end of the day's work rather than as
a failure, the way `us_prices` treats an exhausted request budget.

Columns are read by header name, never by position, and both spellings Stooq
serves are accepted — this endpoint's CSV uses `Date,Open,...` with ISO dates,
while the bulk `.txt` members used `<TICKER>,<PER>,<DATE>,...` with `YYYYMMDD`
ones. That tolerance is kept rather than narrowed to the endpoint in hand: it
costs two tuples, and it is the half of the original module that was right. A
file matching neither raises and names what it saw, rather than guessing.
"""

import io
import logging
import os
import time

import pandas as pd
import requests

logger = logging.getLogger(__name__)

SOURCE = "stooq-daily"
MARKET = "US"
CURRENCY = "USD"

# Every source tag this vendor has ever written, `stooq-bulk` included. What
# `covered_symbols` matches on, so a plane carrying rows from the bulk attempt
# fences those symbols off from the metered feed exactly as it does new ones —
# both conventions are split-adjusted and neither may be spliced with a raw one.
SOURCE_PREFIX = "stooq"

QUOTE_URL = "https://stooq.com/q/d/l/"

_TIMEOUT = 60

# Politeness, not correctness. This is a small site serving a whole market one
# symbol at a time for free; a request every half second is ~27 minutes for the
# three thousand names that matter and leaves the host alone. Overridable
# because the right value is a property of the host's mood, not of this code.
DEFAULT_DELAY_SECONDS = 0.5

# The bodies Stooq returns instead of CSV, lowercased for comparison. Matched as
# prefixes on a short body rather than by equality: the wording has picked up
# trailing punctuation and whitespace before, and being wrong here means storing
# an error message as a price series.
_THROTTLE_MARKERS = ("exceeded the daily hits limit",)
_EMPTY_MARKERS = ("no data",)

# Longer than any of those sentences and shorter than any real symbol's history.
# A body above this is CSV or a malformed answer, and either way not a marker.
_MARKER_MAX_BYTES = 200

# Header spellings, normalised (angle brackets stripped, lowercased). `openint`
# and `per`/`time` appeared in the bulk layout and carry nothing this plane
# stores.
_DATE_KEYS = ("date",)
_VOLUME_KEYS = ("vol", "volume")
_OHLC_KEYS = ("open", "high", "low", "close")

# Pinned, never inferred. `to_datetime` guessing per element reads 03/04 as two
# different days; both formats are tried whole and the one that parses wins.
_DATE_FORMATS = ("%Y%m%d", "%Y-%m-%d")


class Throttled(RuntimeError):
    """Stooq refused because the day's anonymous quota is spent.

    Its own exception type because the caller's response is not the one an
    error deserves: the run stops, records what it got, and reports the stop as
    a normal outcome. Nothing about the plane is wrong.
    """


def delay_seconds() -> float:
    """Seconds to wait between requests, from the environment."""
    raw = os.environ.get("STOOQ_DELAY_SECONDS")
    return DEFAULT_DELAY_SECONDS if raw is None or raw == "" else float(raw)


def classify_response(body: str) -> str:
    """`"csv"`, `"throttled"` or `"empty"` for one response body.

    Pure, so the three-way branch that decides whether a run continues can be
    tested without a socket. See the module docstring on why a 200 is not
    evidence of data here.
    """
    if len(body.encode("utf-8", errors="replace")) <= _MARKER_MAX_BYTES:
        head = body.strip().lower()
        if any(head.startswith(m) for m in _THROTTLE_MARKERS):
            return "throttled"
        if any(head.startswith(m) for m in _EMPTY_MARKERS):
            return "empty"
    return "csv"


def fetch_symbol(symbol: str, session=None, url: str = QUOTE_URL) -> str:
    """One symbol's daily history as CSV text.

    Raises `Throttled` when the day's quota is spent and returns `""` for a
    symbol the vendor does not know — the caller distinguishes "stop" from
    "skip this one", which is the whole reason `classify_response` exists.

    A `session` is threaded through because the walk makes thousands of requests
    to one host and a fresh connection for each is a TLS handshake this does not
    need to pay for.
    """
    get = (session or requests).get
    r = get(url, params={"s": f"{symbol.lower()}.us", "i": "d"},
            timeout=_TIMEOUT)
    r.raise_for_status()
    kind = classify_response(r.text)
    if kind == "throttled":
        raise Throttled(
            f"stooq refused at {symbol}: the anonymous daily hit limit is "
            "spent. What this run already wrote is kept; the next run resumes "
            "from the cursor.")
    return "" if kind == "empty" else r.text


def sleep_between(seconds=None) -> None:
    """Wait between requests. Indirected so a test never actually sleeps."""
    seconds = delay_seconds() if seconds is None else seconds
    if seconds > 0:
        time.sleep(seconds)


def _normalise(columns) -> list:
    return [str(c).strip().strip("<>").lower() for c in columns]


def _pick(columns: list, candidates, what: str) -> str:
    for name in candidates:
        if name in columns:
            return name
    raise ValueError(
        f"{what}: no column among {list(candidates)} — the file's shape "
        f"changed. Columns seen: {sorted(columns)}")


def _dates(values: pd.Series, what: str) -> pd.Series:
    """Dates in whichever of the two published formats this file uses.

    Tried whole rather than per element, and a column that parsed nothing under
    either raises: coercing every row to NaT would otherwise read as a symbol
    with no history instead of a parser that stopped working.
    """
    text = values.astype(str).str.strip()
    for fmt in _DATE_FORMATS:
        parsed = pd.to_datetime(text, format=fmt, errors="coerce")
        if parsed.notna().any():
            return parsed
    raise ValueError(
        f"{what}: no value parsed as any of {list(_DATE_FORMATS)} — the date "
        f"format changed. First values: {list(text.head(3))}")


def parse_bars(text: str, symbol: str) -> pd.DataFrame:
    """One symbol's CSV -> `[date, open, high, low, close, share_volume]`.

    `share_volume` is named for what it is, exactly as in `av_parsers`: the
    vendor publishes a share count and `market_prices.volume` is turnover in the
    quote currency. The conversion belongs to the pipeline, not here, so this
    stays a faithful transcription of the file.
    """
    what = f"{SOURCE}({symbol})"
    empty = pd.DataFrame(
        columns=["date", "open", "high", "low", "close", "share_volume"])
    if not text.strip():
        return empty
    df = pd.read_csv(io.StringIO(text))
    if df.empty:
        return empty
    df.columns = _normalise(df.columns)

    date_col = _pick(list(df.columns), _DATE_KEYS, what)
    ohlc = {k: _pick(list(df.columns), (k,), what) for k in _OHLC_KEYS}
    volume_col = _pick(list(df.columns), _VOLUME_KEYS, what)

    out = pd.DataFrame({
        "date": _dates(df[date_col], what).dt.date,
        **{k: pd.to_numeric(df[col], errors="coerce") for k, col in ohlc.items()},
        "share_volume": pd.to_numeric(df[volume_col], errors="coerce"),
    })
    out = out.dropna(subset=["date", "close"])
    return out.sort_values("date").reset_index(drop=True)
