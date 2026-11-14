"""Where the free bulk history stops and the metered feed starts.

Its own module because it has several consumers and belongs to none of them.
The bulk dump and `us_daily` write the split-adjusted bars; `us_prices` spends
the metered budget; this module is the rule that keeps one from undoing the
other, and putting it in any of them makes the rest import a pipeline to ask a
question about a boundary.

The rule itself is one sentence: a symbol the free vendor priced never has its
prices bought again, and its corporate actions always can be. Everything below
is that sentence and the reasons it is not the other way round.
"""

import logging

from b3_pipeline import storage

from . import massive_daily

logger = logging.getLogger(__name__)

MARKET = massive_daily.MARKET

# Stooq's tag, as a literal, now that the per-symbol walk that used to define it
# is deleted. It is not decoration: 13,347 symbols and 28,057,516 rows on the
# plane are tagged `stooq-bulk:split_only`, written by the bulk `d_us_txt` dump,
# and this prefix is the whole of what fences them off from `us_prices`' raw
# metered close. The module that owned the constant went away because its vendor
# stopped answering; the rows it names did not go anywhere.
STOOQ_PREFIX = "stooq"

# Every source tag on the plane that publishes a back-adjusted close. The
# metered feed publishes a raw one, so these are the symbols it must not touch;
# `us_daily` reads the same set from the other side, to know which histories a
# split-adjusted bar may legally be appended to.
SPLIT_ADJUSTED_PREFIXES = (STOOQ_PREFIX, massive_daily.SOURCE_PREFIX)


def covered_symbols(conn) -> set:
    """Symbols whose bars came from the free vendor.

    Read from `market_prices.source` rather than from a flag on the cursor: the
    price table is where the fact actually lives, so a cursor row that was never
    written — or was overwritten by a later run — cannot make `us_prices`
    believe a symbol is safe to re-price when it is not.

    Matched on prefixes, not on exact tags, so every split-adjusted writer
    fences identically: the bulk file and the daily grouped refresh both publish
    a back-adjusted close, and neither may be spliced with the metered feed's
    raw one. A new split-adjusted source belongs in `SPLIT_ADJUSTED_PREFIXES` on
    the day it is added, not after the seam it opens is noticed in a backtest.

    That costs a scan of the US half of the price table, since `source` is in no
    index — tens of millions of rows once this history has landed. Paid rather
    than optimised away: it runs once per daily ingest, inside a job that is
    already minutes long, and the alternative is a denormalised flag whose
    absence fails in the one direction that corrupts a price series. If it ever
    does hurt, the fix is an index on `(market, source, symbol)`, not a flag.
    """
    clauses = " OR ".join(["source LIKE ?"] * len(SPLIT_ADJUSTED_PREFIXES))
    df = storage.read_sql(
        "SELECT DISTINCT symbol FROM market_prices"
        f" WHERE market = ? AND ({clauses})",
        conn, params=(MARKET, *(f"{p}%" for p in SPLIT_ADJUSTED_PREFIXES)))
    return set(df["symbol"]) if not df.empty else set()


def demote_bulk_priced(buckets, covered) -> tuple:
    """Re-file `us_prices.select_work`'s work buckets around the seam.

    A symbol the free vendor covers never gets its prices bought again: the bulk
    dump back-adjusts to the day it was cut and the metered feed publishes a raw
    close, so one symbol may carry only one convention. Its *actions* still get
    bought, and that is the point rather than a leftover: distributions are the
    one thing the free source does not carry, so the free tier's 20 requests a
    day buy the `split_only` -> `total_return` upgrade instead of re-buying bars
    the plane already has.

    **That upgrade is bought but not yet applied.** The actions-only refresh
    stores the events in `us_corporate_actions` and nothing more: `us_prices`
    reads them back only when it rebuilds bars it bought itself, and for these
    symbols it never does — that is this function's whole point — while
    `us_daily` only appends split-adjusted sessions. Folding the dividends into
    these bars needs its own pass, and not a trivial one: `us_adjust` measures a
    distribution against the *raw* previous close, and these bars are already
    split-adjusted, so reusing it as-is would mis-size every dividend paid before
    a split. Until that pass exists the stored events are a record, these
    symbols stay `split_only`, and `/market/status` says so.

    Demoted work lands in bucket 4, behind every symbol that still has no bars
    at all — a symbol the free vendor priced *has* bars and no longer belongs
    in the queue of symbols that have none. At 20 requests a day that ordering is the
    difference between a plane that reaches the tail this vendor does not carry
    and one that spends the allowance re-asking about dividends. A symbol left
    with nothing to do drops out rather than occupying a slot.

    Takes `covered` empty as "nothing to do" so the caller needs no
    branch — the paid-key path and every plane built before this source existed
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
