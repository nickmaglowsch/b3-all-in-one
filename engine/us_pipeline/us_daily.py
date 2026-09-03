"""Keep the US plane current: re-adjust for splits, then append the session.

The history under this comes from a bulk file loaded by hand (Stooq's
`d_us_txt` dump, which no machine can fetch — see `massive_daily`'s header).
This module is what stops that history going stale, and it is deliberately two
steps rather than one.

**Splits first, bars second, and never the other way round.** The vendor serves
a split-adjusted series, so the day after a 4-for-1 its bar is a quarter of the
day before's — correctly. The stored history knows nothing about that split and
is still on the old basis, so appending the new bar onto it fabricates a -75%
return at the join. Re-scaling the old rows first makes the two sides
comparable, and doing it in the other order would leave one session mis-joined
until the next run, which is exactly long enough for a backtest to read it.

**A split is applied once, ever.** `market_storage.applied_splits` is keyed on
the vendor's own split id, so a retry, an overlapping window or a re-run cannot
scale the same history twice. That matters more than it looks: a second
application of a 4-for-1 is not a visible failure, it is a history that is
quietly four times too small, and nothing downstream could tell.

The universe is not this module's to grow. Bars are written only for symbols
`instruments` already carries, because `us_listing` and `symbol_directory` own
what the US market contains and a price feed inventing rows there would undo
the ownership `market_storage.upsert_instruments` was rewritten to protect.
Tickers the vendor returns and the plane does not know are counted and
reported, not stored.
"""

import argparse
import datetime
import logging

import pandas as pd

from b3_pipeline import fx_series, market_storage, split_storage, storage

from . import massive_daily as vendor
from . import us_seam

logger = logging.getLogger(__name__)

MARKET = vendor.MARKET
ADJUST_STATE = vendor.ADJUST_STATE
SOURCE = f"{vendor.SOURCE}:{ADJUST_STATE}"

# A first run after the bulk load has a handful of sessions to catch up; a
# nightly one has exactly one. The cap bounds a run that finds a long gap —
# each session is one request, and five a minute is the free ceiling.
MAX_SESSIONS = 10

# The split window is derived from the plane, never from a constant, and this is
# the subtlest thing in the module.
#
# A back-adjusted history is adjusted *as of some date* — for the bulk file,
# the day it was cut; after that, the last session this job appended. Every
# split executing on or before that date is therefore already baked into the
# stored numbers. Re-applying one would not raise, it would quietly halve or
# quarter a correct history, and nothing downstream could tell.
#
# So the window is `(last_stored_session, today]`: exactly the splits the stored
# basis has not yet absorbed. It needs no seeding on a freshly loaded plane —
# the dump's own last bar excludes everything the dump already reflects — and it
# widens by itself if the job stops running for a week, because `last_stored`
# stops advancing with it. `applied_splits` is the second line of defence, not
# the first: the window is what makes the first run safe.


def last_stored_session(conn) -> datetime.date:
    """The most recent US bar on the plane, or None if it holds none."""
    df = storage.read_sql(
        "SELECT MAX(date) AS d FROM market_prices WHERE market = ?",
        conn, params=(MARKET,))
    if df.empty or pd.isna(df["d"].iloc[0]):
        return None
    return pd.Timestamp(df["d"].iloc[0]).date()


def sessions_to_fetch(last: datetime.date, today: datetime.date,
                      max_sessions: int = MAX_SESSIONS) -> list:
    """Weekday sessions strictly after `last` and strictly before `today`.

    Weekdays only, because asking about a Saturday spends a request to be told
    the market was shut. Holidays are not filtered — the plane has no US holiday
    calendar and inventing one here would be a second source of truth; the
    vendor answers a holiday with an empty result set, which `parse_grouped`
    already reads as "no session".

    `today` is excluded: the free tier refuses a session that has not closed,
    and the run would spend its request to be told so.
    """
    if last is None:
        return []
    out, day = [], last + datetime.timedelta(days=1)
    while day < today and len(out) < max_sessions:
        if day.weekday() < 5:
            out.append(day)
        day += datetime.timedelta(days=1)
    return out


def writable_symbols(conn) -> set:
    """Symbols this module may touch: known to the universe *and* already on a
    split-adjusted basis.

    Both halves matter and for different reasons. `instruments` is the universe
    `us_listing` and `symbol_directory` own, and a price feed inventing rows
    there would undo the ownership `upsert_instruments` was rewritten to
    protect. `us_seam.covered_symbols` is the adjustment basis: appending a
    back-adjusted bar onto a `total_return` history splices two conventions in
    one column and fabricates a return where they meet — the same seam
    `delete_market_prices_from_other_sources` exists to prevent, and one this
    module cannot fix the way a bulk load can, because a single session is not a
    history to replace one with.

    So a WIKI-only symbol is skipped rather than corrupted or half-replaced.
    Today every one of those is delisted and no live session would name it
    anyway; that is a reason the check is cheap, not a reason to omit it."""
    df = storage.read_sql(
        "SELECT symbol FROM instruments WHERE market = ?",
        conn, params=(MARKET,))
    universe = set(df["symbol"]) if not df.empty else set()
    return universe & us_seam.covered_symbols(conn)


def apply_due_splits(conn, since, until, held: set) -> dict:
    """Re-scale history for every split that executed and is not yet applied.

    `since` must be the day after the last stored session — see the note on the
    window above. Passing anything earlier re-applies splits the stored basis has
    already absorbed, which is silent corruption rather than an error.

    Returns a summary. A split for a symbol the plane does not hold is recorded
    as applied with zero rows touched: it is genuinely done — there was nothing
    to scale — and leaving it unrecorded would re-ask about it every night for
    as long as the window reaches back.
    """
    splits = vendor.fetch_splits(since, until)
    done = split_storage.applied_split_ids(conn, MARKET)
    applied, rows, skipped = 0, 0, 0
    for s in splits:
        if s["id"] in done:
            skipped += 1
            continue
        factor = vendor.split_factor(s)
        symbol = str(s["ticker"]).upper().strip()
        n = 0
        if symbol in held:
            n = split_storage.adjust_history_for_split(
                conn, MARKET, symbol, s["execution_date"], factor)
            logger.info(
                f"split {symbol} {s['split_from']}->{s['split_to']} on "
                f"{s['execution_date']}: scaled {n:,} rows by {factor:g}")
        split_storage.record_applied_split(conn, MARKET, s, factor, n)
        applied, rows = applied + 1, rows + n
    return {"splits_seen": len(splits), "splits_applied": applied,
            "splits_already_done": skipped, "rows_rescaled": rows}


def to_market_rows(bars: pd.DataFrame, rates: pd.Series) -> pd.DataFrame:
    """Vendor bars -> `market_prices` rows, in USD and in BRL.

    The same turnover definition and the same `fx_series.to_brl` call the bulk
    load used, so a symbol does not change what its `volume` column means on the
    day this module takes over from the file.
    """
    if bars.empty:
        return bars
    close = bars["close"].astype(float)
    out = pd.DataFrame({
        "market": MARKET, "symbol": bars["symbol"], "date": bars["date"],
        "open": bars["open"], "high": bars["high"], "low": bars["low"],
        "close": close,
        "volume": bars["share_volume"].astype(float) * close,
        "split_adj_close": close, "adj_close": close, "source": SOURCE,
    })
    return fx_series.to_brl(out, rates)


def run_pipeline(db_path=None, today=None, max_sessions: int = MAX_SESSIONS) -> dict:
    """Re-adjust for splits, then append every settled session since the last."""
    today = today or datetime.date.today()
    logger.info(f"US daily -> {storage.describe_target(db_path)}")

    with storage.get_connection(db_path) as conn:
        storage.init_db(conn)

        logger.info("Step 1/4: PTAX USD/BRL...")
        fx = fx_series.build(today)
        if not fx.empty:
            storage.upsert_external_prices(conn, fx)
        rates = fx_series.load(conn)

        last = last_stored_session(conn)
        if last is None:
            raise RuntimeError(
                "the US plane holds no bars — load the bulk history first; "
                "this module refreshes, it does not backfill.")
        held = writable_symbols(conn)
        logger.info(f"last stored session {last}, {len(held):,} writable symbols")

        logger.info("Step 2/4: splits...")
        split_summary = apply_due_splits(
            conn, last + datetime.timedelta(days=1), today, held)
        logger.info(f"  {split_summary}")

        due = sessions_to_fetch(last, today, max_sessions)
        logger.info(f"Step 3/4: {len(due)} session(s) to fetch: "
                    f"{due[0] if due else '-'}..{due[-1] if due else '-'}")

        rows, sessions, unknown_total, not_settled = 0, 0, 0, None
        for day in due:
            try:
                payload = vendor.fetch_grouped(day)
            except vendor.NotSettled as e:
                not_settled = str(e)
                logger.info(f"{day} has not settled yet — stopping here")
                break
            bars = vendor.parse_grouped(payload, day)
            if bars.empty:
                continue
            # Counted, not stored: a ticker the plane does not know, or knows
            # only on the frozen WIKI basis. Reported so a growing number is
            # visible rather than silently dropped every night.
            unknown = set(bars["symbol"]) - held
            unknown_total += len(unknown)
            bars = bars[bars["symbol"].isin(held)]
            if bars.empty:
                continue
            rows += market_storage.upsert_market_prices(
                conn, to_market_rows(bars, rates))
            conn.commit()
            sessions += 1

        logger.info("Step 4/4: done")
        summary = {"rows": rows, "sessions": sessions,
                   "unknown_to_plane": unknown_total,
                   "last_stored_before": str(last),
                   "not_settled": not_settled, **split_summary}
        logger.info(f"Done: {summary}")
        return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--max-sessions", type=int, default=MAX_SESSIONS)
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_pipeline(args.db_path, max_sessions=args.max_sessions)


if __name__ == "__main__":
    main()
