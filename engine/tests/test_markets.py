"""The market registry, and what it keeps from being applied to the wrong market.

Most of these pin a *refusal*: B3-calibrated machinery that silently corrupts
another market's data if it runs there.
"""
from __future__ import annotations

import pandas as pd
import pytest

from backtests.core import markets
from backtests.core.strategy_base import (
    dedup_target_weights, keep_most_liquid_per_root,
)


def test_the_b3_lot_filter_is_the_predicate_the_loaders_have_always_used():
    """Pinned character-for-character. advise_weights used to carry a hand-copied
    duplicate of this string with a comment warning that the two would drift;
    this is the test that makes deleting that copy safe."""
    assert markets.MARKETS["B3"].ticker_sql == (
        "((LENGTH(ticker) = 5 AND SUBSTR(ticker, 5, 1) IN ('3', '4', '5', '6'))"
        " OR (LENGTH(ticker) = 6 AND SUBSTR(ticker, 5, 2) = '11'"
        " AND ticker NOT IN ('IBOV11', 'SMLL11')))"
    )


def test_the_b3_universe_refuses_the_index_lines():
    """IBOV11 / SMLL11 are B3's index lines (an "IND" ISIN, in points), not units.
    IBOV11 out-traded VALE3 twelvefold, so every liquidity-weighted book held it,
    and its quotation factor went 1 -> 100 on 2025-03-05: a -99% month for any
    book that did. Units, ETFs and FIIs ending in 11 stay in; BDRs stay out."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE prices (ticker TEXT)")
    names = ["PETR4", "VALE3", "TAEE11", "BOVA11", "KNRI11", "IBOV11", "SMLL11", "PETR34", "ITSA2"]
    conn.executemany("INSERT INTO prices VALUES (?)", [(t,) for t in names])
    kept = {r[0] for r in conn.execute(
        f"SELECT ticker FROM prices WHERE {markets.MARKETS['B3'].ticker_sql}")}
    assert kept == {"PETR4", "VALE3", "TAEE11", "BOVA11", "KNRI11"}


def test_an_unknown_market_raises_rather_than_defaulting():
    """A typo in a stored strategy spec must not quietly run against B3 and hand
    back a Brazilian result under a US name."""
    with pytest.raises(ValueError, match="Unknown market"):
        markets.get("USA")


def test_a_missing_market_is_b3():
    assert markets.get(None).key == "B3"
    assert markets.get("").key == "B3"
    assert markets.get("crypto").key == "CRYPTO"


def test_split_detection_is_b3_only():
    """The detector fires on a persistent, calm-volume overnight repricing by a
    clean ratio. On B3 that is an unrecorded grupamento; in crypto it is a
    Tuesday, and letting it run would silently rewrite price history."""
    assert markets.MARKETS["B3"].detect_splits
    assert not markets.MARKETS["CRYPTO"].detect_splits
    assert not markets.MARKETS["US"].detect_splits


def test_crypto_glitch_thresholds_are_not_b3s():
    """has_glitch masks a name whose return crossed +100%/-45% in the lookback.
    Applied to crypto with B3's numbers it masks essentially the whole universe,
    permanently."""
    b3, crypto = markets.MARKETS["B3"], markets.MARKETS["CRYPTO"]
    assert crypto.glitch_up > b3.glitch_up
    assert crypto.glitch_down < b3.glitch_down


def test_crypto_declares_no_fundamentals():
    """There are no filings. validate_spec turns this into a rejection rather
    than handing a spec silently empty f_* frames."""
    assert not markets.MARKETS["CRYPTO"].fundamentals
    assert markets.MARKETS["B3"].fundamentals


# ── share-class roots, where a fixed 4-char prefix is actively wrong ───────────

def test_b3_share_classes_still_collapse_to_one_name():
    scores = pd.Series({"PETR3": 1.0, "PETR4": 2.0, "VALE3": 3.0})
    adtv = pd.Series({"PETR3": 10.0, "PETR4": 99.0, "VALE3": 50.0})
    kept = set(keep_most_liquid_per_root(scores, adtv).index)
    assert kept == {"PETR4", "VALE3"}


def test_us_share_classes_are_not_merged_by_prefix():
    """GOOG and GOOGL share four characters and are two tradable lines. Merging
    them would silently halve a book's holdings."""
    scores = pd.Series({"GOOG": 1.0, "GOOGL": 2.0})
    adtv = pd.Series({"GOOG": 10.0, "GOOGL": 99.0})
    kept = keep_most_liquid_per_root(scores, adtv, market="US")
    assert set(kept.index) == {"GOOG", "GOOGL"}


def test_crypto_pairs_are_not_merged_by_prefix():
    """BTCUSDT and BTCUSDC share four characters and are unrelated books."""
    scores = pd.Series({"BTCUSDT": 1.0, "BTCUSDC": 2.0})
    adtv = pd.Series({"BTCUSDT": 10.0, "BTCUSDC": 99.0})
    assert len(keep_most_liquid_per_root(scores, adtv, market="CRYPTO")) == 2


def test_the_global_dedup_is_a_no_op_off_b3():
    """dedup_target_weights runs on EVERY strategy's weights unconditionally, so
    getting this wrong would move weight in every non-B3 backtest."""
    idx = pd.to_datetime(["2020-01-31"])
    tw = pd.DataFrame({"BTCUSDT": [0.5], "BTCUSDC": [0.5]}, index=idx)
    adtv = pd.DataFrame({"BTCUSDT": [10.0], "BTCUSDC": [99.0]}, index=idx)
    out = dedup_target_weights(tw, adtv, market="CRYPTO")
    assert out.loc[idx[0], "BTCUSDT"] == 0.5
    assert out.loc[idx[0], "BTCUSDC"] == 0.5


def test_the_global_dedup_still_merges_on_b3():
    idx = pd.to_datetime(["2020-01-31"])
    tw = pd.DataFrame({"PETR3": [0.5], "PETR4": [0.5]}, index=idx)
    adtv = pd.DataFrame({"PETR3": [10.0], "PETR4": [99.0]}, index=idx)
    out = dedup_target_weights(tw, adtv, market="B3")
    assert out.loc[idx[0], "PETR4"] == 1.0
    assert out.loc[idx[0], "PETR3"] == 0.0


# ── Tax defaults, which must not silently disagree with taxcore ───────────────

def test_each_market_carries_the_isencao_its_holder_actually_gets():
    """A crypto book scored with the R$20k isenção of ações is scored under taxes
    its holder does not pay — and this bench's whole positioning is that the
    numbers that score a backtest are the numbers that file the IRPF."""
    assert markets.MARKETS["B3"].monthly_sales_exemption == 20_000.0
    assert markets.MARKETS["CRYPTO"].monthly_sales_exemption == 35_000.0
    # Lei 14.754/2023 has no monthly isenção — and no monthly DARF either. None
    # rather than 0.0: a class with no isenção and a class whose isenção is zero
    # behave differently on a period with no sales, and only one of them is real.
    assert markets.MARKETS["US"].monthly_sales_exemption is None


def test_every_market_has_a_rate():
    for spec in markets.MARKETS.values():
        assert 0.0 < spec.tax_rate < 1.0


def test_the_rates_match_the_taxcore_rules_they_are_copied_from():
    """The engine and taxcore run in separate venvs and cannot import each other,
    so these values are a deliberate copy. This test is the seam: it reads
    taxcore's REGRAS off disk and fails when the two drift.

    Skipped rather than failed when taxcore is not on the box — the engine is
    deployed on its own, and a missing sibling is not a wrong number.
    """
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[2]
           / "taxcore" / "scripts" / "apuracao.py")
    if not src.exists():
        pytest.skip("taxcore not present alongside the engine")

    text = src.read_text()
    # Read the literals rather than importing: taxcore has its own venv and
    # importing it from here would need pandas resolved in this one.
    # The full Regra call, not just the head of it. The old seam pinned the
    # rate and the isenção — which were never the fields that diverged. `carry`
    # and `periodo` were, and MARKETS' own comments used to admit it: exterior
    # settling at every rebalance instead of once a year, and cripto being given
    # a loss carry ganho de capital does not grant.
    assert '"AÇÃO": Regra(("AÇÃO",), 0.15, 20_000.0, True, "6015", "mensal", grupo="COMUNS")' in text, (
        "taxcore's ações rule moved — update markets.MARKETS['B3']")
    # The other two B3 lines, copied into tax.b3_rules.
    assert '"FII": Regra(("FII",), 0.20, None, True, "6015", "mensal")' in text, (
        "taxcore's FII rule moved — update tax.b3_rules / tax.FII_RATE")
    assert ('"BDR_ETF": Regra(("BDR", "ETF_RV"), 0.15, None, True, "6015", "mensal",\n'
            '                     grupo="COMUNS")') in text, (
        "taxcore's BDR/ETF rule moved — update tax.b3_rules")
    assert '"CRIPTO": Regra(("CRIPTO",), 0.15, 35_000.0, False, "4600", "mensal",' in text, (
        "taxcore's cripto rule moved — update markets.MARKETS['CRYPTO']")
    assert '"EXTERIOR": Regra(("EXTERIOR",), 0.15, None, True, "-", "anual")' in text, (
        "taxcore's exterior rule moved — update markets.MARKETS['US']")
