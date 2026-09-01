"""The listing history the daily capture cannot reach backwards for.

`symbol_directory` builds the US security master **forward**: a symbol's
appearance and disappearance across daily snapshots is its listing interval, and
the record starts the day the capture starts. That is what makes the plane
survivorship-free from here on, and it is free. What it cannot do is remember
1998 — a company that delisted in 2011 has no interval at all, and
`backtests.core.universe` treats an instrument with no interval as tradable on
every date. A long US backtest therefore carried exactly the survivorship bias
the plane exists to remove.

The correction is that the historical universe *is* free after all.
`LISTING_STATUS` returns every US stock and ETF, active **or delisted**, with an
IPO date and a delisting date, in two requests. Nobody had asked it.

## Who owns which column

This module and `symbol_directory` write the same table and must not fight over
it. The split is the one `SCHEMA_INSTRUMENTS` already documents — observed
bounds versus claimed intervals:

  `symbol_directory` owns  first_seen, last_seen, name, venue, kind, is_test_issue
  `us_listing` owns        listing_date, delisting_date, delist_reason

so a run here writes only its own three columns for any symbol the directory has
seen. `upsert_instruments` updates only the columns it is handed, which is what
makes that possible at all. A symbol the directory has *never* seen — delisted
before capture began, which is the entire point of this module — has no other
writer and gets the full row.

## Why this cannot delist the market

`symbol_directory` needs a plausibility floor because it infers delisting from
*absence*, and a truncated download reads as "everything delisted at once". This
module never infers from absence: there is no code path from "symbol missing
from the file" to a write, only from an explicit `status=delisted` row. That is
the strongest guarantee available, and the three refusals below are belt on top
of it — a truncated file, a JSON refusal that somehow slipped the client's
guard, or a vendor bug should stop the run before anything is written.
"""

import logging

import pandas as pd
from b3_pipeline import market_storage, storage

from .alphavantage import Client
from .av_parsers import parse_listing_status

logger = logging.getLogger(__name__)

MARKET = "US"
SOURCE = "alphavantage-listing-status"
DELIST_REASON = "alpha vantage listing status"

# The consolidated US market has not had fewer than five thousand listed symbols
# in years. Same floor and same reason as symbol_directory's.
MIN_PLAUSIBLE_ACTIVE = 5_000

# A share of the currently-live US instruments. A legitimate run adds delistings
# in single digits; anything near this is the vendor having a bad day.
MAX_NEW_DELISTINGS_PCT = 0.02

# A delisting date the vendor claims can legitimately sit a few days before the
# last day the directory saw the symbol trade — settlement date versus last
# trade. Beyond this the two are describing different companies.
SETTLEMENT_SLACK_DAYS = 5


def fetch(client: Client, state: str) -> pd.DataFrame:
    """One `LISTING_STATUS` call. `state` is "active" or "delisted"."""
    return parse_listing_status(client.get_csv("LISTING_STATUS", state=state))


def _known(conn) -> pd.DataFrame:
    """What the plane already believes about US instruments."""
    return storage.read_sql(
        "SELECT symbol, first_seen, last_seen, delisting_date, delist_reason"
        " FROM instruments WHERE market = ?", conn, params=(MARKET,))


def _check(active: pd.DataFrame, delisted: pd.DataFrame, new_delistings: int,
           live: int) -> None:
    """Refuse a snapshot that does not look like the US market. Nothing written."""
    if len(active) < MIN_PLAUSIBLE_ACTIVE:
        raise ValueError(
            f"LISTING_STATUS returned {len(active):,} active symbols, below the "
            f"{MIN_PLAUSIBLE_ACTIVE:,} floor — treating this as a truncated "
            "response rather than a market-wide delisting. Nothing was written.")
    if len(active) and delisted.empty:
        raise ValueError(
            "LISTING_STATUS returned active symbols but no delisted ones. The "
            "delisted set is tens of thousands of rows and never empty, so this "
            "is a truncation signature. Nothing was written.")
    cap = max(50, int(live * MAX_NEW_DELISTINGS_PCT))
    if live and new_delistings > cap:
        raise ValueError(
            f"LISTING_STATUS would newly delist {new_delistings:,} of {live:,} "
            f"live US instruments, over the {cap:,} ceiling. A real day adds "
            "single digits. Nothing was written.")


def _resolve(row, known: dict) -> tuple:
    """One delisted claim -> (delisting_date, verdict).

    Verdict is "write", "conflict" (the claim contradicts what the plane
    watched happen) or "skip".
    """
    claim = row["delisting_date"]
    if pd.isna(claim):
        return None, "skip"
    seen = known.get(row["symbol"])
    if seen is None:
        return claim, "write"
    last_seen = seen["last_seen"]
    if pd.isna(last_seen):
        return claim, "write"
    if claim >= last_seen:
        return claim, "write"
    if (last_seen - claim).days <= SETTLEMENT_SLACK_DAYS:
        # Settlement versus last trade. The observed day is the one the plane
        # can prove, so it wins.
        return last_seen, "write"
    # The claim predates trading the plane actually recorded. That is not a
    # date disagreement, it is two companies under one ticker — a recycled
    # symbol — and merging them would put one company's fundamentals on the
    # other's prices. Doing nothing is the correct outcome.
    return None, "conflict"


def _plan(active: pd.DataFrame, delisted: pd.DataFrame, known: dict) -> tuple:
    """The two row sets this run wants to write, plus the claims it refuses.

    Three sets and not one, because they carry different columns and
    `upsert_instruments` narrows its UPDATE to the columns of the frame it is
    handed. Mixing a seen symbol (interval only) with an unseen one (full row)
    in a single frame would blank the seen symbol's name and venue with the
    NaNs the other row forced into existence — and the same hazard is why
    clearing a delisting needs a frame of its own.
    """
    live_today = set(active["symbol"]) if not active.empty else set()
    seen, unseen, revived, conflicts = [], [], [], []

    for _, row in delisted.iterrows():
        # A symbol the vendor also reports as trading today is not delisted,
        # whatever the other file says.
        if row["symbol"] in live_today:
            continue
        prior = known.get(row["symbol"])
        # A delisting the directory watched happen is proof; this feed is a
        # record. Never restate one with the other.
        if _foreign_delisting(prior):
            continue
        date, verdict = _resolve(row, known)
        if verdict == "conflict":
            conflicts.append(row["symbol"])
            continue
        if verdict == "skip":
            continue
        extra = {"delisting_date": date, "delist_reason": DELIST_REASON}
        (seen if prior is not None else unseen).append(
            {**_columns(row, prior is None), **extra})

    for _, row in active.iterrows():
        prior = known.get(row["symbol"])
        # A stale snapshot must not resurrect a name the directory watched die.
        # Only this module's own claims are ever self-corrected.
        if _foreign_delisting(prior):
            continue
        if prior is not None and not pd.isna(prior["delisting_date"]):
            # This feed said delisted before and says trading now. Leaving the
            # stale claim would keep a symbol that trades out of every
            # point-in-time universe, which is the same bug in the other
            # direction, so the earlier claim is withdrawn.
            revived.append({**_columns(row, False),
                            "delisting_date": None, "delist_reason": None})
            continue
        (seen if prior is not None else unseen).append(
            _columns(row, prior is None))

    return seen, unseen, revived, conflicts


def _foreign_delisting(prior) -> bool:
    """True when another writer has already recorded this symbol as delisted."""
    return (prior is not None
            and not pd.isna(prior["delisting_date"])
            and prior["delist_reason"] != DELIST_REASON)


def reconcile(conn, active: pd.DataFrame, delisted: pd.DataFrame) -> dict:
    """Fold both listing files into `instruments`."""
    known_df = _known(conn)
    for col in ("first_seen", "last_seen", "delisting_date"):
        known_df[col] = pd.to_datetime(known_df[col], errors="coerce").dt.date
    known = {r["symbol"]: r for _, r in known_df.iterrows()}
    live = int(known_df["delisting_date"].isna().sum()) if not known_df.empty else 0

    already_dead = {s for s, r in known.items() if not pd.isna(r["delisting_date"])}
    fresh = ([] if delisted.empty
             else [s for s in delisted["symbol"] if s not in already_dead])
    _check(active, delisted, len(fresh), live)

    seen, unseen, revived, conflicts = _plan(active, delisted, known)
    written = 0
    for rows in (seen, unseen, revived):
        if rows:
            written += market_storage.upsert_instruments(conn, pd.DataFrame(rows))

    summary = {
        "active": len(active), "delisted": len(delisted),
        "written": int(written), "new_delistings": len(fresh),
        "revived": len(revived),
        "conflicts": conflicts[:50], "conflict_count": len(conflicts),
    }
    if conflicts:
        logger.warning(
            f"{len(conflicts)} delisting claims predate trading this plane "
            f"recorded and were not written (recycled tickers): "
            f"{', '.join(conflicts[:10])}")
    logger.info(
        f"listing status: {len(active):,} active, {len(delisted):,} delisted, "
        f"{written:,} instrument rows written, {len(fresh):,} newly delisted")
    return summary


def _columns(row, unseen: bool) -> dict:
    """The columns this module is entitled to write for one symbol.

    A symbol the directory has seen gets the interval and nothing else. A symbol
    it has never seen has no other writer — it delisted before capture began —
    so it gets the descriptive columns too, or it would sit in the master as a
    nameless ticker forever.
    """
    base = {"market": MARKET, "symbol": row["symbol"], "currency": "USD"}
    if not pd.isna(row["ipo_date"]):
        base["listing_date"] = row["ipo_date"]
    if unseen:
        base.update({"name": row["name"] or None, "venue": row["venue"] or None,
                     "kind": row["kind"]})
    return base


def run_pipeline(db_path=None) -> dict:
    """Two requests: the living universe and the dead one."""
    logger.info(f"US listing status -> {storage.describe_target(db_path)}")
    with storage.get_connection(db_path) as conn:
        storage.init_db(conn)
        client = Client(conn)
        active = fetch(client, "active")
        delisted = fetch(client, "delisted")
        return reconcile(conn, active, delisted)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=None)
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_pipeline(args.db_path)


if __name__ == "__main__":
    main()
