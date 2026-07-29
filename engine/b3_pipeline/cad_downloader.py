"""
Download the CVM CAD (company register) bulk CSV file.

The CAD dataset is a single bulk file (not yearly ZIPs) covering all companies
that have ever filed with CVM, including delisted companies.

URL: https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import config, downloader


def download_cad_file(force: bool = False) -> Optional[Path]:
    """Download the CVM CAD company register CSV, or return the cached copy."""
    return downloader.download_file(
        config.CVM_CAD_BASE_URL + config.CVM_CAD_FILENAME,
        config.CVM_DATA_DIR / config.CVM_CAD_FILENAME,
        force=force,
    )
