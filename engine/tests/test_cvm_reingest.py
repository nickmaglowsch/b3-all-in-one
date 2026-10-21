"""
Re-ingesting a CVM year must not blank what the pipeline derived from it.

A DFP/ITR row never carries its own share count or TTM income: the parser
leaves both NULL, and `_propagate_fre_shares` / `compute_net_income_ttm` fill
them in later in the run. The upsert used to overwrite them with those NULLs,
and every step commits, so between the DFP/ITR upsert and the propagation —
and for good if the run died in between, or the FRE download failed — every
re-ingested row had no share count and no market cap.
"""
from __future__ import annotations

import pandas as pd

from b3_pipeline import cvm_main, cvm_storage

CNPJ = "11111111000100"


def _row(filing_id, doc_type, filing_date, **values):
    return {
        "filing_id": filing_id, "cnpj": CNPJ, "ticker": "AAAA",
        "period_end": "2023-12-31", "filing_date": filing_date,
        "filing_version": 1, "doc_type": doc_type, "fiscal_year": 2023,
        "quarter": None, "net_income": 200.0, **values,
    }


def _dfp(**values):
    return _row("DFP_2023", "DFP", "2024-03-15", **values)


def _fre(filing_id, filing_date, shares):
    return _row(filing_id, "FRE", filing_date, net_income=None,
                shares_outstanding=shares, shares_on=shares, shares_pn=0.0)


def _get(conn, filing_id, cols="shares_outstanding, shares_on, net_income_ttm"):
    return conn.execute(
        f"SELECT {cols} FROM fundamentals_pit WHERE filing_id = ?",
        (filing_id,)).fetchone()


def test_reingested_dfp_keeps_its_derived_columns(mem_conn):
    cvm_storage.upsert_fundamentals_pit(mem_conn, pd.DataFrame([_fre("FRE_1", "2024-01-10", 1000.0)]))
    cvm_storage.upsert_fundamentals_pit(mem_conn, pd.DataFrame([_dfp()]))
    cvm_main._propagate_fre_shares(mem_conn)
    cvm_main.compute_net_income_ttm(mem_conn)
    assert _get(mem_conn, "DFP_2023") == (1000.0, 1000.0, 200.0)

    # The parser's view of the same filing, as the next run upserts it.
    cvm_storage.upsert_fundamentals_pit(mem_conn, pd.DataFrame([_dfp(net_income=210.0)]))
    assert _get(mem_conn, "DFP_2023") == (1000.0, 1000.0, 200.0)
    assert _get(mem_conn, "DFP_2023", "net_income") == (210.0,)


def test_propagation_still_follows_the_latest_fre_on_or_before_the_filing(mem_conn):
    """Keeping the stored count must not freeze it: an FRE that arrives later
    but was filed before the DFP still wins, as it did when every re-ingest
    started from NULL."""
    cvm_storage.upsert_fundamentals_pit(mem_conn, pd.DataFrame([
        _fre("FRE_1", "2023-06-10", 1000.0), _dfp()]))
    cvm_main._propagate_fre_shares(mem_conn)
    assert _get(mem_conn, "DFP_2023", "shares_outstanding") == (1000.0,)

    cvm_storage.upsert_fundamentals_pit(mem_conn, pd.DataFrame([
        _fre("FRE_2", "2024-02-01", 2000.0),   # before the DFP: applies
        _fre("FRE_3", "2024-05-01", 3000.0),   # after it: must not leak back
    ]))
    cvm_main._propagate_fre_shares(mem_conn)
    assert _get(mem_conn, "DFP_2023", "shares_outstanding") == (2000.0,)
