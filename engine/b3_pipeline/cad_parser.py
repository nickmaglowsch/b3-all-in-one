"""
CVM CAD (company register) parser.

Parses the bulk CSV file cad_cia_aberta.csv from CVM's open data portal.
This file contains registration metadata for every company that has ever
filed with CVM, including listing/delisting dates for survivorship bias removal,
and CVM's sector classification (SETOR_ATIV) — one per company ever registered,
cancelled ones included, which is what keeps a sector strategy's universe
survivorship-free (backtests.core.data.load_sector_pit).

Output columns: cnpj, cvm_code, company_name, listing_date, delisting_date,
is_active, sector
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Union

import pandas as pd

from .cvm_parser import _clean_cnpj, _parse_date

logger = logging.getLogger(__name__)


_CAD_COLS = ("CNPJ_CIA", "CD_CVM", "DENOM_SOCIAL", "DT_REG", "DT_CANCEL", "SIT",
             "SETOR_ATIV")
_OUT_COLS = ["cnpj", "cvm_code", "company_name", "listing_date", "delisting_date",
             "is_active", "sector"]


# ── Public API ─────────────────────────────────────────────────────────────────

def parse_cad_file(file_path: Union[Path, io.StringIO]) -> pd.DataFrame:
    """Parse the CVM CAD company register CSV.

    Accepts either a filesystem Path or an io.StringIO (for testing).

    Returns a DataFrame with columns:
        cnpj, cvm_code, company_name, listing_date, delisting_date, is_active, sector

    - cnpj: 14-digit string (rows with invalid/empty CNPJ are dropped)
    - listing_date: YYYY-MM-DD string or None
    - delisting_date: YYYY-MM-DD string or None (None for active companies)
    - is_active: bool (True when SIT == 'ATIVO')
    - sector: SETOR_ATIV as CVM writes it (e.g. 'Bancos', 'Emp. Adm. Part. -
      Bancos'), or None when blank
    """
    # Read the CSV — supports Path or StringIO
    try:
        df = pd.read_csv(
            file_path,
            sep=";",
            encoding="latin-1",
            dtype=str,
            low_memory=False,
            usecols=lambda c: c in _CAD_COLS,
        )
    except Exception as e:
        logger.error(f"Failed to read CAD CSV: {e}")
        return pd.DataFrame(columns=_OUT_COLS)

    for col in _CAD_COLS:
        if col not in df.columns:
            df[col] = None

    df["cnpj"] = df["CNPJ_CIA"].apply(lambda x: _clean_cnpj(str(x) if x else ""))
    df = df.dropna(subset=["cnpj"])
    df = df[df["cnpj"] != ""]

    df["listing_date"] = df["DT_REG"].apply(_parse_date)
    df["delisting_date"] = df["DT_CANCEL"].apply(_parse_date)

    df["is_active"] = df["SIT"].apply(
        lambda s: str(s).strip().upper() == "ATIVO" if s and str(s) not in ("nan", "None") else False
    )

    df = df.rename(columns={
        "CD_CVM": "cvm_code",
        "DENOM_SOCIAL": "company_name",
    })
    # object dtype, or pandas' inferred string dtype turns the None back into NaN
    sector = df["SETOR_ATIV"].str.strip().astype(object)
    df["sector"] = sector.where(sector.notna() & (sector != ""), None)

    result = df[_OUT_COLS].copy()

    for col in ("listing_date", "delisting_date"):
        result[col] = result[col].where(result[col].notna(), None)
        result[col] = result[col].apply(
            lambda v: None if v in (None, "nan", "NaT", "None", "") else v
        )

    logger.info(f"Parsed CAD file: {len(result):,} companies")
    return result.reset_index(drop=True)


def extract_cad_summary(df: pd.DataFrame) -> dict:
    """Return summary statistics for the CAD DataFrame.

    Returns:
        {'total': int, 'active': int, 'delisted': int, 'with_listing_date': int}
    """
    total = len(df)
    active = int(df["is_active"].sum())
    delisted = int(df["delisting_date"].notna().sum())
    with_listing_date = int(df["listing_date"].notna().sum())
    return {
        "total": total,
        "active": active,
        "delisted": delisted,
        "with_listing_date": with_listing_date,
    }
