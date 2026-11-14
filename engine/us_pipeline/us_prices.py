"""Daily US bars, bought one symbol at a time under a hard daily ceiling.

Full coverage is three requests a symbol across roughly eleven thousand
symbols. On the free tier's 25 requests a day that is years; on a paid key it is
an afternoon. Both run this module unchanged, so the thing that has to be right
is not throughput but **resumability**: every symbol's outcome is written to
`us_price_cursor` inside the loop, before the next request goes out, so a run
that stops at request 12 of 25 starts at 13 tomorrow rather than at 1.

On a free key this is the **top-up**, not the backfill. Stooq's bulk `d_us_txt`
dump takes the history for nothing and `us_daily` keeps it current, because
"years" is not a throughput problem this module can solve — it is a property of
a per-symbol metered feed. What is left here is the work only a metered key can
do: the symbols those sources do not carry, and the corporate actions that turn
a `split_only` series into a `total_return` one. `select_work` is handed the set
of symbols the free sources already priced and never re-prices them;
`us_seam.demote_bulk_priced` says why that matters and what would break without
it — and that, for those symbols, the actions are stored but not yet applied.

Where each symbol stands is `us_price_cursor`, and its states and transitions
are `price_cursor`'s.

Two conversions happen here and nowhere else.

**Turnover, not share count.** The vendor's `volume` is a number of shares.
`market_prices.volume` is financial volume in the quote currency, which is what
`min_adtv` filters a universe on. Feeding a share count into that filter keeps a
penny stock trading a billion shares and drops Berkshire. So the column stored
is `share_volume * close` — a one-point estimate of the session's turnover,
whose error is bounded by the day's range and is two orders of magnitude below
the resolution the filter needs. The vendor's own number is not destroyed by
this: it is exactly `volume / close`, because that is exactly how it was made.

**BRL.** Through `fx_series.to_brl`, the same call `crypto_main` makes, so
`close_brl`/`adj_close_brl`/`fx_rate` are materialised identically and a day
PTAX cannot cover keeps NULL BRL columns and a NULL rate — visible as
`unconverted_brl` in `GET /market/status` rather than silently priced in dollars.
"""

import datetime
import logging

import pandas as pd
from b3_pipeline import fx_series, market_storage, storage, us_storage

from . import us_adjust, us_seam
from .alphavantage import (
    BadRequest,
    BudgetExhausted,
    Client,
    PremiumOnly,
    RateLimited,
)
from .price_cursor import Outcome, next_cursor, select_work
from .av_parsers import parse_dividends, parse_splits, parse_time_series_daily

logger = logging.getLogger(__name__)

MARKET = "US"
SOURCE = "alphavantage-time-series-daily"

# Requests held back for the listing and benchmark jobs, which share the key.
DEFAULT_RESERVE = 5

# The scheduler (`select_work`) and the cursor's states live in `price_cursor`;
# `select_work` is imported above and stays reachable here, where its callers
# have always found it.


def fetch_prices(client: Client, symbol: str) -> pd.DataFrame:
    """The full daily history for one symbol. One request."""
    return parse_time_series_daily(
        client.get_csv("TIME_SERIES_DAILY", symbol=symbol, outputsize="full"),
        symbol)


def _fetch_one_action(client: Client, function: str, symbol: str, parse):
    """One actions endpoint -> `(frame or None, throttled)`.

    The distinction `us_adjust` rests on: an empty frame is the vendor saying
    "none on record", `None` is the vendor refusing. A premium refusal is
    recorded, not retried into oblivion — if the endpoint is premium on this
    key it will refuse forever, and the symbol's series is honestly split-only.

    A throttle is flagged separately because it is not an answer at all: it
    says nothing about the symbol or the key. Stamping it as one is what used
    to lock a downgraded `adjust_state` in for the 90 days until the actions
    next went stale.
    """
    try:
        return parse(client.get_json(function, symbol=symbol), symbol), False
    except (PremiumOnly, RateLimited) as exc:
        logger.info(f"{symbol}: {function} unavailable ({exc}); "
                    "adjustment will be recorded as partial")
        return None, isinstance(exc, RateLimited)


def _fetch_actions(client: Client, symbol: str) -> tuple:
    """`((splits, throttled), (dividends, throttled))`. Two requests."""
    return (_fetch_one_action(client, "SPLITS", symbol, parse_splits),
            _fetch_one_action(client, "DIVIDENDS", symbol, parse_dividends))


def fetch_actions(client: Client, symbol: str) -> tuple:
    """Splits and dividends for one symbol, `None` where refused. Two requests."""
    (splits, _), (dividends, _) = _fetch_actions(client, symbol)
    return splits, dividends


def to_market_rows(symbol: str, bars: pd.DataFrame, state: str,
                   rates: pd.Series) -> pd.DataFrame:
    """Adjusted bars -> `market_prices` rows, in USD and in BRL."""
    if bars.empty:
        return bars
    out = pd.DataFrame({
        "market": MARKET,
        "symbol": symbol,
        "date": bars["date"],
        "open": bars["open"],
        "high": bars["high"],
        "low": bars["low"],
        "close": bars["close"],
        # Turnover in the quote currency, not a share count. See the module
        # docstring; the vendor's number is recoverable as volume / close.
        "volume": bars["share_volume"].astype(float) * bars["close"].astype(float),
        "split_adj_close": bars["split_adj_close"],
        "adj_close": bars["adj_close"],
        "source": f"{SOURCE}:{state}",
    })
    return fx_series.to_brl(out, rates)


SPLIT, DIVIDEND = "split", "dividend"


def _persist_actions(conn, symbol: str, splits, dividends) -> None:
    """Keep the events so the adjustment can be rebuilt without asking again."""
    rows = []
    for kind, frame, col in ((SPLIT, splits, "ratio"),
                             (DIVIDEND, dividends, "amount")):
        if frame is None or frame.empty:
            continue
        rows.append(pd.DataFrame({"symbol": symbol, "kind": kind,
                                  "ex_date": frame["ex_date"],
                                  "value": frame[col], "source": SOURCE}))
    if rows:
        us_storage.upsert_corporate_actions(conn, pd.concat(rows, ignore_index=True))


def _stored_actions(conn, symbol: str, prior_state):
    """The events this symbol's adjustment was last built from.

    The cursor's recorded state is what says whether the endpoints ever
    answered, so a price-only refresh reproduces the same adjustment instead of
    silently downgrading a total-return series to a raw one. An empty stored set
    under a state of 'total_return' is a symbol with no splits and no dividends,
    which is most of them — and is why the state, not the row count, is the
    thing consulted.

    Stored rows are evidence as well, in one direction only: an event is kept
    only when its endpoint answered. So a kind with rows on file is used even
    under a state that says its endpoint had not answered — which is how an
    actions-only refresh that reached the dividends endpoint upgrades a
    `split_only` series at the next price refresh, rather than never.
    """
    splits = us_storage.load_corporate_actions(conn, symbol, SPLIT).rename(
        columns={"value": "ratio"})
    dividends = us_storage.load_corporate_actions(conn, symbol, DIVIDEND).rename(
        columns={"value": "amount"})
    split_answered = prior_state in (us_adjust.SPLIT_ONLY, us_adjust.TOTAL_RETURN)
    dividend_answered = prior_state == us_adjust.TOTAL_RETURN
    return (splits if split_answered or not splits.empty else None,
            dividends if dividend_answered or not dividends.empty else None)


def _actions(conn, client, symbol, today, prior_state, observed) -> tuple:
    """Buy both actions endpoints; stamp the purchase only if neither throttled.

    A throttled half falls back to the stored events, so a refresh that hit the
    vendor's rate limit rebuilds the adjustment it already had instead of
    downgrading it, and the missing stamp makes the next run ask again.
    """
    (splits, s_throttled), (dividends, d_throttled) = _fetch_actions(client, symbol)
    _persist_actions(conn, symbol, splits, dividends)
    if not (s_throttled or d_throttled):
        observed["actions_fetched_at"] = str(today)
        return splits, dividends
    stored_splits, stored_dividends = _stored_actions(conn, symbol, prior_state)
    return (stored_splits if s_throttled else splits,
            stored_dividends if d_throttled else dividends)


def ingest_symbol(conn, client: Client, symbol: str, rates: pd.Series, *,
                  want_prices: bool, want_actions: bool, today,
                  prior_state=None) -> dict:
    """Fetch, convert and write one symbol.

    Returns what the attempt observed — the cursor columns it established — with
    its `price_cursor.Outcome` under "outcome". Vendor errors propagate; turning
    either into a cursor row is `price_cursor.next_cursor`'s job, not this one's.
    """
    observed = {}
    if want_actions:
        splits, dividends = _actions(conn, client, symbol, today, prior_state,
                                     observed)
    else:
        splits, dividends = _stored_actions(conn, symbol, prior_state)

    if not want_prices:
        return {**observed, "outcome": Outcome.ACTIONS}

    bars = fetch_prices(client, symbol)
    observed["prices_fetched_at"] = str(today)
    if bars.empty:
        logger.warning(f"{symbol}: the vendor returned no bars")
        return {**observed, "outcome": Outcome.EMPTY}

    adjusted, state = us_adjust.apply(bars, splits, dividends)
    market_storage.upsert_market_prices(
        conn, to_market_rows(symbol, adjusted, state, rates))
    observed.update({"first_bar": str(bars["date"].iloc[0]),
                     "last_bar": str(bars["date"].iloc[-1]),
                     "adjust_state": state})
    return {**observed, "outcome": Outcome.BARS}


def _attempt(conn, client, symbol, rates, want_prices, want_actions, today,
             prior) -> tuple:
    """One symbol, start to finish: `(outcome, the cursor row it leaves)`.

    `BudgetExhausted` is not an outcome — nothing about the symbol was learned —
    so it propagates and the caller stops.
    """
    try:
        observed = ingest_symbol(conn, client, symbol, rates,
                                 want_prices=want_prices,
                                 want_actions=want_actions, today=today,
                                 prior_state=(prior or {}).get("adjust_state"))
    except BudgetExhausted:
        raise
    except BadRequest as exc:
        return Outcome.BAD_REQUEST, next_cursor(
            prior, symbol, Outcome.BAD_REQUEST, error=str(exc))
    except Exception as exc:                               # noqa: BLE001
        logger.error(f"{symbol}: {exc}")
        return Outcome.FAILED, next_cursor(
            prior, symbol, Outcome.FAILED, error=str(exc))
    outcome = observed.pop("outcome")
    return outcome, next_cursor(prior, symbol, outcome, observed)


def _universe(conn) -> list:
    """US symbols worth pricing, most useful first.

    Symbols the SEC ingest already has fundamentals for come first: they are the
    ones a fundamental strategy can actually select, so they are the ones whose
    absence makes the plane useless. Test issues and the `CIK##########`
    placeholders are excluded — the first never traded, the second is not a
    ticker any venue would recognise.
    """
    df = storage.read_sql(
        "SELECT i.symbol,"
        "       CASE WHEN i.cik IS NOT NULL THEN 0 ELSE 1 END AS tier"
        "  FROM instruments i"
        " WHERE i.market = ? AND COALESCE(i.is_test_issue, 0) = 0"
        "   AND i.symbol NOT LIKE ?"
        " ORDER BY tier, i.symbol", conn, params=(MARKET, "CIK%"))
    return list(df["symbol"]) if not df.empty else []


def run_pipeline(db_path=None, limit: int = 0, today=None,
                 reserve: int = DEFAULT_RESERVE) -> dict:
    """Spend today's request budget on the most useful bars still missing."""
    today = today or datetime.date.today()
    logger.info(f"US prices -> {storage.describe_target(db_path)}")
    with storage.get_connection(db_path) as conn:
        storage.init_db(conn)
        client = Client(conn, today=today, reserve=reserve)

        fx = fx_series.build(today)
        if not fx.empty:
            storage.upsert_external_prices(conn, fx)
        rates = fx_series.load(conn)

        budget = client.remaining if limit <= 0 else min(
            limit, client.remaining if client.remaining >= 0 else limit)
        cursor_df = us_storage.load_price_cursor(conn)
        # Asked of the price table, not of a flag: `market_prices.source` is
        # where the fact lives, so a missing or overwritten cursor row cannot
        # make this run believe a bulk-priced symbol is safe to re-price.
        bulk = us_seam.covered_symbols(conn)
        if bulk:
            logger.info(f"{len(bulk):,} symbols priced by the bulk archive — "
                        "the budget buys their corporate actions, not their bars")
        plan = select_work(_universe(conn), cursor_df, budget=budget, today=today,
                           bulk_covered=bulk)
        logger.info(f"{len(plan):,} symbols planned against a budget of {budget}")

        prior = {} if cursor_df.empty else {
            r["symbol"]: r for r in cursor_df.to_dict("records")}
        rows, failed, exhausted = 0, [], False
        for i, (symbol, want_prices, want_actions) in enumerate(plan, 1):
            try:
                outcome, cursor = _attempt(conn, client, symbol, rates, want_prices,
                                           want_actions, today,
                                           prior.get(symbol))
            except BudgetExhausted:
                exhausted = True
                break
            # An empty series is the vendor answering, not failing: it moves
            # the cursor towards `no_data` but is no evidence the vendor is gone.
            if outcome in (Outcome.FAILED, Outcome.BAD_REQUEST):
                failed.append(symbol)
            # Inside the loop, before the next request: this is what makes a
            # run that dies halfway still worth having run.
            us_storage.upsert_price_cursor(conn, pd.DataFrame([cursor]))
            rows += 1
            if i % 25 == 0:
                logger.info(f"  {i:,}/{len(plan):,} symbols")

        summary = {
            "planned": len(plan), "symbols": rows, "failed": failed[:50],
            "failed_count": len(failed), "budget_exhausted": exhausted,
            "spent": client.spent,
            "adjust_state": us_storage.adjust_state_counts(conn),
        }
        logger.info(f"Done: {rows:,} symbols, {len(failed)} failed, "
                    f"{client.spent} requests spent"
                    f"{' (budget exhausted)' if exhausted else ''}")
        return summary


def total_failure(summary: dict) -> bool:
    """True when the run planned work and every symbol of it failed.

    Exists because the module this one outlived did not have it. `us_backfill`
    walked a vendor that had started answering 404 to every symbol and reported
    "0 rows over 0 symbols, 20 failed" with an exit code of 0 — a green job, a
    green `ingest_runs` row, and a US plane that stopped advancing in March and
    was not noticed until September. A total failure is the one outcome that is
    never a bad day at the vendor; it is the vendor being gone.

    Deliberately *total*, not any failure: a walk over eleven thousand symbols
    always has a few that 404 for their own reasons, and a job that pages on
    those gets muted, which costs more than it saves. `budget_exhausted` is not
    failure either — the day's allowance running out is what the day's allowance
    does, and it leaves `failed_count` at zero anyway.
    """
    planned = summary.get("planned") or 0
    return bool(planned) and (summary.get("failed_count") or 0) >= planned


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--limit", type=int, default=0,
                    help="cap the requests this run may spend (0 = the budget)")
    ap.add_argument("--reserve", type=int, default=DEFAULT_RESERVE,
                    help="requests to leave for the listing and benchmark jobs")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run_pipeline(args.db_path, args.limit, reserve=args.reserve)
    if total_failure(summary):
        raise SystemExit(
            f"every one of the {summary['planned']:,} symbols this run planned "
            f"failed — this is a vendor that has stopped answering, not a bad "
            f"day. First failures: {', '.join(summary['failed'][:5])}")


if __name__ == "__main__":
    main()
