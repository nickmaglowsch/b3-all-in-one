"""Data defects found auditing market caps on prod (2026-11), one test per class.

- B3 desdobramento factors are percentages (DIRR 1:3 = 200).
- B3 split dates are the data-com; the price jump is on the next bar.
- FRE share counts are stated at the filing date, not the form's period_end
  (CIEL's 2014 split applied twice -> R$144bn Cielo in 2015).
- All-zero DFP/ITR statement blocks are empty forms (TIMS FY2024).
- net_debt nets short-term financial investments (1.01.02).
"""
import pandas as pd
import pytest

from b3_pipeline.adjustments import validate_split_actions_against_prices
from b3_pipeline.b3_corporate_actions import parse_stock_dividends
from b3_pipeline.cvm_metrics import blank_financial_net_debt, blank_zero_statements
from backtests.core.pit import share_count_anchor
from backtests.strategies.sp500_b3 import select_constituents


def test_desdobramento_factor_is_a_percentage():
    rec = {"label": "DESDOBRAMENTO", "factor": "200", "isinCode": "BRDIRRACNOR0",
           "lastDatePrior": "08/08/2025"}
    stock_df = parse_stock_dividends([rec])[1]
    assert stock_df.iloc[0]["action_type"] == "STOCK_SPLIT"
    assert stock_df.iloc[0]["factor"] == pytest.approx(3.0)


def _prices(isin, closes):
    dates = pd.bdate_range("2025-08-04", periods=len(closes))
    return pd.DataFrame({"isin_code": isin, "date": dates.strftime("%Y-%m-%d"), "close": closes})


def test_b3_split_on_data_com_moves_to_the_ex_bar_and_supersedes_detector():
    # data-com Fri 2025-08-08 (bar 4); the 1:3 jump is on Mon 2025-08-11 (bar 5)
    px = _prices("BRDIRRACNOR0", [40, 40, 40, 41, 41, 13.6, 13.7, 13.6])
    actions = pd.DataFrame([
        {"isin_code": "BRDIRRACNOR0", "ex_date": "2025-08-08", "action_type": "STOCK_SPLIT",
         "factor": 3.0, "source": "B3"},
        {"isin_code": "BRDIRRACNOR0", "ex_date": "2025-08-11", "action_type": "STOCK_SPLIT",
         "factor": 3.0, "source": "DETECTED"},
    ])
    out = validate_split_actions_against_prices(actions, px)
    assert len(out) == 1, "kept once: not dropped as phantom, not doubled with the detector's row"
    assert str(out.iloc[0]["ex_date"])[:10] == "2025-08-11"
    assert out.iloc[0]["source"] == "B3"


def test_b3_split_without_any_jump_is_still_phantom():
    px = _prices("BRXXXXACNOR0", [40] * 8)
    actions = pd.DataFrame([{"isin_code": "BRXXXXACNOR0", "ex_date": "2025-08-08",
                             "action_type": "STOCK_SPLIT", "factor": 3.0, "source": "B3"}])
    assert validate_split_actions_against_prices(actions, px).empty


def test_fre_share_count_is_anchored_at_filing_date():
    df = pd.DataFrame({"doc_type": ["FRE", "ITR"],
                       "period_end": ["2014-01-01", "2014-03-31"],
                       "filing_date": ["2015-03-10", "2014-05-06"]})
    assert list(share_count_anchor(df)) == [pd.Timestamp("2015-03-10"), pd.Timestamp("2014-03-31")]


def test_cielo_split_is_not_applied_twice():
    # 786m shares pre-split; 1:2 split ex 2014-04-01; the FRE "2014" (period_end
    # 2014-01-01, filed 2015-03-10) already states the post-split 1.572bn.
    t = pd.Timestamp("2015-03-31")
    fund = pd.DataFrame([
        ("CIEL", "2014-12-31", "2015-01-28", 1, "DFP", 3e6, 6e6, 1.572e9),
        ("CIEL", "2014-01-01", "2015-03-10", 8, "FRE", None, None, 1.572e9),
    ], columns=["ticker", "period_end", "filing_date", "filing_version", "doc_type",
                "net_income_ttm", "equity", "shares_outstanding"])
    fund["period_end"] = pd.to_datetime(fund["period_end"])
    fund["filing_date"] = pd.to_datetime(fund["filing_date"])
    actions = pd.DataFrame({"ticker": ["CIEL3"], "ex_date": [pd.Timestamp("2014-04-01")],
                            "action_type": ["STOCK_SPLIT"], "factor": [2.0]})
    sel = select_constituents(t, fund, pd.Series({"CIEL3": 46.0}), pd.Series({"CIEL3": 3e8}),
                              actions, min_market_cap=0, min_adtv=0)
    assert sel.iloc[0]["market_cap"] == pytest.approx(1.572e9 * 46.0)


def test_all_zero_statement_blocks_are_blanked():
    m = pd.DataFrame({
        "revenue": [0.0, 10.0, 0.0], "net_income": [0.0, 1.0, 0.0], "ebitda": [0.0, 2.0, 0.0],
        "total_assets": [0.0, 50.0, 70.0], "equity": [0.0, 20.0, 30.0], "net_debt": [0.0, 0.0, 5.0],
    })
    blank_zero_statements(m)
    assert m.iloc[0].isna().all()                       # TIMS FY2024: empty form
    assert m.iloc[1]["net_debt"] == 0.0                 # one zero is a value
    assert m.iloc[2][["revenue", "net_income", "ebitda"]].isna().all()  # GPAR: empty DRE
    assert m.iloc[2]["total_assets"] == 70.0


def test_net_debt_nets_short_term_investments():
    from tests.test_cvm_parser import BASE_DRE, _make_fake_dfp_zip
    from b3_pipeline.cvm_parser import parse_dfp_zip
    row = {"CNPJ_CIA": "33.000.167/0001-01", "DT_REFER": "2023-12-31", "DT_RECEB": "2024-03-15",
           "VERSAO": "1", "ORDEM_EXERC": "ÚLTIMO"}
    bpa = [dict(row, CD_CONTA="1.01.01", VL_CONTA="100"), dict(row, CD_CONTA="1.01.02", VL_CONTA="400")]
    bpp = [dict(row, CD_CONTA="2.01.04", VL_CONTA="200"), dict(row, CD_CONTA="2.02.01", VL_CONTA="600")]
    _, f = parse_dfp_zip(_make_fake_dfp_zip(BASE_DRE, bpa, bpp), {"33000167000101": "EZTC"})
    assert f["net_debt"].iloc[0] == pytest.approx(200 + 600 - 100 - 400)


def _dirr_mcap(t, row, close):
    """DIRR mcap at t from one ITR row whose count was copied from an FRE (shares_asof)."""
    fund = pd.DataFrame([row], columns=["ticker", "period_end", "filing_date", "filing_version",
                                        "doc_type", "net_income_ttm", "equity",
                                        "shares_outstanding", "shares_asof"])
    for c in ("period_end", "filing_date", "shares_asof"):
        fund[c] = pd.to_datetime(fund[c])
    actions = pd.DataFrame({"ticker": ["DIRR3"], "ex_date": [pd.Timestamp("2025-08-11")],
                            "action_type": ["STOCK_SPLIT"], "factor": [3.0]})
    sel = select_constituents(pd.Timestamp(t), fund, pd.Series({"DIRR3": close}),
                              pd.Series({"DIRR3": 3e8}), actions, min_market_cap=0, min_adtv=0)
    return sel.iloc[0]["market_cap"]


def test_copied_pre_split_count_gets_the_split_after_its_fre():
    # ITR 2025-09-30 carries the 173.5m of an FRE filed 2025-05 (before the 1:3)
    mcap = _dirr_mcap("2026-03-31", ("DIRR", "2025-09-30", "2025-11-10", 1, "ITR", 1e6, 2e6,
                                     173.5e6, "2025-05-30"), 13.0)
    assert mcap == pytest.approx(173.5e6 * 3 * 13.0)


def test_copied_post_split_count_is_not_split_again():
    # ITR 2025-06-30 filed after an FRE (2025-08-20) that already states 520.5m
    mcap = _dirr_mcap("2025-09-30", ("DIRR", "2025-06-30", "2025-08-25", 1, "ITR", 1e6, 2e6,
                                     520.5e6, "2025-08-20"), 13.0)
    assert mcap == pytest.approx(520.5e6 * 13.0)


def _b3(label, factor, isin="BRVIVTACNOR0", day="11/04/2025"):
    return {"label": label, "factor": factor, "isinCode": isin, "lastDatePrior": day}


def test_same_day_desdobramento_and_grupamento_net_into_one_split():
    # VIVT 2025: 7900% desdobramento (80x) + 0.025 grupamento on one date = 1:2
    _, stock_df, skipped = parse_stock_dividends([_b3("DESDOBRAMENTO", "7900,00"),
                                                  _b3("GRUPAMENTO", "0,025")])
    assert skipped.empty
    assert stock_df[["action_type", "factor"]].values.tolist() == [["STOCK_SPLIT", 2.0]]
    # and the netted factor is what validation sees: kept, moved to the ex bar
    px = _prices("BRVIVTACNOR0", [50, 50, 50, 50, 50, 25.2, 25.0, 25.1])
    actions = stock_df.assign(ex_date="2025-08-08")
    out = validate_split_actions_against_prices(actions, px)
    assert out[["ex_date", "factor"]].astype(str).values.tolist() == [["2025-08-11", "2.0"]]


def test_extreme_bound_is_on_the_share_ratio_not_the_percentage():
    _, kept, _ = parse_stock_dividends([_b3("DESDOBRAMENTO", "7900")])     # 80x: real
    assert kept.iloc[0]["factor"] == pytest.approx(80.0)
    _, kept, skipped = parse_stock_dividends([_b3("DESDOBRAMENTO", "200000")])  # 2001x
    assert kept.empty and skipped.iloc[0]["reason"] == "extreme_factor"


def test_bank_and_insurer_net_debt_is_null(mem_conn):
    for cnpj, sector in (("1", "Bancos"), ("2", "Seguradoras e Corretoras"),
                         ("3", "Emp. Adm. Part. - Intermediação Financeira"), ("4", "Construção Civil")):
        mem_conn.execute("INSERT INTO cvm_companies (cnpj, cvm_code, company_name, sector) "
                         "VALUES (?, ?, ?, ?)", (cnpj, int(cnpj), cnpj, sector))
        mem_conn.execute("INSERT INTO fundamentals_pit (filing_id, cnpj, period_end, filing_date, "
                         "doc_type, net_debt) VALUES (?, ?, '2024-12-31', '2025-03-01', 'DFP', 7e8)",
                         (cnpj, cnpj))
    blank_financial_net_debt(mem_conn)
    got = dict(mem_conn.execute("SELECT cnpj, net_debt FROM fundamentals_pit").fetchall())
    assert got == {"1": None, "2": None, "3": None, "4": 7e8}
