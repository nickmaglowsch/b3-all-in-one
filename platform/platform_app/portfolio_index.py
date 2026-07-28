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


def is_excluded(ticker: str, market: str | None,
                rf_priced: frozenset[str] | set[str] = frozenset()) -> bool:
    """True when a trade has no place in the index at all.

    Kept as a predicate rather than a set membership test because "Renda Fixa"
    needs the ticker to decide — see RENDA_FIXA_MARKET above.

    A contract is admitted only once `accrue_rf` can actually draw it, which
    `rf_priced` names. An unpriced holding is not merely missing from the NAV:
    its aplicação still counts as an inflow, so the chain divides a NAV that
    never rose by a denominator that did, and books the whole purchase as a
    same-day loss. Better out of the book and disclosed. Default empty, so a
    caller that knows nothing about pricing gets the old behaviour.
    """
    if market in EXCLUDED_MARKETS:
        return True
    if market != RENDA_FIXA_MARKET or _LISTED_TICKER_RE.match(ticker or ""):
        return False
    return ticker not in rf_priced

# cash_events that are a return of/on capital — they leave the book, so TWR
# counts them as return on the day they land.
INCOME_CATEGORIES = frozenset({"income", "amortization"})

# cash_events deliberately outside the index, disclosed rather than silently
# folded in: option premiums have no underlying position here, reembolsos are
# cost refunds rather than investment return.
EXCLUDED_CATEGORIES = frozenset({"premium", "reembolso"})

DAYS_PER_YEAR = 365.25          # absorbs the leap day

# Shortest window `summarize` will annualise. Not 1.0: picking a calendar year
# off a date picker gives 01-01 → 12-31, which is 364 elapsed days — genuinely
# a hair under a year, and refusing it would caption twelve real months
# "período < 1 ano". Annualising 364 days moves the figure by ~0.3% relative,
# which is noise; annualising a quarter is the thing this guard exists to stop,
# and 0.99 still refuses everything short of about 362 days.
MIN_ANNUALISE_YEARS = 0.99


def years_between(first: datetime.date, last: datetime.date) -> float:
    """Elapsed calendar years covered by a window — what `summarize` annualises
    over. See the note there for why this is not a session count."""
    return (last - first).days / DAYS_PER_YEAR


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

    The F strip is scoped to the *stripped* form's shape instead, because renda
    fixa contracts share the "Renda Fixa" label with the listed ETFs and a
    contract code can end in F ("LCI - LCI24H9RKF"). Truncating one here while
    taxcore truncates it at a different stage would leave the two layers
    disagreeing about the same holding.
    """
    if market == PRIMARY_ETF_MARKET:
        return ticker[:-1] if ticker.endswith("H") else ticker
    if ticker.endswith("F") and _LISTED_TICKER_RE.fullmatch(ticker[:-1]):
        return ticker[:-1]
    return ticker


def select_tickers(
    qty_deltas: list[tuple[datetime.date, str, float]],
    flows: list[tuple[datetime.date, str, float, str | None]],
    keep: set[str] | None,
) -> tuple[
    list[tuple[datetime.date, str, float]],
    list[tuple[datetime.date, str, float]],
]:
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


# How a contract's rate is quoted, and what `rate_pct` means for each. Business
# days, base 252, which is the convention every Brazilian issuer prices against.
#   cdi_pct    95   -> 95% do CDI:   ∏(1 + 0.95·i_t)
#   cdi_plus   2.5  -> CDI + 2,5%:   ∏(1 + i_t) · (1.025)^(du/252)
#   ipca_plus  6.0  -> IPCA + 6%:    same, over the IPCA level
#   pre        12   -> 12% a.a.:     (1.12)^(du/252), no series at all
RF_INDEXERS = frozenset({"cdi_pct", "cdi_plus", "ipca_plus", "pre"})
# The engine stores these on the price plane (b3_pipeline/index_series.py). The
# "$" is what keeps them from ever colliding with a real holding: no B3 code and
# no renda fixa contract prefix has that shape.
_RF_SERIES = {"cdi_pct": "$CDI", "cdi_plus": "$CDI", "ipca_plus": "$IPCA"}
RF_SERIES_TICKERS = frozenset(_RF_SERIES.values())
BUSINESS_DAYS_PER_YEAR = 252


def rf_series_for(indexer: str) -> str | None:
    """The plane ticker an indexer needs, or None when it needs no series."""
    return _RF_SERIES.get(indexer)


def _factor_path(
    grid: list[datetime.date],
    start: int,
    terms: tuple[str, float],
    levels: dict[datetime.date, float] | None,
) -> list[float] | None:
    """Growth factors from `grid[start]` onward under `terms`, or None.

    None means "cannot be drawn honestly": an indexed contract whose series is
    missing or does not reach far enough gets no curve rather than a flat one,
    because flat would read as "this paid nothing".
    """
    indexer, rate = terms
    pct = rate / 100.0

    if indexer == "pre":
        daily = (1.0 + pct) ** (1.0 / BUSINESS_DAYS_PER_YEAR)
        return [daily ** i for i in range(len(grid) - start)]

    if not levels:
        return None
    # The series has its own calendar — CDI accrues on bank business days, IPCA
    # once a month, and the grid is the exchange's. Carried forward onto the grid
    # the same way the IBOV/CDI overlays are, so a bank holiday is a flat day
    # rather than a hole. A grid day before the series begins has no level to
    # carry, and forcing one would invent the growth up to it.
    ordered = sorted(levels)
    if ordered[0] > grid[start]:
        return None
    sampled: list[float] = []
    last = levels[ordered[0]]
    i = 0
    for day in grid[start:]:
        while i < len(ordered) and ordered[i] <= day:
            last = levels[ordered[i]]
            i += 1
        sampled.append(last)

    base = sampled[0]
    if base <= 0:
        return None

    if indexer == "cdi_pct":
        # A percentage of the CDI compounds day by day at that fraction of the
        # daily rate. It is NOT `pct ×` the accumulated level — 95% of a 21% run
        # is not a 19.95% run — so the level is differentiated back to rates
        # first. Exact: cumprod seeds at 1 + i₀, so L_t/L_{t-1} - 1 is i_t.
        path, running = [1.0], 1.0
        for prev, cur in zip(sampled, sampled[1:]):
            daily_rate = (cur / prev - 1.0) if prev > 0 else 0.0
            running *= 1.0 + pct * daily_rate
            path.append(running)
        return path

    # CDI+x% and IPCA+x%: the index itself, times the spread accrued per
    # business day on top of it.
    daily = (1.0 + pct) ** (1.0 / BUSINESS_DAYS_PER_YEAR)
    return [(v / base) * daily ** i for i, v in enumerate(sampled)]


def accrue_rf(
    grid: list[datetime.date],
    marks: dict[str, list[tuple[datetime.date, float]]],
    terms: dict[str, tuple[str, float]] | None = None,
    levels: dict[str, dict[datetime.date, float]] | None = None,
) -> dict[str, dict[datetime.date, float]]:
    """Daily per-unit price path for renda fixa contracts, from their own marks.

    A CDB has no market, so no feed can ever mark it. What B3 *does* report is
    the price of every aplicação and every resgate — real, dated, per-unit, in
    the tenant's own extract. Between two of them the paper accrues at whatever
    the issuer promised, and those two endpoints imply that rate exactly: the
    indexer never has to be known, or guessed, to draw the segment. Compounding
    is geometric, so the interpolation is too.

    A contract still held has only its aplicação, and one point implies no rate.
    That is what `terms` is for: the indexer the tenant entered, compounded from
    the last real mark against `levels` — so the curve is still anchored to a
    reported price and only its *slope* is modelled. Absent both a second mark
    and terms, the contract is skipped here and disclosed by the caller.

    Args:
        grid: the same trading days `build_index` walks.
        marks: ticker -> [(date, per-unit price)].
        terms: ticker -> (indexer, rate_pct); see RF_INDEXERS.
        levels: series name ("$CDI", "$IPCA") -> {date: cumulative factor}.

    Returns ticker -> {date: price}, shaped for `build_index`'s `closes`.

    Interpolates on the grid index rather than on calendar days: interest on
    Brazilian paper accrues per business day (the 252 convention), so counting
    the exchange's own sessions is closer than counting dates, and needs no
    second calendar to go stale.
    """
    if not grid:
        return {}
    terms = terms or {}
    levels = levels or {}
    out: dict[str, dict[datetime.date, float]] = {}
    for ticker, points in marks.items():
        pts = sorted((d, float(pu)) for d, pu in points if pu and float(pu) > 0)
        if not pts:
            continue
        contract_terms = terms.get(ticker)
        if len(pts) < 2 and not contract_terms:
            continue
        series: dict[datetime.date, float] = {}
        for (d0, pu0), (d1, pu1) in zip(pts, pts[1:]):
            i0, i1 = _snap(d0, grid), _snap(d1, grid)
            if i1 <= i0:
                # Two marks the grid cannot separate (same session, or both
                # rolled onto a stale plane's last day). The later price wins.
                series[grid[i1]] = pu1
                continue
            growth = pu1 / pu0
            span = i1 - i0
            for i in range(i0, i1 + 1):
                series[grid[i]] = pu0 * growth ** ((i - i0) / span)

        # Past the last mark the contract is still accruing, and only the terms
        # the tenant entered say how fast. Anchored at that mark's price, so the
        # curve stays pinned to a reported number and only its slope is a model.
        last_day, last_pu = pts[-1]
        anchor = _snap(last_day, grid)
        if contract_terms and anchor < len(grid) - 1:
            path = _factor_path(
                grid, anchor, contract_terms,
                levels.get(_RF_SERIES.get(contract_terms[0], "")))
            if path:
                for i, factor in enumerate(path):
                    series[grid[anchor + i]] = last_pu * factor
            elif len(pts) < 2:
                # No second mark and no usable series: nothing to draw at all.
                continue
        if series:
            out[ticker] = series
    return out


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


def summarize(index: list[float], years: float) -> dict:
    """Total return, CAGR and max drawdown for a base-100 series spanning
    `years` of elapsed calendar time.

    `ann_return_pct` is null under a year: annualising three months of a lucky
    run reports a number nobody should act on.

    Calendar years, not sessions/252. The proxy was serviceable while the only
    window was the whole book, but the frontend's date filter hands users
    windows chosen on a calendar, and B3 runs ~248-251 sessions a year — so a
    full twelve months counts ~250 sessions, lands under 1.0, and gets captioned
    "período < 1 ano". Mirrored in frontend/lib/chartRange.summarize, which
    summarises the narrowed windows this endpoint never sees; keep them in step.
    """
    if not index:
        return {"total_return_pct": None, "ann_return_pct": None, "max_dd_pct": None}
    total = index[-1] / index[0] - 1.0
    ann = None
    if years >= MIN_ANNUALISE_YEARS and 1.0 + total > 0:
        ann = round(100.0 * ((1.0 + total) ** (1.0 / years) - 1.0), 2)
    return {
        "total_return_pct": round(100.0 * total, 2),
        "ann_return_pct": ann,
        "max_dd_pct": max_drawdown_pct(index),
    }
