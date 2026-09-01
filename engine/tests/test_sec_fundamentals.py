"""SEC companyfacts -> point-in-time fundamentals.

Offline. The fixture is a trimmed copy of the real `companyfacts` shape (cik /
entityName / facts / taxonomy / tag / units / observations with start, end, val,
accn, fy, fp, form, filed), so a change to that shape breaks these rather than
production.

The assertion that matters most is the restatement one: two accessions reporting
the same period must both survive, because that is the only reason this table
exists.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from b3_pipeline import storage
from us_pipeline.sec_parser import parse_companyfacts
from us_pipeline.us_main import ingest_company


def fact(val, end, accn, filed, start=None, form="10-K", fy=2023, fp="FY"):
    f = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp,
         "form": form, "filed": filed}
    if start:
        f["start"] = start
    return f


def doc(**tags):
    """A companyfacts document. Each kwarg is tag -> list of observations."""
    return {
        "cik": 320193, "entityName": "Apple Inc.",
        "facts": {"us-gaap": {t: {"units": {"USD": obs}}
                              for t, obs in tags.items()}},
    }


FY23 = dict(end="2023-09-30", accn="acc-2023", filed="2023-11-03")


# ── the shape ─────────────────────────────────────────────────────────────────

def test_one_row_per_accession_and_period():
    df = parse_companyfacts(doc(
        NetIncomeLoss=[fact(96_995e6, start="2022-10-01", **FY23)],
        Assets=[fact(352_583e6, **FY23)]), "AAPL")
    assert len(df) == 1
    row = df.iloc[0]
    assert row["cik"] == "0000320193" and row["ticker"] == "AAPL"
    assert row["net_income"] == 96_995e6
    assert row["total_assets"] == 352_583e6
    assert row["filing_date"] == "2023-11-03"
    assert row["accn"] == "acc-2023"


def test_values_are_raw_dollars_not_thousands():
    """CVM reports in thousands of BRL and the ratio helpers scale for it. XBRL
    reports units. Getting this wrong makes every US P/E wrong by 1000x."""
    df = parse_companyfacts(doc(
        NetIncomeLoss=[fact(96_995_000_000.0, start="2022-10-01", **FY23)]))
    assert df.iloc[0]["net_income"] == 96_995_000_000.0


# ── the point-in-time property ────────────────────────────────────────────────

def test_a_restatement_is_a_second_row_with_its_own_filing_date():
    """The next year's 10-K reports FY2023 again, revised. Both must survive: a
    backtest dated before the revision has to read the number the market had."""
    df = parse_companyfacts(doc(NetIncomeLoss=[
        fact(96_995e6, start="2022-10-01", **FY23),
        fact(96_000e6, end="2023-09-30", accn="acc-2024", filed="2024-11-01",
             start="2022-10-01", fy=2024),
    ]))
    assert len(df) == 2
    by_filed = df.set_index("filing_date")["net_income"].to_dict()
    assert by_filed["2023-11-03"] == 96_995e6
    assert by_filed["2024-11-01"] == 96_000e6


def test_a_fact_that_cannot_be_placed_in_time_is_dropped():
    """No accession or no filing date means no point in time, and a row with a
    value but no knowable date is worse than no row."""
    df = parse_companyfacts(doc(NetIncomeLoss=[
        {"end": "2023-09-30", "val": 1.0, "form": "10-K", "accn": "a"},   # no filed
        {"val": 2.0, "form": "10-K", "accn": "b", "filed": "2023-01-01"},  # no end
    ]))
    assert df.empty


def test_only_periodic_reports_are_read():
    """An 8-K carries facts but not a comparable set; mixing them makes a
    quarter's numbers depend on whether a prospectus happened to be filed."""
    df = parse_companyfacts(doc(NetIncomeLoss=[
        fact(1.0, end="2023-09-30", accn="a", filed="2023-11-03", form="8-K")]))
    assert df.empty


# ── tag preference and derived metrics ────────────────────────────────────────

def test_the_preferred_revenue_tag_wins_over_its_fallback():
    """Filers report revenue under different concepts depending on when they
    adopted ASC 606. First tag present wins; the rest only fill holes."""
    df = parse_companyfacts(doc(
        RevenueFromContractWithCustomerExcludingAssessedTax=[
            fact(383_285e6, start="2022-10-01", **FY23)],
        Revenues=[fact(1.0, start="2022-10-01", **FY23)]))
    assert df.iloc[0]["revenue"] == 383_285e6


def test_a_fallback_tag_fills_a_hole():
    df = parse_companyfacts(doc(
        Revenues=[fact(100.0, start="2022-10-01", **FY23)]))
    assert df.iloc[0]["revenue"] == 100.0


def test_ebitda_is_operating_income_plus_depreciation():
    df = parse_companyfacts(doc(
        OperatingIncomeLoss=[fact(114_301e6, start="2022-10-01", **FY23)],
        DepreciationDepletionAndAmortization=[
            fact(11_519e6, start="2022-10-01", **FY23)]))
    assert df.iloc[0]["ebitda"] == 114_301e6 + 11_519e6


def test_ebitda_is_null_without_depreciation():
    """Operating income wearing an EBITDA label is a different number, and a
    screen ranking on EV/EBITDA would rank it as if it were the real one."""
    df = parse_companyfacts(doc(
        OperatingIncomeLoss=[fact(114_301e6, start="2022-10-01", **FY23)],
        Assets=[fact(1.0, **FY23)]))
    assert pd.isna(df.iloc[0]["ebitda"])


def test_net_debt_is_total_debt_less_cash():
    df = parse_companyfacts(doc(
        LongTermDebtNoncurrent=[fact(95_281e6, **FY23)],
        LongTermDebtCurrent=[fact(9_822e6, **FY23)],
        CashAndCashEquivalentsAtCarryingValue=[fact(29_965e6, **FY23)]))
    assert df.iloc[0]["net_debt"] == 95_281e6 + 9_822e6 - 29_965e6


def test_net_debt_is_null_when_no_debt_is_reported():
    """"No debt tag" and "zero debt" are different claims. Reporting zero would
    make every filer that omits the tag look deeply net-cash."""
    df = parse_companyfacts(doc(
        CashAndCashEquivalentsAtCarryingValue=[fact(29_965e6, **FY23)],
        Assets=[fact(1.0, **FY23)]))
    assert pd.isna(df.iloc[0]["net_debt"])


def test_a_period_with_no_readable_metric_is_dropped():
    """An accession that reported none of the metrics we read would otherwise
    put a filing date on the plane with no numbers behind it — which forward-
    fills as "the company reported and we know nothing"."""
    df = parse_companyfacts(doc(
        SomeTagWeDoNotRead=[fact(1.0, **FY23)]))
    assert df.empty


def test_an_empty_document_is_an_empty_frame():
    assert parse_companyfacts({"cik": 1, "facts": {}}).empty


# ── through the writer ────────────────────────────────────────────────────────

def test_ingest_writes_facts_and_an_instrument(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "us.sqlite"))
    storage.init_db(conn)
    n = ingest_company(conn, "0000320193", doc(
        NetIncomeLoss=[fact(96_995e6, start="2022-10-01", **FY23)]), "AAPL")
    assert n == 1
    instr = storage.read_sql("SELECT * FROM instruments", conn).iloc[0]
    assert instr["market"] == "US" and instr["symbol"] == "AAPL"
    assert instr["cik"] == "0000320193" and instr["currency"] == "USD"


def test_the_ingest_writes_no_delisting_date(tmp_path):
    """A company that stops filing has not necessarily stopped trading, and
    `delisting_date` is the one column the backtest universe reads. Guessing it
    from the filings would truncate a live name's history."""
    conn = sqlite3.connect(str(tmp_path / "us.sqlite"))
    storage.init_db(conn)
    ingest_company(conn, "0000320193", doc(
        NetIncomeLoss=[fact(96_995e6, start="2022-10-01", **FY23)]), "AAPL")
    assert pd.isna(storage.read_sql(
        "SELECT delisting_date FROM instruments", conn).iloc[0]["delisting_date"])


def test_a_filer_with_no_ticker_lands_under_its_cik(tmp_path):
    """Companies that stopped filing are absent from company_tickers.json — that
    file is a snapshot of who trades today. They still get their facts."""
    conn = sqlite3.connect(str(tmp_path / "us.sqlite"))
    storage.init_db(conn)
    ingest_company(conn, "0000320193", doc(
        NetIncomeLoss=[fact(1.0, start="2022-10-01", **FY23)]), None)
    assert storage.read_sql(
        "SELECT symbol FROM instruments", conn).iloc[0]["symbol"] == "CIK0000320193"


def test_ingest_is_idempotent(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "us.sqlite"))
    storage.init_db(conn)
    payload = doc(NetIncomeLoss=[fact(96_995e6, start="2022-10-01", **FY23)])
    for _ in range(2):
        ingest_company(conn, "0000320193", payload, "AAPL")
    assert storage.read_sql(
        "SELECT COUNT(*) c FROM fundamentals_pit_us", conn).iloc[0]["c"] == 1


# ── read back through the engine's point-in-time loaders ──────────────────────

def test_the_pit_loader_reads_the_us_table(tmp_path):
    """The same `_pit_frame` machinery serves both markets — that reuse is the
    reason the US table mirrors fundamentals_pit column for column."""
    from backtests.core.data import load_all_fundamentals

    db_path = str(tmp_path / "us.sqlite")
    conn = sqlite3.connect(db_path)
    storage.init_db(conn)
    conn.execute(
        "INSERT INTO market_prices (market, symbol, date, close, source)"
        " VALUES ('US','AAPL','2023-12-29', 190.0, 't')")
    conn.execute(
        "INSERT INTO prices (ticker, isin_code, date, close, volume)"
        " VALUES ('PETR4','x','2023-12-29', 30.0, 1.0)")
    ingest_company(conn, "0000320193", doc(
        NetIncomeLoss=[fact(96_995e6, start="2022-10-01", **FY23)]), "AAPL")
    conn.commit()
    conn.close()

    out = load_all_fundamentals(db_path, "2023-01-01", "2023-12-31",
                                market="US")
    assert "AAPL" in out["net_income"].columns


def test_a_restatement_stays_invisible_before_it_was_filed(tmp_path):
    """The property the whole table exists for, read back through the loader a
    strategy actually uses."""
    from backtests.core.data import load_all_fundamentals

    db_path = str(tmp_path / "us.sqlite")
    conn = sqlite3.connect(db_path)
    storage.init_db(conn)
    conn.execute(
        "INSERT INTO prices (ticker, isin_code, date, close, volume)"
        " VALUES ('PETR4','x','2024-12-30', 30.0, 1.0)")
    ingest_company(conn, "0000320193", doc(NetIncomeLoss=[
        fact(96_995e6, start="2022-10-01", **FY23),
        fact(96_000e6, end="2023-09-30", accn="acc-2024", filed="2024-11-01",
             start="2022-10-01", fy=2024),
    ]), "AAPL")
    conn.commit()
    conn.close()

    before = load_all_fundamentals(db_path, "2023-11-01", "2024-01-31",
                                   market="US")["net_income"]
    assert before["AAPL"].dropna().iloc[-1] == 96_995e6

    after = load_all_fundamentals(db_path, "2023-11-01", "2024-12-31",
                                  market="US")["net_income"]
    assert after["AAPL"].dropna().iloc[-1] == 96_000e6


def test_crypto_has_no_fundamentals_table_and_says_so_quietly(tmp_path):
    """validate_spec rejects a crypto spec that asks for f_* signals. A backtest
    that reaches the loader anyway gets empty frames, not a crash."""
    from backtests.core.data import load_all_fundamentals

    db_path = str(tmp_path / "us.sqlite")
    conn = sqlite3.connect(db_path)
    storage.init_db(conn)
    conn.close()
    out = load_all_fundamentals(db_path, "2023-01-01", "2023-12-31",
                                market="CRYPTO")
    assert all(df.empty or df.isna().all().all() for df in out.values())


def test_the_root_broadcast_does_not_run_off_b3(tmp_path):
    """`GOOG`[:4] == `GOOGL`[:4]. On B3 that prefix is a company root; here it
    would put one company's financials on two real, separate companies."""
    from backtests.core.data import load_all_fundamentals

    db_path = str(tmp_path / "us.sqlite")
    conn = sqlite3.connect(db_path)
    storage.init_db(conn)
    conn.execute(
        "INSERT INTO prices (ticker, isin_code, date, close, volume)"
        " VALUES ('PETR4','x','2023-12-29', 30.0, 1.0)")
    ingest_company(conn, "0001652044", doc(
        NetIncomeLoss=[fact(73_795e6, start="2022-10-01", **FY23)]), "GOOGL")
    conn.commit()
    conn.close()

    out = load_all_fundamentals(db_path, "2023-01-01", "2023-12-31",
                                tickers=["GOOG", "GOOGL"], market="US")
    assert "GOOG" not in out["net_income"].columns
