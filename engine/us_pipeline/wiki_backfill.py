"""Backfill fifty-six years of US history from the WIKI bulk export.

The third bulk pass on this plane, after COTAHIST for B3 and the Binance bucket
for crypto, and the sibling of `us_backfill`: same shape, different vendor, and
it exists because the vendor `us_backfill` was written against turned out to
gate its archive behind an account. This one needs a free key and one request.

**What it is and is not.** WIKI stopped being maintained in April 2018. This
module therefore fills 1962..2018 and nothing after — see `wiki_archive` for the
dataset's shape and the limits of its survivorship coverage. It is a history
backfill, not a price feed.

**Which symbols it ends up owning.** `us_backfill` walks Stooq's quote endpoint,
which reaches the present, and it *replaces* this module's rows for every symbol
it can serve — one symbol gets one source and one convention, because a raw
close spliced onto a split-adjusted one fabricates a split-sized return where
they meet and no arithmetic repairs it without the post-2018 splits. So what
stays here is the part only this file has: the names that died inside WIKI's
maintenance window and that the quote endpoint no longer quotes. There the
splice question does not arise, because nothing extends them.

That division is why the order of the two jobs does not matter and why this one
is still worth running first on an empty plane: it is fast, it is one request,
and every symbol Stooq later reaches is one this module was only ever holding.

**Why it can claim a total return series when `us_backfill` cannot.** Stooq
serves split-adjusted closes and no distributions, so its bars are `split_only`
and the metered feed has to buy dividends per symbol to upgrade them. WIKI ships
raw OHLC *and* the events — 118,131 dividends and 4,096 splits ride along in the
bar rows — so `us_adjust` computes both adjusted columns here from the same
file, for free, and the result is `total_return`.

That is a real loss on every symbol `us_backfill` takes over, and it is stated
rather than buried: those symbols drop to `split_only`, `/market/status` reports
the drop, and the metered feed's corporate-actions budget is what buys the
adjustment back — ten symbols a day, on the names a strategy actually holds. The
plane trades a dividend adjustment it had for eight years of bars it did not.

The adjusted columns are computed rather than copied from the vendor's own
`adj_close`. The vendor's series is back-adjusted to *its* last day, 2018-03-27;
recomputing through `us_adjust` puts this plane's adjustment on the same footing
as every other symbol on it, and means the split guards and the pathological
dividend guard apply to this file too.

**What it deliberately does not write.** No `listing_date`, `delisting_date` or
`delist_reason`, for exactly the reason `us_backfill` gives: a symbol's absence
from a frozen file is a fact about the file, not about the company. Apple did
not delist in March 2018. `first_seen` and `last_seen` are observations of this
file and are safe.

**Why these symbols are not fenced off from the metered feed.** `us_backfill`
adds its symbols to `covered_symbols` so `us_prices` never re-prices them and
splices two adjustment conventions into one column. This module writes a raw
`close` — the same convention `us_prices` buys — and stops in 2018, so for a
symbol Stooq cannot serve, the metered feed topping it up to the present is the
wanted outcome rather than a seam. The two do meet in `adj_close`, whose
back-adjustment anchor differs either side of 2018; that is recorded in the
source tag rather than papered over, and a strategy reading `adj_close` across
the seam should know it.
"""

import argparse
import datetime
import logging

import pandas as pd

from b3_pipeline import config, fx_series, market_storage, storage, us_storage

from . import us_adjust
from . import wiki_archive as archive

logger = logging.getLogger(__name__)

MARKET = archive.MARKET

# Progress cadence. Three thousand symbols and 15M rows is a long silence.
_LOG_EVERY = 250


def bulk_path(db_dir=None):
    """Where the export lands. The engine data volume, like the SEC bulks."""
    return (db_dir or config.DATA_DIR) / archive.BULK_FILENAME


def ensure_bulk(path, fetch=None, force: bool = False) -> str:
    """The export on disk, downloaded if absent.

    No staleness check, and that is the point rather than an omission: this
    dataset was frozen in 2018, so a copy of it can never be out of date.
    Re-downloading 463MB on a schedule would buy nothing at all. `force` is the
    escape hatch for a corrupted file.
    """
    fetch = fetch or archive.download_bulk
    if path.exists() and not force:
        logger.info(f"reusing {path} ({path.stat().st_size:,} bytes)")
        return str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return fetch(str(path))


def to_market_rows(symbol: str, bars: pd.DataFrame,
                   rates: pd.Series) -> tuple:
    """Archive bars -> (`market_prices` rows, adjust_state).

    `volume` is money and not share count, the same definition `us_prices` and
    `us_backfill` use, because `min_adtv` filters on money — a symbol that moves
    between the three sources must not change what its `volume` column means.
    """
    splits, dividends = archive.events(bars)
    # Both frames are always passed, empty or not, and that is what makes the
    # state honest: `us_adjust` reads a `None` as "this endpoint did not
    # answer", and here the file answered for both. A symbol that never paid a
    # dividend has a total-return series identical to its split-adjusted one,
    # which is a fact about the symbol rather than a gap in the data.
    adjusted, state = us_adjust.apply(bars, splits, dividends)
    close = adjusted["close"].astype(float)
    out = pd.DataFrame({
        "market": MARKET,
        "symbol": symbol,
        "date": adjusted["date"],
        "open": adjusted["open"],
        "high": adjusted["high"],
        "low": adjusted["low"],
        "close": close,
        "volume": adjusted["share_volume"].astype(float) * close,
        "split_adj_close": adjusted["split_adj_close"],
        "adj_close": adjusted["adj_close"],
        "source": f"{archive.SOURCE}:{state}",
    })
    return fx_series.to_brl(out, rates), state


def _instrument_row(symbol: str, bars: pd.DataFrame) -> dict:
    """One `instruments` row. Observed bounds only — see the module docstring."""
    return {
        "market": MARKET,
        "symbol": symbol,
        "currency": archive.CURRENCY,
        "first_seen": bars["date"].min(),
        "last_seen": bars["date"].max(),
    }


def ingest_symbol(conn, symbol: str, bars: pd.DataFrame, rates: pd.Series,
                  today) -> dict:
    """Write one symbol's bars, master row and cursor. Returns rows written."""
    if bars.empty:
        return {"rows": 0, "cursor": None}
    rows_df, state = to_market_rows(symbol, bars, rates)
    market_storage.upsert_instruments(
        conn, pd.DataFrame([_instrument_row(symbol, bars)]))
    rows = market_storage.upsert_market_prices(conn, rows_df)
    return {"rows": rows, "cursor": {
        "symbol": symbol,
        "first_bar": str(bars["date"].iloc[0]),
        "last_bar": str(bars["date"].iloc[-1]),
        "prices_fetched_at": str(today),
        "adjust_state": state,
        "status": "ok",
        "error": None,
    }}


def _check_complete(symbols: int, rows: int, newest: str, limit: int) -> None:
    """Refuse a run that streamed a short file. Nothing is rolled back.

    Checked after the stream rather than before it, because neither the symbol
    count nor the newest bar is knowable until the file ends — and checked at
    all because a plane quietly holding a third of the history looks exactly
    like one holding all of it. `limit` means a deliberate smoke test, which is
    short on purpose.
    """
    if limit:
        return
    if symbols < archive.MIN_PLAUSIBLE_SYMBOLS:
        raise RuntimeError(
            f"the export yielded {symbols:,} symbols, below the floor of "
            f"{archive.MIN_PLAUSIBLE_SYMBOLS:,}. Most likely a truncated "
            f"download. {rows:,} rows were written and are safe to keep, but "
            "the run is not a complete backfill — delete the zip and re-run.")
    if newest and newest < archive.LAST_BAR:
        raise RuntimeError(
            f"the newest bar in the export is {newest}, before the known last "
            f"day of the dataset ({archive.LAST_BAR}). The file is truncated; "
            "delete the zip and re-run.")


def run_pipeline(db_path=None, zip_path=None, limit: int = 0, today=None,
                 force_download: bool = False) -> dict:
    """Ingest the bulk export into the plane. Returns a summary for `ingest_runs`."""
    today = today or datetime.date.today()
    logger.info(f"WIKI bulk backfill -> {storage.describe_target(db_path)}")

    with storage.get_connection(db_path) as conn:
        storage.init_db(conn)

        logger.info("Step 1/3: PTAX USD/BRL...")
        fx = fx_series.build(today)
        if not fx.empty:
            storage.upsert_external_prices(conn, fx)
        rates = fx_series.load(conn)
        if rates.empty:
            logger.warning("no PTAX on the plane — BRL columns will be NULL")

        logger.info("Step 2/3: fetching the export...")
        path = zip_path or ensure_bulk(bulk_path(), force=force_download)

        logger.info("Step 3/3: streaming symbols...")
        rows, symbols, failed, cursors, states = 0, 0, [], [], {}
        newest = ""
        for symbol, bars in archive.iter_symbols(path):
            if limit and symbols >= limit:
                break
            try:
                result = ingest_symbol(conn, symbol, bars, rates, today)
            except Exception as e:                          # noqa: BLE001
                # One bad ticker must not cost the other three thousand. The
                # names come back so a caller can look rather than re-streaming
                # 15M rows to find them.
                logger.error(f"{symbol}: {e}")
                failed.append(symbol)
                continue
            rows += result["rows"]
            if result["cursor"]:
                symbols += 1
                cursors.append(result["cursor"])
                states[result["cursor"]["adjust_state"]] = states.get(
                    result["cursor"]["adjust_state"], 0) + 1
                newest = max(newest, result["cursor"]["last_bar"])
            if len(cursors) >= _LOG_EVERY:
                # Batched for `us_backfill`'s reason: the file is already on
                # disk, so a re-run costs one read, and three thousand
                # single-row upserts would dominate the runtime.
                us_storage.upsert_price_cursor(conn, pd.DataFrame(cursors))
                cursors = []
                logger.info(f"  {symbols:,} symbols, {rows:,} rows")
        if cursors:
            us_storage.upsert_price_cursor(conn, pd.DataFrame(cursors))

        _check_complete(symbols, rows, newest, limit)

        summary = {"symbols": symbols, "rows": rows, "newest_bar": newest,
                   "adjust_states": states,
                   "failed": failed[:50], "failed_count": len(failed)}
        logger.info(f"Done: {rows:,} rows over {symbols:,} symbols, newest "
                    f"{newest}, states {states}, {len(failed)} failed")
        return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--zip-path", default=None,
                    help="use an export already on disk instead of fetching")
    ap.add_argument("--limit", type=int, default=0,
                    help="smoke-test only: stop after N symbols")
    ap.add_argument("--force-download", action="store_true",
                    help="re-fetch the export even if a copy is on disk")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_pipeline(args.db_path, args.zip_path, args.limit,
                 force_download=args.force_download)


if __name__ == "__main__":
    main()
