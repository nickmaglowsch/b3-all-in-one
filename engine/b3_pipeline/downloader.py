"""
Download COTAHIST files from B3, and the streaming download every other
b3_pipeline fetcher is built on.

`download_file` is the single implementation of "stream a URL to a path". CAD,
IPE and the CVM bulk files (DFP/ITR/FRE/FCA) are the same fetch with a different
URL and filename, so they call this rather than carrying their own copy — four
copies of one try/except was four places to fix a dropped-connection bug in.

Note: Corporate actions are now fetched via b3_corporate_actions module,
which queries B3 directly as the single source of truth.
"""

import logging
import os
import zipfile
from pathlib import Path
from typing import List, Optional

import requests

from . import config

logger = logging.getLogger(__name__)


def detect_available_years() -> List[int]:
    """
    Probe B3 server to detect which years have COTAHIST files available.
    Returns list of years from START_YEAR to current year that exist.
    """
    available = []
    current_year = config.CURRENT_YEAR

    for year in range(config.START_YEAR, current_year + 1):
        url = config.BASE_COTAHIST_URL.format(year=year)
        try:
            resp = requests.head(
                url, headers=config.B3_HEADERS, timeout=10, allow_redirects=True
            )
            if resp.status_code == 200:
                available.append(year)
        except requests.RequestException:
            pass

    return available


def download_file(url: str, filepath: Path, force: bool = False) -> Optional[Path]:
    """
    Stream `url` into `filepath`.

    Skips the download if the file already exists and force=False.
    Streams into `<file>.part` and only replaces `filepath` once the payload is
    complete and (for .zip) opens as a ZIP. On any failure — refused connection,
    dropped stream, a 200 maintenance page — the previous copy stays untouched,
    the .part is removed and None is returned. A truncated file is never left
    where a later run would skip it as "already downloaded".

    Returns the local Path on success, None on failure.
    """
    if filepath.exists() and not force:
        logger.debug(f"File already exists: {filepath}")
        return filepath

    logger.info(f"Downloading {url}...")
    part = filepath.with_name(filepath.name + ".part")

    try:
        resp = requests.get(url, headers=config.B3_HEADERS, timeout=300, stream=True)
        resp.raise_for_status()

        downloaded = 0
        with open(part, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)

        if filepath.suffix.lower() == ".zip" and not zipfile.is_zipfile(part):
            raise ValueError("payload is not a ZIP")
        os.replace(part, filepath)
        logger.info(f"Downloaded {filepath} ({downloaded:,} bytes)")
        return filepath

    except (requests.RequestException, OSError, ValueError) as e:
        logger.error(f"Failed to download {url}: {e}")
        part.unlink(missing_ok=True)
        return None


def download_annual_file(year: int, force: bool = False) -> Optional[Path]:
    """Download the annual COTAHIST ZIP file for a given year."""
    return download_file(
        config.BASE_COTAHIST_URL.format(year=year),
        config.DATA_DIR / f"COTAHIST_A{year}.ZIP",
        force=force,
    )


