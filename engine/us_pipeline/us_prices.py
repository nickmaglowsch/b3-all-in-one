"""Daily US bars, bought one symbol at a time under a hard daily ceiling.

Full coverage is three requests a symbol across roughly eleven thousand
symbols. On the free tier's 25 requests a day that is years; on a paid key it is
an afternoon. Both run this module unchanged, so the thing that has to be right
is not throughput but **resumability**: every symbol's outcome is written to
`us_price_cursor` inside the loop, before the next request goes out, so a run
that stops at request 12 of 25 starts at 13 tomorrow rather than at 1.

On a free key this is the **top-up**, not the backfill. `us_backfill` takes the
history from a free vendor for nothing, because "years" is not a throughput
problem this module can solve — it is a property of a per-symbol metered feed.
What is left here is the work only a metered key can do: the symbols that vendor
does not carry, and the corporate actions that turn a `split_only` series into a
`total_return` one. `select_work` is handed the set of symbols the
vendor already priced and never re-prices them; `us_seam.demote_bulk_priced` says
why that matters and what would break without it.

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
    AlphaVantageError,
    BudgetExhausted,
    Client,
    PremiumOnly,
    RateLimited,
)
from .av_parsers import parse_dividends, parse_splits, parse_time_series_daily

logger = logging.getLogger(__name__)

MARKET = "US"
SOURCE = "alphavantage-time-series-daily"

# How long a symbol's corporate actions stay fresh. Splits and dividends are
# rare; re-asking every day would spend two thirds of a free tier's budget on
# answers that have not changed.
ACTIONS_STALE_DAYS = 90

# Requests held back for the listing and benchmark jobs, which share the key.
DEFAULT_RESERVE = 5

_PRICE_COST = 1
_ACTIONS_COST = 2


def fetch_prices(client: Client, symbol: str) -> pd.DataFrame:
    """The full daily history for one symbol. One request."""
    return parse_time_series_daily(
        client.get_csv("TIME_SERIES_DAILY", symbol=symbol, outputsize="full"),
        symbol)


def _fetch_one_action(client: Client, function: str, symbol: str, parse):
    """One actions endpoint, or `None` if this key cannot reach it.

    The distinction `us_adjust` rests on: an empty frame is the vendor saying
    "none on record", `None` is the vendor refusing. A refusal is recorded, not
    retried into oblivion — if the endpoint is premium on this key it will
    refuse forever, and the symbol's series is honestly split-only.
    """
    try:
        return parse(client.get_json(function, symbol=symbol), symbol)
    except (PremiumOnly, RateLimited) as exc:
        logger.info(f"{symbol}: {function} unavailable ({exc}); "
                    "adjustment will be recorded as partial")
        return None


def fetch_actions(client: Client, symbol: str) -> tuple:
    """Splits and dividends for one symbol. Two requests."""
    return (_fetch_one_action(client, "SPLITS", symbol, parse_splits),
            _fetch_one_action(client, "DIVIDENDS", symbol, parse_dividends))


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


def select_work(universe, cursor: pd.DataFrame, *, budget: int, today,
                actions_stale_days: int = ACTIONS_STALE_DAYS,
                bulk_covered=frozenset()) -> list:
    """What this run can afford to do, in the order it should do it.

    Pure — no network, no database — so the policy can be tested directly, in
    the spirit of `external_prices.days_to_fetch`.

    `universe` is the symbols in preference order; the caller does the ordering
    in SQL. The order here is between *kinds* of work:

    1. symbols recorded as needing a retry, oldest first — the durable retry
       queue, drained before new work, or a transient failure would keep its
       place at the back of an eleven-thousand-symbol line forever;
    2. symbols with no bars at all, in the caller's preference order — a
       backtest is useless with eight random tickers and useful with the two
       hundred largest;
    3. symbols whose last bar is behind today;
    4. symbols whose corporate actions have gone stale.

    Returns `[(symbol, want_prices, want_actions)]` costing no more than
    `budget` requests. A negative budget means no ceiling.
    """
    today = pd.Timestamp(today).date()
    state = {} if cursor.empty else {
        r["symbol"]: r for _, r in cursor.iterrows()}

    retry, fresh, stale_bars, stale_actions = [], [], [], []
    for symbol in universe:
        row = state.get(symbol)
        if row is None:
            fresh.append((symbol, True, True))
            continue
        if row.get("status") == "retry":
            retry.append((symbol, True, _actions_due(row, today, actions_stale_days)))
            continue
        last = _as_date(row.get("last_bar"))
        actions_due = _actions_due(row, today, actions_stale_days)
        if last is None:
            fresh.append((symbol, True, True))
        elif last < today:
            stale_bars.append((symbol, True, actions_due))
        elif actions_due:
            stale_actions.append((symbol, False, True))

    retry, fresh, stale_bars, stale_actions = us_seam.demote_bulk_priced(
        (retry, fresh, stale_bars, stale_actions), bulk_covered)

    plan, spent = [], 0
    for item in [*retry, *fresh, *stale_bars, *stale_actions]:
        cost = (_PRICE_COST if item[1] else 0) + (_ACTIONS_COST if item[2] else 0)
        if budget >= 0 and spent + cost > budget:
            # Prices alone still make progress when actions do not fit.
            if item[1] and budget >= 0 and spent + _PRICE_COST <= budget:
                plan.append((item[0], True, False))
                spent += _PRICE_COST
            break
        plan.append(item)
        spent += cost
    return plan


def _as_date(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    ts = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(ts) else ts.date()


def _actions_due(row, today, stale_days: int) -> bool:
    fetched = _as_date(row.get("actions_fetched_at"))
    return fetched is None or (today - fetched).days >= stale_days


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
    """
    if prior_state in (None, us_adjust.UNADJUSTED):
        return None, None
    splits = us_storage.load_corporate_actions(conn, symbol, SPLIT).rename(
        columns={"value": "ratio"})
    if prior_state == us_adjust.SPLIT_ONLY:
        return splits, None
    return splits, us_storage.load_corporate_actions(
        conn, symbol, DIVIDEND).rename(columns={"value": "amount"})


def ingest_symbol(conn, client: Client, symbol: str, rates: pd.Series, *,
                  want_prices: bool, want_actions: bool, today,
                  prior_state=None) -> dict:
    """Fetch, convert and write one symbol. Returns the row for the cursor."""
    cursor = {"symbol": symbol, "status": "ok", "error": None, "attempts": 0}
    if want_actions:
        splits, dividends = fetch_actions(client, symbol)
        _persist_actions(conn, symbol, splits, dividends)
        cursor["actions_fetched_at"] = str(today)
    else:
        splits, dividends = _stored_actions(conn, symbol, prior_state)

    if not want_prices:
        return cursor

    bars = fetch_prices(client, symbol)
    cursor["prices_fetched_at"] = str(today)
    if bars.empty:
        logger.warning(f"{symbol}: the vendor returned no bars")
        return cursor

    adjusted, state = us_adjust.apply(bars, splits, dividends)
    market_storage.upsert_market_prices(
        conn, to_market_rows(symbol, adjusted, state, rates))
    cursor.update({"first_bar": str(bars["date"].iloc[0]),
                   "last_bar": str(bars["date"].iloc[-1]),
                   "adjust_state": state})
    return cursor


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

        prior = {} if cursor_df.empty else dict(
            zip(cursor_df["symbol"], cursor_df["adjust_state"]))
        rows, failed, exhausted = 0, [], False
        for i, (symbol, want_prices, want_actions) in enumerate(plan, 1):
            try:
                cursor = ingest_symbol(conn, client, symbol, rates,
                                       want_prices=want_prices,
                                       want_actions=want_actions, today=today,
                                       prior_state=prior.get(symbol))
            except BudgetExhausted:
                exhausted = True
                break
            except AlphaVantageError as exc:
                failed.append(symbol)
                cursor = {"symbol": symbol, "status": "retry",
                          "error": str(exc)[:500]}
            except Exception as exc:                       # noqa: BLE001
                failed.append(symbol)
                logger.error(f"{symbol}: {exc}")
                cursor = {"symbol": symbol, "status": "retry",
                          "error": str(exc)[:500]}
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
    run_pipeline(args.db_path, args.limit, reserve=args.reserve)


if __name__ == "__main__":
    main()
