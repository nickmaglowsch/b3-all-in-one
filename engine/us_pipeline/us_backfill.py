"""Backfill the US price plane from one bulk file, off the metered budget.

The counterpart to `us_prices`, and the reason that module can stop trying to be
a backfill. Free-tier arithmetic, spelled out once: 25 requests a day, three per
fresh symbol, ~11,000 symbols — five years to a first pass, and 550x the daily
allowance to refresh what it had. `us_prices` is careful and resumable and none
of that moves the number, because the number is a property of a per-symbol
metered feed and not of the code calling it.

One `d_us_txt.zip` carries the same history, so this module is the bulk pass and
`us_prices` becomes the top-up. That is not a new pattern here — it is the one
`b3_pipeline.main` uses with COTAHIST year archives and `crypto_main` uses with
the Binance bucket, and it is why those two planes were never on a request
budget in the first place.

**What this writes, and what it deliberately does not.**

It writes bars into `market_prices`, the observed bounds into `instruments`, and
a cursor row per symbol so `us_prices` can see the coverage. It does **not**
write `listing_date`, `delisting_date` or `delist_reason`. Those are declared
intervals and belong to `us_listing`, which buys them from the metered feed's
`LISTING_STATUS` for two requests; the bulk dump carries only the currently
listed, so inferring a delisting from a symbol's absence here would delist every
name that ever died — the precise inversion PR #44 fixed. `first_seen` and
`last_seen` are observations of this file and are safe to write.

**Why the two sources must not both price a symbol.** Stooq publishes a
split-adjusted series and the metered feed publishes a raw one. `market_prices`
is keyed `(market, symbol, date)`, so letting both write one symbol splices two
different price conventions into one column and invents a return at the seam —
silently, because a split-sized jump is exactly what `MARKETS["US"].glitch_down`
is built to mask. `covered_symbols` is what stops that: `us_prices.select_work`
takes the set and never schedules prices for a symbol in it, while still buying
that symbol's corporate actions, which is the one thing the metered feed has
that this file does not.

So the states divide cleanly. A symbol here is `split_only` — real, labelled,
and already enough for a momentum or liquidity screen. Upgrading it to
`total_return` needs dividends, those cost two requests each, and at 20 usable
requests a day the budget now buys ten symbols' distributions a day instead of
six symbols' prices — spent on the names a strategy actually holds.
"""

import argparse
import datetime
import logging

import pandas as pd

from b3_pipeline import config, fx_series, market_storage, storage, us_storage

from . import stooq_archive as archive
from . import us_adjust

logger = logging.getLogger(__name__)

MARKET = archive.MARKET

# The bars arrive split-adjusted and without distributions. `us_adjust` already
# has the vocabulary for exactly this and `/market/status` already reports it.
ADJUST_STATE = us_adjust.SPLIT_ONLY

# Re-download rather than reuse a local copy older than this. The archive is
# republished daily; a stale zip is not wrong, only behind, and the top-up job
# covers the gap. Weekly keeps the ~hundreds of MB transfer off the daily path.
STALE_AFTER_DAYS = 7

# Progress cadence. Eleven thousand symbols is a long silence otherwise.
_LOG_EVERY = 500


def bulk_path(db_dir=None):
    """Where the archive lands. The engine data volume, like the SEC bulks."""
    return (db_dir or config.DATA_DIR) / archive.BULK_FILENAME


def ensure_bulk(path, fetch=None, today=None,
                stale_after_days: int = STALE_AFTER_DAYS) -> str:
    """The archive on disk, downloaded if absent or stale.

    Age is checked rather than assumed both ways: re-downloading hundreds of
    megabytes on every run wastes the volume's bandwidth, and never
    re-downloading freezes the plane at whatever day the file was first fetched
    — which is the failure that would look exactly like a working pipeline.
    """
    fetch = fetch or archive.download_bulk
    today = today or datetime.date.today()
    if path.exists():
        age = (today - datetime.date.fromtimestamp(path.stat().st_mtime)).days
        if age < stale_after_days:
            logger.info(f"reusing {path} ({age}d old)")
            return str(path)
        logger.info(f"{path} is {age}d old — refetching")
    path.parent.mkdir(parents=True, exist_ok=True)
    return fetch(str(path))


def to_market_rows(symbol: str, bars: pd.DataFrame, rates: pd.Series) -> pd.DataFrame:
    """Archive bars -> `market_prices` rows, in USD and in BRL.

    Both conversions are `us_prices`', on purpose and not by coincidence: the
    same turnover definition (`share_volume * close`, because `min_adtv` filters
    on money and not on share count) and the same `fx_series.to_brl` call, so a
    symbol that moves between the two sources does not change what its `volume`
    column means or how its BRL columns were made.

    `split_adj_close` and `adj_close` both carry the close because the file is
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
        "source": f"{archive.SOURCE}:{ADJUST_STATE}",
    })
    return fx_series.to_brl(out, rates)


def _instrument_row(symbol: str, venue: str, kind: str,
                    bars: pd.DataFrame) -> dict:
    """One `instruments` row from a symbol's observed history.

    Observed bounds only. See the module docstring on why no listing or
    delisting date is claimed from this file.
    """
    return {
        "market": MARKET,
        "symbol": symbol,
        "venue": venue,
        "currency": archive.CURRENCY,
        "kind": kind,
        "first_seen": bars["date"].min(),
        "last_seen": bars["date"].max(),
    }


def covered_symbols(conn) -> set:
    """Symbols whose bars came from the bulk archive.

    Read from `market_prices.source` rather than from a flag on the cursor: the
    price table is where the fact actually lives, so a cursor row that was never
    written — or was overwritten by a later run — cannot make `us_prices`
    believe a symbol is safe to re-price when it is not.

    That costs a scan of the US half of the price table, since `source` is in no
    index — tens of millions of rows once this archive has landed. Paid rather
    than optimised away: it runs once per daily ingest, inside a job that is
    already minutes long, and the alternative is a denormalised flag whose
    absence fails in the one direction that corrupts a price series. If it ever
    does hurt, the fix is an index on `(market, source, symbol)`, not a flag.
    """
    df = storage.read_sql(
        "SELECT DISTINCT symbol FROM market_prices"
        " WHERE market = ? AND source LIKE ?",
        conn, params=(MARKET, f"{archive.SOURCE}%"))
    return set(df["symbol"]) if not df.empty else set()


def demote_bulk_priced(buckets, covered) -> tuple:
    """Re-file `us_prices.select_work`'s work buckets around the bulk seam.

    This module writes a split-adjusted series and the metered feed buys a raw
    one. `market_prices` is keyed `(market, symbol, date)`, so a symbol both of
    them price ends up with two conventions in one column and a fabricated
    return at the seam — silently, because a split-sized jump is exactly what
    `MARKETS["US"].glitch_down` is built to mask. So a symbol this archive
    covers never gets its prices bought again.

    Its *actions* still do, and that is the point rather than a leftover:
    distributions are the one thing the bulk file does not carry, so the free
    tier's 20 requests a day buy the `split_only` -> `total_return` upgrade
    instead of re-buying bars the plane already has.

    Demoted work lands in bucket 4, behind every symbol that still has no bars
    at all — a symbol this archive priced *has* bars and no longer belongs in
    the queue of symbols that have none. At 20 requests a day that ordering is
    the difference between a plane that reaches the tail the archive does not
    carry and one that spends the allowance re-asking about dividends. A symbol
    left with nothing to do drops out rather than occupying a slot.

    Lives here rather than in `us_prices` because the seam is this module's to
    explain, and takes `covered` empty as "nothing to do" so the caller needs no
    branch — the paid-key path and every plane built before this archive existed
    go through unchanged.
    """
    if not covered:
        return tuple(buckets)
    kept, demoted = [], []
    for bucket in buckets:
        survivors = []
        for symbol, want_prices, want_actions in bucket:
            if not (want_prices and symbol in covered):
                survivors.append((symbol, want_prices, want_actions))
            elif want_actions:
                demoted.append((symbol, False, True))
        kept.append(survivors)
    retry, fresh, stale_bars, stale_actions = kept
    return retry, fresh, stale_bars, stale_actions + demoted


def ingest_symbol(conn, zf, member: str, symbol: str, venue: str, kind: str,
                  rates: pd.Series, today) -> dict:
    """Write one symbol's bars, master row and cursor. Returns rows written."""
    bars = archive.read_member(zf, member, symbol)
    if bars.empty:
        return {"rows": 0, "cursor": None}

    market_storage.upsert_instruments(
        conn, pd.DataFrame([_instrument_row(symbol, venue, kind, bars)]))
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


def run_pipeline(db_path=None, zip_path=None, limit: int = 0, today=None,
                 stale_after_days: int = STALE_AFTER_DAYS) -> dict:
    """Ingest the bulk archive into the plane. Returns a summary for `ingest_runs`."""
    today = today or datetime.date.today()
    logger.info(f"US bulk backfill -> {storage.describe_target(db_path)}")

    with storage.get_connection(db_path) as conn:
        storage.init_db(conn)

        logger.info("Step 1/4: PTAX USD/BRL...")
        fx = fx_series.build(today)
        if not fx.empty:
            storage.upsert_external_prices(conn, fx)
        rates = fx_series.load(conn)
        if rates.empty:
            logger.warning("no PTAX on the plane — BRL columns will be NULL")

        logger.info("Step 2/4: fetching the archive...")
        path = zip_path or ensure_bulk(bulk_path(), today=today,
                                       stale_after_days=stale_after_days)

        logger.info("Step 3/4: enumerating members...")
        members = archive.list_members(path)
        if len(members) < archive.MIN_PLAUSIBLE_SYMBOLS:
            # Refused, not written. A truncated archive would look like a US
            # market of four hundred names; see the archive module's docstring.
            raise RuntimeError(
                f"{path} holds {len(members):,} US symbols, below the floor of "
                f"{archive.MIN_PLAUSIBLE_SYMBOLS:,}. Most likely a truncated "
                "download or an error page. Nothing was written.")
        if limit:
            members = members[:limit]

        logger.info(f"Step 4/4: ingesting {len(members):,} symbols...")
        rows, symbols, failed, cursors = 0, 0, [], []
        with archive.open_archive(path) as zf:
            for i, (member, symbol, venue, kind) in enumerate(members, 1):
                try:
                    result = ingest_symbol(conn, zf, member, symbol, venue,
                                           kind, rates, today)
                except Exception as e:                      # noqa: BLE001
                    # One bad member must not cost the other eleven thousand.
                    # The names come back so a caller can look rather than
                    # re-running the whole archive to find them.
                    logger.error(f"{symbol}: {e}")
                    failed.append(symbol)
                    continue
                rows += result["rows"]
                if result["cursor"]:
                    symbols += 1
                    cursors.append(result["cursor"])
                if len(cursors) >= _LOG_EVERY:
                    # Batched, unlike `us_prices`, and the difference is the
                    # point: there each row precedes a metered request that may
                    # be the last one today, so it is written before the request
                    # goes out. Here the whole archive is already on disk, a
                    # re-run costs one file read, and eleven thousand
                    # single-row upserts would dominate the runtime.
                    us_storage.upsert_price_cursor(conn, pd.DataFrame(cursors))
                    cursors = []
                    logger.info(f"  {i:,}/{len(members):,} symbols, {rows:,} rows")
        if cursors:
            us_storage.upsert_price_cursor(conn, pd.DataFrame(cursors))

        summary = {"symbols": symbols, "rows": rows, "members": len(members),
                   "failed": failed[:50], "failed_count": len(failed),
                   "adjust_state": us_storage.adjust_state_counts(conn)}
        logger.info(f"Done: {rows:,} rows over {symbols:,} symbols, "
                    f"{len(failed)} failed")
        return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--zip-path", default=None,
                    help="use an archive already on disk instead of fetching")
    ap.add_argument("--limit", type=int, default=0,
                    help="smoke-test only: truncate the universe alphabetically")
    ap.add_argument("--stale-after-days", type=int, default=STALE_AFTER_DAYS,
                    help="re-download the archive once it is older than this")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_pipeline(args.db_path, args.zip_path, args.limit,
                 stale_after_days=args.stale_after_days)


if __name__ == "__main__":
    main()
