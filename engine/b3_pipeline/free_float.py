"""
Point-in-time free float from CVM's Formulário de Referência (FRE).

Source: fre_cia_aberta_distribuicao_capital_YYYY.csv inside the yearly FRE ZIP
(FRE item 15.3, "ações em circulação": shares not held by the controller, its
related persons, managers or treasury). Columns used: CNPJ_Companhia,
Data_Referencia, Versao, ID_Documento, Quantidade_Acoes_{Ordinarias,
Preferenciais,Total}_Circulacao and Percentual_*_Circulacao. The receipt date is
not in that CSV: it comes from the yearly index fre_cia_aberta_YYYY.csv
(ID_DOC -> DT_RECEB), joined on the document id.

Point in time: CVM ships only the LATEST version of each (company, reference
year), so a historical backfill sees one version per year, dated by that
version's receipt — typically late in the year, after many resubmissions. That
is conservative (a value is never used before the document carrying it was
received). Each run upserts by (cnpj, ref_date, version) and never deletes, so
from the first ingest on every new version is kept with its own receipt date
and a restatement cannot leak backward.

Percent is CVM's 0-100 scale. 0 (the section left blank — ~1/3 of filers, mostly
issuers with no listed shares) and >100 are stored as NULL, not as a real float.
Total shares are not stored: fundamentals_pit.shares_outstanding has them from
the same FRE (capital_social).
"""
from __future__ import annotations

import logging

import pandas as pd

from . import cvm_parser

logger = logging.getLogger(__name__)

SCHEMA_FREE_FLOAT_PIT = """
CREATE TABLE IF NOT EXISTS free_float_pit (
    cnpj TEXT NOT NULL,
    ref_date DATE NOT NULL,
    version INTEGER NOT NULL,
    doc_id TEXT,
    receipt_date DATE NOT NULL,
    shares_free_on REAL,
    shares_free_pn REAL,
    shares_free_total REAL,
    pct_free_on REAL,
    pct_free_pn REAL,
    pct_free_total REAL,
    source_file TEXT,
    PRIMARY KEY (cnpj, ref_date, version)
);
"""
INDEX_FREE_FLOAT_PIT_RECEIPT = (
    "CREATE INDEX IF NOT EXISTS idx_free_float_pit_receipt ON free_float_pit(receipt_date);"
)
DDL = [SCHEMA_FREE_FLOAT_PIT, INDEX_FREE_FLOAT_PIT_RECEIPT]

COLUMNS = ["cnpj", "ref_date", "version", "doc_id", "receipt_date",
           "shares_free_on", "shares_free_pn", "shares_free_total",
           "pct_free_on", "pct_free_pn", "pct_free_total", "source_file"]
_KEY = ["cnpj", "ref_date", "version"]
_NUMERIC = {
    "Quantidade_Acoes_Ordinarias_Circulacao": "shares_free_on",
    "Quantidade_Acoes_Preferenciais_Circulacao": "shares_free_pn",
    "Quantidade_Total_Acoes_Circulacao": "shares_free_total",
    "Percentual_Acoes_Ordinarias_Circulacao": "pct_free_on",
    "Percentual_Acoes_Preferenciais_Circulacao": "pct_free_pn",
    "Percentual_Total_Acoes_Circulacao": "pct_free_total",
}


def parse_free_float_zip(zip_path) -> pd.DataFrame:
    """One row per (cnpj, ref_date, version) in a FRE ZIP, in COLUMNS order."""
    source_file, zf_ctx = cvm_parser._open_zip_source(zip_path)
    with zf_ctx as zf:
        # "_2" (the year) skips distribuicao_capital_classe_acao and the
        # capital_social_* tables, which the plain stems would also match.
        dist = cvm_parser._load_csv_from_zip(zf, "distribuicao_capital_2")
        index = cvm_parser._load_csv_from_zip(zf, "fre_cia_aberta_2")
    if dist is None or dist.empty or index is None or "DT_RECEB" not in index.columns:
        logger.warning(f"{source_file}: no distribuicao_capital / FRE index CSV")
        return pd.DataFrame(columns=COLUMNS)

    receipts = index.drop_duplicates("ID_DOC").set_index("ID_DOC")["DT_RECEB"]
    out = pd.DataFrame({
        "cnpj": dist["CNPJ_Companhia"].map(cvm_parser._clean_cnpj),
        "ref_date": dist["Data_Referencia"].map(cvm_parser._parse_date),
        "version": pd.to_numeric(dist["Versao"], errors="coerce"),
        "doc_id": dist["ID_Documento"],
        "receipt_date": dist["ID_Documento"].map(receipts).map(cvm_parser._parse_date),
    })
    for src, dst in _NUMERIC.items():
        out[dst] = pd.to_numeric(dist.get(src), errors="coerce")
    for col in ("pct_free_on", "pct_free_pn", "pct_free_total"):
        out[col] = out[col].where((out[col] > 0) & (out[col] <= 100))
    out["source_file"] = source_file

    # No receipt date = no honest "known since"; never seen in 2010-2026 files.
    bad = out[["cnpj", "ref_date", "version", "receipt_date"]].isna().any(axis=1)
    if bad.any():
        logger.warning(f"{source_file}: dropped {int(bad.sum())} free-float row(s) without key/receipt date")
    out = out[~bad].drop_duplicates(_KEY, keep="last")
    out["version"] = out["version"].astype(int)
    return out[COLUMNS]


def upsert_free_float(conn, df: pd.DataFrame) -> int:
    from . import storage

    if df.empty:
        return 0
    rows = [tuple(r.values()) for r in storage.df_records(df[COLUMNS])]
    storage.executemany(conn, storage._upsert_sql("free_float_pit", COLUMNS, _KEY), rows)
    conn.commit()
    return len(rows)


def ingest(conn, fre_paths) -> int:
    """Upsert free float from every FRE ZIP given. Never raises: free float is
    an add-on to the fundamentals ingest, so a bad file or a CVM outage (the
    ZIP never arrived) logs a warning and the ingest carries on."""
    total = 0
    for path in fre_paths:
        try:
            total += upsert_free_float(conn, parse_free_float_zip(path))
        except Exception as e:  # noqa: BLE001 - see docstring
            conn.rollback()
            logger.warning(f"free_float_pit: skipped {path}: {e}")
    logger.info(f"free_float_pit: {total:,} rows upserted from {len(fre_paths)} FRE file(s)")
    return total


def main() -> None:
    """One-off backfill: `python -m b3_pipeline.free_float` (every FRE year)."""
    from . import config, cvm_downloader, storage

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    years = range(config.CVM_START_YEAR, config.get_current_year() + 1)
    paths = [p for p in map(cvm_downloader.download_fre_file, years) if p]
    with storage.get_connection() as conn:
        storage.init_db(conn)
        ingest(conn, paths)


if __name__ == "__main__":
    main()
