"""bench_series serves the portfolio page's IBOV/CDI overlay from the plane.

It used to fetch IBOV from Yahoo and CDI from BCB on every cache miss, inside a
request. Both series are already stored — `$IBOV` and `$CDI` in
`external_prices` (written by b3_pipeline/ibov_series.py and index_series.py) — so the request path
now reads the database and touches nothing external.

The consequence worth testing: `offline` no longer means "we were told not to
fetch", it means "the plane has nothing for this window", which is the only
honest reason to draw no benchmark.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from b3_pipeline import storage

ROOT = Path(__file__).resolve().parents[1]


def _load_bench_series():
    spec = importlib.util.spec_from_file_location(
        "bench_series", ROOT / "scripts" / "bench_series.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def plane(tmp_path, monkeypatch):
    db_path = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(db_path)
    storage.init_db(conn)
    conn.commit()
    conn.close()
    monkeypatch.setenv("ENGINE_DATABASE_URL", db_path)
    return db_path


def _ibov(db_path: str, rows: list[tuple[str, float]]) -> None:
    conn = sqlite3.connect(db_path)
    for date_str, pts in rows:
        conn.execute(
            """INSERT INTO external_prices
                   (ticker, isin_code, date, open, high, low, close, volume, source)
               VALUES ('$IBOV', 'UNKNOWN', ?, NULL, NULL, NULL, ?, 0, 'B3_INDEX')""",
            (date_str, pts),
        )
    conn.commit()
    conn.close()


def _cdi(db_path: str, rows: list[tuple[str, float]]) -> None:
    conn = sqlite3.connect(db_path)
    for date_str, level in rows:
        conn.execute(
            """INSERT INTO external_prices
                   (ticker, isin_code, date, open, high, low, close, volume, source)
               VALUES ('$CDI', 'UNKNOWN', ?, NULL, NULL, NULL, ?, 0, 'BCB_SGS')""",
            (date_str, level),
        )
    conn.commit()
    conn.close()


def test_both_series_come_from_the_plane(plane):
    _ibov(plane, [("2024-01-02", 132696.63), ("2024-01-03", 131000.0)])
    _cdi(plane, [("2024-01-02", 1.5), ("2024-01-03", 1.5004)])

    out = _load_bench_series().series("2024-01-01", "2024-01-31")

    assert out["offline"] is False
    assert out["ibov"] == {"2024-01-02": 132696.63, "2024-01-03": 131000.0}
    assert out["cdi"] == {"2024-01-02": 1.5, "2024-01-03": 1.5004}


def test_empty_plane_reports_offline(plane):
    """No rows is not a flat benchmark — the caller must be able to say so."""
    out = _load_bench_series().series("2024-01-01", "2024-01-31")

    assert out["offline"] is True
    assert out["ibov"] == {} and out["cdi"] == {}


def test_one_series_present_is_not_offline(plane):
    """CDI is written by a different pipeline step than $IBOV; one being late
    must not blank the other."""
    _ibov(plane, [("2024-01-02", 100.0)])

    out = _load_bench_series().series("2024-01-01", "2024-01-31")

    assert out["offline"] is False
    assert out["ibov"] == {"2024-01-02": 100.0}
    assert out["cdi"] == {}


# ── the named catalogue ───────────────────────────────────────────────────────
# The bench compares backtests against each other; a run also wants a ruler that
# is not another run. Those rulers are already on the plane — the same rows the
# portfolio page reads — so the script grew a name list rather than a second
# reader. `ibov,cdi` stays the default so the portfolio index's call is unchanged.


def _external(db_path: str, ticker: str, rows: list[tuple[str, float]],
              source: str = "TEST") -> None:
    conn = sqlite3.connect(db_path)
    for date_str, close in rows:
        conn.execute(
            """INSERT INTO external_prices
                   (ticker, isin_code, date, open, high, low, close, volume, source)
               VALUES (?, 'UNKNOWN', ?, NULL, NULL, NULL, ?, 0, ?)""",
            (ticker, date_str, close, source),
        )
    conn.commit()
    conn.close()


def _crypto(db_path: str, symbol: str, rows: list[tuple[str, float]]) -> None:
    """BTC's ruler is its BRL close — `adj_close_brl`, the column every non-B3
    reader projects, because the numeraire is reais for every market."""
    conn = sqlite3.connect(db_path)
    for date_str, brl in rows:
        conn.execute(
            """INSERT INTO market_prices
                   (market, symbol, date, open, high, low, close, volume,
                    split_adj_close, adj_close, close_brl, adj_close_brl,
                    fx_rate, source)
               VALUES ('CRYPTO', ?, ?, NULL, NULL, NULL, ?, 0, NULL, NULL,
                       ?, ?, 5.0, 'TEST')""",
            (symbol, date_str, brl / 5.0, brl, brl),
        )
    conn.commit()
    conn.close()


def test_the_catalogue_names_every_ruler_the_bench_offers(plane):
    """The keys are the API's contract — the platform and the UI both spell
    them, so a rename here is a breaking change and should fail a test."""
    mod = _load_bench_series()

    assert set(mod.BENCHMARKS) == {
        "ibov", "cdi", "ipca", "sp500", "bitcoin", "selic",
        "irfm1", "irfm1p", "irfm", "irfmp2", "imab5", "imab5p", "imab5p2",
        "imab", "imas", "imageral"}


def test_named_benchmarks_read_their_own_plane_rows(plane):
    _external(plane, "$IPCA", [("2024-01-02", 1.02)])
    _external(plane, "$SPX", [("2024-01-02", 2500.0)])
    _crypto(plane, "BTCUSDT", [("2024-01-02", 210000.0)])

    out = _load_bench_series().series(
        "2024-01-01", "2024-01-31", ["ipca", "sp500", "bitcoin"])

    assert out["ipca"] == {"2024-01-02": 1.02}
    assert out["sp500"] == {"2024-01-02": 2500.0}
    # In reais, not in USDT: adj_close_brl is what the reader projects.
    assert out["bitcoin"] == {"2024-01-02": 210000.0}
    assert out["offline"] is False


def test_only_the_requested_names_come_back(plane):
    """Asking for IPCA must not pay for four other reads, nor hand the caller
    keys it has to filter out."""
    _external(plane, "$IPCA", [("2024-01-02", 1.02)])
    _external(plane, "$IBOV", [("2024-01-02", 130000.0)])

    out = _load_bench_series().series("2024-01-01", "2024-01-31", ["ipca"])

    assert set(out) == {"ipca", "offline"}


def test_offline_is_about_the_names_that_were_asked_for(plane):
    """A plane with IBOV but no bitcoin is not "the plane is empty" for a
    caller that asked for IBOV — and is, for one that asked for bitcoin."""
    _external(plane, "$IBOV", [("2024-01-02", 130000.0)])

    assert _load_bench_series().series(
        "2024-01-01", "2024-01-31", ["ibov"])["offline"] is False
    assert _load_bench_series().series(
        "2024-01-01", "2024-01-31", ["bitcoin"])["offline"] is True


def test_an_unknown_name_is_refused_rather_than_answered_empty(plane):
    """Empty and unknown are different answers: the first says the plane has
    no rows yet, the second says the caller has a typo. Reporting a typo as an
    empty series is how a benchmark silently disappears from a chart."""
    with pytest.raises(KeyError):
        _load_bench_series().series("2024-01-01", "2024-01-31", ["ibovespa"])


def test_the_cli_parses_a_name_list(plane, capsys):
    """`bench_series.py <start> <end> [names]` — the platform passes the third
    argument; the portfolio index still calls it with two."""
    _external(plane, "$IPCA", [("2024-01-02", 1.02)])
    mod = _load_bench_series()

    mod.main(["2024-01-01", "2024-01-31", "ipca"])

    assert json.loads(capsys.readouterr().out) == {
        "ipca": {"2024-01-02": 1.02}, "offline": False}
