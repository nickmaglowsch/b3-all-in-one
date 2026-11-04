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
from b3_pipeline import anbima_series, config, storage
from backtests.core import markets

# A first price this long after the plane's own first session is a listing date
# the author should hear about (IVVB11 lists in 2014, years into the history);
# a last price this long before the plane's last session is a delisting. Inside
# either, it is just where the history starts or stops.
_GAP_DAYS = 31

# An index refused as a sleeve says which listed fund holds it, where one does;
# an index no fund here tracks ($CDI, $SELIC, $IPCA, ...) gets no hint at all
# rather than a pointer to the Ibovespa's.
_TRACKED_BY = {
    "$IBOV": "BOVA11.SA tracks the Ibovespa",
    "$IMAB": "IMAB11.SA tracks IMA-B",
    "$IMAB5P": "IB5M11.SA tracks IMA-B 5+",
    "$IMAB5P2": "B5P211.SA tracks IMA-B 5 P2",
    "$IRFMP2": "IRFM11.SA tracks IRF-M P2",
    "$IMAB5": "the closest listed fund is B5P211.SA, which tracks IMA-B 5 P2",
    "$IRFM": "the closest listed fund is IRFM11.SA, which tracks IRF-M P2",
    "$IMAS": "the closest listed fund is LFTS11.SA, which tracks Teva Tesouro Selic",
}


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


def price_spans(symbols: list[str]) -> tuple[dict, tuple, dict]:
    """({plane symbol: (first, last) priced date}, (plane's first, last date),
    {external ticker: (first, last)}).

    Index probes only: `prices` is keyed on (ticker, date) and indexed on date. A
    row without `adj_close` does not count, because `download_benchmark` drops it
    too — a symbol that only has those is as unpriced to the run as a missing one.

    A renda-fixa ETF is not in `prices` at all. Its span is the one
    `download_benchmark` will hand the backtest: from the first day of its
    spliced index (stored_series.rf_etf_series) to its own last close. The raw
    `external_prices` spans come back too, for the warning that says where the
    ETF's own closes start and what stands in before them.
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
        rf = [s for s in symbols if s in config.RF_ETF_INDEX]
        wanted = sorted({t for s in rf for t in (s, config.RF_ETF_INDEX[s].index)})
        ext_params = {f"e{i}": t for i, t in enumerate(wanted)}
        ext_rows = storage.read_sql(
            "SELECT ticker AS symbol, MIN(date) AS first_day, MAX(date) AS last_day "
            "FROM external_prices WHERE ticker IN "
            f"({', '.join(f':{k}' for k in ext_params)}) GROUP BY ticker",
            conn, params=ext_params) if wanted else found.iloc[0:0]
    spans = {row.symbol: (_as_date(row.first_day), _as_date(row.last_day))
             for row in found.itertuples()}
    ext = {row.symbol: (_as_date(row.first_day), _as_date(row.last_day))
           for row in ext_rows.itertuples()}
    if rf:
        # The span is read off the very series the backtest will get, so the
        # check and the run cannot disagree about where the splice starts.
        from backtests.core.stored_series import rf_etf_series
        for s in rf:
            px = rf_etf_series(s, "1900-01-01", "2999-12-31")
            if len(px):
                spans[s] = (px.index[0].date(), px.index[-1].date())
    if not len(plane):
        return spans, (None, None), ext
    return (spans, (_as_date(plane["first_day"].iloc[0]),
                    _as_date(plane["last_day"].iloc[0])), ext)


def _plane_symbols(tickers: list[str], errors: list[str]) -> dict[str, str]:
    """{ticker: plane symbol} for the tickers that can be on the plane at all.

    The `$` namespace is stored series — the Ibovespa, the CDI, IPCA, the ANBIMA
    indexes — not things a holder can buy; `^BVSP` resolves into it. They are
    refused by name, saying which ETF holds the index where one does.

    ETFs de renda fixa are tradable but B3 books them outside COTAHIST, so their
    own closes start in mid-2025. The ones with an ANBIMA index in
    config.RF_ETF_INDEX pass, with that index spliced in front of them (see
    `_splice_warning`); the rest have nothing to splice and are refused, saying
    why, rather than held in CDI for all but their last year.
    """
    out = {}
    for t in tickers:
        sym = markets.plane_symbol(t)
        if sym.startswith("$"):
            hint = _TRACKED_BY.get(sym)
            errors.append(f"ticker {t!r} is an index, not a listed instrument"
                          + (f" — hold it through an ETF ({hint})" if hint else ""))
        elif sym in config.RF_ETF_NO_INDEX:
            errors.append(f"ticker {t!r} is a renda-fixa ETF with prices only from "
                          f"mid-2025, and {config.RF_ETF_NO_INDEX[sym]}, so a backtest "
                          "cannot hold it — use CASH_CDI for cash")
        else:
            out[t] = sym
    return out


def _splice_warning(t: str, sym: str, span: tuple, ext: dict) -> str | None:
    """What a renda-fixa ETF sleeve holds before its own first close; None when
    its own closes cover everything the index could have.

    Names the index, the dates and the fee, and says when the index is not the
    fund's own: a backtest that reads IMA-B 5 for an IMA-B 5 P2 fund, or IMA-S
    for a Teva Tesouro Selic one, is close, and is still something else. An
    index that stops short of the ETF's first close is said too — nothing is
    spliced then (stored_series.rf_etf_series), and the late-listing warning alone
    would not say why.
    """
    p = config.RF_ETF_INDEX[sym]
    own_first = ext[sym][0]
    name = f"{anbima_series.NAMES[p.index]} (ANBIMA)"
    if span[0] >= own_first:
        base = ext.get(p.index)
        if base and base[0] < own_first:
            return (f"ticker {t!r}: {name} has no level on {own_first.isoformat()}, "
                    f"its first own close (the index stops at {base[1].isoformat()}), "
                    "so nothing is spliced in front of it")
        return None
    fee = f"{p.fee * 100:.2f}%/yr"
    lead = (f"ticker {t!r} has no price of its own before {own_first.isoformat()}: "
            f"from {span[0].isoformat()} until then it follows")
    if p.tracks:
        return f"{lead} {name} minus {fee}, a stand-in: its own index is {p.tracks}"
    return f"{lead} its own index, {name}, minus {fee}"


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
        spans, plane, ext = price_spans(sorted(set(symbols.values())))
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
        if sym in config.RF_ETF_INDEX and sym in ext and sym in spans:
            note = _splice_warning(t, sym, spans[sym], ext)
            if note:
                warnings.append(note)
    return errors, warnings


# How many names a universe.tickers allow-list may hold: a hand-picked list, not
# a way to restate the market. platform strategy_specs says the same.
_UNIVERSE_TICKERS_MAX = 50


def check_universe_shape(spec: dict, errors: list[str]) -> None:
    """universe.tickers, when given, is a short non-empty list of codes. Whether
    the plane prices them is report()'s to say; this is the shape."""
    uni = spec.get("universe") or {}
    if "tickers" not in uni:
        return
    t = uni["tickers"]
    if (not isinstance(t, list) or not t or len(t) > _UNIVERSE_TICKERS_MAX
            or not all(isinstance(x, str) and x.strip() for x in t)):
        errors.append(f"universe.tickers: a list of 1..{_UNIVERSE_TICKERS_MAX} ticker "
                      "codes (e.g. [\"BTCUSDT\"] on CRYPTO, [\"IVVB11\"] on B3)")
        return
    # Distinct names as the run will see them: BTC and BTCUSDT are one coin, and
    # with dedup_roots PETR3/PETR4 are one company. (min_hold is a floor on the
    # held count, not a gate — it never stops a book from investing.)
    try:
        spec_market = markets.get(spec.get("market"))
    except ValueError:
        return                                          # validate() reports it
    names = {markets.plane_symbol(x, spec_market.key) for x in t}
    sel = spec.get("selection") or {}
    if sel.get("dedup_roots") and spec_market.root_len:
        names = {n[:spec_market.root_len] for n in names}
    need = sel.get("min_names", 5)                      # RankAndHold's default
    if isinstance(need, int) and need > len(names):
        errors.append(f"selection.min_names = {need} but universe.tickers holds "
                      f"{len(names)} distinct name(s): the book could never invest "
                      "— lower min_names")


def _universe_symbols_found(symbols: list[str], key: str) -> set[str]:
    """Which of `symbols` the market's universe query can see: priced on the
    market's table, and of the shape `MarketSpec.ticker_sql` admits — so a
    BTCFDUSD on the crypto plane, outside the `%USDT` universe, is not found."""
    spec = markets.get(key)
    col, table = spec.symbol_column, spec.table
    params = {f"s{i}": s for i, s in enumerate(symbols)}
    marks = ", ".join(f":{k}" for k in params)
    price = "adj_close" if table == "prices" else "adj_close_brl"
    scope = " AND market = :market" if table != "prices" else ""
    shape = f" AND {spec.ticker_sql}" if spec.ticker_sql else ""
    if scope:
        params["market"] = spec.key
    with _connect() as conn:
        found = storage.read_sql(
            f"SELECT DISTINCT {col} AS symbol FROM {table} "
            f"WHERE {col} IN ({marks}) AND {price} IS NOT NULL{scope}{shape}",
            conn, params=params)
    return set(found["symbol"])


def check_universe(spec: dict) -> list[str]:
    """Errors for a rank spec's `universe.tickers` allow-list; [] when it has none.

    The same failure check() exists for, one step removed: a code the plane never
    priced leaves the universe empty, the book parks in CDI on every date, and the
    run reports a calm, cash-like strategy under the name of the coin. Each code
    is resolved as the backtest resolves it (markets.plane_symbol) and looked up
    on the spec's own market. A plane that cannot be read is an error, as there.
    """
    tickers = (spec.get("universe") or {}).get("tickers") or []
    if not tickers:
        return []
    key = markets.get(spec.get("market")).key
    symbols = {t: markets.plane_symbol(t, key) for t in tickers}
    try:
        found = _universe_symbols_found(sorted(set(symbols.values())), key)
    except Exception as e:  # noqa: BLE001 — any driver error means "not checked"
        print(f"sleeve_tickers: plane read failed: {type(e).__name__}: {e}", file=sys.stderr)
        return [f"could not check universe.tickers: the {key} price plane could not be read"]
    return [f"universe.tickers: unknown ticker {t!r} — the {key} price plane has no "
            f"prices for it{' (crypto pairs are quoted in USDT, e.g. BTCUSDT)' if key == 'CRYPTO' else ''}"
            for t, sym in symbols.items() if sym not in found]
