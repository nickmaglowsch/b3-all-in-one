"""
Download CVM IPE document index ZIP files.

IMPORTANT: As documented in tasks/ipe-structure-report.md, the IPE dataset
is a DOCUMENT FILING INDEX — not structured financial statement data.
Each yearly ZIP contains a single CSV with metadata (company ID, document
category, dates, download links to PDFs/HTML) but no financial values.

URL pattern: https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{year}.zip
Year range available: 2003-present
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import config, downloader


def download_ipe_file(year: int, force: bool = False) -> Optional[Path]:
    """Download the IPE document index ZIP for `year`, or return the cached copy."""
    filename = f"ipe_cia_aberta_{year}.zip"
    return downloader.download_file(
        f"{config.CVM_IPE_BASE_URL}{filename}",
        config.CVM_DATA_DIR / filename,
        force=force,
    )
