#!/usr/bin/env python3
"""
B3 Historical Market Data Pipeline

Downloads, parses, and adjusts historical equity data from B3 (Brazilian Stock Exchange).

B3 is the single source of truth for:
- Price data (COTAHIST)
- Cash dividends and JCP
- Stock splits, reverse splits, and bonus shares

Usage:
    python main.py              # Run pipeline (downloads missing data)
    python main.py --rebuild    # Rebuild entire database from scratch
    python main.py --year 2024  # Process specific year only
"""

import argparse
import logging
import sys
from datetime import datetime
from typing import Optional

import pandas as pd

from . import (adjustments, b3_corporate_actions, config, downloader,
               external_prices, level_series, parser, storage)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def run_pipeline(
    rebuild: bool = False,
    year: Optional[int] = None,
    start_year: Optional[int] = None,
    skip_corporate_actions: bool = False,
    detect_nonstandard_splits: bool = False,
    force_download: bool = False,
) -> None:
    """
    Execute the complete B3 data pipeline.

    Steps:
    1. Initialize database (drop + recreate if rebuild=True)
    2. Detect and download COTAHIST files
    3. Parse COTAHIST files into standardized DataFrame
    4. Upsert raw prices to SQLite
    5. Fetch corporate actions from B3 (dividends, JCP, splits)
    6. Compute split adjustment factors
    7. Compute dividend adjustment factors
    8. Update adjusted columns in database
    9. Log summary statistics

    Args:
        rebuild: If True, drop and recreate all tables
        year: If specified, only process this year
        skip_corporate_actions: If True, skip fetching corporate actions
    """
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("B3 Historical Market Data Pipeline")
    logger.info("=" * 60)
    logger.info(f"Started at: {start_time}")
    logger.info(f"Rebuild mode: {rebuild}")
    logger.info(f"Target year: {year or 'all'}")

    conn = storage.get_connection()

    try:
        logger.info("")
        logger.info("Step 1/9: Initializing database...")
        storage.init_db(conn, rebuild=rebuild)

        logger.info("")
        logger.info("Step 2/9: Detecting available years...")
        available_years = downloader.detect_available_years()
        if not available_years:
            logger.error("No available years detected from B3. Check network connectivity.")
            return
        logger.info(
            f"Found {len(available_years)} years with data: {available_years[0]} to {available_years[-1]}"
        )

        if year:
            if year not in available_years:
                logger.error(f"Year {year} not available on B3")
                return
            years_to_process = [year]
        elif start_year:
            years_to_process = [y for y in available_years if y >= start_year]
            if not years_to_process:
                logger.error(f"No available years >= {start_year}")
                return
            logger.info(f"Processing {len(years_to_process)} years from {start_year}")
        else:
            years_to_process = available_years

        logger.info("")
        logger.info("Step 3/9: Downloading COTAHIST files...")
        downloaded_files = []
        for y in years_to_process:
            # force_download matters for the CURRENT year only, and it is what makes
            # a repeat run mean anything: B3 rewrites COTAHIST_A<current>.ZIP every
            # trading day, but download_annual_file returns the local copy when one
            # exists. Without this a scheduled run re-parses yesterday's file
            # forever — green logs, no new rows. Closed years never change, so
            # forcing those would only re-download gigabytes.
            filepath = downloader.download_annual_file(
                y, force=force_download and y == config.get_current_year()
            )
            if filepath:
                downloaded_files.append(filepath)

        logger.info("")
        logger.info("Step 4/9: Parsing COTAHIST files...")
        prices = parser.parse_all_cotahist_files(config.DATA_DIR, years=years_to_process)

        if prices.empty:
            logger.error("No price data parsed. Exiting.")
            return

        logger.info("")
        logger.info("Step 5/9: Upserting raw prices to database...")
        storage.upsert_prices(conn, prices)
        del prices  # the parsed table would otherwise live through steps 7-9

        # Instruments B3 keeps out of COTAHIST (listed fixed-income ETFs), from
        # B3's own daily file. Runs here — after prices, before corporate actions —
        # so the roots step below still derives its company codes from `prices`
        # alone and never asks B3 about a ticker with no listed company behind it.
        #
        # Resumes from the last stored date because B3's window is ~14 months and
        # rolling: what is missed rolls off permanently, so this is a daily
        # obligation, not a backfill that can be repeated later.
        logger.info("")
        logger.info("Step 5b/9: Fetching external prices (fixed-income ETFs)...")
        try:
            today = datetime.now().date()
            source = external_prices.SOURCE
            # Fetch the days we are missing, not everything after the newest one:
            # resuming from the max would step permanently over any day a network
            # blip skipped. The trailing window is bounded so old market holidays
            # — which have no rows and never will — are not re-probed forever,
            # and the failure ledger reaches past it for the days that genuinely
            # broke. On the very first run there is nothing to heal and the whole
            # history is owed, so the window opens to the configured start.
            first_run = storage.max_external_price_date(conn, source) is None
            heal_days = ((today - config.EXTERNAL_PRICES_START).days if first_run
                         else config.EXTERNAL_PRICES_HEAL_DAYS)
            stored = storage.stored_external_dates(
                conn, config.EXTERNAL_PRICES_START, source)
            pending = storage.pending_external_failures(conn, source)

            fetch, abandoned = external_prices.days_to_fetch(
                today, stored=stored, pending=pending,
                start=config.EXTERNAL_PRICES_START, heal_days=heal_days,
                retention_days=config.EXTERNAL_PRICES_RETENTION_DAYS)

            # Arrived by some other path (a manual run, a wider heal window on an
            # earlier pass) while still listed as owed.
            for day in sorted(pending & stored):
                storage.clear_external_failure(conn, source, day)

            if abandoned:
                # The one genuinely unrecoverable outcome, so it is stated rather
                # than dropped: B3 no longer serves these dates and no future run
                # can repair them. Cleared so they stop costing round trips.
                logger.error(
                    f"Giving up on {len(abandoned)} external price day(s) now "
                    f"past B3's ~{config.EXTERNAL_PRICES_RETENTION_DAYS}-day "
                    f"window: {abandoned[0]} to {abandoned[-1]}. These marks are "
                    f"permanently unavailable; holdings on those dates stay "
                    f"unpriced and are disclosed as such."
                )
                for day in abandoned:
                    storage.clear_external_failure(conn, source, day)

            retries = sorted(set(fetch) & pending)
            logger.info(
                f"Fetching {len(fetch)} external price day(s) up to {today} "
                f"({len(stored)} already stored, {len(retries)} retried)")

            # Record what broke, store the rows, then forget what was paid — in
            # that order, or a run killed mid-write loses a failure that only
            # the ledger remembers (see store_result).
            result = external_prices.backfill(fetch)
            external_prices.store_result(conn, source, result, pending)

            if result.failed:
                logger.warning(
                    f"{len(result.failed)} external price day(s) failed and are "
                    f"queued for retry on the next run")
        except Exception as e:
            # A third-party feed must never take the COTAHIST pipeline down with
            # it; the index degrades to "unpriced and disclosed", which is where
            # it was before this step existed.
            logger.error(f"External price fetch failed, continuing: {e}")

        level_series.fetch(conn)

        if not skip_corporate_actions:
            logger.info("")
            logger.info("Step 6/9: Fetching corporate actions from B3...")
            tickers = storage.get_all_tickers(conn)
            logger.info(f"Found {len(tickers)} unique tickers")

            if tickers:
                ticker_roots = sorted(set(t[:4] for t in tickers if len(t) >= 4))
                logger.info(f"Fetching data for {len(ticker_roots)} company codes")

                ticker_to_isin = storage.get_ticker_isin_map(conn)
                corporate_actions, stock_actions, skipped_events = (
                    b3_corporate_actions.fetch_all_corporate_actions(
                        ticker_roots, ticker_to_isin, conn=conn
                    )
                )

                if not corporate_actions.empty:
                    storage.upsert_corporate_actions(conn, corporate_actions)

                if not stock_actions.empty:
                    storage.upsert_stock_actions(conn, stock_actions)

                if not skipped_events.empty:
                    storage.upsert_skipped_events(conn, skipped_events)
                    logger.info(
                        f"Stored {len(skipped_events)} skipped events for manual review"
                    )
        else:
            logger.info("")
            logger.info("Step 6/9: Skipping corporate actions fetch...")

        logger.info("")
        logger.info("Step 7/10: Loading prices and detecting missing splits...")

        prices_from_db = storage.get_all_prices(conn)

        # Always reload the FULL action tables from the DB: this run's fetch may
        # have failed for some companies, and adjustments must still include
        # their previously stored dividends/splits.
        corporate_actions = storage.get_all_corporate_actions(conn)
        stock_actions = storage.get_all_stock_actions(conn)

        logger.info("Detecting missing splits from price data...")
        detected_splits = adjustments.detect_splits_from_prices(
            prices_from_db, stock_actions,
            detect_nonstandard=detect_nonstandard_splits,
        )
        if not detected_splits.empty:
            logger.info(f"Detected {len(detected_splits)} new splits from price data")
            # Stored only by replace_stock_actions below, after validation drops
            # the ones a B3 row explains: a killed run leaves no stray rows.
            stock_actions = pd.concat([stock_actions, detected_splits], ignore_index=True)
        else:
            logger.info("No new splits detected from price data")

        adjusted_prices, splits, validated_actions = adjustments.compute_all_adjustments(
            prices_from_db, corporate_actions, stock_actions
        )

        if not splits.empty:
            storage.upsert_detected_splits(conn, splits)

        # Persist the validated action set (phantom rows dropped, factors
        # corrected against prices) so share-count consumers see the same truth
        storage.replace_stock_actions(conn, validated_actions)

        logger.info("")
        logger.info("Step 8/10: Computing adjustments...")

        logger.info("")
        logger.info("Step 9/10: Updating adjusted columns in database...")
        storage.update_adjusted_columns(conn, adjusted_prices)

        if not skip_corporate_actions:
            unresolved = storage.get_unresolved_failures(conn)
            if unresolved:
                logger.info("")
                logger.info(
                    f"Step 9b/10: Retrying {len(unresolved)} previously failed corporate "
                    "action fetches..."
                )
                _retry_failures(conn, unresolved, nested=True)

        logger.info("")
        logger.info("Step 10/10: Summary statistics...")
        stats = storage.get_summary_stats(conn)

        logger.info("")
        logger.info("=" * 60)
        logger.info("PIPELINE COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Total price records: {stats['total_prices']:,}")
        logger.info(f"Unique tickers: {stats['total_tickers']:,}")
        logger.info(f"Date range: {stats['date_range'][0]} to {stats['date_range'][1]}")
        logger.info(f"Corporate actions: {stats['total_corporate_actions']:,}")
        logger.info(f"Stock actions (splits/bonuses): {stats['total_stock_actions']:,}")
        logger.info(f"Skipped events (manual review): {stats.get('total_skipped_events', 0):,}")
        logger.info(f"Unresolved fetch failures: {stats.get('unresolved_fetch_failures', 0):,}")

        end_time = datetime.now()
        duration = end_time - start_time
        logger.info(f"Total duration: {duration}")
        # Report where the rows actually went. ENGINE_DATABASE_URL overrides
        # DB_PATH, so printing DB_PATH unconditionally claimed a SQLite file even
        # on runs that wrote to Postgres — a confusing first clue when a scheduled
        # ingest looks successful but the plane hasn't moved. Masked: the URL
        # carries the password.
        logger.info(f"Database: {storage.describe_target()}")

    except Exception as e:
        logger.exception(f"Pipeline failed with error: {e}")
        raise
    finally:
        conn.close()


def _run_update_cnpj_map() -> None:
    """
    Fetch CNPJ from the B3 API for every ticker root in the prices table
    and upsert into cvm_companies. Runs in ~1-2 minutes vs the full pipeline.

    Delegates to cvm_main._fetch_ticker_mappings() so the logic is not duplicated.
    """
    from . import cvm_main as _cvm_main
    logger.info("Updating CNPJ → ticker map from B3 API...")
    with storage.get_connection() as conn:
        storage.init_db(conn)
        tickers = storage.get_all_tickers(conn)
        if not tickers:
            logger.warning("No tickers found in the database. Run the full pipeline first.")
            return
        updated = _cvm_main._fetch_ticker_mappings(conn)
        logger.info(f"Done. Upserted {updated} CNPJ mappings into cvm_companies.")


def _retry_failures(conn, failures: list, nested: bool = False) -> None:
    """Re-fetch each recorded failure; resolve and store the ones that now answer.

    `nested` is the run_pipeline step 9b flavour: indented, terser log lines.
    """
    ticker_to_isin = storage.get_ticker_isin_map(conn)
    indent = "  " if nested else ""
    for failure in failures:
        company_code = failure["company_code"]
        logger.info(f"{indent}Retrying {company_code}...")
        company_data = b3_corporate_actions.fetch_company_data(company_code, conn=conn)
        if company_data is None:
            logger.warning(f"  Retry still failed for {company_code}" if nested
                           else f"Retry failed for {company_code}")
            continue
        storage.resolve_fetch_failure(conn, company_code, failure["endpoint"])
        if not nested:
            logger.info(f"Successfully fetched {company_code}")
        _store_company_actions(conn, company_data, company_code, ticker_to_isin)


def _store_company_actions(conn, company_data: dict, company_code: str,
                           ticker_to_isin: dict) -> None:
    stock_divs = company_data.get("stockDividends", [])
    if stock_divs:
        corp_df, stock_df, skipped_df = b3_corporate_actions.parse_stock_dividends(
            stock_divs
        )
        if not corp_df.empty:
            storage.upsert_corporate_actions(conn, corp_df)
        if not stock_df.empty:
            storage.upsert_stock_actions(conn, stock_df)
        if not skipped_df.empty:
            storage.upsert_skipped_events(conn, skipped_df)

    # Also fetch cash dividends for recovered companies
    full_trading_name = company_data.get("tradingName", "").strip()
    if full_trading_name:
        cash_divs = b3_corporate_actions.fetch_cash_dividends_paginated(
            full_trading_name
        )
        if cash_divs:
            corp_df = b3_corporate_actions.parse_cash_dividends(
                cash_divs, company_code, ticker_to_isin
            )
            if not corp_df.empty:
                storage.upsert_corporate_actions(conn, corp_df)


def retry_failed_companies() -> None:
    """
    Re-fetch corporate actions for companies that previously failed.

    Reads the fetch_failures table for unresolved failures, re-attempts
    the fetch, and marks resolved on success.
    """
    logger.info("=" * 60)
    logger.info("Retrying failed company fetches")
    logger.info("=" * 60)

    conn = storage.get_connection()

    try:
        failures = storage.get_unresolved_failures(conn)
        if not failures:
            logger.info("No unresolved fetch failures found")
            return

        logger.info(f"Found {len(failures)} unresolved failures to retry")
        _retry_failures(conn, failures)
        logger.info("Retry complete")
    finally:
        conn.close()


def main():
    """CLI entry point."""
    arg_parser = argparse.ArgumentParser(
        description="B3 Historical Market Data Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python -m b3_pipeline.main              # Run pipeline
    python -m b3_pipeline.main --rebuild    # Rebuild from scratch
    python -m b3_pipeline.main --year 2024  # Process 2024 only
        """,
    )

    arg_parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Drop and recreate database tables before processing",
    )

    arg_parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Process only the specified year",
    )

    arg_parser.add_argument(
        "--start-year",
        type=int,
        default=None,
        help="Process all years from this year onwards (e.g. --start-year 2010)",
    )

    arg_parser.add_argument(
        "--skip-corporate-actions",
        action="store_true",
        help="Skip fetching corporate actions (use existing data)",
    )

    arg_parser.add_argument(
        "--detect-nonstandard-splits",
        action="store_true",
        help="Also correct price jumps that don't match standard integer split ratios, using the raw observed ratio",
    )

    arg_parser.add_argument(
        "--retry-failures",
        action="store_true",
        help="Re-fetch only companies that previously failed (from fetch_failures table)",
    )

    arg_parser.add_argument(
        "--update-cnpj-map",
        action="store_true",
        help="Fetch CNPJ from B3 API for all known tickers and populate cvm_companies. Fast alternative to running the full pipeline.",
    )

    arg_parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download the current year's COTAHIST file instead of reusing the "
             "local copy. Required for any repeat/scheduled run: B3 rewrites the "
             "current year's archive daily, and without this the pipeline re-parses "
             "the already-downloaded file and never advances. Closed years are "
             "immutable and are never re-downloaded.",
    )

    arg_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging",
    )

    args = arg_parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.update_cnpj_map:
        _run_update_cnpj_map()
    elif args.retry_failures:
        retry_failed_companies()
    else:
        run_pipeline(
            rebuild=args.rebuild,
            year=args.year,
            start_year=args.start_year,
            skip_corporate_actions=args.skip_corporate_actions,
            detect_nonstandard_splits=args.detect_nonstandard_splits,
            force_download=args.force_download,
        )


if __name__ == "__main__":
    main()
