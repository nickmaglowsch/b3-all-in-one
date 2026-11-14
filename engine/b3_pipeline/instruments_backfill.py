"""Fill `instruments` for B3 from what the plane already knows.

The survivorship semantics in `backtests.core.universe` read listing intervals
out of `instruments`. Crypto writes its own rows (the archive's first and last
bar), and the US pipeline will write its own (a symbol appearing in and then
disappearing from the daily directory). B3 has neither: its listing history is
spread across `prices`, `company_tickers_pit` (the FCA dataset, which covers
companies that have since delisted) and `cvm_companies`. This joins them.

Two rules, both chosen so the result cannot invent a delisting:

**Nothing is inferred from silence.** For crypto, an archive that stops *is* the
delisting — the bucket is the complete record. COTAHIST is not: a B3 ticker can
go quiet for months and come back, and a year's file can be missing from the
plane entirely. So a B3 delisting is written only when CVM or FCA says there was
one, and a ticker nobody claims to have delisted gets NULL — tradable
open-endedly, exactly as it behaves today.

**A claimed delisting is clamped to the last real price.** CVM's delisting date
is a registration event and often falls months after the last trade. Trusting it
literally would let the forward fill carry a flat price across that gap, which is
the smaller version of the bug this whole thing removes. The interval ends at the
earlier of the two.

`listing_date` is the first observed price rather than the claimed listing, for
the same reason in reverse: nothing can be traded before there is a price for it,
and a claimed date that disagrees with the data would mask real sessions.

`is_worthless` stays 0 for every B3 row. CVM records that a company deregistered,
not what its shareholders recovered — and guessing zero would fabricate a total
loss on every acquisition and every voluntary delisting. Liquidating at the last
mark is the conservative reading; a name that really did go to nothing has to be
marked by hand.
"""

import logging

import pandas as pd

from . import market_storage, storage

logger = logging.getLogger(__name__)

MARKET = "B3"

# Observed bounds per ticker, and whatever the two reference tables claim about
# the company behind it. LEFT JOINs throughout: a ticker with no CVM record is a
# ticker with no claimed delisting, which is the permissive default.
_SQL = """
    SELECT p.ticker            AS symbol,
           MIN(p.date)         AS first_seen,
           MAX(p.date)         AS last_seen,
           MIN(p.isin_code)    AS isin_code,
           MIN(m.cnpj)         AS cnpj,
           MIN(t.end_date)     AS fca_end,
           MIN(c.delisting_date) AS cvm_end
    FROM prices p
    LEFT JOIN company_isin_map m ON m.ticker = p.ticker
    LEFT JOIN company_tickers_pit t ON t.ticker = p.ticker
    LEFT JOIN cvm_companies c ON c.ticker = SUBSTR(p.ticker, 1, 4)
    GROUP BY p.ticker
"""


def build(conn) -> pd.DataFrame:
    """`instruments` rows for every B3 ticker the price plane carries."""
    df = storage.read_sql(_SQL, conn)
    if df.empty:
        return pd.DataFrame()

    for col in ("first_seen", "last_seen", "fca_end", "cvm_end"):
        df[col] = pd.to_datetime(df[col], errors="coerce")

    # The earliest date anyone claims the listing ended. NaT when nobody claims
    # one — which is most tickers, and which means "still listed".
    claimed = df[["fca_end", "cvm_end"]].min(axis=1)
    df["delisting_date"] = claimed.where(claimed.isna(), claimed.combine(
        df["last_seen"], min))

    df["market"] = MARKET
    df["venue"] = "BOVESPA"
    df["currency"] = "BRL"
    df["kind"] = "EQUITY"
    df["listing_date"] = df["first_seen"]
    df["delist_reason"] = df["delisting_date"].notna().map(
        {True: "CVM/FCA delisting, clamped to the last traded session", False: None})
    df["is_worthless"] = 0
    df["isin_code"] = df["isin_code"].replace("UNKNOWN", None)
    return df[[c for c in market_storage.INSTRUMENT_COLUMNS if c in df.columns]]


def run(db_path=None) -> int:
    """Backfill `instruments` for B3. Returns rows written.

    Idempotent: it rebuilds every row from the current plane and upserts, so a
    later COTAHIST year or a fresh FCA download widens the bounds. Only widens:
    `first_seen`/`last_seen` never move inwards (market_storage._OBSERVED_BOUNDS),
    so after a price history is shrunk or corrected, clear those two columns for
    the affected rows before re-running if the bounds must follow it.
    """
    with storage.get_connection(db_path) as conn:
        rows = build(conn)
        if rows.empty:
            logger.warning("no B3 prices on the plane — nothing to backfill")
            return 0
        n = market_storage.upsert_instruments(conn, rows)
        dead = int(rows["delisting_date"].notna().sum())
    logger.info(f"B3 security master: {n:,} tickers, {dead:,} with a delisting date")
    return n


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=None)
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(args.db_path)


if __name__ == "__main__":
    main()
