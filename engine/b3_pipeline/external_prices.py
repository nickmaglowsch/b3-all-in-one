"""Daily marks for instruments COTAHIST does not carry.

COTAHIST's scope is B3's BOVESPA segment. Exchange-listed fixed-income ETFs
(LFTB11, IMAB11, IRFM11, ...) are booked under RendaFixaPrivada instead, so they
appear in no COTAHIST year at all — not under any BDI code, not under any market
type. They still trade all day like any other ticker, so a book holding one has a
real position the price plane simply cannot see.

B3 publishes them itself, in the daily consolidated trade file on its bulk portal:
free, unauthenticated, semicolon-delimited, latin-1, comma decimals. Two requests
per day — ask for a token, spend the token.

Why not Yahoo, which also has these tickers: 65 of its 202 LFTB11 rows are
fabricated. They carry zero volume and Open == Close on days B3 records tens of
millions of reais of real trades, at prices ~0.17% away. Where Yahoo reports real
volume it matches B3 to the cent, which is exactly what makes the synthetic head
dangerous — it looks like data. B3's own file has no such gap.

These rows do NOT belong in `prices`; see storage.upsert_external_prices.
"""

import csv
import datetime
import io
import logging
from typing import NamedTuple

import pandas as pd
import requests

from . import config, storage

logger = logging.getLogger(__name__)


class FetchFailed(Exception):
    """B3 did not answer for a date.

    Deliberately distinct from B3 answering "nothing for that date", which is an
    empty frame. The two used to be the same value, and that is what made the
    feed unrecoverable: a day lost to a network blip left no rows, a holiday left
    no rows, so nothing downstream could tell which one was owed a retry. Only
    this one is.
    """


class BackfillResult(NamedTuple):
    """Rows fetched, and the days that could not be fetched at all.

    `failed` is the retry queue. It is returned rather than logged because the
    caller is the only layer that can durably record it, and a failure that is
    only logged is a failure that is lost at the end of the run.

    `attempted` is the days a request was actually sent for. Only those are
    settled either way; a loader that stops early (anbima_series.GaveUp) never
    asked about the rest, and clearing their debt would forget it. Empty means
    nothing is known to have been asked, so nothing gets cleared.
    """
    rows: pd.DataFrame
    failed: dict
    attempted: frozenset = frozenset()

# The file's own column names. Only the ones we keep are named here.
_COL_DATE = "RptDt"
_COL_TICKER = "TckrSymb"
_COL_ISIN = "ISIN"
_COL_LOW = "MinPric"
_COL_HIGH = "MaxPric"
_COL_CLOSE = "LastPric"
# Financial volume in BRL. Deliberately NOT FinInstrmQty (a share count): `prices`
# stores COTAHIST's volume in reais, and a sibling table that means something else
# by the same column name would be a trap for whoever joins them later.
_COL_VOLUME = "NtlFinVol"

# `external_prices` is shared with other feeds (see index_series.py), and the
# resume helpers in storage.py scope on this to decide what this one still owes.
SOURCE = "B3_TRADE_INFO"


def _num(value: str) -> float | None:
    """Parse B3's comma-decimal numerics. Blank fields are common and mean absent."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def fetch_day(day: datetime.date, tickers=None) -> pd.DataFrame:
    """One session's marks for `tickers` (default: the fixed-income ETFs).

    Returns an empty frame — not an error — when B3 has nothing for that date.
    B3 says that two different ways, and both are normal:

      - **HTTP 400 on the token leg** for a date it never had a file for, i.e. a
        non-session day. Measured 2026-07-28: Sunday 2026-07-26 and the weekday
        holidays 2026-01-01, 2026-04-21 and 2026-05-01 all answer 400, while
        2026-07-24 and 2026-07-27 hand out tokens.
      - **200 with a 0-byte body** for a session that has aged out of the ~14
        month window. 2024-01-02 still issues a token, then serves nothing.

    Raises FetchFailed when B3 did not answer at all. That case must not be an
    empty frame: on a rolling window an unfetched day is an unrecoverable day, so
    the caller has to be able to tell "nothing here" from "ask again". It must
    also not swallow the 400 — a holiday recorded as a failure is a debt that can
    never be paid, retried on every run until it ages out.
    """
    tickers = config.RENDA_FIXA_ETFS if tickers is None else frozenset(tickers)
    try:
        token_url = config.B3_FILE_TOKEN_URL.format(
            name=config.B3_TRADE_INFO_FILE, date=day)
        resp = requests.get(token_url, timeout=30)
        if resp.status_code == 400:
            # Only 400, and only on this leg. The URL is built from constants and
            # a date, so the request itself cannot be malformed — a 400 here is
            # B3 saying "no file for that date", not "your request was bad".
            logger.debug(f"{day}: no file published (non-session day)")
            return pd.DataFrame()
        resp.raise_for_status()
        redirect = resp.json().get("redirectUrl", "")
        token = redirect.partition("token=")[2]
        if not token:
            # A 200 carrying no token is a broken answer, not an answer of
            # "nothing" — B3 returns an empty *body* for that, below.
            raise FetchFailed(f"no download token in B3's response for {day}")

        resp = requests.get(
            config.B3_FILE_DOWNLOAD_URL.format(token=token), timeout=120)
        resp.raise_for_status()
        body = resp.content
    except (requests.RequestException, ValueError) as e:
        raise FetchFailed(f"B3 daily file unavailable for {day}: {e}") from e

    if not body:
        logger.debug(f"{day}: empty file (non-session day, or past retention)")
        return pd.DataFrame()

    df = parse_file(body, tickers)
    if df.empty:
        # A session-day file that parses but holds none of our tickers is worth
        # saying out loud: B3's daily file varies 25x in size between dates for
        # reasons we could not establish, so silent truncation is plausible.
        logger.info(f"{day}: file parsed but carried none of {sorted(tickers)}")
    return df


def parse_file(body: bytes, tickers) -> pd.DataFrame:
    """One day's raw file -> the rows we keep. Split out from the HTTP so the
    layout can be tested without network."""
    tickers = frozenset(tickers)
    # Line 1 is a status banner ("Status do Arquivo: Final"), line 2 the header.
    text = body.decode("latin-1")
    _, _, rest = text.partition("\n")
    rows = []
    for row in csv.DictReader(io.StringIO(rest), delimiter=";"):
        if (row.get(_COL_TICKER) or "").strip() not in tickers:
            continue
        close = _num(row.get(_COL_CLOSE))
        if not close or close <= 0:
            continue
        rows.append({
            "ticker": row[_COL_TICKER].strip(),
            "isin_code": (row.get(_COL_ISIN) or "").strip() or "UNKNOWN",
            "date": row[_COL_DATE].strip(),
            # The file publishes no opening price, only the day's range, average
            # and last. `open` stays NULL rather than being faked from another
            # field — the index reads `close` and nothing else.
            "open": None,
            "high": _num(row.get(_COL_HIGH)),
            "low": _num(row.get(_COL_LOW)),
            "close": close,
            "volume": _num(row.get(_COL_VOLUME)) or 0.0,
            "source": SOURCE,
        })

    return pd.DataFrame(rows)


def level_rows(ticker, dates, closes, source: str) -> pd.DataFrame:
    """`external_prices` rows for a series that is a level and nothing else —
    an index, a rate, a published PU.

    A level has no range and no volume. `open`/`high`/`low` stay NULL rather
    than being faked from the close, the same posture parse_file takes on
    `open` — but as NaN, not None: a column that is None all the way down
    infers object dtype, and upsert_external_prices' containment check compares
    close against low before it looks at notna().
    """
    return pd.DataFrame({
        "ticker": ticker,
        "isin_code": "UNKNOWN",
        "date": dates,
        "open": float("nan"), "high": float("nan"), "low": float("nan"),
        "close": closes,
        "volume": 0.0,
        "source": source,
    })


def days_to_fetch(today: datetime.date, *, stored, pending, start,
                  heal_days: int, retention_days: int):
    """Decide what this run owes B3, and what it has to give up on.

    Two sources of work, because neither covers the other:

      - the trailing heal window, which finds holes nobody recorded (a run that
        died mid-step, a day that never got attempted at all), but is short;
      - `pending`, the recorded failures, which reach back as far as B3 still
        serves and are the only way a long outage is ever repaired.

    Returns (fetch, abandoned). `abandoned` is pending days now past B3's window:
    a retry there cannot succeed, so they are dropped loudly rather than retried
    forever. A pending day that turns up in `stored` is neither — it arrived by
    some other path and is simply resolved.

    Pure, so the policy can be tested without a network or a database.
    """
    horizon = max(start, today - datetime.timedelta(days=retention_days))
    unresolved = {d for d in pending if d not in stored}

    fetch, day = set(), max(start, today - datetime.timedelta(days=heal_days))
    while day <= today:
        if day.weekday() < 5 and day not in stored:
            fetch.add(day)
        day += datetime.timedelta(days=1)

    fetch |= {d for d in unresolved if d >= horizon}
    abandoned = {d for d in unresolved if d < horizon}
    return sorted(fetch), sorted(abandoned)


def backfill(days, tickers=None) -> BackfillResult:
    """Fetch each day in `days`, surviving the ones that fail.

    One bad day must not cost the others: an outage in the middle of a long
    catch-up would otherwise abort the run and lose everything after it, and on a
    rolling window "lose" is not "retry tomorrow".
    """
    days = list(days)
    frames, failed = [], {}
    for day in days:
        try:
            df = fetch_day(day, tickers)
        except FetchFailed as e:
            logger.warning(str(e))
            failed[day] = str(e)
            continue
        if not df.empty:
            frames.append(df)

    if not frames:
        logger.warning(f"No external prices found across {len(days)} days")
        return BackfillResult(pd.DataFrame(), failed, frozenset(days))

    out = pd.concat(frames, ignore_index=True)
    logger.info(
        f"Fetched {len(out):,} external price rows for "
        f"{out['ticker'].nunique()} tickers ({out['date'].min()} to {out['date'].max()})"
    )
    return BackfillResult(out, failed, frozenset(days))


def store_result(conn, source: str, result: BackfillResult, pending: set) -> None:
    """Write one fetch into `external_prices` and settle `source`'s failure ledger.

    The order is the point. Failures go on the books first: once rows are stored
    the next run resumes from them, and a failed day behind its window is owed
    only in the ledger, so a run killed between the two writes must not have
    written the rows alone. Debts are cleared last, once the rows that pay them
    are in. A day that answered — with rows or an empty holiday file — is settled
    either way; only a day that was asked (`attempted`) can be.
    """
    for day, error in result.failed.items():
        storage.record_external_failure(conn, source, day, error)
    if not result.rows.empty:
        storage.upsert_external_prices(conn, result.rows)
    for day in sorted((pending & result.attempted) - result.failed.keys()):
        storage.clear_external_failure(conn, source, day)


def demo() -> None:
    """Self-check against a known session. Needs network; hits B3 twice."""
    day = datetime.date(2026, 7, 24)
    df = fetch_day(day)
    assert not df.empty, f"expected rows for {day}"
    row = df[df["ticker"] == "LFTB11"].iloc[0]
    assert row["close"] == 124.15, row["close"]
    assert row["low"] == 123.96 and row["high"] == 124.15, row.to_dict()
    assert row["isin_code"] == "BRLFTBCTF002", row["isin_code"]
    assert row["volume"] > 26_000_000, row["volume"]   # BRL, not share count

    assert _num("1.234,56") == 1234.56
    assert _num("") is None and _num("x") is None

    # A Sunday is not an error, just nothing. Nor is a weekday holiday — B3
    # answers 400 for both, and reading that as a failure would book a debt no
    # retry can ever pay. Weekends never reach here (days_to_fetch drops them),
    # holidays do, so this is the case that actually matters in production.
    assert fetch_day(datetime.date(2026, 7, 26)).empty      # Sunday
    assert fetch_day(datetime.date(2026, 5, 1)).empty       # Dia do Trabalho
    print(f"ok — {len(df)} rows for {day}, LFTB11 close {row['close']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    demo()
