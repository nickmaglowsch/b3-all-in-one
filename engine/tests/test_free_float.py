"""free_float_pit: FRE distribuicao_capital parsing, version/receipt-date PIT,
and the root-keyed loader. Fixture ZIPs mirror the real CVM layout (2010-2026):
the distribution CSV carries only the latest version per company-year and no
receipt date; the index CSV maps ID_DOC -> DT_RECEB."""
from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

import pandas as pd

from b3_pipeline import config, cvm_downloader, free_float
from backtests.core.free_float import asof, load_free_float_pit
from tests.conftest import csv_bytes, seed_cvm_company, sqlite_db

PETR, BPAC, BLANK = "33.000.167/0001-01", "30.306.294/0001-45", "11.111.111/0001-11"


def _dist(cnpj, ref, ver, doc, pct, on=100.0, pn=0.0):
    return {"CNPJ_Companhia": cnpj, "Data_Referencia": ref, "Versao": ver,
            "ID_Documento": doc, "Nome_Companhia": "X",
            "Quantidade_Acoes_Ordinarias_Circulacao": on,
            "Percentual_Acoes_Ordinarias_Circulacao": pct,
            "Quantidade_Acoes_Preferenciais_Circulacao": pn,
            "Percentual_Acoes_Preferenciais_Circulacao": 0,
            "Quantidade_Total_Acoes_Circulacao": on + pn,
            "Percentual_Total_Acoes_Circulacao": pct}


def _fre_zip(year, dist_rows, receipts: dict) -> io.BytesIO:
    index = [{"CNPJ_CIA": "x", "DT_REFER": "x", "VERSAO": 1, "ID_DOC": d, "DT_RECEB": r}
             for d, r in receipts.items()]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"fre_cia_aberta_{year}.csv", csv_bytes(index))
        zf.writestr(f"fre_cia_aberta_capital_social_{year}.csv", csv_bytes([{"a": 1}]))
        zf.writestr(f"fre_cia_aberta_distribuicao_capital_{year}.csv", csv_bytes(dist_rows))
        # same stem: must not be picked over the plain distribution CSV
        zf.writestr(f"fre_cia_aberta_distribuicao_capital_classe_acao_{year}.csv",
                    csv_bytes([{"CNPJ_Companhia": PETR, "Percentual_Acoes_Preferenciais_Circulacao": 99}]))
    buf.seek(0)
    return buf


def test_parse_joins_receipt_by_document_and_nulls_blank_sections():
    df = free_float.parse_free_float_zip(_fre_zip(2023, [
        _dist(PETR, "2023-12-31", 4, "900", 63.2),
        _dist(BLANK, "2023-12-31", 1, "901", 0),       # section left blank
        _dist(BPAC, "2023-12-31", 2, "999", 30.0),     # doc not in the index
    ], {"900": "2023-11-20", "901": "2023-06-01"}))
    assert list(df.columns) == free_float.COLUMNS
    assert set(df["cnpj"]) == {"33000167000101", "11111111000111"}  # no receipt -> dropped
    petr = df.set_index("cnpj").loc["33000167000101"]
    assert (petr["receipt_date"], petr["version"], petr["pct_free_total"]) == ("2023-11-20", 4, 63.2)
    assert pd.isna(df.set_index("cnpj").loc["11111111000111", "pct_free_total"])


def test_versions_accumulate_and_asof_never_leaks_backward(tmp_path):
    conn = sqlite_db(tmp_path)
    seed_cvm_company(conn, "33000167000101", "PETR4", "PETROBRAS", "9512", "PETROBRAS")
    conn.execute("INSERT INTO company_tickers_pit (cnpj, ticker, ticker_root, start_date) "
                 "VALUES ('30306294000145', 'BPAC11', 'BPAC', '2015-01-01')")
    conn.commit()
    # Run 1 (early 2024): ref 2022 final + ref 2023 v2.
    free_float.ingest(conn, [
        _fre_zip(2022, [_dist(PETR, "2022-12-31", 7, "1", 60.0), _dist(BPAC, "2022-12-31", 3, "2", 25.0)],
                 {"1": "2022-12-01", "2": "2022-12-05"}),
        _fre_zip(2023, [_dist(PETR, "2023-12-31", 2, "3", 62.0)], {"3": "2023-11-01"}),
    ])
    # Run 2 (later): CVM now ships ref 2023 v5 (restated) and a late v9 of 2022.
    free_float.ingest(conn, [
        _fre_zip(2023, [_dist(PETR, "2023-12-31", 5, "4", 64.0)], {"4": "2024-02-10"}),
        _fre_zip(2022, [_dist(PETR, "2022-12-31", 9, "5", 10.0)], {"5": "2024-03-01"}),
        io.BytesIO(b"not a zip"),  # broken file: warned and skipped
    ])
    stored = conn.execute("SELECT version FROM free_float_pit WHERE cnpj='33000167000101' "
                          "AND ref_date='2023-12-31' ORDER BY version").fetchall()
    assert stored == [(2,), (5,)]
    conn.close()

    ff = load_free_float_pit(str(tmp_path / "engine.sqlite"))
    pct = lambda d, root="PETR": asof(ff, d).loc[root, "free_float_pct"]  # noqa: E731
    assert "PETR" not in asof(ff, "2022-11-30").index      # nothing received yet
    assert pct("2023-06-30") == 60.0                       # prior year's final
    assert pct("2023-11-15") == 62.0                       # v2, not the later v5
    assert pct("2024-02-15") == 64.0                       # restatement once received
    assert pct("2024-06-30") == 64.0                       # late old-year v9 never wins
    assert pct("2024-06-30", "BPAC") == 25.0               # company_tickers_pit root


def test_recent_fre_years_are_always_refetched():
    now = config.get_current_year()
    with patch.object(cvm_downloader.downloader, "download_file") as dl:
        for y in (now - 2, now - 1, now):
            cvm_downloader.download_fre_file(y)
    assert [c.kwargs["force"] for c in dl.call_args_list] == [False, True, True]


def test_ingest_never_raises(mem_conn):
    paths = [io.BytesIO(b"x"), "/nonexistent/fre_cia_aberta_2099.zip"]
    assert free_float.ingest(mem_conn, paths) == 0
