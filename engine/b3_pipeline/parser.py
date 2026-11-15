"""
Parse COTAHIST fixed-width files from B3.

Fixed-width record parsing over the COTAHIST ZIPs B3 publishes.
"""

import logging
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from . import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Record parsing helpers
# ---------------------------------------------------------------------------

def _parse_price(value: str) -> float:
    """Parse price string (integer with 2 implied decimals) to float."""
    try:
        return int(value.strip() or "0") / 100.0
    except ValueError:
        return 0.0


def _parse_date(value: str) -> Optional[datetime]:
    """Parse date string in YYYYMMDD format."""
    try:
        return datetime.strptime(value.strip(), "%Y%m%d")
    except ValueError:
        return None


def _parse_int(value: str) -> int:
    """Parse integer string."""
    try:
        return int(value.strip() or "0")
    except ValueError:
        return 0


_SPOT_MARKETS = ("010", "011", "012", "013", "014", "015")


def _is_plane_instrument(line: str) -> bool:
    """Whether a COTAHIST line is a quote record for something the plane stores:
    a spot-market row (tipo de mercado) in an equity BDI class, and not an index.

    The index check: IBOV11 / SMLL11 are the Ibovespa and Small Cap in points,
    printed as lote-padrão rows with an "IND" ISIN (BRIBOVINDM18). They are no
    position anyone holds; see backtests.core.markets.B3_INDEX_LINES.
    """
    return (len(line) >= 245
            and line[0:2] == "01"
            and line[10:12].strip() in config.EQUITY_BDI_CODES
            and line[24:27].strip() in _SPOT_MARKETS
            and line[236:239] != "IND")


def parse_cotahist_file(zip_path: Path) -> pd.DataFrame:
    """
    Parse a single COTAHIST ZIP file.

    Args:
        zip_path: Path to COTAHIST ZIP file

    Returns:
        DataFrame with columns: [date, ticker, isin_code, open, high, low, close, volume, quotation_factor]
        Prices (open/high/low/close) are normalized to per-share basis by dividing by quotation_factor.
        Returns an empty DataFrame if the file does not exist or cannot be parsed.
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        logger.error(f"File not found: {zip_path}")
        return pd.DataFrame()

    records = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            txt_files = [f for f in zf.namelist() if not f.endswith("/")]
            if not txt_files:
                logger.error(f"No files found in {zip_path}")
                return pd.DataFrame()

            with zf.open(txt_files[0]) as f:
                for line_bytes in f:
                    try:
                        line = line_bytes.decode("latin-1")
                    except UnicodeDecodeError:
                        line = line_bytes.decode("utf-8", errors="replace")

                    if not _is_plane_instrument(line):
                        continue

                    ticker = line[12:24].strip().upper()
                    if not ticker:
                        continue

                    isin_code = line[230:242].strip()
                    date = _parse_date(line[2:10])
                    if date is None:
                        continue

                    open_price = _parse_price(line[56:69])
                    high_price = _parse_price(line[69:82])
                    low_price = _parse_price(line[82:95])
                    close_price = _parse_price(line[108:121])
                    volume = _parse_price(line[170:188])

                    quotation_factor = _parse_int(line[210:217])
                    if quotation_factor <= 0:
                        quotation_factor = 1

                    if quotation_factor != 1:
                        open_price = open_price / quotation_factor
                        high_price = high_price / quotation_factor
                        low_price = low_price / quotation_factor
                        close_price = close_price / quotation_factor

                    records.append({
                        "date": date,
                        "ticker": ticker,
                        "isin_code": isin_code,
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": close_price,
                        "volume": volume,
                        "quotation_factor": quotation_factor,
                    })

    except zipfile.BadZipFile as e:
        logger.error(f"Bad ZIP file {zip_path}: {e}")
        return pd.DataFrame()

    if records:
        df = pd.DataFrame(records)
        logger.debug(f"Parsed {len(df)} records from {zip_path.name}")
        return df

    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_all_cotahist_files(directory: Path, years=None) -> pd.DataFrame:
    """
    Parse all COTAHIST ZIP files in a directory.

    Args:
        directory: Path to directory containing COTAHIST_*.ZIP files
        years: only these years' files. The directory is a persistent volume:
            one all-years run leaves 1994+ there, and a `--start-year 2005` job
            parsing everything re-inserted 617k pre-2005 rows (prod 2026-11-02).

    Returns:
        Combined DataFrame with all price records, deduplicated and sorted.
    """
    zip_files = sorted(f for f in directory.glob("COTAHIST_*.ZIP")
                       if years is None or f.stem[-4:] in {str(y) for y in years})

    if not zip_files:
        logger.warning(f"No COTAHIST ZIP files found in {directory}")
        return pd.DataFrame()

    logger.info(f"Found {len(zip_files)} COTAHIST files to parse")

    all_dfs = []
    for i, zip_path in enumerate(zip_files, 1):
        logger.info(f"Parsing {zip_path.name} ({i}/{len(zip_files)})")
        df = parse_cotahist_file(zip_path)
        if not df.empty:
            all_dfs.append(df)

    if not all_dfs:
        return pd.DataFrame()

    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.drop_duplicates(subset=["ticker", "date"], keep="last")
    combined = combined.sort_values(["ticker", "date"]).reset_index(drop=True)

    logger.info(f"Total parsed records: {len(combined):,}")
    logger.info(f"Unique tickers: {combined['ticker'].nunique():,}")
    logger.info(f"Date range: {combined['date'].min()} to {combined['date'].max()}")

    return combined


