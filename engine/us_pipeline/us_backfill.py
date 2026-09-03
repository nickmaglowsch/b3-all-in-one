"""Backfill the US price plane one symbol at a time, off the metered budget.

The counterpart to `us_prices`, and the reason that module can stop trying to be
a backfill. Free-tier arithmetic, spelled out once: 25 requests a day, three per
fresh symbol, ~11,000 symbols — five years to a first pass, and 550x the daily
allowance to refresh what it had. None of that moves with more care in the
client, because it is a property of a per-symbol metered feed.

Stooq's quote endpoint serves the same history for nothing, so this module is
the bulk pass and `us_prices` is the top-up. It walks rather than downloads —
see `stooq_daily` for why the one-file version had to be abandoned — but the
shape is `crypto_pipeline`'s with the Binance bucket: a free source that charges
in wall-clock, walked in resumable slices.

**Why it is resumable, and why that is not optional.** Anonymous callers have a
daily hit ceiling. Hitting it is the expected end of a run, not a failure: the
walk stops, keeps everything it wrote, and the next run starts where the cursor
says this one stopped. `--refresh-after-days` is what makes "where it stopped"
mean something — a symbol priced recently enough is skipped, so a re-run costs
nothing on the ground it already covered and the ceiling is spent on new ground.

**The seam with WIKI, which is the part worth reading twice.** WIKI ships a
*raw* close and stops at 2018-03-27; Stooq ships a *split-adjusted* one and
reaches today. `market_prices` is keyed `(market, symbol, date)`, so a symbol
both of them wrote holds two conventions in one column with a fabricated
split-sized return where they meet — and there is no arithmetic that repairs it,
because converting either series to the other's convention needs the post-2018
splits and neither source has them.

So a symbol gets one source and one convention, never two.
`delete_market_prices_from_other_sources` drops WIKI's rows for a symbol before
this module writes it — an upsert alone would leave whatever WIKI had in front
of Stooq's first bar, still raw, still spliced, and now hidden behind dates that
do look right. WIKI keeps the symbols Stooq cannot serve, which are mostly the
names that died inside its maintenance window, and there the question does not
arise because nothing extends them.

**What that costs, stated rather than buried.** A symbol moved from WIKI to
Stooq drops from `total_return` to `split_only`, because Stooq carries no
distributions, and `/market/status` reports the drop. It is also the drop the
metered feed exists to undo: `demote_bulk_priced` stops `us_prices` re-buying
bars this module already has and points its 20 usable requests a day at
corporate actions instead. The plane trades a dividend adjustment it had for
eight years of bars it did not, and buys the adjustment back at ten a day.

**What this deliberately does not write.** No `listing_date`, `delisting_date`
or `delist_reason`. Those are declared intervals and belong to `us_listing`,
which buys them from `LISTING_STATUS` for two requests; Stooq serves the
currently-quoted, so inferring a delisting from a symbol's absence here would
delist every name that ever died — the precise inversion PR #44 fixed.
`first_seen` and `last_seen` are observations of this source and are safe.
"""

import argparse
import datetime
import logging

import pandas as pd

from b3_pipeline import fx_series, market_storage, storage, us_storage

from . import stooq_daily as vendor
from . import us_adjust

logger = logging.getLogger(__name__)

MARKET = vendor.MARKET

# The bars arrive split-adjusted and without distributions. `us_adjust` already
# has the vocabulary for exactly this and `/market/status` already reports it.
ADJUST_STATE = us_adjust.SPLIT_ONLY

# A symbol priced within this many days is left alone. The walk is bounded by
# the vendor's daily ceiling rather than by our patience, so the scarce resource
# is requests, and re-asking about a symbol that already has last week's bars
# spends one on ground the plane covers. A week matches the cadence a
# split-only history actually needs; `us_prices` carries the daily edge.
REFRESH_AFTER_DAYS = 7

# Recorded for a symbol this vendor does not quote, so the walk remembers having
# asked. Without it every run re-asks about every name Stooq has never heard of
# — thousands of them, since `instruments` carries the delisted tail — and a run
# bounded by a daily ceiling would spend the whole ceiling on them, every day,
# and never reach a symbol it could actually price.
UNKNOWN_STATUS = "unknown_to_vendor"

# Progress cadence. Eleven thousand symbols is a long silence otherwise.
_LOG_EVERY = 200

# Cursor rows are flushed in batches this size. Unlike `us_prices`, which writes
# each row before the metered request that may be its last, a batch here risks
# only re-fetching a few symbols after a crash.
_CURSOR_BATCH = 50


def to_market_rows(symbol: str, bars: pd.DataFrame, rates: pd.Series) -> pd.DataFrame:
    """Vendor bars -> `market_prices` rows, in USD and in BRL.

    Both conversions are `us_prices`', on purpose and not by coincidence: the
    same turnover definition (`share_volume * close`, because `min_adtv` filters
    on money and not on share count) and the same `fx_series.to_brl` call, so a
    symbol that moves between the sources does not change what its `volume`
    column means or how its BRL columns were made.

    `split_adj_close` and `adj_close` both carry the close because the series is
    already split-adjusted and carries no distributions — which is what
    `ADJUST_STATE` records. Nothing is inferred here; `us_adjust` is not called
    because there are no events to apply.
    """
    if bars.empty:
        return bars
    close = bars["close"].astype(float)
    out = pd.DataFrame({
        "market": MARKET,
        "symbol": symbol,
        "date": bars["date"],
        "open": bars["open"],
        "high": bars["high"],
        "low": bars["low"],
        "close": close,
        "volume": bars["share_volume"].astype(float) * close,
        "split_adj_close": close,
        "adj_close": close,
        "source": f"{vendor.SOURCE}:{ADJUST_STATE}",
    })
    return fx_series.to_brl(out, rates)


def _instrument_row(symbol: str, bars: pd.DataFrame) -> dict:
    """One `instruments` row from a symbol's observed history.

    Observed bounds only, and no `venue`: the bulk dump named the exchange in
    its directory paths and this endpoint names nothing, so claiming one would
    be a guess written over what `symbol_directory` actually captured. See the
    module docstring on why no listing or delisting date is claimed either.
    """
    return {
        "market": MARKET,
        "symbol": symbol,
        "currency": vendor.CURRENCY,
        "first_seen": bars["date"].min(),
        "last_seen": bars["date"].max(),
    }


def universe(conn) -> list:
    """US symbols worth fetching, most useful first.

    Symbols the SEC ingest already has fundamentals for come first: they are the
    ones a fundamental strategy can actually select. Test issues and the
    `CIK##########` placeholders are excluded — the first never traded, the
    second is not a ticker any venue would recognise. The same query as
    `us_prices._universe`, copied rather than shared because `us_prices` imports
    this module for the seam and importing back is a cycle.

    The order matters more here than it looks: a run ends when the vendor's
    daily ceiling is spent, so it is the priority in which a multi-day first
    pass reaches the market.
    """
    df = storage.read_sql(
        "SELECT i.symbol,"
        "       CASE WHEN i.cik IS NOT NULL THEN 0 ELSE 1 END AS tier"
        "  FROM instruments i"
        " WHERE i.market = ? AND COALESCE(i.is_test_issue, 0) = 0"
        "   AND i.symbol NOT LIKE 'CIK%'"
        " ORDER BY tier, i.symbol", conn, params=(MARKET,))
    return list(df["symbol"]) if not df.empty else []


def select_work(symbols, cursor: pd.DataFrame, *, today,
                refresh_after_days: int = REFRESH_AFTER_DAYS) -> list:
    """The symbols this run should fetch, in order.

    Pure — no network, no database — so the resume policy can be tested
    directly, in the spirit of `us_prices.select_work` and
    `external_prices.days_to_fetch`.

    A symbol is due when this source has never *asked* about it, or asked longer
    ago than `refresh_after_days`. Asked, not priced: a name the vendor does not
    quote has to count as work done or it comes back every run forever.

    A symbol another source priced is still due: the point of this run is to
    replace a raw, frozen history with a live one, and `ingest_symbol` is what
    drops the old rows.
    """
    today = pd.Timestamp(today).date()
    fetched = {}
    if not cursor.empty:
        for _, row in cursor.iterrows():
            ours = (row.get("adjust_state") == ADJUST_STATE
                    or row.get("status") == UNKNOWN_STATUS)
            if not ours:
                # Priced by another source, or by nothing. Due either way.
                continue
            when = pd.to_datetime(row.get("prices_fetched_at"), errors="coerce")
            if not pd.isna(when):
                fetched[row["symbol"]] = when.date()

    due = []
    for symbol in symbols:
        when = fetched.get(symbol)
        if when is None or (today - when).days >= refresh_after_days:
            due.append(symbol)
    return due


def ingest_symbol(conn, symbol: str, rates: pd.Series, today,
                  session=None) -> dict:
    """Fetch, replace and record one symbol. Returns rows written.

    Raises `stooq_daily.Throttled` straight through: that is the run's signal to
    stop, and swallowing it here would turn one refusal into eleven thousand.
    """
    bars = vendor.parse_bars(vendor.fetch_symbol(symbol, session=session), symbol)
    if bars.empty:
        # `status` is the one field here another writer also sets, so this can
        # clear a `retry` `us_prices` left. That costs priority and not
        # correctness — the symbol still has no bars, so it lands in that
        # module's "no bars at all" bucket, one place behind where it was.
        return {"rows": 0, "cursor": {
            "symbol": symbol, "prices_fetched_at": str(today),
            "status": UNKNOWN_STATUS,
            "error": f"{vendor.SOURCE} does not quote this symbol"}}

    # Before the write, never after: see the module docstring on the seam. A
    # crash between the two costs a re-fetch, which is the cheap direction.
    market_storage.delete_market_prices_from_other_sources(
        conn, MARKET, symbol, vendor.SOURCE_PREFIX)

    market_storage.upsert_instruments(
        conn, pd.DataFrame([_instrument_row(symbol, bars)]))
    rows = market_storage.upsert_market_prices(
        conn, to_market_rows(symbol, bars, rates))
    return {"rows": rows, "cursor": {
        "symbol": symbol,
        "first_bar": str(bars["date"].iloc[0]),
        "last_bar": str(bars["date"].iloc[-1]),
        "prices_fetched_at": str(today),
        "adjust_state": ADJUST_STATE,
        "status": "ok",
        "error": None,
    }}


def _walk(conn, due: list, rates: pd.Series, today, delay, session) -> dict:
    """Fetch each due symbol until the list runs out or the vendor says stop.

    Split from `run_pipeline` so the loop and the run's setup can be read
    separately; it carries the two failure policies that differ from each other
    and matter more than the arithmetic around them.

    A `Throttled` ends the walk: everything already written stays and the next
    run resumes from the cursor. Any other exception costs only its own symbol —
    one bad response must not end a walk with ten thousand good ones left — and
    the names come back so a caller can look rather than re-running the whole
    universe to find them.
    """
    rows, symbols, empty, failed, cursors = 0, 0, 0, [], []
    throttled = False

    def flush():
        if cursors:
            us_storage.upsert_price_cursor(conn, pd.DataFrame(cursors))
            cursors.clear()

    for i, symbol in enumerate(due, 1):
        try:
            result = ingest_symbol(conn, symbol, rates, today, session=session)
        except vendor.Throttled as e:
            logger.info(str(e))
            throttled = True
            break
        except Exception as e:                              # noqa: BLE001
            logger.error(f"{symbol}: {e}")
            failed.append(symbol)
            continue
        rows += result["rows"]
        cursors.append(result["cursor"])
        # On the cursor's own state, not on a row count: a symbol whose bars all
        # failed `upsert_market_prices`' OHLC validation is still a symbol this
        # vendor quotes, and counting it as one it does not know would be a lie
        # in the one number an operator reads to judge coverage.
        if result["cursor"].get("adjust_state"):
            symbols += 1
        else:
            empty += 1
        if len(cursors) >= _CURSOR_BATCH:
            flush()
        if i % _LOG_EVERY == 0:
            logger.info(f"  {i:,}/{len(due):,} symbols, {rows:,} rows")
        vendor.sleep_between(delay)
    flush()

    return {"symbols": symbols, "rows": rows, "unknown_to_vendor": empty,
            "throttled": throttled, "failed": failed}


def run_pipeline(db_path=None, limit: int = 0, today=None,
                 refresh_after_days: int = REFRESH_AFTER_DAYS,
                 delay=None, session=None) -> dict:
    """Walk the universe until it is done or the vendor says stop.

    Returns a summary for `ingest_runs`. A throttled stop is reported in that
    summary and not in the exit code, the same call `us_prices` makes about an
    exhausted request budget: the day's allowance running out is what the day's
    allowance does.
    """
    today = today or datetime.date.today()
    logger.info(f"US backfill -> {storage.describe_target(db_path)}")

    with storage.get_connection(db_path) as conn:
        storage.init_db(conn)

        logger.info("Step 1/3: PTAX USD/BRL...")
        fx = fx_series.build(today)
        if not fx.empty:
            storage.upsert_external_prices(conn, fx)
        rates = fx_series.load(conn)
        if rates.empty:
            logger.warning("no PTAX on the plane — BRL columns will be NULL")

        logger.info("Step 2/3: selecting work...")
        due = select_work(universe(conn), us_storage.load_price_cursor(conn),
                          today=today, refresh_after_days=refresh_after_days)
        if limit:
            due = due[:limit]

        logger.info(f"Step 3/3: fetching {len(due):,} symbols...")
        walked = _walk(conn, due, rates, today, delay, session)

        summary = {**walked, "due": len(due),
                   "failed": walked["failed"][:50],
                   "failed_count": len(walked["failed"]),
                   "adjust_state": us_storage.adjust_state_counts(conn)}
        logger.info(
            f"Done: {summary['rows']:,} rows over {summary['symbols']:,} "
            f"symbols, {summary['failed_count']} failed"
            + (", stopped on the vendor's daily limit"
               if summary["throttled"] else ""))
        return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--limit", type=int, default=0,
                    help="smoke-test only: stop after this many symbols")
    ap.add_argument("--refresh-after-days", type=int, default=REFRESH_AFTER_DAYS,
                    help="re-fetch a symbol once its bars are older than this")
    ap.add_argument("--delay", type=float, default=None,
                    help="seconds between requests (default $STOOQ_DELAY_SECONDS)")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_pipeline(args.db_path, args.limit,
                 refresh_after_days=args.refresh_after_days, delay=args.delay)


if __name__ == "__main__":
    main()
