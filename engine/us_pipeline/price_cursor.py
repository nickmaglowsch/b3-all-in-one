"""The per-symbol price cursor, as an explicit state machine.

`us_price_cursor.status` used to have two values and no rules. Everything that
went wrong was `retry`, retries went to the front of the queue, and nothing ever
left it: a symbol the vendor refuses outright ("Invalid API call" — retrying
cannot help) was re-bought every night, ahead of all new work, so a handful of
bad tickers could spend a 25-a-day budget for good. A vendor answer with no bars
was stored as `ok` with no `last_bar`, which the scheduler reads as "never
priced" and buys again, also every night. And `attempts` was written as 0 by the
one path that wrote it at all.

So the states are named here, and one pure function moves between them:

  ok        priced (or its actions refreshed). The normal state.
  retry     the last attempt failed in a way that may pass. Re-tried ahead of
            new work, but only up to `RETRY_SHARE` of the day's budget, fewest
            failures first.
  no_data   the vendor answered, repeatedly, with an empty series. Re-asked
            once per `NO_DATA_RECHECK_DAYS`, behind everything else.
  dead      the vendor refused the call itself, repeatedly. Never scheduled.

The stored strings are the enum values, so `ok` and `retry` rows written before
this module existed read back unchanged and nothing needs migrating. A symbol is
brought back from `dead` by hand — `UPDATE us_price_cursor SET status = 'retry',
attempts = 0 WHERE symbol = ...` — which is deliberate: the only way out of a
terminal state should be a person deciding the vendor changed its mind.

`attempts` is the length of the current failure streak — failures and empty
answers since the last success — and is what the thresholds below count.
"""

import enum

import pandas as pd

from . import us_seam

# A BadRequest is the vendor's "Error Message" envelope, which is what an
# unknown symbol gets. It goes to `dead` on the third consecutive failure rather
# than the first because which envelope means what is still the vendor's
# documentation, not a captured sample (see `alphavantage._REFUSAL_KEYS`): one
# misread throttle must not write a good symbol off forever. Three is still a
# hard ceiling — three requests per bad ticker, ever, against "every night" — and
# a deploy that broke the call for every symbol is loud long before then,
# because a run where every symbol failed exits non-zero (`total_failure`).
DEAD_AFTER = 3

# One empty answer can be a vendor hiccup; two in a row is the vendor saying it
# does not carry the symbol.
NO_DATA_AFTER = 2

# How long a symbol's corporate actions stay fresh. Splits and dividends are
# rare; re-asking every day would spend two thirds of a free tier's budget on
# answers that have not changed.
ACTIONS_STALE_DAYS = 90

# The cadence an empty symbol is re-asked at. The same as the corporate-actions
# staleness: both are "has anything changed at the vendor", at the same price.
NO_DATA_RECHECK_DAYS = ACTIONS_STALE_DAYS

# Retries may take at most this share of a day's budget (but always at least one
# symbol's worth, or a small budget would never retry anything). Without the cap
# a run of transient failures crowds out every symbol that has no bars at all;
# the retries beyond it are not dropped, they go to the back of the plan.
RETRY_SHARE = 0.5

PRICE_COST = 1
ACTIONS_COST = 2


class CursorStatus(enum.StrEnum):
    OK = "ok"
    RETRY = "retry"
    NO_DATA = "no_data"
    DEAD = "dead"


class Outcome(enum.StrEnum):
    """What one attempt at one symbol produced."""

    BARS = "bars"                # prices bought and written
    ACTIONS = "actions"          # an actions-only refresh that answered
    EMPTY = "empty"              # the vendor answered with no bars
    FAILED = "failed"            # anything that may pass: throttle, timeout, bug
    BAD_REQUEST = "bad_request"  # the vendor refused the call itself


def status_of(row) -> CursorStatus:
    """A stored row's status. NULL and anything unrecognised read as `ok`,
    which is what a row written before this module existed meant by them."""
    try:
        return CursorStatus((row or {}).get("status") or CursorStatus.OK)
    except ValueError:
        return CursorStatus.OK


def _streak(row) -> int:
    if row is None or status_of(row) is CursorStatus.OK:
        return 0
    value = row.get("attempts")
    return 0 if value is None or pd.isna(value) else int(value)


def _failure_status(outcome: Outcome, attempts: int) -> CursorStatus:
    if outcome is Outcome.BAD_REQUEST and attempts >= DEAD_AFTER:
        return CursorStatus.DEAD
    if outcome is Outcome.EMPTY and attempts >= NO_DATA_AFTER:
        return CursorStatus.NO_DATA
    return CursorStatus.RETRY


def next_cursor(prior, symbol: str, outcome: Outcome, observed=None,
                error=None) -> dict:
    """The cursor row one attempt leaves behind. Pure.

    `prior` is the stored row (a dict) or None. `observed` is what the attempt
    established — bar range, adjust state, the fetch stamps — and is carried
    through as given: `upsert_price_cursor` writes only the columns a row has,
    so a failure that observed nothing leaves the stored bar range alone.
    """
    row = {"symbol": symbol, **(observed or {})}
    if outcome in (Outcome.BARS, Outcome.ACTIONS):
        return {**row, "status": CursorStatus.OK.value, "attempts": 0,
                "error": None}
    attempts = _streak(prior) + 1
    if outcome is Outcome.EMPTY and error is None:
        error = "the vendor returned no bars"
    return {**row, "status": _failure_status(outcome, attempts).value,
            "attempts": attempts, "error": (error or "")[:500] or None}


# ── The scheduler ─────────────────────────────────────────────────────────────

def _as_date(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    ts = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(ts) else ts.date()


def _due(stamp, today, days: int) -> bool:
    fetched = _as_date(stamp)
    return fetched is None or (today - fetched).days >= days


def _actions_fresh(row, today, stale_days: int) -> bool:
    """A bulk-priced symbol's only work is its actions; with those fresh it has
    none. Asked before `_classify` because the cursor row an actions-only
    refresh leaves has no `last_bar`, which `_classify` reads as never priced —
    and `us_seam.demote_bulk_priced` turns "never priced" into "buy the actions",
    so the same few symbols had their actions re-bought every single run."""
    return row is not None and not _due(row.get("actions_fetched_at"), today,
                                        stale_days)


def _classify(symbol, row, today, stale_days: int):
    """(bucket, work item) for one symbol, or None when it has nothing to do."""
    if row is None:
        return "fresh", (symbol, True, True)
    status = status_of(row)
    actions_due = _due(row.get("actions_fetched_at"), today, stale_days)
    if status is CursorStatus.DEAD:
        return None
    if status is CursorStatus.RETRY:
        return "retry", (symbol, True, actions_due)
    if status is CursorStatus.NO_DATA:
        recheck = _due(row.get("prices_fetched_at"), today, NO_DATA_RECHECK_DAYS)
        return ("no_data", (symbol, True, actions_due)) if recheck else None
    last = _as_date(row.get("last_bar"))
    if last is None:
        return "fresh", (symbol, True, True)
    if last < today:
        return "stale_bars", (symbol, True, actions_due)
    return ("stale_actions", (symbol, False, True)) if actions_due else None


def cost(item) -> int:
    return (PRICE_COST if item[1] else 0) + (ACTIONS_COST if item[2] else 0)


def _split_retries(retry: list, budget: int) -> tuple:
    """The retries that fit their share of the budget, and the rest."""
    if budget < 0:
        return retry, []
    cap = max(PRICE_COST + ACTIONS_COST, int(budget * RETRY_SHARE))
    spent = 0
    for i, item in enumerate(retry):
        if spent + cost(item) > cap:
            return retry[:i], retry[i:]
        spent += cost(item)
    return retry, []


def _fit(items, budget: int) -> list:
    """The longest prefix of `items` the budget pays for."""
    plan, spent = [], 0
    for item in items:
        if budget >= 0 and spent + cost(item) > budget:
            # Prices alone still make progress when actions do not fit.
            if item[1] and spent + PRICE_COST <= budget:
                plan.append((item[0], True, False))
            break
        plan.append(item)
        spent += cost(item)
    return plan


def select_work(universe, cursor: pd.DataFrame, *, budget: int, today,
                actions_stale_days: int = ACTIONS_STALE_DAYS,
                bulk_covered=frozenset()) -> list:
    """What this run can afford to do, in the order it should do it.

    Pure — no network, no database — so the policy can be tested directly, in
    the spirit of `external_prices.days_to_fetch`.

    `universe` is the symbols in preference order; the caller does the ordering
    in SQL. The order here is between *kinds* of work:

    1. symbols recorded as needing a retry, fewest failures first, up to
       `RETRY_SHARE` of the budget — the durable retry queue, drained before
       new work, or a transient failure would keep its place at the back of an
       eleven-thousand-symbol line forever; but capped, or a few symbols that
       fail every night would keep every new one out;
    2. symbols with no bars at all, in the caller's preference order — a
       backtest is useless with eight random tickers and useful with the two
       hundred largest;
    3. symbols whose last bar is behind today;
    4. symbols whose corporate actions have gone stale, then `no_data` symbols
       due their periodic re-check;
    5. the retries that did not fit their share.

    `dead` symbols are never planned. Returns `[(symbol, want_prices,
    want_actions)]` costing no more than `budget` requests. A negative budget
    means no ceiling.
    """
    today = pd.Timestamp(today).date()
    # One plain dict per cursor row, not one `pd.Series`. `iterrows` builds a
    # Series per row and this lookup holds every one of them for the length of
    # the run: at ~13,000 US symbols that measured ~19 MB retained and ~2.1 s to
    # build, against ~5 MB and ~0.3 s here, on a scheduler that runs every day in
    # the same worker a backtest shares. Nothing below wants a Series — the row
    # is only ever read by column name.
    state = {} if cursor.empty else {
        r["symbol"]: r for r in cursor.to_dict("records")}

    buckets = {k: [] for k in
               ("retry", "fresh", "stale_bars", "stale_actions", "no_data")}
    for symbol in universe:
        row = state.get(symbol)
        if symbol in bulk_covered and _actions_fresh(row, today, actions_stale_days):
            continue
        work = _classify(symbol, row, today, actions_stale_days)
        if work is not None:
            buckets[work[0]].append(work[1])

    retry = sorted(buckets["retry"],
                   key=lambda item: _streak(state.get(item[0])))
    retry, fresh, stale_bars, stale_actions = us_seam.demote_bulk_priced(
        (retry, buckets["fresh"], buckets["stale_bars"],
         buckets["stale_actions"] + buckets["no_data"]), bulk_covered)
    head, tail = _split_retries(retry, budget)
    return _fit([*head, *fresh, *stale_bars, *stale_actions, *tail], budget)
