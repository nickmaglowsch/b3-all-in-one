"""Latest closes for advise_weights' --weights valuation, straight off the planes.

--weights is a valuation, not a backtest: it needs one number per ticker and the
date those numbers are as of. Split out of advise_weights.py once a fixed target
could hold more than the B3 standard lots: a BDR (AAPL34), a renda-fixa ETF, a
US symbol (SPY) or a crypto pair (BTCUSDT) held directly as a live sleeve.

Every close here is in reais and is the raw close — what a unit costs today, the
number a trade is sized with. US and crypto bars carry it as `close_brl`,
converted through PTAX at ingest (b3_pipeline/market_storage.py). The platform's
save-time check (platform_app/market_assets.py) reads the same columns, so the
sleeve the user saved and the trade the plan proposes are priced alike.
"""
from __future__ import annotations

import pandas as pd

from backtests.core import markets

# Standard-lot universe. This used to be a hand-copied duplicate of the
# predicate in the loaders, with a comment warning that moving one without the
# other would make the two disagree about which names exist. It now comes from
# the market registry, which is a plain dataclass table with no pandas import —
# so the reason for duplicating it (keeping this path off the heavy import) no
# longer applies, and the drift hazard is gone.
#
# B3, explicitly and not by default: this advises on a real tenant portfolio,
# and those are built from B3 extracts.
STANDARD_LOT_SQL = markets.MARKETS["B3"].ticker_sql


def _closes(rows: pd.DataFrame, column: str = "close") -> dict[str, float]:
    return {str(t): float(c) for t, c in zip(rows["ticker"], rows[column])
            if pd.notna(c) and float(c) > 0}


def latest_plane_close(
    db_path: str, start: str, end: str
) -> tuple["pd.Timestamp | None", dict[str, float]]:
    """(as_of, {ticker: latest raw close}) read straight from the market plane.

    It used to obtain them by building the entire shared-data plane — 20 years
    of HLC for every listed name, the split detector, MA200, ATR, the
    mean-reversion feature set — and then reading ``raw_close.ffill().iloc[-1]``
    out of it. Both platform surfaces that value a book sit on this path and
    neither caches it.

    Equivalence with that row is the contract, and it rests on two properties of
    the frame this replaces (pinned by test_weights_mode_prices_match_the_full_plane):

      · ``raw_close`` is ``close_px`` resampled to month end, so ``as_of`` is the
        *label* of the final bucket — the last calendar day of the plane's last
        month. That is why it can legitimately fall a few days after the last
        close, and why rolling the max date forward with MonthEnd(0) is the
        reproduction rather than the max date itself.
      · ``.ffill()`` carries each name's last known close into that final bucket,
        so the per-ticker answer is just its most recent close in the window. A
        name that stopped trading years ago keeps its last mark, as before.

    Window filtering is kept even though only the newest row survives: `start`
    bounds nothing in practice but `end` does — it is what makes an as-of-a-past
    -date valuation reproducible instead of silently using today's plane.
    """
    from b3_pipeline import storage

    ranked = f"""
        SELECT ticker, close FROM (
            SELECT ticker, close,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
            FROM prices
            WHERE date >= ? AND date <= ? AND close IS NOT NULL
              AND {STANDARD_LOT_SQL}
        ) latest
        WHERE rn = 1
    """
    with storage.get_connection(db_path) as conn:
        rows = storage.read_sql(ranked, conn, params=[start, end])
        max_date = storage.read_sql(
            "SELECT MAX(date) AS d FROM prices "
            f"WHERE date >= ? AND date <= ? AND {STANDARD_LOT_SQL}",
            conn, params=[start, end],
        ).iloc[0]["d"]

    if max_date is None or pd.isna(max_date) or rows.empty:
        return None, {}
    as_of = pd.Timestamp(max_date) + pd.offsets.MonthEnd(0)
    return as_of, _closes(rows)


def _latest_in(db_path: str, table: str, tickers: list[str], end: str) -> dict[str, float]:
    """{ticker: latest positive close} of exactly `tickers` in a ticker-keyed
    table. A zero close is skipped *before* picking the newest row, so it falls
    back to the last real price — as platform_app/market_assets.py does — rather
    than leaving the name unpriced."""
    from b3_pipeline import storage

    if not tickers:
        return {}
    marks = ", ".join("?" for _ in tickers)
    ranked = f"""
        SELECT ticker, close FROM (
            SELECT ticker, close,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
            FROM {table}
            WHERE date <= ? AND close > 0 AND ticker IN ({marks})
        ) latest
        WHERE rn = 1
    """
    with storage.get_connection(db_path) as conn:
        rows = storage.read_sql(ranked, conn, params=[end, *tickers])
    return _closes(rows)


def rf_etf_closes(db_path: str, end: str) -> dict[str, float]:
    """{ticker: latest own close} for the renda-fixa ETFs a sleeve can hold.

    B3 books them outside COTAHIST, so neither `prices` nor the shared frame's
    raw_close ever carries them, and a target weight on IMAB11 used to come back
    as `unpriced_skipped` — accepted at save time, then never traded. Their
    marks are in `external_prices` (b3_pipeline/external_prices.py). Kept apart
    from `latest_plane_close` so that function stays row-for-row equal to the
    raw_close it replaced, which is what its equivalence test pins.

    Only the ETF's own close, never the spliced index level: a trade is sized in
    shares at what the share costs today.
    """
    from b3_pipeline import config

    return _latest_in(db_path, "external_prices", sorted(config.RF_ETF_INDEX), end)


def b3_ticker_closes(db_path: str, tickers: list[str], end: str) -> dict[str, float]:
    """{code: latest close} for B3 codes outside the standard-lot universe —
    a BDR (AAPL34) held as a sleeve, or a renda-fixa ETF with no index
    (LFTB11) — from `prices`, then from `external_prices` for what COTAHIST
    does not carry. Never a stored `$` series: an index is not a price."""
    out = _latest_in(db_path, "prices", tickers, end)
    rest = [t for t in tickers if t not in out and not t.startswith("$")]
    return {**out, **_latest_in(db_path, "external_prices", rest, end)}


def market_closes_brl(db_path: str, symbols: dict[str, str], end: str) -> dict[str, float]:
    """{symbol: latest close in BRL} for US / crypto symbols ({symbol: market}),
    from `market_prices.close_brl`. A bar with no PTAX for its date has no BRL
    close (and a zero one is no price): either is skipped for the previous bar,
    never priced in dollars."""
    from b3_pipeline import storage

    out: dict[str, float] = {}
    for market in sorted(set(symbols.values())):
        names = sorted(s for s, m in symbols.items() if m == market)
        marks = ", ".join("?" for _ in names)
        ranked = f"""
            SELECT symbol AS ticker, close_brl FROM (
                SELECT symbol, close_brl,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
                FROM market_prices
                WHERE market = ? AND date <= ? AND close_brl > 0
                  AND symbol IN ({marks})
            ) latest
            WHERE rn = 1
        """
        with storage.get_connection(db_path) as conn:
            rows = storage.read_sql(ranked, conn, params=[market, end, *names])
        out |= _closes(rows, "close_brl")
    return out


def sleeve_closes(db_path: str, tickers: list[str], off_b3: dict[str, str],
                  end: str) -> dict[str, float]:
    """Closes for target tickers the standard-lot query did not price: each
    off-B3 one from its own plane (`off_b3` names it), the rest as B3 codes."""
    b3 = [t for t in tickers if t not in off_b3]
    other = {t: off_b3[t] for t in tickers if t in off_b3}
    return {**b3_ticker_closes(db_path, b3, end),
            **(market_closes_brl(db_path, other, end) if other else {})}
