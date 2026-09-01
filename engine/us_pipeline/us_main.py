"""Ingest SEC fundamentals into the market plane.

Linear, like the other two pipelines. Two modes, both idempotent:

  --bulk PATH   every filer in a downloaded companyfacts.zip. This is the mode
                that makes the plane survivorship-free: it enumerates from the
                archive, which still contains the companies that stopped filing,
                rather than from a list of who trades today.
  (default)     the CIKs already on the plane, refreshed one by one.

Instruments are written from what the facts prove: a filer with facts is a
company that existed, and the earliest and latest period it reported bound its
life on this plane. Nothing here writes a `delisting_date` — the fundamentals
say when a company stopped *filing*, which is a different event from its shares
stopping trading, and guessing one from the other would put a wrong date on the
one column the backtest universe reads.
"""

import argparse
import logging

import pandas as pd

from b3_pipeline import market_storage, storage

from . import sec_downloader as sec
from .sec_parser import MARKET, parse_companyfacts

logger = logging.getLogger(__name__)


def _instrument(cik: str, ticker: str, rows: pd.DataFrame) -> dict:
    periods = pd.to_datetime(rows["period_end"], errors="coerce").dropna()
    return {
        "market": MARKET,
        "symbol": ticker or f"CIK{cik}",
        "venue": None,
        "currency": "USD",
        "kind": "EQUITY",
        "cik": cik,
        # Observed from the filings, and only that. `listing_date` is what the
        # backtest universe reads, and the first period a company reported is
        # not the day its shares started trading — so this fills `first_seen`
        # and leaves the listing interval to whatever ingests prices.
        "first_seen": periods.min().date() if len(periods) else None,
        "last_seen": periods.max().date() if len(periods) else None,
    }


def ingest_company(conn, cik: str, doc: dict, ticker: str = None) -> int:
    rows = parse_companyfacts(doc, ticker)
    if rows.empty:
        return 0
    market_storage.upsert_instruments(
        conn, pd.DataFrame([_instrument(cik, ticker, rows)]))
    return market_storage.upsert_fundamentals_pit_us(conn, rows)


def _known_ciks(conn) -> list:
    df = storage.read_sql(
        "SELECT DISTINCT cik FROM instruments WHERE market = ? AND cik IS NOT NULL",
        conn, params=[MARKET])
    return df["cik"].dropna().tolist() if not df.empty else []


def run_pipeline(db_path=None, bulk_zip: str = None, limit: int = 0) -> dict:
    logger.info(f"US fundamentals -> {storage.describe_target(db_path)}")
    facts, failed, rows = 0, [], 0

    with storage.get_connection(db_path) as conn:
        storage.init_db(conn)

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
                    help="path to a downloaded companyfacts.zip")
    ap.add_argument("--download-bulk", default=None,
                    help="fetch companyfacts.zip to this path first")
    ap.add_argument("--limit", type=int, default=0,
                    help="smoke-test only: stop after N filers")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    bulk = args.bulk_zip
    if args.download_bulk:
        bulk = sec.download_bulk_facts(args.download_bulk)
    run_pipeline(args.db_path, bulk, args.limit)


if __name__ == "__main__":
    main()
