"""The backtest's B3 apuração is taxcore's: same classes, same lines, same tax.

The engine cannot import taxcore (separate venvs, and the quality gate forbids
it), so `backtests.core.asset_classes` and `tax.b3_rules` are copies. This is the
seam that keeps the copies honest: it runs taxcore's own `asset_class` and
`monthly_apuracao` in a child interpreter — taxcore needs nothing but pandas,
which this venv has — over the same inputs, and compares. Skipped only when
taxcore is not checked out next to the engine.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtests.core import asset_classes, markets, tax

TAXCORE = Path(__file__).resolve().parents[2] / "taxcore"

REPRESENTATIVE = {
    "PETR4": "AÇÃO", "VALE3": "AÇÃO", "ITUB4": "AÇÃO", "B3SA3": "AÇÃO",
    "TAEE11": "AÇÃO",                        # a unit: ações, isenção included
    "BOVA11": "ETF_RV", "IVVB11": "ETF_RV", "DIVO11": "ETF_RV", "SMAL11": "ETF_RV",
    "AAPL34": "BDR", "MSFT34": "BDR", "BIVB39": "BDR",
    "HGLG11": "FII", "MXRF11": "FII",
    "XFIX11": "FII",                         # an ETF of FIIs, apurado as FII
    "ZZZZ11": "FII",                         # unlisted *11: taxcore presumes FII
    "LFTB11": "ETF_RF",
    "CDI_ASSET": "AÇÃO",                     # the engine's cash sleeve, as before
    "IBOV": "AÇÃO",                          # a regime overlay's index column
}


def _taxcore(code: str, payload) -> object:
    if not (TAXCORE / "scripts" / "ativos_b3.py").exists():
        pytest.skip("taxcore not present alongside the engine")
    proc = subprocess.run(
        [sys.executable, "-c", code], input=json.dumps(payload), capture_output=True,
        text=True, cwd=str(TAXCORE), env={**os.environ, "PYTHONPATH": str(TAXCORE)})
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


# ── Classification ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker, cls", sorted(REPRESENTATIVE.items()))
def test_representative_tickers(ticker, cls):
    assert asset_classes.b3_class(ticker) == cls
    assert asset_classes.b3_class(ticker.lower() + ".SA") == cls


def test_lines_follow_taxcore_regras():
    line = tax.b3_line
    assert [line(t) for t in ("PETR4", "TAEE11", "CDI_ASSET", "LFTB11")] == ["AÇÃO"] * 4
    assert [line(t) for t in ("IVVB11", "DIVO11", "BOVA11", "AAPL34")] == ["BDR_ETF"] * 4
    assert line("HGLG11") == "FII"


def test_the_classifier_is_taxcores_on_every_listed_name():
    names = sorted(set(REPRESENTATIVE) | asset_classes.ETF_RV
                   | asset_classes.ETF_RF | asset_classes.UNITS)
    theirs = _taxcore(
        "import json, sys\n"
        "from scripts import ativos_b3 as a\n"
        "names = json.load(sys.stdin)\n"
        "print(json.dumps({'cls': {n: a.asset_class(n) for n in names},\n"
        "  'lists': [sorted(a.ETF_RV), sorted(a.ETF_RF), sorted(a.UNITS),\n"
        "            list(a.BDR_SUFFIXES), sorted(a.ETF_FII)]}))", names)
    assert theirs["cls"] == {n: asset_classes.b3_class(n) for n in names}
    # And the lists themselves: a name taxcore adds must be added here too.
    assert theirs["lists"][:4] == [sorted(asset_classes.ETF_RV),
                                   sorted(asset_classes.ETF_RF),
                                   sorted(asset_classes.UNITS),
                                   list(asset_classes.BDR_SUFFIXES)]
    assert all(asset_classes.b3_class(t) == "FII" for t in theirs["lists"][4])


# ── The apuração ──────────────────────────────────────────────────────────────

_SAMPLE = {"AÇÃO": "PETR4", "ETF_RV": "BOVA11", "BDR": "AAPL34", "FII": "HGLG11"}


def _months(seed: int, n: int = 36) -> list[dict]:
    """Random months straddling the R$20k ações line, with losses and gains in
    every class, so carries build, cross classes and get spent."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        row = {"year": 2020 + i // 12, "month": i % 12 + 1}
        for cls in _SAMPLE:
            gross = 0.0 if rng.random() < 0.2 else float(rng.uniform(1_000, 40_000))
            row[f"{cls}_gross"] = gross
            row[f"{cls}_pnl"] = round(gross * float(rng.uniform(-0.4, 0.5)), 2)
        out.append(row)
    return out


def _engine_irs(months: list[dict]) -> tuple[list[float], float]:
    s = markets.resolve_tax("B3")
    ledger = tax.market_ledger(s["tax_rate"], s["monthly_sales_exemption"],
                               s["tax_period"], s["loss_carry"], s["asset_classes"])
    dues = []
    for m in months:
        # One sale per class: gross sold, gain = gross - cost.
        positions = {t: {"cost_basis": m[f"{c}_gross"] - m[f"{c}_pnl"],
                         "current_value": m[f"{c}_gross"]} for c, t in _SAMPLE.items()}
        sales = {t: m[f"{c}_gross"] for c, t in _SAMPLE.items()}
        dues.append(ledger.accrue(pd.Timestamp(m["year"], m["month"], 15), sales, positions))
    dues.append(ledger.close())
    return dues[1:], ledger.carry


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_ledger_is_taxcores_monthly_apuracao(seed):
    months = _months(seed)
    theirs = _taxcore(
        "import json, sys\n"
        "import pandas as pd\n"
        "from scripts.apuracao import monthly_apuracao\n"
        "rows = monthly_apuracao(pd.DataFrame(json.load(sys.stdin)),\n"
        "                        classes=('AÇÃO', 'FII', 'BDR_ETF'))\n"
        "ir, carry = {}, {}\n"
        "for r in rows:\n"
        "    k = (r['year'], r['month'])\n"
        "    ir[str(k)] = ir.get(str(k), 0.0) + r['ir']\n"
        "    carry[r['classe']] = r['carry']\n"
        "print(json.dumps({'ir': ir, 'carry': carry['AÇÃO'] + carry['FII']}))", months)
    ours, carry = _engine_irs(months)
    expected = [theirs["ir"][str((m["year"], m["month"]))] for m in months]
    np.testing.assert_allclose(ours, expected, rtol=1e-9, atol=1e-6)
    assert carry == pytest.approx(theirs["carry"], abs=1e-6)
    assert sum(expected) > 0          # the comparison is not between two zeros


# ── What changed, in the cases that motivated it ──────────────────────────────

def _b3_ledger() -> tax.Ledger:
    return tax.market_ledger(0.15, 20_000.0, tax.MONTHLY, tax.FORWARD, "B3")


def _settle(ledger, sales_pnl: dict) -> float:
    """One month of sales {ticker: (gross, pnl)}, settled."""
    positions = {t: {"cost_basis": g - p, "current_value": g}
                 for t, (g, p) in sales_pnl.items()}
    ledger.accrue(pd.Timestamp("2024-01-31"), {t: g for t, (g, _) in sales_pnl.items()},
                  positions)
    return ledger.close()


def test_an_etf_sale_under_20k_is_taxed_and_a_stock_sale_is_not():
    assert _settle(_b3_ledger(), {"IVVB11": (10_000.0, 1_000.0)}) == pytest.approx(150.0)
    assert _settle(_b3_ledger(), {"PETR4": (10_000.0, 1_000.0)}) == 0.0
    # Nor do ETF sales count towards the ações R$20k.
    assert _settle(_b3_ledger(), {"PETR4": (15_000.0, 1_000.0),
                                  "BOVA11": (15_000.0, 1_000.0)}) == pytest.approx(150.0)


def test_an_etf_loss_shelters_an_acao_gain_but_a_fii_loss_does_not():
    comuns = _settle(_b3_ledger(), {"PETR4": (30_000.0, 2_000.0),
                                    "IVVB11": (5_000.0, -1_500.0)})
    assert comuns == pytest.approx(0.15 * 500.0)
    fii = _b3_ledger()
    assert _settle(fii, {"PETR4": (30_000.0, 2_000.0), "HGLG11": (5_000.0, -1_500.0)}) \
        == pytest.approx(0.15 * 2_000.0)
    assert fii.carry == pytest.approx(1_500.0)   # waits for a FII gain
    assert _settle(_b3_ledger(), {"HGLG11": (5_000.0, 1_000.0)}) == pytest.approx(200.0)


def test_a_fii_write_off_lands_on_the_fii_line():
    ledger = _b3_ledger()
    positions = {"HGLG11": {"cost_basis": 1_000.0, "current_value": 0.0},
                 "PETR4": {"cost_basis": 30_000.0, "current_value": 32_000.0}}
    ledger.realise_write_offs(positions)
    assert "HGLG11" not in positions
    ledger.accrue(pd.Timestamp("2024-01-31"), {"PETR4": 32_000.0}, positions)
    assert ledger.close() == pytest.approx(0.15 * 2_000.0)
    assert ledger.carry == pytest.approx(1_000.0)


@pytest.mark.parametrize("market, names", [
    ("US", ("AAA", "BBB")), ("CRYPTO", ("ETHUSDT", "SOLUSDT")), ("B3", ("PETR4", "VALE3"))])
def test_simultaneous_write_offs_book_as_one_sum(market, names):
    """Two names wiped out on one rebalance add to the period's loss as one sum,
    L + (a + b), as the single-line ledger always did — not (L + a) + b, which
    differs in the last bits and broke the byte-for-byte promise for US, crypto
    and ações-only books."""
    s = markets.resolve_tax(market)
    ledger = tax.market_ledger(s["tax_rate"], s["monthly_sales_exemption"],
                               s["tax_period"], s["loss_carry"], s["asset_classes"])
    line = next(iter(ledger.rules))
    ledger.realise_write_offs({"OLD": {"cost_basis": 0.1, "current_value": 0.0}})
    positions = {names[0]: {"cost_basis": 0.2, "current_value": 0.0},
                 names[1]: {"cost_basis": 0.3, "current_value": 0.0}}
    ledger.realise_write_offs(positions)
    assert positions == {}
    assert (0.1 + 0.2) + 0.3 != 0.1 + (0.2 + 0.3)     # the case is a real one
    assert ledger._totals[line][2] == 0.1 + (0.2 + 0.3)


def test_a_book_of_acoes_and_cdi_scores_exactly_as_before():
    """Bit for bit: the per-class ledger only moves books holding ETFs, BDRs or
    FIIs. Everything else lands on the ações line, which is the old rule."""
    rng = np.random.default_rng(9)
    old = tax.Ledger(tax.Rule(rate=0.15, exemption=20_000.0))
    new = _b3_ledger()
    for i in range(48):
        positions = {t: {"cost_basis": float(rng.uniform(1e3, 2e4)),
                         "current_value": float(rng.uniform(1e3, 2e4))}
                     for t in ("PETR4", "VALE3", "TAEE11", "CDI_ASSET")}
        sales = {t: p["current_value"] * float(rng.uniform(0, 1)) for t, p in positions.items()}
        when = pd.Timestamp(2020 + i // 12, i % 12 + 1, 28)
        assert old.accrue(when, sales, positions) == new.accrue(when, sales, positions)
        assert old.carry == new.carry
    assert old.close() == new.close()


def test_one_ledger_cannot_mix_periods():
    with pytest.raises(ValueError):
        tax.Ledger({"a": tax.Rule(0.15, None), "b": tax.Rule(0.15, None, tax.ANNUAL)})
    with pytest.raises(ValueError):
        tax.market_ledger(0.15, None, tax.MONTHLY, tax.FORWARD, "XX")
