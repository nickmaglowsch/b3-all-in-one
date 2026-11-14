"""How a US instrument moves between listed and delisted, and who may move it.

Two writers decide whether a US symbol is alive, and they know it differently.
`symbol_directory` *watches*: a symbol in today's consolidated file is trading,
and one that drops out is delisted at the last day it was seen. `us_listing`
*is told*: the vendor's listing file says delisted on a date. Both write the
same two columns, `delisting_date` and `delist_reason`, and the reason string is
how a later run knows whose verdict a row carries.

Every transition either writer makes is one of the four functions here, so the
rules sit in one place instead of in two `_plan`s that each had to remember the
other one's:

  observe_present   the directory saw the symbol today. Its sighting widens the
                    observed bounds, and if the directory itself had recorded
                    the symbol dead, the symbol is revived — a relisting, or a
                    snapshot that missed it. A delisting `us_listing` claimed
                    is left alone: withdrawing that is `withdraw_claim`'s, on
                    `us_listing`'s own evidence.
  observe_absent    the directory did not see a live symbol it had seen before.
                    Delisted at its `last_seen`, the last day the plane can
                    prove it traded. A symbol the directory never saw has no
                    `last_seen` and nothing to infer from.
  claim_delisted    the vendor says delisted. Never over another writer's
                    verdict, never dated before trading the plane watched.
  withdraw_claim    the vendor says trading. Only its own claim is withdrawn.

Reviving used to be missing, and the cost was permanent: the directory read only
*live* symbols, so a delisted symbol that reappeared was treated as a first
sighting — `first_seen` reset to that day, `delisting_date` kept — and from the
old delisting date on `backtests.core.universe.tradable_mask` excluded a symbol
that was trading. The observed bounds are also protected at the storage layer
now (`market_storage._OBSERVED_BOUNDS`), so `first_seen` can only move back and
`last_seen` only forward whichever writer gets it wrong next.

The functions are pure — a stored row (dict) in, the columns to write out — and
`write` is the one place that turns them into upserts.
"""

import pandas as pd
from b3_pipeline import market_storage

# The reason strings are the stored values; rows written before this module
# existed carry exactly these, so ownership reads back unchanged.
DIRECTORY_REASON = "absent from the consolidated symbol directory"
LISTING_REASON = "alpha vantage listing status"

# A delisting date the vendor claims can legitimately sit a few days before the
# last day the directory saw the symbol trade — settlement date versus last
# trade. Beyond this the two are describing different companies.
SETTLEMENT_SLACK_DAYS = 5

CLEARED = {"delisting_date": None, "delist_reason": None}


def _missing(value) -> bool:
    return value is None or bool(pd.isna(value))


def is_delisted(prior) -> bool:
    return prior is not None and not _missing(prior.get("delisting_date"))


def _delisted_by(prior, reason: str) -> bool:
    return is_delisted(prior) and prior.get("delist_reason") == reason


def foreign_delisting(prior) -> bool:
    """True when a writer other than `us_listing` recorded this symbol dead."""
    return is_delisted(prior) and prior.get("delist_reason") != LISTING_REASON


def observe_present(prior, as_of) -> dict:
    """The columns a sighting in today's directory writes."""
    row = {"first_seen": as_of, "last_seen": as_of}
    if _delisted_by(prior, DIRECTORY_REASON):
        row.update(CLEARED)
    return row


def observe_absent(prior):
    """The columns a live, previously seen symbol's absence writes, or None."""
    if prior is None or is_delisted(prior) or _missing(prior.get("last_seen")):
        return None
    return {"delisting_date": prior["last_seen"],
            "delist_reason": DIRECTORY_REASON}


def _dated(claim, last_seen) -> tuple:
    """The delisting date a claim may be written with, against the plane's own
    last sighting. -> (date, verdict)."""
    if _missing(last_seen) or claim >= last_seen:
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


def claim_delisted(prior, claim) -> tuple:
    """A vendor's delisting claim -> (columns to write or None, verdict).

    Verdict is "write", "conflict" (the claim contradicts what the plane
    watched happen) or "skip".
    """
    # A delisting the directory watched happen is proof; this feed is a
    # record. Never restate one with the other.
    if _missing(claim) or foreign_delisting(prior):
        return None, "skip"
    date, verdict = _dated(claim, None if prior is None else prior.get("last_seen"))
    if verdict != "write":
        return None, verdict
    return {"delisting_date": date, "delist_reason": LISTING_REASON}, verdict


def withdraw_claim(prior):
    """The vendor reports trading a symbol it earlier claimed dead: the columns
    that withdraw that claim, or None. Leaving the stale claim would keep a
    symbol that trades out of every point-in-time universe."""
    return dict(CLEARED) if _delisted_by(prior, LISTING_REASON) else None


def write(conn, rows: list) -> int:
    """Upsert `instruments` rows, one frame per distinct set of columns.

    `upsert_instruments` updates only the columns of the frame it is handed, and
    a frame forces every column any row has onto every row. So a revived symbol
    (which clears its delisting) and a plain sighting (which must not touch it)
    cannot share a frame — mixing them would clear, or blank, the other's.
    """
    groups = {}
    for row in rows:
        groups.setdefault(tuple(sorted(row)), []).append(row)
    return sum(market_storage.upsert_instruments(conn, pd.DataFrame(group))
               for group in groups.values())
