"""Persisting split re-adjustments, and the record that stops them repeating.

Split out of `market_storage` rather than added to it, and not only for the line
count. That module is about writing bars; this one is about *rewriting* them,
which is a different and far more dangerous operation — the only place in the
plane that multiplies stored prices in situ. Giving it its own file makes the
one destructive verb easy to find and easy to review.

The whole design is the idempotency. A split applied once makes a history
comparable across its execution date; applied twice it quarters a history that
was already correct, and it does so silently: no exception, no constraint
violation, no downstream check that could tell the difference between a stock
that fell 75% and one that was adjusted twice. So the record of what has been
applied is not bookkeeping around the operation, it *is* the operation's safety.
"""

import logging

logger = logging.getLogger(__name__)

# Keyed on the vendor's own stable split id, so a re-run, a retry or an
# overlapping date window cannot apply the same ratio twice. `rows_adjusted` is
# kept so an operator can see what a given split actually touched.
SCHEMA_APPLIED_SPLITS = """
CREATE TABLE IF NOT EXISTS applied_splits (
    market TEXT NOT NULL,
    split_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    execution_date DATE NOT NULL,
    split_from REAL NOT NULL,
    split_to REAL NOT NULL,
    factor REAL NOT NULL,
    rows_adjusted INTEGER,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (market, split_id)
);
"""

INDEX_APPLIED_SPLITS_SYMBOL = (
    "CREATE INDEX IF NOT EXISTS idx_applied_splits_symbol"
    " ON applied_splits(market, symbol);"
)

# Appended to storage.init_db's ddl_statements, like market_storage.DDL, so this
# runs in the same pass and through the same _pg_ddl translation.
DDL = [SCHEMA_APPLIED_SPLITS, INDEX_APPLIED_SPLITS_SYMBOL]


# Price columns a split re-scales. `volume` is deliberately absent: market_prices
# stores turnover in the quote currency, and a split multiplies the share count
# by exactly what it divides the price by, so the money traded is unchanged.
# `fx_rate` is a rate, not a price. Multiplying either would be a silent
# corruption that no later run could detect.
_SPLIT_SCALED_COLUMNS = (
    "open", "high", "low", "close",
    "split_adj_close", "adj_close", "close_brl", "adj_close_brl",
)


def applied_split_ids(conn, market: str) -> set:
    """Vendor split ids this plane has already re-adjusted for.

    The guard that makes `adjust_history_for_split` safe to call from a job that
    re-runs, retries, or asks for an overlapping date window.
    """
    from . import storage

    df = storage.read_sql(
        "SELECT split_id FROM applied_splits WHERE market = ?",
        conn, params=(market,))
    return set(df["split_id"]) if not df.empty else set()


def adjust_history_for_split(conn, market: str, symbol: str,
                             execution_date, factor: float) -> int:
    """Re-scale one symbol's bars *before* `execution_date` by `factor`.

    `factor` is `split_from / split_to`: a 4-for-1 (from=1, to=4) scales the old
    history by 0.25, a 1-for-50 reverse (from=50, to=1) by 50. That is the
    convention every vendor publishes and the only one that leaves the series
    continuous across the execution date.

    Strictly `<` the execution date: the vendor already reports the execution
    day itself on the new basis, so including it would scale a bar that needs no
    scaling and manufacture the very gap this is here to close.

    NULL columns stay NULL — `NULL * x` is NULL on both backends — so a bar that
    never carried a BRL price does not acquire one here.

    Does not commit. On its own this is the unguarded half of the operation;
    `apply_split` is what runs it, inside the transaction that also records it.
    """
    from . import storage

    sets = ", ".join(f"{c} = {c} * ?" for c in _SPLIT_SCALED_COLUMNS)
    cur = storage.execute(
        conn,
        f"UPDATE market_prices SET {sets}"
        "  WHERE market = ? AND symbol = ? AND date < ?",
        tuple([factor] * len(_SPLIT_SCALED_COLUMNS))
        + (market, symbol, str(execution_date)))
    return cur.rowcount or 0


def apply_split(conn, market: str, split: dict, factor: float,
                rescale: bool = True):
    """Claim, re-scale and record one split as a single transaction.

    The record and the re-scale commit together or not at all. Committed
    separately, a process killed between them (OOM, a deploy, a timeout) left a
    history already scaled with no record of it; the next run, its window still
    reaching back over the split because no bar had been appended yet, scaled it
    again — a 4-for-1 became a 16-for-1, with nothing to say so.

    The record is written first, as the claim: `ON CONFLICT DO NOTHING` on the
    `split_id` key, and only the run whose insert landed goes on to touch the
    prices. A second process racing for the same split waits on the first one's
    uncommitted row and then inserts nothing, rather than scaling a second time
    in the gap between reading `applied_split_ids` and writing.

    `rescale=False` records the split without touching any bar — for a symbol
    the plane does not hold, which is genuinely done with zero rows.

    Returns the rows re-scaled, or None when the split was already applied.
    """
    from . import storage

    try:
        claimed = storage.execute(
            conn,
            "INSERT INTO applied_splits"
            " (market, split_id, symbol, execution_date, split_from, split_to,"
            "  factor, rows_adjusted)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, NULL)"
            " ON CONFLICT (market, split_id) DO NOTHING",
            (market, split["id"], split["ticker"], str(split["execution_date"]),
             float(split["split_from"]), float(split["split_to"]),
             float(factor))).rowcount
        if not claimed:
            conn.commit()
            return None
        n = 0
        if rescale:
            n = adjust_history_for_split(
                conn, market, str(split["ticker"]).upper().strip(),
                split["execution_date"], factor)
        storage.execute(
            conn,
            "UPDATE applied_splits SET rows_adjusted = ?"
            " WHERE market = ? AND split_id = ?",
            (int(n), market, split["id"]))
        conn.commit()
        return n
    except Exception:
        conn.rollback()
        raise
