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
from b3_pipeline.cvm_metrics import blank_zero_statements
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
