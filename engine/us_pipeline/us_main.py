"""Ingest SEC fundamentals into the market plane.

Linear, like the other two pipelines, and it bootstraps itself.

  bulk          every filer in `companyfacts.zip`, downloaded if it is not
                already on disk. This is the mode that makes the plane
                survivorship-free: it enumerates from the archive, which still
                contains the companies that stopped filing, rather than from a
                list of who trades today.
  incremental   the CIKs already on the plane, refreshed one by one.

**Which one runs is decided by the plane, not by a flag.** An empty plane gets
the bulk pass; a populated one gets the incremental. That is the difference
between a scheduled job that works on a box nobody prepared and one that walks an
empty CIK list forever and reports success — which is what a bulk-only-by-hand
default would have done, weekly, silently.

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
import logging
import zipfile
from pathlib import Path

import pandas as pd
from b3_pipeline import config, market_storage, storage, us_storage

from . import sec_downloader as sec
from .sec_parser import MARKET, parse_companyfacts

logger = logging.getLogger(__name__)

# Lands beside the COTAHIST and CVM archives, under the volume that already
# exists to keep those from re-downloading on every container restart.
BULK_FILENAME = "sec_companyfacts.zip"


def _instrument(cik: str, ticker: str) -> dict:
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
        "symbol": ticker or f"CIK{cik}",
        "currency": "USD",
        "kind": "EQUITY",
        "cik": cik,
    }


def ingest_company(conn, cik: str, doc: dict, ticker: str = None) -> int:
    rows = parse_companyfacts(doc, ticker)
    if rows.empty:
        return 0
    market_storage.upsert_instruments(
        conn, pd.DataFrame([_instrument(cik, ticker)]))
    return us_storage.upsert_fundamentals_pit_us(conn, rows)


def _known_ciks(conn) -> list:
    df = storage.read_sql(
        "SELECT DISTINCT cik FROM instruments WHERE market = ? AND cik IS NOT NULL",
        conn, params=[MARKET])
    return df["cik"].dropna().tolist() if not df.empty else []


def _ensure_bulk(path: Path) -> str:
    """The bulk archive, downloaded if it is not already on disk.

    Re-used rather than re-fetched: it is ~1.5 GB, and the run that needs it is
    the one bootstrapping an empty plane. Delete the file to force a refresh.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and zipfile.is_zipfile(path):
        logger.info(f"reusing {path} ({path.stat().st_size:,} bytes)")
        return str(path)
    return sec.download_bulk_facts(str(path))


def run_pipeline(db_path=None, bulk_zip: str = None, limit: int = 0) -> dict:
    logger.info(f"US fundamentals -> {storage.describe_target(db_path)}")
    facts, failed, rows = 0, [], 0

    with storage.get_connection(db_path) as conn:
        storage.init_db(conn)

        if bulk_zip is None and not _known_ciks(conn):
            # Nothing on the plane yet. Walking an empty CIK list would report
            # success having done nothing, every week, forever.
            logger.info("no US filers on the plane — bootstrapping from bulk")
            bulk_zip = _ensure_bulk(config.DATA_DIR / BULK_FILENAME)

        logger.info("Step 1/2: ticker labels (current filers only)...")
        try:
            tickers = sec.fetch_company_tickers()
        except Exception as e:                          # noqa: BLE001
            # Labels, not the universe. A company with no current ticker still
            # gets its facts; it just lands under CIK-prefixed symbol.
            logger.warning(f"company_tickers unavailable, ingesting unlabelled: {e}")
            tickers = {}

        logger.info("Step 2/2: facts...")
        source = (sec.iter_bulk_facts(bulk_zip) if bulk_zip
                  else ((cik, None) for cik in _known_ciks(conn)))
        for i, (cik, doc) in enumerate(source, 1):
            if limit and i > limit:
                break
            try:
                if doc is None:
                    doc = sec.fetch_company_facts(cik)
                rows += ingest_company(conn, cik, doc, tickers.get(cik))
                facts += 1
            except Exception as e:                      # noqa: BLE001
                logger.error(f"CIK {cik}: {e}")
                failed.append(cik)
            if i % 500 == 0:
                logger.info(f"  {i:,} filers, {rows:,} rows")

    summary = {"filers": facts, "rows": rows, "failed": failed}
    logger.info(f"Done: {rows:,} rows over {facts:,} filers, {len(failed)} failed")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--bulk-zip", default=None,
                    help="use this companyfacts.zip instead of deciding from "
                         "the plane (downloads it if the path does not exist)")
    ap.add_argument("--force-bulk", action="store_true",
                    help="take the bulk pass even on a populated plane")
    ap.add_argument("--limit", type=int, default=0,
                    help="smoke-test only: stop after N filers")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    bulk = args.bulk_zip
    if args.force_bulk and not bulk:
        bulk = str(config.DATA_DIR / BULK_FILENAME)
    if bulk:
        bulk = _ensure_bulk(Path(bulk))
    run_pipeline(args.db_path, bulk, args.limit)


if __name__ == "__main__":
    main()
