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
from pathlib import Path

import pandas as pd

from b3_pipeline import config, storage
from backtests.core import markets

# A first price this long after the plane's own first session is a listing date
# the author should hear about (IVVB11 lists in 2014, years into the history);
# a last price this long before the plane's last session is a delisting. Inside
# either, it is just where the history starts or stops.
_GAP_DAYS = 31


def ticker_sleeves(spec: dict) -> list[str]:
    """The `ticker` of every ticker sleeve, in spec order."""
    return [sl["ticker"] for sl in spec.get("sleeves") or []
            if isinstance(sl, dict) and isinstance(sl.get("ticker"), str)]


def _as_date(value):
    # SQLite hands dates back as text, Postgres as date objects.
    return None if value is None or pd.isna(value) else pd.Timestamp(value).date()


def _connect():
    """The plane's connection — but never a new, empty SQLite file. With
    ENGINE_DATABASE_URL unset, get_connection falls back to config.DB_PATH, and
    sqlite3.connect creates whatever is not there: a save-time check would leave
    an empty database behind and then call every ticker unknown."""
    target = storage.describe_target()
    if target.startswith("sqlite:") and not Path(target.removeprefix("sqlite:")).exists():
        raise FileNotFoundError(target)
    return storage.get_connection()


def price_spans(symbols: list[str]) -> tuple[dict, tuple]:
    """({plane symbol: (first, last) priced date}, (plane's first, last date)).

    Index probes only: `prices` is keyed on (ticker, date) and indexed on date. A
    row without `adj_close` does not count, because `download_benchmark` drops it
    too — a symbol that only has those is as unpriced to the run as a missing one.
    """
    spec = markets.get(markets.DEFAULT)
    col, table = spec.symbol_column, spec.table
    params = {f"s{i}": s for i, s in enumerate(symbols)}
    marks = ", ".join(f":{k}" for k in params)
    with _connect() as conn:
        found = storage.read_sql(
            f"SELECT {col} AS symbol, MIN(date) AS first_day, MAX(date) AS last_day "
            f"FROM {table} WHERE {col} IN ({marks}) AND adj_close IS NOT NULL "
            f"GROUP BY {col}", conn, params=params)
        plane = storage.read_sql(
            f"SELECT MIN(date) AS first_day, MAX(date) AS last_day FROM {table}", conn)
    spans = {row.symbol: (_as_date(row.first_day), _as_date(row.last_day))
             for row in found.itertuples()}
    if not len(plane):
        return spans, (None, None)
    return spans, (_as_date(plane["first_day"].iloc[0]), _as_date(plane["last_day"].iloc[0]))


def _plane_symbols(tickers: list[str], errors: list[str]) -> dict[str, str]:
    """{ticker: plane symbol} for the tickers that can be on the plane at all.

    The `$` namespace is stored series — the Ibovespa, the CDI, IPCA — not things
    a holder can buy; `^BVSP` resolves into it. ETFs de renda fixa are tradable
    but B3 books them outside COTAHIST, so they live in `external_prices` from
    mid-2025 only and `download_benchmark` cannot read them as a sleeve. Both are
    refused by name, saying why, rather than reported as "unknown".
    """
    out = {}
    for t in tickers:
        sym = markets.plane_symbol(t)
        if sym.startswith("$"):
            errors.append(f"ticker {t!r} is an index, not a listed instrument — hold "
                          "it through an ETF (BOVA11.SA tracks the Ibovespa)")
        elif sym in config.RENDA_FIXA_ETFS:
            errors.append(f"ticker {t!r} is a renda-fixa ETF: B3 publishes it outside "
                          "the equity price history this bench reads, so it is not "
                          "supported as a sleeve yet — use CASH_CDI for cash")
        else:
            out[t] = sym
    return out


def _judge(t: str, span: tuple | None, plane: tuple, errors: list, warnings: list):
    """One ticker against the plane's own first and last session."""
    if span is None:
        errors.append(f"unknown ticker {t!r}: the B3 price plane has no prices for it "
                      "(use a listed code such as IVVB11.SA, DIVO11.SA or BOVA11.SA)")
        return
    first, last = span
    if (first - plane[0]).days > _GAP_DAYS:
        warnings.append(f"ticker {t!r} has no price before {first.isoformat()}: "
                        "until then its weight is held in CDI")
    if (plane[1] - last).days > _GAP_DAYS:
        # The backtest carries the last price forward, so from here the sleeve
        # earns exactly 0% — a delisting reads as a very calm asset.
        warnings.append(f"ticker {t!r} has no price after {last.isoformat()}: from "
                        "then on its weight sits at that last price, earning 0%")


def check(spec: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings) for a spec's ticker sleeves; ([], []) when it has none.

    An unknown ticker is an error naming it. A late first price or an early last
    one is a warning naming the date: the blend holds CDI in that sleeve's place
    before its first price and a flat mark after its last, so a backtest that
    reaches past either is partly something else. A plane that cannot be read is
    an error too: saving an unchecked ticker is exactly the silent-cash portfolio
    this module exists to prevent.
    """
    tickers = ticker_sleeves(spec)
    if not tickers:
        return [], []
    errors: list[str] = []
    symbols = _plane_symbols(tickers, errors)
    if not symbols:
        return errors, []
    try:
        spans, plane = price_spans(sorted(set(symbols.values())))
    except Exception as e:  # noqa: BLE001 — any driver error means "not checked"
        # The detail goes to stderr, not into the report: a driver error can carry
        # the plane's host and role, and the 422 the report becomes is shown to
        # the user. Re-running validate_spec.py by hand shows it.
        print(f"sleeve_tickers: plane read failed: {type(e).__name__}: {e}", file=sys.stderr)
        return [*errors, ("could not check the ticker sleeves: the B3 price plane "
                          "could not be read")], []
    if plane[0] is None:
        return [*errors, ("the B3 price plane is empty, so no ticker sleeve can be "
                          "checked (or backtested) yet")], []
    warnings: list[str] = []
    for t, sym in symbols.items():
        _judge(t, spans.get(sym), plane, errors, warnings)
    return errors, warnings
