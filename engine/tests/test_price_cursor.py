"""The price cursor's states and the one function that moves between them.

Pure: no database, no vendor. What is pinned here is the property the free tier
depends on — that no symbol can hold a slice of a 25-request day forever, and
that nothing the vendor says once is taken as final.
"""
from __future__ import annotations

import datetime

import pandas as pd
import pytest
from b3_pipeline import us_storage
from us_pipeline import price_cursor as pc
from us_pipeline.price_cursor import CursorStatus, Outcome

TODAY = datetime.date(2026, 9, 1)


def _walk(outcomes, prior=None):
    for outcome in outcomes:
        prior = pc.next_cursor(prior, "XYZ", outcome, error="boom")
    return prior


# ── transitions ───────────────────────────────────────────────────────────────

def test_a_failure_counts_an_attempt():
    """`attempts` used to be written as 0 by success and not at all by failure,
    so it never counted anything."""
    row = _walk([Outcome.FAILED, Outcome.FAILED])
    assert row["status"] == CursorStatus.RETRY
    assert row["attempts"] == 2


def test_a_success_resets_the_streak():
    row = _walk([Outcome.FAILED, Outcome.FAILED, Outcome.BARS])
    assert row["status"] == CursorStatus.OK
    assert row["attempts"] == 0 and row["error"] is None


def test_a_refused_call_is_written_off_after_three_refusals():
    """"Retrying cannot help" — and it used to be retried, at the front of the
    queue, every night."""
    assert _walk([Outcome.BAD_REQUEST] * 2)["status"] == CursorStatus.RETRY
    row = _walk([Outcome.BAD_REQUEST] * 3)
    assert row["status"] == CursorStatus.DEAD
    assert row["attempts"] == 3


def test_one_refusal_is_not_enough_to_write_a_symbol_off():
    """Which envelope means what is still the vendor's documentation, not a
    captured sample; a single misread throttle must not kill a good symbol."""
    assert _walk([Outcome.BAD_REQUEST])["status"] == CursorStatus.RETRY


def test_transient_failures_never_make_a_symbol_dead():
    assert _walk([Outcome.FAILED] * 20)["status"] == CursorStatus.RETRY


def test_repeated_empty_answers_become_no_data():
    """It used to be `ok` with no `last_bar`, which reads as never priced and
    was bought again every run."""
    first = pc.next_cursor(None, "XYZ", Outcome.EMPTY)
    assert first["status"] == CursorStatus.RETRY
    assert "no bars" in first["error"]
    second = pc.next_cursor(first, "XYZ", Outcome.EMPTY)
    assert second["status"] == CursorStatus.NO_DATA and second["attempts"] == 2


def test_a_failure_does_not_blank_what_the_cursor_already_knew():
    """`upsert_price_cursor` writes only the columns a row carries, so a failure
    that observed no bar range must not carry one."""
    row = pc.next_cursor({"status": "ok", "last_bar": "2026-08-31"}, "XYZ",
                         Outcome.FAILED, error="timeout")
    assert "last_bar" not in row and "adjust_state" not in row


def test_the_attempt_count_of_an_ok_row_is_not_inherited():
    row = pc.next_cursor({"status": "ok", "attempts": 7}, "XYZ", Outcome.FAILED)
    assert row["attempts"] == 1


def test_rows_written_before_the_enum_read_back_unchanged():
    assert pc.status_of({"status": "ok"}) is CursorStatus.OK
    assert pc.status_of({"status": "retry"}) is CursorStatus.RETRY
    assert pc.status_of({"status": None}) is CursorStatus.OK
    assert pc.status_of(None) is CursorStatus.OK
    assert pc.status_of({"status": "something-else"}) is CursorStatus.OK


def test_errors_are_capped_at_the_column_budget():
    row = pc.next_cursor(None, "XYZ", Outcome.FAILED, error="x" * 2_000)
    assert len(row["error"]) == 500


# ── scheduling ────────────────────────────────────────────────────────────────

def _cursor(**rows):
    return pd.DataFrame([{"symbol": s, **v} for s, v in rows.items()]) \
        if rows else pd.DataFrame(columns=us_storage.CURSOR_COLUMNS)


def test_a_dead_symbol_is_never_planned():
    cursor = _cursor(Z={"status": "dead", "attempts": 3, "last_bar": None})
    assert pc.select_work(["Z"], cursor, budget=-1, today=TODAY) == []


def test_retries_take_a_bounded_share_of_the_budget():
    """Five symbols that fail every night used to take a 15-request day whole,
    ahead of every symbol that has no bars at all."""
    failing = {s: {"status": "retry", "attempts": 1,
                   "actions_fetched_at": None} for s in "VWXYZ"}
    plan = pc.select_work([*"VWXYZ", "NEW"], _cursor(**failing), budget=15,
                          today=TODAY)
    order = [p[0] for p in plan]
    ahead = sum(pc.cost(p) for p in plan[:order.index("NEW")])
    assert ahead <= 15 * pc.RETRY_SHARE


def test_retries_over_their_share_still_use_leftover_budget():
    failing = {s: {"status": "retry", "attempts": 1,
                   "actions_fetched_at": str(TODAY)} for s in "WXYZ"}
    plan = pc.select_work([*"WXYZ"], _cursor(**failing), budget=4, today=TODAY)
    # Nothing else to do: the share is a priority, not a ceiling on its own.
    assert [p[0] for p in plan] == list("WXYZ")


def test_retries_with_the_fewest_failures_go_first():
    cursor = _cursor(OLD={"status": "retry", "attempts": 9},
                     NEW={"status": "retry", "attempts": 1})
    plan = pc.select_work(["OLD", "NEW"], cursor, budget=3, today=TODAY)
    assert plan[0][0] == "NEW"


def test_a_no_data_symbol_waits_for_its_recheck():
    recent = _cursor(E={"status": "no_data", "attempts": 2,
                        "prices_fetched_at": str(TODAY - datetime.timedelta(days=10)),
                        "actions_fetched_at": str(TODAY)})
    assert pc.select_work(["E"], recent, budget=-1, today=TODAY) == []

    due = _cursor(E={"status": "no_data", "attempts": 2,
                     "prices_fetched_at": "2026-01-01",
                     "actions_fetched_at": str(TODAY)})
    assert pc.select_work(["E"], due, budget=-1, today=TODAY) == [
        ("E", True, False)]


def test_a_no_data_recheck_comes_after_every_other_kind_of_work():
    cursor = _cursor(E={"status": "no_data", "attempts": 2,
                        "prices_fetched_at": "2026-01-01",
                        "actions_fetched_at": str(TODAY)},
                     S={"status": "ok", "last_bar": "2026-08-01",
                        "actions_fetched_at": str(TODAY)})
    plan = pc.select_work(["E", "S", "NEW"], cursor, budget=-1, today=TODAY)
    assert [p[0] for p in plan] == ["NEW", "S", "E"]


@pytest.mark.parametrize("status", ["ok", "retry", None])
def test_legacy_rows_schedule_as_before(status):
    cursor = _cursor(A={"status": status, "last_bar": None,
                        "actions_fetched_at": None})
    assert pc.select_work(["A"], cursor, budget=3, today=TODAY) == [
        ("A", True, True)]
