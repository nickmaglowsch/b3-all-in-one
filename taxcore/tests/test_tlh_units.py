"""Unit tests for the TLH planner's decision logic.

`test_tlh_noninteractive.py` drives the whole script over the vendored book, which
proves the CLI contract but can only assert what that one book happens to contain.
The rules underneath — when an isento month spends the carry, how many shares a
partial harvest sells — are exercised here against timelines built line by line, so
each rule can be stated, and broken, on its own.
"""
from __future__ import annotations

import pandas as pd
import pytest

from scripts.tlh_plan import (
    build_result, find_losers, harvest, parse_sells, prompt_sells, realize,
    render_report, walk_history,
)

# 2020: always in the past, whenever this suite runs.
PAST = "2020-03-15"
EARLIER = "2020-01-10"


def timeline(*rows) -> pd.DataFrame:
    """(ticker, date, kind, qty, amount) tuples -> the frame walk_history reads."""
    return pd.DataFrame([
        {"ticker": t, "date": pd.Timestamp(d), "kind": k, "qty": q, "amount": a}
        for t, d, k, q, a in rows
    ])


def losers(*rows) -> pd.DataFrame:
    """(ticker, qty, avg_cost, price) tuples -> the frame harvest() consumes."""
    return pd.DataFrame([
        {"ticker": t, "qty": q, "avg_cost": c, "price": p,
         "unrealized_pnl": q * (p - c), "max_loss": q * (p - c)}
        for t, q, c, p in rows
    ])


def quotes(**by_ticker) -> pd.DataFrame:
    return pd.DataFrame({"ticker": list(by_ticker), "price": list(by_ticker.values())})


# --------------------------------------------------------------------------
# walk_history — the carry, month by month
# --------------------------------------------------------------------------

def test_a_loss_in_an_isento_month_still_builds_carry():
    """Under R$20k the month owes nothing, but the loss is still a loss: it has to
    reach the carryforward or it is simply forfeited."""
    carry, _ = walk_history(timeline(
        ("VALE3", EARLIER, "buy", 100, 10_000.0),
        ("VALE3", PAST, "sell", 100, 5_000.0),   # gross 5k <= 20k, pnl -5k
    ))
    assert carry["AÇÃO"] == pytest.approx(5_000.0)


def test_an_isento_gain_does_not_spend_the_carry():
    """The R$20k exemption is not a deduction — a tax-free profit has to leave the
    accumulated loss intact for a month that actually owes something."""
    carry, _ = walk_history(timeline(
        ("VALE3", EARLIER, "buy", 100, 10_000.0),
        ("VALE3", "2020-02-05", "sell", 100, 5_000.0),   # -5k carry, isento month
        ("PETR4", "2020-04-01", "buy", 100, 1_000.0),
        ("PETR4", "2020-05-04", "sell", 100, 5_000.0),   # +4k gain, gross 5k: isento
    ))
    assert carry["AÇÃO"] == pytest.approx(5_000.0)


def test_a_gain_past_the_limit_spends_the_carry_it_needs_and_no_more():
    carry, _ = walk_history(timeline(
        ("VALE3", EARLIER, "buy", 100, 10_000.0),
        ("VALE3", "2020-02-05", "sell", 100, 5_000.0),          # -5k carry
        ("PETR4", "2020-04-01", "buy", 1_000, 10_000.0),
        ("PETR4", "2020-05-04", "sell", 1_000, 32_000.0),       # +22k, gross > 20k
    ))
    assert carry["AÇÃO"] == pytest.approx(0.0)   # the 5k is consumed, not overdrawn


def test_fiis_have_no_exemption_so_even_a_small_gain_spends_the_carry():
    carry, _ = walk_history(timeline(
        ("HGLG11", EARLIER, "buy", 100, 10_000.0),
        ("HGLG11", "2020-02-05", "sell", 100, 7_000.0),   # -3k carry
        ("KNRI11", "2020-04-01", "buy", 100, 5_000.0),
        ("KNRI11", "2020-05-04", "sell", 100, 6_000.0),   # +1k, far under 20k
    ))
    assert carry["FII"] == pytest.approx(2_000.0)
    assert carry["AÇÃO"] == pytest.approx(0.0)   # the two buckets never cross


def test_the_current_month_is_reported_as_mtd_and_not_folded_into_the_carry():
    """The split is the whole point: this month is still open, so its result is an
    input to the proposal rather than settled history."""
    today = pd.Timestamp.today().normalize()
    carry, mtd = walk_history(timeline(
        ("VALE3", EARLIER, "buy", 100, 10_000.0),
        ("VALE3", today.isoformat(), "sell", 100, 4_000.0),   # -6k, this month
    ))
    assert carry["AÇÃO"] == pytest.approx(0.0)
    assert mtd["AÇÃO"]["pnl"] == pytest.approx(-6_000.0)
    assert mtd["AÇÃO"]["gross"] == pytest.approx(4_000.0)


def test_a_future_dated_sell_is_ignored_rather_than_taxed_early():
    future = (pd.Timestamp.today().normalize() + pd.DateOffset(months=2)).replace(day=1)
    carry, mtd = walk_history(timeline(
        ("VALE3", EARLIER, "buy", 100, 10_000.0),
        ("VALE3", future.isoformat(), "sell", 100, 30_000.0),
    ))
    assert carry["AÇÃO"] == pytest.approx(0.0)
    assert mtd["AÇÃO"]["gross"] == pytest.approx(0.0)


def test_a_sell_with_nothing_held_is_not_a_hundred_percent_profit():
    """An orphan sell — position opened outside the timeline — would otherwise book
    the whole proceeds as gain against a zero cost basis, and eat the carry with it."""
    carry, _ = walk_history(timeline(
        ("VALE3", EARLIER, "buy", 100, 10_000.0),
        ("VALE3", "2020-02-05", "sell", 100, 5_000.0),    # -5k carry
        ("PETR4", PAST, "sell", 100, 30_000.0),           # never bought
    ))
    assert carry["AÇÃO"] == pytest.approx(5_000.0)


def test_an_amortizacao_lowers_the_cost_basis_but_never_below_zero():
    today = pd.Timestamp.today().normalize()
    _, mtd = walk_history(timeline(
        ("KNCR11", EARLIER, "buy", 100, 10_000.0),
        ("KNCR11", "2020-02-01", "amort", 0, 25_000.0),   # amortized past the cost
        ("KNCR11", today.isoformat(), "sell", 100, 1_000.0),
    ))
    # Basis floored at zero, so the sale is pure gain. A negative basis would have
    # reported +16k on a position that only ever cost 10k.
    assert mtd["FII"]["pnl"] == pytest.approx(1_000.0)


def test_bonus_shares_raise_the_quantity_without_raising_the_cost():
    carry, _ = walk_history(timeline(
        ("HGLG11", EARLIER, "buy", 100, 10_000.0),
        ("HGLG11", "2020-02-01", "qty_event", 100, 0.0),   # 100 -> 200, free
        ("HGLG11", PAST, "sell", 200, 9_000.0),
    ))
    # 200 shares at an average of 50, sold at 45: a 1k loss. Ignoring the event
    # would have matched 100 shares at 100 against a 45 print and booked -5.5k.
    assert carry["FII"] == pytest.approx(1_000.0)


# --------------------------------------------------------------------------
# harvest — the greedy loser walk
# --------------------------------------------------------------------------

def test_nothing_is_sold_to_erase_a_gain_of_pennies():
    assert harvest(-0.50, losers(("VALE3", 100, 20.0, 10.0))) == []


def test_positions_that_do_not_reach_the_target_are_taken_whole_then_topped_up():
    plan = harvest(-1_000.0, losers(
        ("ABEV3", 100, 10.0, 6.0),     # -400 in full: not enough on its own
        ("VALE3", 100, 20.0, 10.0),    # -1000 in full: would overshoot
    ))
    assert [(p["ticker"], p["qty"], p["partial"]) for p in plan] == [
        ("ABEV3", 100, False), ("VALE3", 60.0, True)]
    assert sum(p["realized_loss"] for p in plan) == pytest.approx(-1_000.0)


def test_a_partial_rounds_down_to_whole_shares_and_undershoots_the_target():
    """Rounding up would realize more loss than the gain being offset, dumping the
    excess into the carryforward for no reason."""
    (p,) = harvest(-1_000.0, losers(("VALE3", 1_000, 20.0, 13.0)))
    assert p["partial"] and p["qty"] == 142            # 142.85 shares needed
    assert p["realized_loss"] == pytest.approx(-994.0)
    assert p["realized_loss"] > -1_000.0


def test_a_fractional_position_is_skipped_rather_than_sold_as_zero_shares():
    """floor() on half a share is zero, and an order for zero shares is not a plan."""
    assert harvest(-10.0, losers(("VALE3F", 0.5, 100.0, 10.0))) == []


def test_the_plan_stops_short_and_says_so_when_the_losses_run_out():
    plan = harvest(-5_000.0, losers(("VALE3", 100, 20.0, 10.0)))
    assert [p["qty"] for p in plan] == [100]
    assert sum(p["realized_loss"] for p in plan) == pytest.approx(-1_000.0)


# --------------------------------------------------------------------------
# find_losers / realize — reading the book against today's quotes
# --------------------------------------------------------------------------

PM_NOW = {
    "PETR4": (100.0, 5_000.0),     # avg 50, a gainer at 60
    "VALE3": (100.0, 20_000.0),    # avg 200, a loser at 150
    "ABEV3": (100.0, 1_500.0),     # avg 15, a loser at 10
    "HGLG11": (100.0, 12_000.0),   # avg 120, a FII loser at 100
}
PRICES = quotes(PETR4=60.0, VALE3=150.0, ABEV3=10.0, HGLG11=100.0)


def test_only_losers_of_the_requested_bucket_are_offered_worst_first():
    df = find_losers(PM_NOW, PRICES, "AÇÃO", exclude=set())
    assert list(df["ticker"]) == ["VALE3", "ABEV3"]   # -5000 before -500
    assert df.loc[0, "unrealized_pnl"] == pytest.approx(-5_000.0)


def test_an_excluded_ticker_is_never_offered_as_a_harvest_candidate():
    df = find_losers(PM_NOW, PRICES, "AÇÃO", exclude={"VALE3"})
    assert list(df["ticker"]) == ["ABEV3"]


def test_no_candidates_returns_an_empty_frame_rather_than_raising():
    assert find_losers(PM_NOW, PRICES, "AÇÃO", exclude={"VALE3", "ABEV3"}).empty


def test_a_position_with_no_quote_is_skipped_instead_of_priced_at_zero():
    """A missing quote read as 0 would make every unpriced holding the biggest
    loser in the book and put it at the top of the sell list."""
    pm = {**PM_NOW, "XPTO3": (100.0, 50_000.0)}
    assert "XPTO3" not in set(find_losers(pm, PRICES, "AÇÃO", exclude=set())["ticker"])


def test_realize_prices_the_proposed_sells_off_the_average_cost():
    (s,) = realize([("PETR4", 40.0)], PM_NOW, PRICES)
    assert s["avg_cost"] == pytest.approx(50.0)
    assert s["pnl"] == pytest.approx(40 * (60.0 - 50.0))
    assert s["sell_value"] == pytest.approx(2_400.0)
    assert s["cls"] == "AÇÃO"


def test_realize_drops_the_unquotable_sell_and_keeps_the_rest():
    pm = {**PM_NOW, "XPTO3": (100.0, 1_000.0)}
    out = realize([("XPTO3", 10.0), ("PETR4", 10.0)], pm, PRICES)
    assert [s["ticker"] for s in out] == ["PETR4"]


# --------------------------------------------------------------------------
# parse_sells — the platform's trust boundary
# --------------------------------------------------------------------------

def test_blank_tokens_between_commas_are_tolerated():
    assert parse_sells(["PETR4:10,,VALE3:5"], PM_NOW) == [("PETR4", 10.0), ("VALE3", 5.0)]


def test_a_lowercase_ticker_still_matches_the_book():
    assert parse_sells(["petr4:10"], PM_NOW) == [("PETR4", 10.0)]


@pytest.mark.parametrize("spec", ["PETR4:0", "PETR4:-5"])
def test_a_non_positive_quantity_is_rejected(spec):
    with pytest.raises(ValueError, match="> 0"):
        parse_sells([spec], PM_NOW)


# --------------------------------------------------------------------------
# build_result / render_report — what the callers actually read
# --------------------------------------------------------------------------

def _decision(**over):
    d = {"status": "TLH recomendado", "rationale": "porque sim",
         "gross_after": 30_000.0, "pnl_after": 5_000.0, "carry_used": 0.0,
         "target_loss": -5_000.0, "final_gross": 40_000.0, "final_pnl": 0.0,
         "ir": 0.0}
    return {**d, **over}


def test_overharvesting_shows_up_as_carry_gained_not_as_carry_spent():
    """carry_used only ever counts the carry a plan consumes. A plan that harvests
    past zero *adds* to the carryforward, and carry_after is the only field that
    says so."""
    result = build_result(
        [], {"AÇÃO": 1_000.0, "FII": 0.0},
        {"AÇÃO": {"gross": 0.0, "pnl": 0.0}, "FII": {"gross": 0.0, "pnl": 0.0}},
        [], [], {"AÇÃO": _decision(final_pnl=-2_000.0, carry_used=0.0)})
    assert result["buckets"]["AÇÃO"]["carry_after"] == pytest.approx(3_000.0)


def test_the_report_says_so_plainly_when_no_sell_was_proposed():
    md = render_report([], {"AÇÃO": 0.0, "FII": 0.0},
                       {"AÇÃO": {"gross": 0.0, "pnl": 0.0},
                        "FII": {"gross": 0.0, "pnl": 0.0}}, [], [], {})
    assert "Nenhuma venda válida informada" in md
    assert "## Execução" not in md


def test_every_harvest_sell_gets_a_matching_t_plus_one_rebuy_line():
    """Rebuying the same session is a day-trade — 20%, no exemption, separate
    bucket — so the plan is only safe if the rebuy is spelled out as T+1."""
    sells = [{"ticker": "PETR4", "qty": 100.0, "avg_cost": 50.0, "price": 60.0,
              "sell_value": 6_000.0, "pnl": 1_000.0, "cls": "AÇÃO"}]
    plan = harvest(-1_000.0, losers(("VALE3", 100, 20.0, 10.0)))
    md = render_report(sells, {"AÇÃO": 0.0, "FII": 0.0},
                       {"AÇÃO": {"gross": 0.0, "pnl": 0.0},
                        "FII": {"gross": 0.0, "pnl": 0.0}},
                       plan, [], {"AÇÃO": _decision()})
    assert "T+1" in md and "NÃO recomprar no mesmo dia" in md
    # One sell row and one rebuy row for the harvested ticker.
    assert md.count("VALE3") == 2


# --------------------------------------------------------------------------
# prompt_sells — the interactive front door
# --------------------------------------------------------------------------

def answers(monkeypatch, *lines):
    it = iter(lines)
    monkeypatch.setattr("builtins.input", lambda _="": next(it))


def test_the_prompt_reads_pairs_until_a_blank_line(monkeypatch):
    answers(monkeypatch, "PETR4 40", "vale3, 10", "")
    assert prompt_sells(PM_NOW) == [("PETR4", 40.0), ("VALE3", 10.0)]


@pytest.mark.parametrize("bad", [
    "PETR4",             # no quantity
    "PETR4 40 extra",    # too many fields
    "PETR4 muitas",      # quantity is not a number
    "XPTO3 10",          # not in the book
    "PETR4 99999",       # more than is held
])
def test_a_bad_line_is_refused_and_the_prompt_carries_on(monkeypatch, bad, capsys):
    """Typing is interactive: one fat-fingered line has to be re-asked, not
    silently accepted and not fatal to the whole session."""
    answers(monkeypatch, bad, "PETR4 40", "")
    assert prompt_sells(PM_NOW) == [("PETR4", 40.0)]
    assert "⚠" in capsys.readouterr().out


def test_end_of_input_ends_the_prompt(monkeypatch):
    """Piped stdin runs out instead of sending a blank line."""
    def eof(_=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    assert prompt_sells(PM_NOW) == []
