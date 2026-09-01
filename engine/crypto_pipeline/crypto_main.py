"""Ingest the Binance spot archive into the market plane.

Linear, like `b3_pipeline.main.run_pipeline` — enumerate, fetch, convert, write —
with no DAG framework and no scheduler of its own; the platform's procrastinate
task calls this.

The survivorship property is not something this pipeline adds. It comes from the
archive being a bulk dump rather than a query against today's listings, and this
module's only job is not to throw it away: it enumerates from the bucket (never
from `exchangeInfo`, which knows only the living), it keeps zero-volume sessions,
and it records the observed first and last bar of every pair — including the ones
that stopped years ago — as that pair's life.
"""

import argparse
import datetime
import logging

import pandas as pd

from b3_pipeline import fx_series, market_storage, storage

from . import binance_archive as archive

logger = logging.getLogger(__name__)

# USDT is the deepest book and the longest history on this venue; the BRL pairs
# matter because they need no conversion at all. Both are ingested by default.
DEFAULT_QUOTES = ("USDT", "BRL")

# A pair whose archive stops for longer than this is treated as delisted. Not
# zero: the monthly file for the current month only appears once the month ends,
# so a live pair is routinely two months "behind" in the monthly archive. Too
# short and every living pair is declared dead on the 1st; too long and a real
# delisting takes a quarter to show up. Ninety days is the smallest window that
# clears the monthly publication lag with room for a late upload.
DELISTED_AFTER_DAYS = 90

# USDT is not USD. It has held its peg closely but not exactly, and converting a
# USDT close to BRL through the USD PTAX rate carries that basis. It is the
# honest approximation available for free — the alternative is a paid USDT/USD
# reference rate — and it is recorded here rather than buried: any strategy that
# trades the peg itself must not read the *_brl columns.
_USD_QUOTES = frozenset({"USDT", "USDC", "BUSD", "FDUSD", "USD1"})


def _quote_of(symbol: str, quotes) -> str:
    """The quote asset of a pair, longest suffix first so USDT beats USD."""
    for q in sorted(quotes, key=len, reverse=True):
        if symbol.endswith(q):
            return q
    return ""


def select_symbols(quotes=DEFAULT_QUOTES, limit: int = 0) -> list:
    """Archive symbols whose quote asset is one we ingest.

    Deliberately no liveness filter. Every pair the bucket has ever held is a
    candidate, which is the entire point; `limit` exists for smoke tests and
    truncates alphabetically, so it must never be used for a production run.
    """
    symbols = [s for s in archive.list_symbols() if _quote_of(s, quotes)]
    logger.info(f"{len(symbols):,} pairs quoted in {'/'.join(quotes)}")
    return symbols[:limit] if limit else symbols


def _instrument_row(symbol: str, quote: str, bars: pd.DataFrame,
                    today: datetime.date) -> dict:
    """One `instruments` row from a pair's observed history.

    listing_date and delisting_date are the first and last bar. The archive
    announces nothing, so these are observations, and delist_reason says so.
    """
    first, last = bars["date"].min(), bars["date"].max()
    dead = (today - last).days > DELISTED_AFTER_DAYS
    return {
        "market": archive.MARKET,
        "symbol": symbol,
        "venue": archive.VENUE,
        "currency": quote,
        "kind": "SPOT_PAIR",
        "listing_date": first,
        "delisting_date": last if dead else None,
        # Never "delisted", full stop: this plane is one venue, and a pair that
        # left Binance may well still trade elsewhere.
        "delist_reason": "archive ends — delisted from Binance spot" if dead else None,
        "first_seen": first,
        "last_seen": last,
        # A pair stops trading; the token behind it is not thereby worthless. The
        # simulator liquidates at the last mark unless something else sets this.
        "is_worthless": 0,
    }


def _with_brl(bars: pd.DataFrame, quote: str, rates: pd.Series) -> pd.DataFrame:
    """Materialise the BRL columns for one pair's bars.

    A BRL-quoted pair is already in reais: rate 1.0, no PTAX involved, and no
    rounding through a conversion that would only lose precision.
    """
    if quote == "BRL":
        out = bars.copy()
        out["fx_rate"] = 1.0
        out["close_brl"] = out["close"]
        out["adj_close_brl"] = out["adj_close"]
        return out
    if quote not in _USD_QUOTES:
        # No rate for this quote asset. NULL BRL columns, which is the honest
        # answer, and the native prices are still worth storing.
        return bars
    return fx_series.to_brl(bars, rates)


def ingest_symbol(conn, symbol: str, quote: str, rates: pd.Series,
                  today: datetime.date, months=None) -> int:
    """Fetch, convert and write one pair. Returns rows written."""
    months = months if months is not None else archive.list_months(symbol)
    if not months:
        logger.warning(f"{symbol}: archive has no monthly files")
        return 0

    frames = [archive.fetch_month(symbol, m) for m in months]
    bars = pd.concat([f for f in frames if not f.empty], ignore_index=True) \
        if any(not f.empty for f in frames) else pd.DataFrame()
    if bars.empty:
        logger.warning(f"{symbol}: {len(months)} files, no parseable bars")
        return 0

    market_storage.upsert_instruments(
        conn, pd.DataFrame([_instrument_row(symbol, quote, bars, today)]))
    return market_storage.upsert_market_prices(
        conn, _with_brl(bars, quote, rates))


def run_pipeline(db_path=None, quotes=DEFAULT_QUOTES, limit: int = 0,
                 today: datetime.date = None) -> dict:
    """Ingest the archive into the plane. Returns a summary for `ingest_runs`."""
    today = today or datetime.date.today()
    logger.info(f"Crypto ingest -> {storage.describe_target(db_path)}")

    with storage.get_connection(db_path) as conn:
        storage.init_db(conn)

        logger.info("Step 1/3: PTAX USD/BRL...")
        fx = fx_series.build(today)
        if not fx.empty:
            storage.upsert_external_prices(conn, fx)
        rates = fx_series.load(conn)
        if rates.empty:
            logger.warning("no PTAX on the plane — BRL columns will be NULL")

        logger.info("Step 2/3: enumerating the archive...")
        symbols = select_symbols(quotes, limit)

        logger.info(f"Step 3/3: ingesting {len(symbols):,} pairs...")
        rows, failed, dead = 0, [], 0
        for i, symbol in enumerate(symbols, 1):
            quote = _quote_of(symbol, quotes)
            try:
                rows += ingest_symbol(conn, symbol, quote, rates, today)
            except Exception as e:                      # noqa: BLE001
                # One unreachable pair must not cost the other 3,694. The names
                # are returned so a caller can retry them rather than re-running
                # the whole archive.
                logger.error(f"{symbol}: {e}")
                failed.append(symbol)
            if i % 100 == 0:
                logger.info(f"  {i:,}/{len(symbols):,} pairs, {rows:,} rows")

        dead = storage.read_sql(
            "SELECT COUNT(*) c FROM instruments"
            " WHERE market = ? AND delisting_date IS NOT NULL",
            conn, params=[archive.MARKET]).iloc[0]["c"]

    summary = {"symbols": len(symbols), "rows": rows,
               "delisted": int(dead), "failed": failed}
    logger.info(f"Done: {rows:,} rows over {len(symbols):,} pairs, "
                f"{dead:,} of them delisted, {len(failed)} failed")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--quotes", default=",".join(DEFAULT_QUOTES),
                    help="comma-separated quote assets to ingest")
    ap.add_argument("--limit", type=int, default=0,
                    help="smoke-test only: truncate the universe alphabetically")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_pipeline(args.db_path, tuple(args.quotes.split(",")), args.limit)


if __name__ == "__main__":
    main()
