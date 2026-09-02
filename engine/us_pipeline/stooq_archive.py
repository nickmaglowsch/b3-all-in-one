"""Reader for Stooq's free bulk archive of US daily bars.

This exists because of arithmetic, not preference. The metered vendor's free
tier is a hard 25 requests a day; a fresh symbol costs three (one price, two
corporate actions) against a universe of roughly eleven thousand. That is about
six symbols a day, so a first pass takes on the order of **five years**, and a
daily refresh of the same universe would need 550x the whole day's allowance.
`us_prices` is resumable and correct and none of that changes the number. A
per-symbol metered feed cannot backfill this market on a free key.

So the history comes the way B3's and crypto's already do: one bulk file.
`b3_pipeline` pulls COTAHIST year archives, `crypto_pipeline` walks the Binance
bucket, and this module downloads Stooq's `d_us_txt.zip` — every US daily bar it
publishes, in one request, costing nothing against the metered budget. The
metered key then stops being the source of prices and goes back to being the
source of the things only it has: the listing universe, the benchmark, and the
corporate actions that upgrade a split-adjusted series to a total-return one.

Three properties of this archive are load-bearing, and two of them are limits.

**The bars are already split-adjusted.** Stooq publishes an adjusted series, so
the `close` here is not the raw close the metered feed returns. That is not a
detail to smooth over: writing both into `market_prices` for the same symbol
would splice a split-adjusted history onto raw recent bars and invent a jump at
the seam, which is why `us_prices.select_work` is told which symbols this module
covers and never re-prices them. The state recorded is `split_only` — the
vocabulary `us_adjust` already defines — because that is exactly what it is.

**It does not carry the delisted.** The dump is the currently-listed universe,
so this module deepens history for living names and adds no dead ones. It is
therefore *not* the survivorship fix, and saying otherwise would be the exact
error PR #44 corrected: `us_listing` owns the declared intervals (including
delistings, from two metered requests) and `symbol_directory` owns the observed
ones. This module writes neither — only `first_seen`/`last_seen`, which are
observations of its own file.

**A truncated download looks like a small market.** Same failure mode
`symbol_directory` guards: a proxy error page or a half-finished transfer parses
as "the US has four hundred symbols". Nothing is written unless the archive
holds at least `MIN_PLAUSIBLE_SYMBOLS` members.

Columns are read by header name, never by position, and both spellings Stooq
serves are accepted — the bulk `.txt` members use `<TICKER>,<PER>,<DATE>,...`
with `YYYYMMDD` dates, while its single-symbol CSV uses `Date,Open,...` with
ISO ones. A file matching neither raises and names what it saw, rather than
guessing: this module was written against the documented shape from an
environment that cannot reach `stooq.com`, so the first real run is where the
shape gets confirmed, and it has to fail loudly rather than store nonsense.
"""

import io
import logging
import os
import re
import zipfile

import pandas as pd
import requests

logger = logging.getLogger(__name__)

SOURCE = "stooq-bulk"
MARKET = "US"
CURRENCY = "USD"

BULK_URL = "https://stooq.com/db/h/d_us_txt.zip"
BULK_FILENAME = "stooq_d_us_txt.zip"

_TIMEOUT = 600
_CHUNK = 1 << 20

# The dump has held five figures of members for years. Below this it is a
# truncated transfer or an error page, not a market. See the module docstring.
MIN_PLAUSIBLE_SYMBOLS = 3_000

# `.../us/nasdaq stocks/1/aapl.us.txt` -> venue "nasdaq", kind "stocks".
# Kept as one expression because the directory name is the *only* place the
# archive says which venue a symbol trades on.
_VENUE_SEGMENT = re.compile(r"^(?P<venue>[a-z0-9 ]+?)\s+(?P<kind>stocks|etfs)$")

_VENUE_NAMES = {
    "nasdaq": "NASDAQ",
    "nyse": "NYSE",
    "nysemkt": "NYSE MKT",
    "amex": "NYSE MKT",
}

# Header spellings, normalised (angle brackets stripped, lowercased). `openint`
# and `per`/`time` are present in the bulk layout and carry nothing this plane
# stores.
_DATE_KEYS = ("date",)
_VOLUME_KEYS = ("vol", "volume")
_OHLC_KEYS = ("open", "high", "low", "close")

# Pinned, never inferred. `to_datetime` guessing per element reads 03/04 as two
# different days; both formats are tried whole and the one that parses wins.
_DATE_FORMATS = ("%Y%m%d", "%Y-%m-%d")


def download_bulk(dest: str, url: str = BULK_URL) -> str:
    """Fetch the archive to `dest`, streamed to disk.

    Streamed because the dump is hundreds of megabytes and the ingest container
    should not hold it in memory, and checked with `is_zipfile` for the reason
    `sec_downloader.download_bulk` checks: a 200 is not evidence of a payload.
    """
    logger.info(f"downloading {url} -> {dest}")
    with requests.get(url, stream=True, timeout=_TIMEOUT) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            fh.writelines(r.iter_content(chunk_size=_CHUNK))
    if not zipfile.is_zipfile(dest):
        raise RuntimeError(
            f"{dest} is not a zip — stooq.com most likely answered with an "
            "error or rate-limit page. Nothing was parsed.")
    logger.info(f"downloaded {os.path.getsize(dest):,} bytes")
    return dest


def classify(member: str) -> tuple:
    """`(symbol, venue, kind)` for one archive member, or `None` to skip it.

    The member path is the only carrier of venue and instrument kind, so a path
    that does not name one is skipped rather than guessed into `instruments`
    with a NULL venue.
    """
    if not member.lower().endswith(".txt"):
        return None
    parts = [p.strip().lower() for p in member.split("/") if p.strip()]
    base = parts[-1][: -len(".txt")]
    if not base.endswith(".us"):
        return None
    symbol = base[: -len(".us")].upper()
    if not symbol:
        return None

    for segment in reversed(parts[:-1]):
        m = _VENUE_SEGMENT.match(segment)
        if not m:
            continue
        venue = _VENUE_NAMES.get(m.group("venue"), m.group("venue").upper())
        return symbol, venue, "ETF" if m.group("kind") == "etfs" else "EQUITY"
    return None


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
    """One member -> `[date, open, high, low, close, share_volume]`.

    `share_volume` is named for what it is, exactly as in `av_parsers`: the
    archive publishes a share count and `market_prices.volume` is turnover in
    the quote currency. The conversion belongs to the pipeline, not here, so
    this stays a faithful transcription of the file.
    """
    what = f"{SOURCE}({symbol})"
    df = pd.read_csv(io.StringIO(text))
    if df.empty:
        return pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "share_volume"])
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


def list_members(zip_path: str) -> list:
    """Every `(member, symbol, venue, kind)` the archive holds, symbol-sorted.

    Read once and returned whole: it is a few tens of thousands of tuples, and
    the pipeline needs the count before it writes anything so it can refuse a
    truncated archive.
    """
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    found = []
    for name in names:
        hit = classify(name)
        if hit:
            found.append((name, *hit))
    logger.info(f"archive holds {len(found):,} US symbols across "
                f"{len(names):,} members")
    return sorted(found, key=lambda row: row[1])


def open_archive(zip_path: str) -> zipfile.ZipFile:
    """The archive, opened once for a whole run.

    Eleven thousand members means eleven thousand central-directory reads if
    each one reopens the file. The pipeline holds this open across its loop.
    """
    return zipfile.ZipFile(zip_path)


def read_member(zf: zipfile.ZipFile, member: str, symbol: str) -> pd.DataFrame:
    """Bars for one member of an open archive.

    An unreadable or unparseable member costs only itself: one malformed file
    must not end a run that has ten thousand good ones left, which is the same
    call `sec_downloader.iter_bulk_facts` makes.
    """
    try:
        with zf.open(member) as fh:
            blob = fh.read()
    except (KeyError, zipfile.BadZipFile, RuntimeError) as e:
        logger.warning(f"{member}: unreadable ({e})")
        return pd.DataFrame()
    return parse_bars(blob.decode("utf-8", errors="replace"), symbol)
