"""A portfolio's ticker sleeves, checked against the price plane before it is saved.

`FixedWeight` reads a ticker sleeve through `download_benchmark`, which answers an
empty series for a symbol the plane does not carry, and the blend reads "no
price" as "not listed yet" and parks that weight in CDI. For a built-in spec that
is a listing date. For a typo in a user's spec it is a portfolio that holds cash
for its whole history under the name IVVB11 — a result nobody would think to
question. So validate_spec asks the plane first, and refuses a ticker it has never
priced.

B3 only, whatever else the spec says: `FixedWeight` calls `download_benchmark`
without a market, so a ticker sleeve is always a B3 instrument. The symbol goes
through `markets.plane_symbol`, the resolution the backtest itself uses, so the
check and the run cannot disagree about what `IVVB11.SA` means.
"""
from __future__ import annotations

import sys

import pandas as pd

from b3_pipeline import storage
from backtests.core import markets

# A first price this long after the plane's own first session is a listing date
# the author should hear about (IVVB11 lists in 2014, years into the history).
# Inside it, it is just where the history starts.
_LATE_DAYS = 31


def ticker_sleeves(spec: dict) -> list[str]:
    """The `ticker` of every ticker sleeve, in spec order."""
    return [sl["ticker"] for sl in spec.get("sleeves") or []
            if isinstance(sl, dict) and isinstance(sl.get("ticker"), str)]


def _as_date(value):
    # SQLite hands dates back as text, Postgres as date objects.
    return None if value is None or pd.isna(value) else pd.Timestamp(value).date()


def first_prices(symbols: list[str]) -> tuple[dict, object]:
    """({plane symbol: its first priced date}, the plane's first date).

    Two index probes: `prices` is keyed on (ticker, date) and indexed on date. A
    row without `adj_close` does not count, because `download_benchmark` drops it
    too — a symbol that only has those is as unpriced to the run as a missing one.
    """
    spec = markets.get(markets.DEFAULT)
    col, table = spec.symbol_column, spec.table
    params = {f"s{i}": s for i, s in enumerate(symbols)}
    marks = ", ".join(f":{k}" for k in params)
    with storage.get_connection() as conn:
        found = storage.read_sql(
            f"SELECT {col} AS symbol, MIN(date) AS first_day FROM {table} "
            f"WHERE {col} IN ({marks}) AND adj_close IS NOT NULL GROUP BY {col}",
            conn, params=params)
        start = storage.read_sql(f"SELECT MIN(date) AS first_day FROM {table}", conn)
    first = {row.symbol: _as_date(row.first_day) for row in found.itertuples()}
    return first, (_as_date(start["first_day"].iloc[0]) if len(start) else None)


def _plane_symbols(tickers: list[str], errors: list[str]) -> dict[str, str]:
    """{ticker: plane symbol} for the tickers that can name an instrument at all.

    The `$` namespace is stored series — the Ibovespa, the CDI, IPCA — not things
    a holder can buy; `^BVSP` resolves into it. Refused by name, with the ETF that
    does the job, rather than reported as "unknown".
    """
    out = {}
    for t in tickers:
        sym = markets.plane_symbol(t)
        if sym.startswith("$"):
            errors.append(f"ticker {t!r} is an index, not a listed instrument — hold "
                          "it through an ETF (BOVA11.SA tracks the Ibovespa)")
        else:
            out[t] = sym
    return out


def check(spec: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings) for a spec's ticker sleeves; ([], []) when it has none.

    An unknown ticker is an error naming it. A late first price is a warning
    naming the date, because the blend holds CDI in that sleeve's place until
    then and a backtest that starts earlier is partly a cash backtest. A plane
    that cannot be read is an error too: saving an unchecked ticker is exactly
    the silent-cash portfolio this module exists to prevent.
    """
    tickers = ticker_sleeves(spec)
    if not tickers:
        return [], []
    errors: list[str] = []
    symbols = _plane_symbols(tickers, errors)
    if not symbols:
        return errors, []
    try:
        first, plane_start = first_prices(sorted(set(symbols.values())))
    except Exception as e:  # noqa: BLE001 — any driver error means "not checked"
        # The detail goes to stderr, not into the report: a driver error can carry
        # the plane's host and role, and the 422 the report becomes is shown to
        # the user. Re-running validate_spec.py by hand shows it.
        print(f"sleeve_tickers: plane read failed: {type(e).__name__}: {e}", file=sys.stderr)
        return [*errors, ("could not check the ticker sleeves: the B3 price plane "
                          "could not be read")], []
    if plane_start is None:
        return [*errors, ("the B3 price plane is empty, so no ticker sleeve can be "
                          "checked (or backtested) yet")], []
    warnings: list[str] = []
    for t, sym in symbols.items():
        day = first.get(sym)
        if day is None:
            errors.append(f"unknown ticker {t!r}: the B3 price plane has no prices for "
                          f"{sym} (use a listed code such as IVVB11.SA, DIVO11.SA or "
                          "BOVA11.SA)")
        elif (day - plane_start).days > _LATE_DAYS:
            warnings.append(f"ticker {t!r} has no price before {day.isoformat()}: "
                            "until then its weight is held in CDI")
    return errors, warnings
