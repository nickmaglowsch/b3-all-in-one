"""Ingest SEC fundamentals into the market plane.

Linear, like the other two pipelines, and it bootstraps itself.

  bulk          every filer in `companyfacts.zip`, downloaded if it is not
                already on disk. This is the mode that makes the plane
                survivorship-free: it enumerates from the archive, which still
                contains the companies that stopped filing, rather than from a
                list of who trades today.
  incremental   the CIKs already on the plane, refreshed one by one.

**Which one runs is decided by the plane, not by a flag.** A plane with no
completed bulk pass gets one; a plane with one gets the incremental. That is the
difference between a scheduled job that works on a box nobody prepared and one
that walks an empty CIK list forever and reports success — which is what a
bulk-only-by-hand default would have done, weekly, silently.

"Completed" is a row in `us_ingest_milestones`, written after the archive's last
filer, and not an inference from the plane's contents. The inference this
replaces ("any US CIK on the plane?") was satisfied by the first filer a bulk
pass wrote, so a pass killed halfway, or a `--limit 10` smoke test, switched
every later run to incremental over whatever subset had landed — and the
filers it never reached stayed missing for good. The submissions history had
the same inference and the same failure.

The archive lands in the same data directory the COTAHIST and CVM downloads use,
so the volume that already exists to stop those re-downloading covers this too.

Instruments are written from what the facts prove: a filer with facts is a
company that existed, and the earliest and latest period it reported bound its
life on this plane. Nothing here writes a `delisting_date` — the fundamentals
say when a company stopped *filing*, which is a different event from its shares
stopping trading, and guessing one from the other would put a wrong date on the
one column the backtest universe reads.
"""

import argparse
import datetime
import logging
import zipfile
from pathlib import Path

import pandas as pd
from b3_pipeline import config, market_storage, storage, us_storage

from . import sec_downloader as sec
from . import ticker_pit
from .sec_parser import MARKET, parse_companyfacts

logger = logging.getLogger(__name__)

# Lands beside the COTAHIST and CVM archives, under the volume that already
# exists to keep those from re-downloading on every container restart.
BULK_FILENAME = "sec_companyfacts.zip"
SUBMISSIONS_FILENAME = "sec_submissions.zip"

# `us_ingest_milestones.name` for each bulk pass.
FACTS_BULK = "sec_companyfacts_bulk"
SUBMISSIONS_BULK = "sec_submissions_bulk"


def _bulk_done(conn, name: str) -> bool:
    """Whether this bulk pass has ever run to the end on this plane."""
    return not storage.read_sql(
        "SELECT name FROM us_ingest_milestones WHERE name = ?",
        conn, params=(name,)).empty


def _mark_bulk_done(conn, name: str, detail: str) -> None:
    """Record a bulk pass that reached the end of its archive. Only ever called
    after the last record, so a run killed before then leaves no row."""
    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
    storage.execute(
        conn,
        "INSERT INTO us_ingest_milestones (name, reached_at, detail)"
        " VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE"
        " SET reached_at = excluded.reached_at, detail = excluded.detail",
        (name, now, detail))
    conn.commit()


def _instrument(cik: str, symbol: str) -> dict:
    """The security-master row this ingest is entitled to claim, and no more.

    Deliberately narrow. `upsert_instruments` now writes only the columns it is
    given, so every key here is a claim over whatever another writer knew, and
    this job only knows two things: that a CIK exists and what it is called.

    In particular it no longer writes `first_seen`/`last_seen`. Those are
    *observed in the market* — the consolidated symbol directory and the price
    archive own them, and `/market/status` reports them as the plane's coverage
    boundary. The first period a company reported is not the day its shares
    started trading, so filling them from filing periods made that boundary mean
    one thing on Sunday and another on Monday. The filing range is not lost: it
    is `min/max(period_end)` in `fundamentals_pit_us`, keyed by the same CIK.
    """
    return {
        "market": MARKET,
        "symbol": symbol or f"CIK{cik}",
        "currency": "USD",
        "kind": "EQUITY",
        "cik": cik,
    }


def ingest_company(conn, cik: str, doc: dict, ticker=None) -> int:
    """One filer's facts onto the plane, labelled with the ticker of the day.

    `ticker` may be a string (the current label) or a resolver from a filing
    date to the ticker valid then. The resolver is what reaches the companies
    that stopped filing: `company_tickers.json` has no entry for them at all, so
    without it their facts land under `CIK##########` — present in the database,
    counted by `/market/status`, and selectable by no strategy. That is
    survivorship bias surviving in the *join* rather than in the data.
    """
    rows = parse_companyfacts(doc, ticker)
    if rows.empty:
        return 0
    symbol = ticker if isinstance(ticker, str) else _latest_label(rows)
    market_storage.upsert_instruments(
        conn, pd.DataFrame([_instrument(cik, symbol)]))
    return us_storage.upsert_fundamentals_pit_us(conn, rows)


def _latest_label(rows: pd.DataFrame) -> str | None:
    """The ticker on the most recent filing. `instruments.symbol` is one value —
    half a primary key — so a renamed company gets its latest name there while
    its facts keep the label each filing actually carried."""
    labelled = rows.dropna(subset=["ticker"]).sort_values("filing_date")
    return labelled["ticker"].iloc[-1] if not labelled.empty else None


def _known_ciks(conn) -> list:
    df = storage.read_sql(
        "SELECT DISTINCT cik FROM instruments WHERE market = ? AND cik IS NOT NULL",
        conn, params=[MARKET])
    return df["cik"].dropna().tolist() if not df.empty else []


def _ensure_bulk(path: Path, fetch) -> str:
    """A bulk archive, downloaded if it is not already on disk.

    Re-used rather than re-fetched: these are gigabytes each, and the run that
    needs them is the one bootstrapping an empty plane. Delete the file to force
    a refresh.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and zipfile.is_zipfile(path):
        logger.info(f"reusing {path} ({path.stat().st_size:,} bytes)")
        return str(path)
    return fetch(str(path))


def _refresh_ticker_history(conn, force: bool) -> dict:
    """Keep the CIK->ticker history current, backward and forward.

    The backward pass runs until it has completed once: `submissions.zip` is
    the only free source that looks back, and it is the difference between a
    company that stopped filing in 2009 being `LEH` and being `CIK0000806085`.
    "Has history" is not the test, because the forward pass below writes some
    on every run — a backward pass that failed once would never be retried.
    The forward pass runs every time and costs one small request.
    """
    out = {}
    if force or not _bulk_done(conn, SUBMISSIONS_BULK):
        logger.info("no completed submissions pass on the plane — reading it")
        try:
            out["submissions"] = ticker_pit.ingest_bulk(
                conn, _ensure_bulk(config.DATA_DIR / SUBMISSIONS_FILENAME,
                                   sec.download_bulk_submissions))
            _mark_bulk_done(conn, SUBMISSIONS_BULK, str(out["submissions"]))
        except Exception as e:                          # noqa: BLE001
            # Coarse history is better than none, but no history is not a
            # reason to skip the facts: the CIK placeholder still works.
            logger.error(f"submissions archive unavailable: {e}")
    return out


def _ingest_facts(conn, source, label, limit: int) -> dict:
    """Walk `source`, one filer at a time. `complete` says it reached the end."""
    facts, failed, rows = 0, [], 0
    for i, (cik, doc) in enumerate(source, 1):
        if limit and i > limit:
            return {"filers": facts, "rows": rows, "failed": failed,
                    "complete": False}
        try:
            if doc is None:
                doc = sec.fetch_company_facts(cik)
            rows += ingest_company(conn, cik, doc, label(cik))
            facts += 1
        except Exception as e:                      # noqa: BLE001
            logger.error(f"CIK {cik}: {e}")
            failed.append(cik)
        if i % 500 == 0:
            logger.info(f"  {i:,} filers, {rows:,} rows")
    return {"filers": facts, "rows": rows, "failed": failed, "complete": True}


def run_pipeline(db_path=None, bulk_zip: str = None, limit: int = 0,
                 force_submissions: bool = False) -> dict:
    logger.info(f"US fundamentals -> {storage.describe_target(db_path)}")

    with storage.get_connection(db_path) as conn:
        storage.init_db(conn)

        if bulk_zip is None and not _bulk_done(conn, FACTS_BULK):
            # No bulk pass has finished here. Walking the CIKs already on the
            # plane would report success over whatever subset a killed or
            # smoke-test run left — or over nothing, every week, forever.
            logger.info("no completed bulk pass on the plane — bootstrapping")
            bulk_zip = _ensure_bulk(config.DATA_DIR / BULK_FILENAME,
                                    sec.download_bulk_facts)

        logger.info("Step 1/2: ticker history...")
        history = _refresh_ticker_history(conn, force_submissions)
        try:
            tickers = sec.fetch_company_tickers()
            ticker_pit.apply_snapshot(conn, tickers, datetime.date.today())
        except Exception as e:                          # noqa: BLE001
            # Labels, not the universe. A company with no recorded ticker still
            # gets its facts; it just lands under a CIK-prefixed symbol.
            logger.warning(f"company_tickers unavailable, ingesting unlabelled: {e}")
            tickers = {}

        def label(cik):
            """The label for one filer: its recorded history if there is any,
            otherwise today's snapshot. A filer with neither keeps the
            CIK-prefixed placeholder, and `scripts/us_coverage.py` counts how
            many those are — which is the size of what is left of #38."""
            resolve = ticker_pit.resolver(conn, cik)
            return resolve if resolve(None) else tickers.get(cik)

        logger.info("Step 2/2: facts...")
        source = (sec.iter_bulk_facts(bulk_zip) if bulk_zip
                  else ((cik, None) for cik in _known_ciks(conn)))
        out = _ingest_facts(conn, source, label, limit)
        if bulk_zip and out["complete"]:
            # Only here: after the archive's last filer, never on a `--limit`.
            _mark_bulk_done(conn, FACTS_BULK, f"{out['filers']:,} filers, "
                                              f"{len(out['failed'])} failed")

    summary = {**out, **history}
    logger.info(f"Done: {out['rows']:,} rows over {out['filers']:,} filers, "
                f"{len(out['failed'])} failed")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--bulk-zip", default=None,
                    help="use this companyfacts.zip instead of deciding from "
                         "the plane (downloads it if the path does not exist)")
    ap.add_argument("--force-bulk", action="store_true",
                    help="take the bulk pass even when one has completed")
    ap.add_argument("--force-submissions", action="store_true",
                    help="re-read submissions.zip for the CIK->ticker history "
                         "even when the plane already has some")
    ap.add_argument("--limit", type=int, default=0,
                    help="smoke-test only: stop after N filers")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    bulk = args.bulk_zip
    if args.force_bulk and not bulk:
        bulk = str(config.DATA_DIR / BULK_FILENAME)
    if bulk:
        bulk = _ensure_bulk(Path(bulk), sec.download_bulk_facts)
    run_pipeline(args.db_path, bulk, args.limit, args.force_submissions)


if __name__ == "__main__":
    main()
