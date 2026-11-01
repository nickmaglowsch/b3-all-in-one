"""A renda-fixa ETF sleeve gets a trade, not just a weight.

advise_weights priced only from `prices`, where B3's RF ETFs never are, so an
IMAB11 target came back `unpriced_skipped`. Runs on a temporary SQLite plane.
"""
import argparse
import json
import sqlite3

from b3_pipeline import storage
from scripts import advise_weights


def test_weights_mode_prices_a_renda_fixa_etf_from_its_own_close(tmp_path, monkeypatch):
    db = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    conn.execute("INSERT INTO prices (ticker, isin_code, date, close, adj_close, volume)"
                 " VALUES ('PETR4', 'X', '2026-10-30', 40.0, 40.0, 1e6)")
    conn.executemany(
        "INSERT INTO external_prices (ticker, isin_code, date, close, volume, source)"
        " VALUES (?, 'X', ?, ?, 0, 'T')",
        [("IMAB11", "2026-10-29", 99.0), ("IMAB11", "2026-10-30", 100.0),
         ("$IMAB", "2026-10-30", 11919.9)])           # an index is never a price
    conn.commit()
    conn.close()
    monkeypatch.setenv("ENGINE_DATABASE_URL", db)

    out = advise_weights._advise_from_weights(argparse.Namespace(
        weights=json.dumps({"IMAB11": 0.5, "PETR4": 0.5}),
        positions=json.dumps({}), nav_cash=10_000.0))

    trades = json.dumps(out["trades"])
    assert "IMAB11" not in out["trades"].get("unpriced_skipped", []), trades
    assert "IMAB11" in trades and "100.0" in trades
    assert advise_weights._rf_etf_closes(db, "2026-10-29") == {"IMAB11": 99.0}
