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
        " OR (LENGTH(ticker) = 6 AND SUBSTR(ticker, 5, 2) = '11'))"
    )


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
