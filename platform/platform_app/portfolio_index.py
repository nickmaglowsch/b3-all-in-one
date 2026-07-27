"""Time-weighted daily index of the tenant's book (spec/03 "Benchmarking").

Reconstructs daily holdings from the tenant's own trades + qty_events, values
them at the market plane's closes, and chains a time-weighted return — so
deposits and withdrawals move the NAV but not the index. The index *opens* at
100, which makes it directly comparable to IBOV/CDI on one axis; the first
plotted point already carries that session's return, so it reads 100 only when
the book was held into the window rather than bought on day one.

Pure functions over plain dicts and lists: no pandas (the platform venv has
none by design — heavy numerics live in engine/taxcore) and no I/O, so the math
is unit-testable without a database. Sizes are tiny by construction: ~250
trading days a year x the tickers one retail book holds.

Why raw `close` and not `adj_close`: the tenant's share counts are historical
(a split arrives as a dated qty_event from B3's own records), and raw closes are
on that same as-traded basis, so qty_t x close_t stays continuous across a split
with no rebasing. The taxcore CLI this replaces needed a 90-line split-detection
and validation pass only because Yahoo serves prices adjusted to the present.
"""
from __future__ import annotations

import bisect
import datetime
import re

# Markets with no daily-position meaning, excluded from the index and disclosed.
# Futuro is WIN/WDO daytrading: the contracts net to zero every session, so there
# is never an overnight position to mark at a close.
EXCLUDED_MARKETS = frozenset({"Futuro"})

# "Renda Fixa" is deliberately NOT in the set above, because it is not one thing.
# B3 files two unrelated kinds of row under that single label — the label names
# the underlying, not where the instrument trades:
#   - real OTC contracts (CDB, LCI, LCA): long alphanumeric codes, no listed price
#     anywhere, genuinely outside any market plane;
#   - exchange-listed fixed-income ETFs (LFTB11, IMAB11, IRFM11, B5P211, ...):
#     ordinary B3 tickers that trade all day like any other.
# Excluding the label wholesale threw the ETFs out along with the contracts. taxcore
# has always kept them (load_b3's `is_rf_etf`), so the two layers disagreed about
# the same book: `positions` held 1260 cotas of LFTB11 while the index reported its
# market as nonexistent. The ticker's shape is what separates the two — the same
# predicate as taxcore/scripts/load_b3.py:179, copied rather than imported because
# the platform venv has no taxcore on its path. Keep them in step.
RENDA_FIXA_MARKET = "Renda Fixa"
_LISTED_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{3}\d{1,2}$")

# "Mercado Primário de ETF" used to be excluded here too, as "receipt bookkeeping
# that duplicates the real buy". It duplicates nothing — it IS the buy: an ETF
# subscription, booked under a primary-market-only code (HASH11H for HASH11) that
# never trades on the exchange and so never appears in COTAHIST. Dropping it while
# keeping the secondary-market sell of the same units left an orphan sell, and an
# orphan sell is a phantom short: qty goes negative and subtracts qty x close from
# every later NAV. Latent while the ETF itself was unpriced, real the moment the
# plane learned to carry ETFs.
PRIMARY_ETF_MARKET = "Mercado Primário de ETF"


def is_excluded(ticker: str, market: str | None) -> bool:
    """True when a trade has no place in the index at all.

    Kept as a predicate rather than a set membership test because "Renda Fixa"
    needs the ticker to decide — see RENDA_FIXA_MARKET above.
    """
    if market in EXCLUDED_MARKETS:
        return True
    return (market == RENDA_FIXA_MARKET
            and not _LISTED_TICKER_RE.match(ticker or ""))

# cash_events that are a return of/on capital — they leave the book, so TWR
# counts them as return on the day they land.
INCOME_CATEGORIES = frozenset({"income", "amortization"})

# cash_events deliberately outside the index, disclosed rather than silently
# folded in: option premiums have no underlying position here, reembolsos are
# cost refunds rather than investment return.
EXCLUDED_CATEGORIES = frozenset({"premium", "reembolso"})

TRADING_DAYS_PER_YEAR = 252


def canonical(ticker: str, market: str | None = None) -> str:
    """Fold a ticker onto the code the price plane actually carries.

    Two B3 conventions book the same security under a second code, and neither
    of those codes is in COTAHIST:

      *F, fractional market   PETR4F is PETR4 at a smaller lot
      *H, ETF primary market  HASH11H is a subscription of HASH11

    A trade left under the suffixed code can never be priced. Worse, when a round
    trip is booked under both codes — subscribe as HASH11H, sell as HASH11 — the
    unfolded leg becomes an orphan and the index carries a phantom position for it.

    taxcore already folds the fractional case when it derives positions.csv
    (load_b3.consolidate_fractional); the index is rebuilt from raw `trades`,
    which keep both suffixes, so it has to fold them itself.

    The H strip is scoped to `market` because that label identifies the primary
    leg exactly, so the rule cannot reach a spot ticker even in principle. Checked
    against the archives: no spot B3 code ends in F or in H.
    """
    if market == PRIMARY_ETF_MARKET:
        return ticker[:-1] if ticker.endswith("H") else ticker
    return ticker[:-1] if ticker.endswith("F") else ticker


def select_tickers(
    qty_deltas: list[tuple[datetime.date, str, float]],
    flows: list[tuple[datetime.date, str, float, str | None]],
    keep: set[str] | None,
) -> tuple[list, list]:
    """Restrict a book to `keep` before it is chained. `None` keeps everything.

    Answers "which of my assets is helping and which is hurting": rebuild the
    same TWR over a subset and read the difference. Holdings are the obvious
    half; the cash is the half that is easy to get wrong. A buy of an asset the
    user just excluded is not an inflow to the book that remains — left in, it
    inflates that day's denominator and the subset reads as a loss it never had.
    So flows carry the ticker that caused them, purely so this function can
    decide whether they survive.

    Returns flows as the 3-tuples `build_index` takes, which is why the ticker
    rides alongside rather than inside the chaining math.
    """
    return (
        [q for q in qty_deltas if keep is None or q[1] in keep],
        [(d, kind, amount) for d, kind, amount, ticker in flows
         # An untickered cash event cannot be attributed to a selected asset, so
         # a filtered book must not claim it.
         if keep is None or (ticker is not None and ticker in keep)],
    )


def _snap(d: datetime.date, grid: list[datetime.date]) -> int:
    """Index of the first grid day >= `d`, clamped to the last day.

    Trades settle on days the market plane may not carry (a holiday in one
    feed, a stale plane at the end). Rolling forward keeps the flow inside the
    window instead of dropping it.
    """
    i = bisect.bisect_left(grid, d)
    return min(i, len(grid) - 1)


def build_index(
    grid: list[datetime.date],
    closes: dict[str, dict[datetime.date, float]],
    qty_deltas: list[tuple[datetime.date, str, float]],
    flows: list[tuple[datetime.date, str, float]],
) -> dict:
    """Chain a daily TWR index over `grid`.

    Args:
        grid: trading days, ascending and distinct — the market plane's own calendar.
        closes: ticker -> {date: close}. Gaps are forward-filled from the last
            known close; a ticker absent here is unpriced and reported.
        qty_deltas: (date, ticker, signed qty change). Buys/bonuses positive,
            sells negative. Entries before the window fold into opening holdings.
        flows: (date, kind, amount) with kind in {"buy", "sell", "income"} and
            amount positive. Entries before the window are dropped — their
            holdings are already in the opening position, so counting the cash
            again would swamp day one.

    Returns {dates, index, nav, unpriced}: the base-100 index, the NAV path it
    was chained from, and the tickers that were held but never priced.

    The chain is r_t = (NAV_t + sells_t + income_t) / (NAV_{t-1} + buys_t) - 1:
    buys are start-of-day inflows, sells and income end-of-day outflows. Days
    with no capital at risk are flat rather than undefined.
    """
    if not grid:
        return {"dates": [], "index": [], "nav": [], "unpriced": []}

    n = len(grid)
    start = grid[0]

    # Opening holdings (everything before the window) + per-day deltas after it.
    qty: dict[str, float] = {}
    deltas: list[dict[str, float]] = [{} for _ in range(n)]
    for d, ticker, dq in qty_deltas:
        if not ticker or not dq:
            continue
        if d < start:
            qty[ticker] = qty.get(ticker, 0.0) + dq
        else:
            day = deltas[_snap(d, grid)]
            day[ticker] = day.get(ticker, 0.0) + dq

    buys = [0.0] * n
    sells = [0.0] * n
    income = [0.0] * n
    bucket = {"buy": buys, "sell": sells, "income": income}
    for d, kind, amount in flows:
        if d < start or kind not in bucket:
            continue
        bucket[kind][_snap(d, grid)] += abs(amount)

    # Seed every series at its own earliest close, before the walk starts. A ticker
    # whose marks begin after the window opens would otherwise be worth 0 until its
    # first mark lands and then jump to full value in one session — and that jump is
    # a fabricated return, because NAV steps up with no matching `buys` flow to
    # divide it out, so the chain books it as profit. Carrying it flat at its first
    # known price is wrong by however much the price moved before the plane was
    # watching, which is bounded and disclosable; the jump is unbounded and silent.
    last_close: dict[str, float] = {
        t: series[min(series)] for t, series in closes.items() if series
    }
    held: set[str] = set(qty)
    nav: list[float] = []
    for i, day in enumerate(grid):
        for ticker, dq in deltas[i].items():
            qty[ticker] = qty.get(ticker, 0.0) + dq
            held.add(ticker)
        for ticker, series in closes.items():
            px = series.get(day)
            if px is not None:
                last_close[ticker] = px
        nav.append(sum(q * last_close.get(t, 0.0) for t, q in qty.items() if q))

    # Held at some point but never priced — their value is missing from every
    # NAV, so the caller discloses them rather than quietly under-reporting.
    unpriced = sorted(t for t in held if t not in last_close)

    index: list[float] = []
    level = 100.0
    prev_nav = 0.0
    for i in range(n):
        denom = prev_nav + buys[i]
        numer = nav[i] + sells[i] + income[i]
        # ponytail: a zero numerator would multiply the index to 0 and it could
        # never recover — one bad day silently destroys the whole chart. A book
        # that empties always books the proceeds as `sells`, so a zero here means
        # a data gap (a qty_event that zeroed a position with no matching sell),
        # not a real -100%. Held flat. Upgrade path: if genuine total losses ever
        # need showing, they need their own event, not this arithmetic.
        if denom > 0 and numer > 0:
            level *= numer / denom
        index.append(round(level, 4))
        prev_nav = nav[i]

    return {
        "dates": [d.isoformat() for d in grid],
        "index": index,
        "nav": [round(v, 2) for v in nav],
        "unpriced": unpriced,
    }


def rebase(values: list[float], base: float = 100.0) -> list[float]:
    """Rescale a series so its first non-zero point is `base`. Benchmarks arrive
    in their own units (points, an accrual factor); the chart needs one axis."""
    first = next((v for v in values if v), None)
    if not first:
        return values
    return [round(base * v / first, 4) for v in values]


def max_drawdown_pct(values: list[float]) -> float:
    """Worst peak-to-trough fall, as a non-positive percentage."""
    peak = 0.0
    worst = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1.0)
    return round(100.0 * worst, 2)


def summarize(index: list[float], n_days: int) -> dict:
    """Total return, CAGR and max drawdown for a base-100 series.

    `ann_return_pct` is null for windows under a year: annualising three months
    of a lucky run reports a number nobody should act on.
    """
    if not index:
        return {"total_return_pct": None, "ann_return_pct": None, "max_dd_pct": None}
    total = index[-1] / index[0] - 1.0
    years = n_days / TRADING_DAYS_PER_YEAR
    ann = None
    if years >= 1.0 and 1.0 + total > 0:
        ann = round(100.0 * ((1.0 + total) ** (1.0 / years) - 1.0), 2)
    return {
        "total_return_pct": round(100.0 * total, 2),
        "ann_return_pct": ann,
        "max_dd_pct": max_drawdown_pct(index),
    }
