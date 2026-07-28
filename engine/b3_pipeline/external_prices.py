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

import pandas as pd
import requests

from . import config

logger = logging.getLogger(__name__)

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
    Weekends, holidays and dates past the retention window all answer 200 with an
    empty body, and none of them are failures worth stopping a backfill for.
    """
    tickers = config.RENDA_FIXA_ETFS if tickers is None else frozenset(tickers)
    try:
        token_url = config.B3_FILE_TOKEN_URL.format(
            name=config.B3_TRADE_INFO_FILE, date=day)
        resp = requests.get(token_url, timeout=30)
        resp.raise_for_status()
        redirect = resp.json().get("redirectUrl", "")
        token = redirect.partition("token=")[2]
        if not token:
            logger.warning(f"{day}: no download token in B3's response")
            return pd.DataFrame()

        resp = requests.get(
            config.B3_FILE_DOWNLOAD_URL.format(token=token), timeout=120)
        resp.raise_for_status()
        body = resp.content
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"{day}: B3 daily file unavailable ({e})")
        return pd.DataFrame()

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


def backfill(start: datetime.date, end: datetime.date, tickers=None,
             skip=frozenset()) -> pd.DataFrame:
    """Every session from `start` to `end` inclusive, concatenated.

    Weekdays only — asking B3 for a Saturday spends two round trips to be told
    nothing, and there are ~100 of them in a year's backfill. `skip` is the set of
    dates already stored, so a re-run costs nothing for the days it already has.
    """
    frames = []
    day = start
    while day <= end:
        if day.weekday() < 5 and day not in skip:
            df = fetch_day(day, tickers)
            if not df.empty:
                frames.append(df)
        day += datetime.timedelta(days=1)

    if not frames:
        logger.warning(f"No external prices found between {start} and {end}")
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    logger.info(
        f"Fetched {len(out):,} external price rows for "
        f"{out['ticker'].nunique()} tickers ({out['date'].min()} to {out['date'].max()})"
    )
    return out


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

    # A Sunday is not an error, just nothing.
    assert fetch_day(datetime.date(2026, 7, 26)).empty
    print(f"ok — {len(df)} rows for {day}, LFTB11 close {row['close']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    demo()
