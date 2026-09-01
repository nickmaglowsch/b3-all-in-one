"""Vendor text -> DataFrame. No network, no database, no clock.

Every parser here reads its columns **by header name** and raises a `ValueError`
naming what went missing. That is the same shape `symbol_directory.parse_directory`
already uses, and it is the mitigation `spec/00-overview.md` asks for under risk
4 (parser drift): a parser that indexes by position turns an inserted column into
silently wrong numbers, while one that indexes by name turns it into a loud
failure that stops the run.

These functions are the only place that knows what the vendor's files look like,
so they are also the only place a captured fixture has to be pointed at.
`engine/tests/test_av_parsers.py` drives them from samples built in-process, and
`engine/tests/test_av_fixtures.py` drives the same functions from real captures
under `tests/fixtures/us/` when those exist — which is what turns "written
against the documented shape" into "checked against the real one".
"""

import io
import logging

import pandas as pd

logger = logging.getLogger(__name__)

_DAILY_COLUMNS = {"timestamp", "open", "high", "low", "close", "volume"}
_LISTING_COLUMNS = {"symbol", "name", "exchange", "assetType", "ipoDate",
                    "delistingDate", "status"}

# How the vendor spells "no date here". An `active` listing row carries one
# of these in `delistingDate` for every row in the file, so they have to be
# excluded before "nothing parsed" can mean "the format changed".
_EMPTY_MARKERS = {"", "null", "none", "nan", "na", "n/a", "-"}


def _require(df: pd.DataFrame, needed: set, what: str) -> None:
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(
            f"{what} is missing {sorted(missing)} — the file's shape changed. "
            f"Columns seen: {sorted(df.columns)}")


def _dates(values: pd.Series, what: str, field: str) -> pd.Series:
    """ISO dates, parsed strictly, with a drift check.

    The format is pinned rather than inferred: `to_datetime` guessing per
    element is both slow and a silent way to read 03/04 as two different days.
    But a pinned format plus `errors="coerce"` has its own failure mode — a
    vendor that switched to another format would coerce *every* row to NaT and
    the run would look like a company with no dates rather than a parser that
    stopped working. So a column that had text and produced nothing raises.
    """
    parsed = pd.to_datetime(values, format="%Y-%m-%d", errors="coerce")
    text = values.astype(str).str.strip().str.lower()
    meaningful = ~text.isin(_EMPTY_MARKERS)
    if meaningful.any() and parsed[meaningful].notna().sum() == 0:
        raise ValueError(
            f"{what}: every value in `{field}` failed to parse as YYYY-MM-DD — "
            f"the date format changed. First values: "
            f"{list(values[meaningful].head(3))}")
    return parsed


def parse_time_series_daily(text: str, symbol: str) -> pd.DataFrame:
    """TIME_SERIES_DAILY as CSV -> [date, open, high, low, close, share_volume].

    `share_volume` is named for what it is. The vendor's `volume` is a count of
    shares, while `market_prices.volume` is turnover in the quote currency —
    the number `min_adtv` filters on. Conflating them would build a liquidity
    filter that keeps a penny stock trading a billion shares and drops Berkshire;
    the conversion happens in `us_prices`, deliberately not here, so this stays a
    faithful transcription of the file.
    """
    df = pd.read_csv(io.StringIO(text))
    _require(df, _DAILY_COLUMNS, f"TIME_SERIES_DAILY({symbol})")
    out = pd.DataFrame({
        "date": _dates(df["timestamp"], f"TIME_SERIES_DAILY({symbol})",
                       "timestamp").dt.date,
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "share_volume": pd.to_numeric(df["volume"], errors="coerce"),
    })
    out = out.dropna(subset=["date", "close"])
    return out.sort_values("date").reset_index(drop=True)


def parse_listing_status(text: str) -> pd.DataFrame:
    """LISTING_STATUS as CSV -> [symbol, name, venue, kind, ipo_date, delisting_date].

    Both states share one shape: an `active` row has an empty `delistingDate`
    and a `delisted` row has a real one. Nothing here decides what to do with
    them — that is `us_listing`'s job, and it is where the reconciliation rules
    against the symbol directory live.
    """
    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    _require(df, _LISTING_COLUMNS, "LISTING_STATUS")
    out = pd.DataFrame({
        "symbol": df["symbol"].str.strip(),
        "name": df["name"].str.strip(),
        "venue": df["exchange"].str.strip(),
        # assetType is "Stock" or "ETF"; instruments.kind is EQUITY/ETF, the
        # vocabulary symbol_directory already writes.
        "kind": df["assetType"].str.strip().str.upper().map(
            {"ETF": "ETF"}).fillna("EQUITY"),
        "ipo_date": _dates(df["ipoDate"], "LISTING_STATUS", "ipoDate").dt.date,
        "delisting_date": _dates(df["delistingDate"], "LISTING_STATUS",
                                 "delistingDate").dt.date,
        "status": df["status"].str.strip().str.lower(),
    })
    return out[out["symbol"] != ""].reset_index(drop=True)


def parse_splits(payload: dict, symbol: str) -> pd.DataFrame:
    """SPLITS as JSON -> [ex_date, ratio], in this repository's convention.

    The vendor reports `split_factor` as new-over-old: "4.0" for a four-for-one.
    `b3_pipeline.adjustments` uses the inverse, old-over-new, so a split is a
    number below 1. The conversion happens here, at the boundary, because this
    is the single easiest place in the whole pipeline to invert a sign — and an
    inverted split turns a 4:1 into a +300% day that no glitch filter would
    catch, because it looks exactly like a stock that quadrupled.
    """
    rows = payload.get("data") or []
    out = []
    for row in rows:
        missing = {"effective_date", "split_factor"} - set(row)
        if missing:
            raise ValueError(
                f"SPLITS({symbol}) row is missing {sorted(missing)} — the "
                f"shape changed. Keys seen: {sorted(row)}")
        factor = pd.to_numeric(row["split_factor"], errors="coerce")
        date = pd.to_datetime(row["effective_date"], format="%Y-%m-%d",
                              errors="coerce")
        if pd.isna(factor) or factor <= 0 or pd.isna(date):
            continue
        out.append({"ex_date": date.date(), "ratio": 1.0 / float(factor)})
    return pd.DataFrame(out, columns=["ex_date", "ratio"]).sort_values(
        "ex_date").reset_index(drop=True)


def parse_dividends(payload: dict, symbol: str) -> pd.DataFrame:
    """DIVIDENDS as JSON -> [ex_date, amount], per share, in the quote currency.

    The ex-date is the whole reason this endpoint is worth a request. The SEC's
    XBRL carries `CommonStockDividendsPerShareDeclared`, but declared is not
    paid and it has no reliable ex-date, so it cannot adjust a series.
    """
    rows = payload.get("data") or []
    out = []
    for row in rows:
        missing = {"ex_dividend_date", "amount"} - set(row)
        if missing:
            raise ValueError(
                f"DIVIDENDS({symbol}) row is missing {sorted(missing)} — the "
                f"shape changed. Keys seen: {sorted(row)}")
        amount = pd.to_numeric(row["amount"], errors="coerce")
        date = pd.to_datetime(row["ex_dividend_date"], format="%Y-%m-%d",
                              errors="coerce")
        if pd.isna(amount) or amount <= 0 or pd.isna(date):
            continue
        out.append({"ex_date": date.date(), "amount": float(amount)})
    return pd.DataFrame(out, columns=["ex_date", "amount"]).sort_values(
        "ex_date").reset_index(drop=True)
