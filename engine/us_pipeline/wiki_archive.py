"""Read the Nasdaq Data Link WIKI/PRICES bulk export.

The reader half of `wiki_backfill`, split off for the same reason
`stooq_archive` is split off from `us_backfill`: fetching and parsing a vendor
file is a different job from folding it into the plane, and only this half needs
to know what the vendor's CSV looks like.

**What this dataset is, stated once so nobody has to rediscover it.** WIKI is a
community-maintained US end-of-day set that Quandl froze in April 2018. It is
not the live US market and never will be again — the newest bar in the file is
2018-03-27, and no request to any endpoint changes that. What it is instead is
fifty-six years of US history, free, in one request, with corporate actions
attached: 15.4M bars over 3,199 tickers from 1962-01-02, carrying 118,131
dividends and 4,096 splits.

**On survivorship.** This file records the companies that died *while it was
being maintained* — RadioShack's last bar is 2015-02-02, Aeropostale's
2016-04-21, Yahoo's 2017-06-16, all correct. It does not reach back past its own
maintenance window: Enron, Lehman, WaMu and Bear Stearns are simply absent, and
`GM` starts in 2010 because the entity that went bankrupt in 2009 is not the one
that trades now. So this is a *partly* survivorship-free record — good from
roughly 2013 to 2018, biased before it — and a backtest that starts in 1990 is
still reading a winners' universe. `us_listing` is what carries the delisting
intervals; this module carries bars, and claims nothing about listing status.

**Why the file is streamed rather than read.** The export is one CSV of 15.4M
rows, and it is grouped by ticker with dates ascending inside each ticker
(verified against the real file, not assumed). That ordering is what lets
`iter_symbols` yield one symbol at a time and never hold more than a single
ticker's bars in memory — a `read_csv` of the whole thing is several GB of
DataFrame for a container that also has to hold the price upsert.
"""

import csv
import io
import logging
import os
import zipfile

import pandas as pd
import requests

logger = logging.getLogger(__name__)

SOURCE = "wiki-prices"
MARKET = "US"
CURRENCY = "USD"

EXPORT_URL = "https://data.nasdaq.com/api/v3/datatables/WIKI/PRICES"
BULK_FILENAME = "wiki_prices.zip"

_TIMEOUT = 900
_CHUNK = 1 << 22

# The file has held 3,199 tickers since it was frozen, and being frozen is
# exactly why a floor is safe to pin here: this number cannot drift upward with
# the market. Anything materially below it is a truncated transfer or an error
# page, not a smaller dataset.
MIN_PLAUSIBLE_SYMBOLS = 2_500

# The newest bar the dataset will ever have. Asserted rather than trusted: if a
# future export ends earlier, the file was truncated mid-ticker and the plane
# would silently lose the tail of its history.
LAST_BAR = "2018-03-27"

# Pinned, never inferred, for the reason `stooq_archive` pins its two: letting
# pandas guess per element reads 03/04 as two different days.
DATE_FORMAT = "%Y-%m-%d"

# The exact header the export has carried. Checked in full rather than by
# presence, so a vendor column *insertion* — which would silently shift every
# positional read — fails here instead of writing shifted prices.
EXPECTED_HEADER = [
    "ticker", "date", "open", "high", "low", "close", "volume",
    "ex-dividend", "split_ratio",
    "adj_open", "adj_high", "adj_low", "adj_close", "adj_volume",
]


class MissingApiKey(RuntimeError):
    """No Nasdaq Data Link key configured.

    Same shape as `alphavantage.MissingApiKey` and for the same reason: the
    export endpoint answers a keyless request with a JSON refusal and HTTP 403,
    which is not a transport error and would otherwise be reported as one.
    """


def api_key() -> str:
    """The configured key, read at call time so tests can set it per-case."""
    key = os.environ.get("NASDAQ_DATA_LINK_API_KEY", "").strip()
    if not key:
        raise MissingApiKey(
            "NASDAQ_DATA_LINK_API_KEY is unset. The WIKI bulk backfill needs "
            "one; a free key is issued at data.nasdaq.com and goes in .env "
            "next to ALPHAVANTAGE_API_KEY.")
    return key


def export_link(key: str = None, session=None) -> str:
    """Ask for a bulk export and return the (signed, expiring) download link.

    Two steps rather than one because the vendor answers with a link to S3
    rather than with the file, and the link carries a 30-minute signature — so
    it is fetched immediately before the download and never cached.
    """
    session = session or requests
    r = session.get(EXPORT_URL, params={"api_key": key or api_key(),
                                        "qopts.export": "true"},
                    timeout=_TIMEOUT)
    r.raise_for_status()
    payload = r.json()
    try:
        return payload["datatable_bulk_download"]["file"]["link"]
    except (KeyError, TypeError):
        raise RuntimeError(
            f"no download link in the export response: {str(payload)[:300]}")


def download_bulk(dest: str, key: str = None, session=None) -> str:
    """Fetch the export to `dest`, streamed to disk.

    Streamed and then checked with `is_zipfile` for the reason
    `stooq_archive.download_bulk` checks: the file is hundreds of megabytes, and
    a 200 is not evidence of a payload.
    """
    link = export_link(key, session)
    logger.info(f"downloading the WIKI bulk export -> {dest}")
    with (session or requests).get(link, stream=True, timeout=_TIMEOUT) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(chunk_size=_CHUNK):
                fh.write(chunk)
    if not zipfile.is_zipfile(dest):
        raise RuntimeError(
            f"{dest} is not a zip — the export link most likely answered with "
            "an error or an expired-signature page. Nothing was parsed.")
    logger.info(f"downloaded {os.path.getsize(dest):,} bytes")
    return dest


def _reader(zf: zipfile.ZipFile):
    """The single CSV member, as a `csv.reader`, header already consumed."""
    members = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    if len(members) != 1:
        raise RuntimeError(
            f"expected exactly one CSV in the export, found {members}")
    fh = io.TextIOWrapper(zf.open(members[0]), encoding="utf-8", newline="")
    reader = csv.reader(fh)
    header = next(reader, None)
    if header != EXPECTED_HEADER:
        raise RuntimeError(
            f"unexpected WIKI header. Expected {EXPECTED_HEADER}, saw "
            f"{header}. Nothing was parsed — the column order is read "
            "positionally and a shifted read would store wrong prices.")
    return reader


def _frame(symbol: str, rows: list) -> pd.DataFrame:
    """One ticker's accumulated rows -> a typed frame, oldest bar first."""
    df = pd.DataFrame(rows, columns=[
        "date", "open", "high", "low", "close", "share_volume",
        "dividend", "split_ratio"])
    df["date"] = pd.to_datetime(df["date"], format=DATE_FORMAT,
                                errors="coerce").dt.date
    bad = int(df["date"].isna().sum())
    if bad:
        raise RuntimeError(
            f"{symbol}: {bad} row(s) whose date did not parse as "
            f"{DATE_FORMAT}. Nothing was parsed for this symbol.")
    for col in ("open", "high", "low", "close", "share_volume",
                "dividend", "split_ratio"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


def iter_symbols(path: str):
    """Yield `(symbol, bars)` per ticker, streaming the archive once.

    Relies on the file being grouped by ticker — a ticker is emitted as soon as
    the next one appears. If a future export interleaves tickers this yields the
    same symbol more than once, which the caller's upsert absorbs without
    corrupting anything: the bars are keyed `(market, symbol, date)` either way.
    """
    with zipfile.ZipFile(path) as zf:
        reader = _reader(zf)
        current, rows = None, []
        for rec in reader:
            symbol = rec[0]
            if symbol != current:
                if current is not None and rows:
                    yield current, _frame(current, rows)
                current, rows = symbol, []
            # Positional, and safe because the header was checked in full.
            rows.append((rec[1], rec[2], rec[3], rec[4], rec[5], rec[6],
                         rec[7], rec[8]))
        if current is not None and rows:
            yield current, _frame(current, rows)


def events(bars: pd.DataFrame) -> tuple:
    """`(splits, dividends)` in the shape `us_adjust` takes.

    The corporate actions ride along in the bar rows rather than arriving from a
    separate endpoint, which is the whole reason this file can produce a
    total-return series without spending a metered request on anything.
    """
    # WIKI publishes the vendor convention — a 7-for-1 split is `split_ratio`
    # 7.0 — and `us_adjust` takes the reciprocal one, where that same split is
    # 1/7 and a *reverse* split is the number above 1. The two look identical
    # in the column name and differ by an inversion, so getting this wrong does
    # not raise: it multiplies the pre-split history by 7 instead of dividing
    # it, on all 4,096 splits in the file, and leaves a price series that still
    # looks like a price series. Inverted here, once, next to the evidence.
    raw = bars["split_ratio"].fillna(1.0)
    splits = bars.loc[(raw != 1.0) & (raw > 0.0), ["date", "split_ratio"]].copy()
    splits["ratio"] = 1.0 / splits["split_ratio"].astype(float)
    splits = splits.drop(columns=["split_ratio"]).rename(
        columns={"date": "ex_date"})
    divs = bars.loc[bars["dividend"].fillna(0.0) > 0.0, ["date", "dividend"]]
    divs = divs.rename(columns={"date": "ex_date", "dividend": "amount"})
    return splits.reset_index(drop=True), divs.reset_index(drop=True)
