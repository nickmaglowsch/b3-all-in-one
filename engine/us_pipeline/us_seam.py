"""Where the free bulk history stops and the metered feed starts.

Split out of `us_backfill` because it has two consumers and belongs to neither.
`us_backfill` writes the split-adjusted bars; `us_prices` spends the metered
budget; this module is the rule that keeps one from undoing the other, and
putting it in either one makes the other import a pipeline to ask a question
about a boundary.

The rule itself is one sentence: a symbol the free vendor priced never has its
prices bought again, and its corporate actions always can be. Everything below
is that sentence and the reasons it is not the other way round.
"""

import logging

from b3_pipeline import storage

from . import stooq_daily as vendor

logger = logging.getLogger(__name__)

MARKET = vendor.MARKET


def covered_symbols(conn) -> set:
    """Symbols whose bars came from the free vendor.

    Read from `market_prices.source` rather than from a flag on the cursor: the
    price table is where the fact actually lives, so a cursor row that was never
    written — or was overwritten by a later run — cannot make `us_prices`
    believe a symbol is safe to re-price when it is not.

    Matched on `SOURCE_PREFIX`, not on the exact tag, so rows left by the bulk
    attempt fence identically to rows written by the walk: both conventions are
    split-adjusted and neither may be spliced with the metered feed's raw one.

    That costs a scan of the US half of the price table, since `source` is in no
    index — tens of millions of rows once this history has landed. Paid rather
    than optimised away: it runs once per daily ingest, inside a job that is
    already minutes long, and the alternative is a denormalised flag whose
    absence fails in the one direction that corrupts a price series. If it ever
    does hurt, the fix is an index on `(market, source, symbol)`, not a flag.
    """
    df = storage.read_sql(
        "SELECT DISTINCT symbol FROM market_prices"
        " WHERE market = ? AND source LIKE ?",
        conn, params=(MARKET, f"{vendor.SOURCE_PREFIX}%"))
    return set(df["symbol"]) if not df.empty else set()


def demote_bulk_priced(buckets, covered) -> tuple:
    """Re-file `us_prices.select_work`'s work buckets around the seam.

    A symbol the free vendor covers never gets its prices bought again — see
    `us_backfill` on why one symbol may carry only one convention. Its
    *actions* still do, and that is the point rather than a leftover:
    distributions are the one thing this vendor does not carry, so the free
    tier's 20 requests a day buy the `split_only` -> `total_return` upgrade
    instead of re-buying bars the plane already has.

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
