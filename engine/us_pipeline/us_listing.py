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

from . import us_lifecycle as lifecycle
from .alphavantage import Client
from .av_parsers import parse_listing_status

logger = logging.getLogger(__name__)

MARKET = "US"
SOURCE = "alphavantage-listing-status"
DELIST_REASON = lifecycle.LISTING_REASON

# The consolidated US market has not had fewer than five thousand listed symbols
# in years. Same floor and same reason as symbol_directory's.
MIN_PLAUSIBLE_ACTIVE = 5_000

# A share of the currently-live US instruments. A legitimate run adds delistings
# in single digits; anything near this is the vendor having a bad day.
MAX_NEW_DELISTINGS_PCT = 0.02


def fetch(client: Client, state: str) -> pd.DataFrame:
    """One `LISTING_STATUS` call. `state` is "active" or "delisted"."""
    return parse_listing_status(client.get_csv("LISTING_STATUS", state=state))


def _known(conn) -> pd.DataFrame:
    """What the plane already believes about US instruments."""
    return storage.read_sql(
        "SELECT symbol, first_seen, last_seen, delisting_date, delist_reason"
        " FROM instruments WHERE market = ?", conn, params=(MARKET,))


def _check(active: pd.DataFrame, delisted: pd.DataFrame, new_delistings: int,
           live: int, max_new_delistings: int = 0) -> None:
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
    cap = max_new_delistings or max(50, int(live * MAX_NEW_DELISTINGS_PCT))
    if live and new_delistings > cap:
        raise ValueError(
            f"LISTING_STATUS would newly delist {new_delistings:,} of {live:,} "
            f"live US instruments, over the {cap:,} ceiling. A real day adds "
            "single digits. Nothing was written.")


def _plan(active: pd.DataFrame, delisted: pd.DataFrame, known: dict) -> tuple:
    """The two row sets this run wants to write, plus the claims it refuses.

    Three sets and not one, because they carry different columns and
    `upsert_instruments` narrows its UPDATE to the columns of the frame it is
    handed. Mixing a seen symbol (interval only) with an unseen one (full row)
    in a single frame would blank the seen symbol's name and venue with the
    NaNs the other row forced into existence — and the same hazard is why
    clearing a delisting needs a frame of its own.

    What each claim may change is `us_lifecycle`'s to say: `claim_delisted`
    for the dead file, `withdraw_claim` for the live one.
    """
    live_today = set(active["symbol"]) if not active.empty else set()
    seen, unseen, revived, conflicts = [], [], [], []

    for row in delisted.to_dict("records"):
        # A symbol the vendor also reports as trading today is not delisted,
        # whatever the other file says.
        if row["symbol"] in live_today:
            continue
        prior = known.get(row["symbol"])
        change, verdict = lifecycle.claim_delisted(prior, row["delisting_date"])
        if verdict == "conflict":
            conflicts.append(row["symbol"])
        if change:
            (seen if prior is not None else unseen).append(
                {**_columns(row, prior is None), **change})

    for row in active.to_dict("records"):
        prior = known.get(row["symbol"])
        # A stale snapshot must not resurrect a name the directory watched die.
        # Only this module's own claims are ever self-corrected.
        if lifecycle.foreign_delisting(prior):
            continue
        withdrawn = lifecycle.withdraw_claim(prior)
        if withdrawn:
            revived.append({**_columns(row, False), **withdrawn})
            continue
        (seen if prior is not None else unseen).append(
            _columns(row, prior is None))

    return seen, unseen, revived, conflicts


def reconcile(conn, active: pd.DataFrame, delisted: pd.DataFrame,
              max_new_delistings: int = 0) -> dict:
    """Fold both listing files into `instruments`.

    `max_new_delistings` raises the refusal ceiling for one run. The ceiling is
    calibrated for a steady-state day ("a real day adds single digits"), which a
    *first* reconciliation is not: the plane starts as one directory snapshot
    with no intervals at all, so the whole standing backlog of delistings lands
    at once. Opt in explicitly rather than widening the daily guard.
    """
    known_df = _known(conn)
    for col in ("first_seen", "last_seen", "delisting_date"):
        known_df[col] = pd.to_datetime(known_df[col], errors="coerce").dt.date
    known = _by_symbol(known_df)
    live = int(known_df["delisting_date"].isna().sum()) if not known_df.empty else 0

    # Only symbols this plane currently holds as *live*. The delisted file is
    # the whole history of the US market — ~9.5k names, ~8.9k of which this
    # plane has never held — and counting those as "newly delisted" made the
    # ceiling below compare the size of that file against our live count. The
    # first real run therefore reported 9,457 of 13,181 and refused, which is
    # the module declining to do the exact job its docstring describes: a
    # symbol the directory never saw is the historical universe this exists to
    # import, not a delisting of anything we hold.
    live_now = {s for s, r in known.items() if pd.isna(r["delisting_date"])}
    fresh = ([] if delisted.empty
             else [s for s in delisted["symbol"] if s in live_now])
    _check(active, delisted, len(fresh), live, max_new_delistings)

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


def _by_symbol(frame: pd.DataFrame) -> dict:
    """`{symbol: row}` for the whole frame, one plain dict per row.

    `{r["symbol"]: r for _, r in frame.iterrows()}` is the same lookup and was
    what this used to be, but `iterrows` builds a `pd.Series` per row — its own
    Index, its own block manager, its own boxed values — and this dict keeps
    every one of them alive for the length of the run. At ~13,000 US symbols
    that measured ~19 MB retained and ~2.1 s to build, against ~5 MB and ~0.3 s
    here, in the same worker whose memory limit a backtest already crowds.
    Nothing downstream needs a Series: `_plan` and the `us_lifecycle`
    transitions only ever index a row by column name.
    """
    return {r["symbol"]: r for r in frame.to_dict("records")}


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


def run_pipeline(db_path=None, max_new_delistings: int = 0) -> dict:
    """Two requests: the living universe and the dead one."""
    logger.info(f"US listing status -> {storage.describe_target(db_path)}")
    with storage.get_connection(db_path) as conn:
        storage.init_db(conn)
        client = Client(conn)
        active = fetch(client, "active")
        delisted = fetch(client, "delisted")
        return reconcile(conn, active, delisted, max_new_delistings)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--max-new-delistings", type=int, default=0,
                    help="raise the refusal ceiling for this run; for the first "
                         "reconciliation, which imports a standing backlog "
                         "rather than one day's delistings")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_pipeline(args.db_path, args.max_new_delistings)


if __name__ == "__main__":
    main()
