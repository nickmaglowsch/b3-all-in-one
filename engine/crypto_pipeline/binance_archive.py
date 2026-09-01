"""Reader for Binance's public data archive (data.binance.vision).

Two things come out of here: the list of every spot pair the archive has ever
held, and the daily bars for any one of them.

Addressed through the S3 origin rather than the `data.binance.vision` vanity
host, for a reason that is not cosmetic: the enumeration below is an S3
ListBucket call, which only the origin answers. Using one host for both halves
also means one failure mode instead of two.

Delisting is inferred, not announced. The archive publishes no status field and
the exchange's `exchangeInfo` only describes what trades today — so a pair whose
monthly files stop is a pair that stopped trading, and the last month present is
its delisting month. That inference is the whole survivorship story here, so it
is recorded as such: `instruments.delist_reason` says the archive ended, not that
Binance announced anything.

Scope: this is Binance spot. A pair delisted here may still trade elsewhere, and
"delisted" in this plane always means "delisted from Binance". The ingest writes
that into delist_reason so no reader has to remember it.
"""

import io
import logging
import re
import zipfile
from xml.etree import ElementTree

import pandas as pd
import requests

logger = logging.getLogger(__name__)

SOURCE = "binance-archive"
MARKET = "CRYPTO"
VENUE = "BINANCE"

# The bucket's own origin. The vanity CNAME serves objects but not ListBucket.
ORIGIN = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

SPOT_MONTHLY = "data/spot/monthly/klines"
SPOT_DAILY = "data/spot/daily/klines"

# Kline CSVs are headerless with 12 columns. Only these are worth storing:
# column 5 is base-asset volume (how many coins) and column 7 is quote volume
# (how much money), and it is the second that answers "was this liquid enough to
# trade", which is what `market_prices.volume` means everywhere else on the plane.
_KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "base_volume", "close_time",
    "volume", "trades", "taker_base", "taker_quote", "ignore",
]

_TIMEOUT = 60

# .../BTCUSDT-1d-2017-08.zip -> 2017-08
_MONTH_IN_KEY = re.compile(r"-(\d{4}-\d{2})\.zip$")


def _get(url: str, params: dict = None) -> requests.Response:
    r = requests.get(url, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    return r


def list_symbols(prefix: str = SPOT_MONTHLY, interval: str = "1d") -> list:
    """Every symbol the archive holds, delisted ones included.

    Paginated: ListBucket truncates at 1000 keys and the spot archive is far
    past that, so a single unpaged call would silently return an alphabetical
    prefix of the universe — which is a survivorship bias of its own, just sorted
    differently. The loop follows `NextMarker`/`IsTruncated` until the bucket
    says it is done.
    """
    symbols, marker = [], ""
    while True:
        root = ElementTree.fromstring(_get(ORIGIN, {
            "delimiter": "/", "prefix": f"{prefix}/", "marker": marker,
        }).content)
        page = [
            node.text.rstrip("/").rsplit("/", 1)[-1]
            for group in root.iter(f"{_S3_NS}CommonPrefixes")
            for node in group.iter(f"{_S3_NS}Prefix")
        ]
        symbols.extend(page)
        truncated = (root.findtext(f"{_S3_NS}IsTruncated") or "false") == "true"
        if not truncated or not page:
            break
        marker = f"{prefix}/{page[-1]}/"
    logger.info(f"archive holds {len(symbols):,} spot symbols (dead ones included)")
    return sorted(symbols)


def list_months(symbol: str, interval: str = "1d") -> list:
    """The months the archive has for one symbol, as 'YYYY-MM', oldest first.

    The first and last of these are the pair's observed life. Nothing else on the
    plane knows when a Binance pair listed or delisted, so this is the source for
    both.
    """
    prefix = f"{SPOT_MONTHLY}/{symbol}/{interval}/"
    months, marker = [], ""
    while True:
        root = ElementTree.fromstring(_get(ORIGIN, {
            "prefix": prefix, "marker": marker,
        }).content)
        keys = [el.text for el in root.iter(f"{_S3_NS}Key")
                if el.text.endswith(".zip")]
        months.extend(m.group(1) for m in
                      (_MONTH_IN_KEY.search(k) for k in keys) if m)
        truncated = (root.findtext(f"{_S3_NS}IsTruncated") or "false") == "true"
        if not truncated or not keys:
            break
        marker = keys[-1]
    return sorted(set(months))


def parse_klines(blob: bytes, symbol: str) -> pd.DataFrame:
    """One kline zip -> `market_prices` rows in native (quote-currency) units.

    `open_time` is milliseconds since the epoch in UTC. Binance switched some
    files to microseconds in 2025, so the unit is inferred from magnitude rather
    than assumed: a 13-digit stamp is ms, a 16-digit one is us. Guessing wrong
    would date every bar to 1970 or to the year 54000, both of which a
    date-ranged query would silently return nothing for.
    """
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = zf.namelist()[0]
        raw = pd.read_csv(io.BytesIO(zf.read(name)), header=None,
                          names=_KLINE_COLUMNS)

    # A few 2025 files carry a header row; it parses as NaN and drops out here.
    raw = raw[pd.to_numeric(raw["open_time"], errors="coerce").notna()]
    if raw.empty:
        return pd.DataFrame()

    stamps = raw["open_time"].astype("int64")
    unit = "us" if stamps.iloc[0] > 10 ** 14 else "ms"
    out = pd.DataFrame({
        "market": MARKET,
        "symbol": symbol,
        "date": pd.to_datetime(stamps, unit=unit, utc=True).dt.date,
        "open": raw["open"].astype(float),
        "high": raw["high"].astype(float),
        "low": raw["low"].astype(float),
        "close": raw["close"].astype(float),
        "volume": raw["volume"].astype(float),
        "source": SOURCE,
    })
    # Spot pairs have no splits and no dividends, so the adjusted close is the
    # close. Storing it rather than leaving it NULL keeps every reader that asks
    # for adj_close working without a market-specific COALESCE.
    out["adj_close"] = out["close"]
    out["split_adj_close"] = out["close"]
    return out


def fetch_month(symbol: str, month: str, interval: str = "1d") -> pd.DataFrame:
    """Bars for one symbol-month. Empty frame when the archive has no such file."""
    url = f"{ORIGIN}/{SPOT_MONTHLY}/{symbol}/{interval}/{symbol}-{interval}-{month}.zip"
    try:
        return parse_klines(_get(url).content, symbol)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return pd.DataFrame()
        raise
