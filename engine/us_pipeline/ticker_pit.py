"""Which ticker a CIK traded under, and when.

The survivorship hole that was left **in the join**, not in the data. The bulk
facts archive keeps the CIKs that stopped filing — a company that went under in
2009 is in there and `sec_parser` reads its numbers fine. What does not exist is
its ticker: `company_tickers.json` is a snapshot of who trades *today*. So
`us_main` labelled a live filer `AAPL` and a dead one `CIK0000806085`, its
prices arrived under the ticker it actually had, and the two rows never met.

The effect was the worst kind. The dead filers' fundamentals were in the
database, `/market/status` counted them as present, and no fundamental strategy
could select even one of them — a US value backtest carried survivorship bias
*despite* the plane being built not to.

## Two feeds, opposite directions

**Backward, from `submissions.zip`.** Each entity's document carries `tickers`,
`exchanges` and `formerNames`, and its filing history. The tickers array has no
dates of its own, so the interval is bounded by the filing history instead: from
the first filing to the last. That is coarse and it is honest about being
coarse — `source` records which feed made the claim. It is also enough for what
#38 asks: a company that stopped filing in 2009 stops being `CIK0000806085` and
becomes reachable under the ticker it had.

**Forward, from the daily `company_tickers.json`.** A pair seen today is
current; a pair that was current and is absent today ended today. Same
appearance/disappearance inference `symbol_directory` makes, with the same
plausibility floor, because a truncated snapshot would otherwise end every
ticker in the market at once.
"""

import logging

import pandas as pd
from b3_pipeline import storage, us_storage

logger = logging.getLogger(__name__)

SUBMISSIONS = "sec-submissions"
SNAPSHOT = "sec-company-tickers"

# The current file lists every filer that trades. It has not been near this low
# in years, and a truncated snapshot would otherwise close every interval.
MIN_PLAUSIBLE_PAIRS = 5_000


def _filing_bounds(doc: dict) -> tuple:
    """(first, last) filing date across the entity's history, as strings.

    `filings.recent` is the current page; `filings.files` lists the older ones
    with their own date ranges, which is what reaches back far enough to bound a
    company that stopped filing decades ago.
    """
    dates = []
    filings = doc.get("filings") or {}
    recent = (filings.get("recent") or {}).get("filingDate") or []
    dates.extend(d for d in recent if d)
    for page in filings.get("files") or []:
        dates.extend(d for d in (page.get("filingFrom"), page.get("filingTo")) if d)
    if not dates:
        return None, None
    return min(dates), max(dates)


def intervals_from_submission(doc: dict) -> pd.DataFrame:
    """One entity's document -> `us_ticker_pit` rows.

    An entity that is still filing gets an open interval (`end_date` NULL). One
    that stopped gets its last filing date, which is the last day the market
    could have known the ticker at all — not the day it delisted, which this
    source does not carry. `us_listing` owns that number; these two are read
    together and neither pretends to be the other.
    """
    cik = str(doc.get("cik", "")).zfill(10)
    tickers = [t for t in (doc.get("tickers") or []) if t]
    if not cik.strip("0") or not tickers:
        return pd.DataFrame()

    exchanges = doc.get("exchanges") or []
    first, last = _filing_bounds(doc)
    # `ein` is present for a live registrant and absent for many that stopped;
    # the reliable signal is whether EDGAR still lists the entity as filing.
    still_filing = bool((doc.get("filings", {}).get("recent", {}) or {}).get("form"))

    return pd.DataFrame([{
        "cik": cik,
        "ticker": ticker,
        "start_date": first,
        "end_date": None if still_filing else last,
        "exchange": exchanges[i] if i < len(exchanges) else None,
        "source": SUBMISSIONS,
    } for i, ticker in enumerate(tickers)])


def apply_snapshot(conn, mapping: dict, as_of) -> dict:
    """Fold today's `company_tickers.json` into the history.

    Refuses an implausibly small snapshot rather than ending every interval in
    the market — the same guard, and the same reason, as `symbol_directory`.
    """
    if len(mapping) < MIN_PLAUSIBLE_PAIRS:
        raise ValueError(
            f"the CIK->ticker snapshot has {len(mapping):,} pairs, below the "
            f"{MIN_PLAUSIBLE_PAIRS:,} floor — treating this as a truncated "
            "download rather than the market delisting. Nothing was written.")

    as_of = str(pd.Timestamp(as_of).date())
    current = pd.DataFrame([
        {"cik": cik, "ticker": ticker, "end_date": None, "source": SNAPSHOT}
        for cik, ticker in mapping.items() if ticker])
    us_storage.upsert_ticker_pit(conn, current)

    known = storage.read_sql(
        "SELECT cik, ticker FROM us_ticker_pit WHERE end_date IS NULL"
        " AND source = ?", conn, params=(SNAPSHOT,))
    seen = set(zip(current["cik"], current["ticker"]))
    gone = [(c, t) for c, t in zip(known["cik"], known["ticker"])
            if (c, t) not in seen]
    if gone:
        us_storage.upsert_ticker_pit(conn, pd.DataFrame([
            {"cik": c, "ticker": t, "end_date": as_of, "source": SNAPSHOT}
            for c, t in gone]))
    logger.info(f"{as_of}: {len(current):,} current pairs, {len(gone):,} ended")
    return {"current": len(current), "ended": len(gone), "as_of": as_of}


def resolver(conn, cik: str):
    """A function from a filing date to the ticker the market knew then.

    Keyed on the **filing date**, not the period end. `data._query_fundamentals_pit`
    pivots on `filing_date` and forward-fills onto the rebalance grid, so the
    label has to be the one the market saw when the filing landed — otherwise
    the fundamentals column and the price column carry different names over the
    same window and never join. A rename then produces two columns that each
    line up with their own price history, which is the only version that works.
    """
    rows = us_storage.load_ticker_pit(conn, cik)
    if rows.empty:
        return lambda _filed: None

    intervals = [
        (r["start_date"], r["end_date"], r["ticker"]) for _, r in rows.iterrows()]
    # Latest first, so an open interval wins over a closed one on any date it
    # covers and the most recent name wins a tie.
    intervals.sort(key=lambda i: (i[1] is None, i[0] or ""), reverse=True)

    def at(filed) -> str | None:
        if filed is None:
            return intervals[0][2]
        day = str(filed)[:10]
        for start, end, ticker in intervals:
            if (start is None or day >= str(start)[:10]) and \
                    (end is None or day <= str(end)[:10]):
                return ticker
        # Outside every recorded interval: a filing from before the earliest
        # bound this coarse source could give. The nearest name beats no name.
        return intervals[0][2]

    return at


def latest(conn, cik: str) -> str | None:
    """The most recent ticker for a CIK. `instruments.symbol` is one value."""
    return resolver(conn, cik)(None)


def ingest_bulk(conn, zip_path: str, limit: int = 0) -> dict:
    """Fold the whole submissions archive into the history."""
    from . import sec_downloader as sec

    entities = pairs = 0
    for i, (cik, doc) in enumerate(sec.iter_bulk_submissions(zip_path), 1):
        if limit and i > limit:
            break
        rows = intervals_from_submission(doc)
        if rows.empty:
            continue
        pairs += us_storage.upsert_ticker_pit(conn, rows)
        entities += 1
        if i % 5_000 == 0:
            logger.info(f"  {i:,} entities, {pairs:,} pairs")
    logger.info(f"submissions: {entities:,} entities, {pairs:,} CIK->ticker pairs")
    return {"entities": entities, "pairs": pairs}
