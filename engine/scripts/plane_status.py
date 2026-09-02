#!/usr/bin/env python3
"""What the market plane actually holds, per market, measured on the box.

The question this answers is the one you ask before starting a backfill: *do we
already have this?* `GET /market/status` answers a version of it over HTTP, but
it needs the API up and a session, and the moment you actually want the number
is while you are already in a shell on the host deciding whether to spend three
hours re-ingesting something.

Touches no network and writes nothing. Safe to run against production while the
ingest worker is mid-run — the numbers will simply be a snapshot of a moving
table, which is what you want to see.

    python -m scripts.plane_status                 # every market
    python -m scripts.plane_status --market US     # one
    python -m scripts.plane_status --json          # for a script to read

`gaps` is the part worth reading twice. A market with rows is not thereby a
market with *coverage*: the US plane can hold eleven thousand symbols and still
be useless to a fundamental strategy if none of them carry a CIK, and the crypto
plane can look complete while holding only the living pairs. Each market reports
the specific hole that would make its rows misleading.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from b3_pipeline import storage  # noqa: E402

logger = logging.getLogger(__name__)

# The B3 plane predates `market_prices` and still lives in `prices`, so it is
# counted separately rather than pretended into the same table.
B3 = "B3"


def _one(conn, sql: str, params=(), default=0):
    df = storage.read_sql(sql, conn, params=params)
    return default if df.empty else df.iloc[0].iloc[0]


def b3_summary(conn) -> dict:
    """The B3 plane, out of `prices` and `instruments`."""
    return {
        "market": B3,
        "table": "prices",
        "symbols": int(_one(conn, "SELECT COUNT(DISTINCT ticker) FROM prices")),
        "rows": int(_one(conn, "SELECT COUNT(*) FROM prices")),
        "first_bar": _one(conn, "SELECT MIN(date) FROM prices", default=None),
        "last_bar": _one(conn, "SELECT MAX(date) FROM prices", default=None),
        "instruments": int(_one(
            conn, "SELECT COUNT(*) FROM instruments WHERE market = ?", (B3,))),
        "delisted": int(_one(
            conn, "SELECT COUNT(*) FROM instruments"
                  " WHERE market = ? AND delisting_date IS NOT NULL", (B3,))),
    }


def market_summary(conn, market: str) -> dict:
    """One non-B3 market, out of `market_prices` and `instruments`."""
    return {
        "market": market,
        "table": "market_prices",
        "symbols": int(_one(
            conn, "SELECT COUNT(DISTINCT symbol) FROM market_prices"
                  " WHERE market = ?", (market,))),
        "rows": int(_one(
            conn, "SELECT COUNT(*) FROM market_prices WHERE market = ?",
            (market,))),
        "first_bar": _one(
            conn, "SELECT MIN(date) FROM market_prices WHERE market = ?",
            (market,), default=None),
        "last_bar": _one(
            conn, "SELECT MAX(date) FROM market_prices WHERE market = ?",
            (market,), default=None),
        "instruments": int(_one(
            conn, "SELECT COUNT(*) FROM instruments WHERE market = ?", (market,))),
        "delisted": int(_one(
            conn, "SELECT COUNT(*) FROM instruments"
                  " WHERE market = ? AND delisting_date IS NOT NULL", (market,))),
        "sources": _sources(conn, market),
        # BRL columns are materialised at ingest through PTAX. A row that PTAX
        # could not cover keeps them NULL, which is honest and also invisible
        # unless something counts them — the portfolio and the tax engine read
        # the BRL column, so this is the number that says whether they can.
        "unconverted_brl": int(_one(
            conn, "SELECT COUNT(*) FROM market_prices"
                  " WHERE market = ? AND close_brl IS NULL", (market,))),
    }


def _sources(conn, market: str) -> dict:
    """Rows per `source`, which is where each feed stamps what it wrote.

    Reading this is how you tell a US plane backfilled from the bulk archive
    (`stooq-bulk:split_only`) from one trickled out of the metered feed.
    """
    df = storage.read_sql(
        "SELECT source, COUNT(*) AS n FROM market_prices WHERE market = ?"
        " GROUP BY source ORDER BY n DESC", conn, params=(market,))
    return {r["source"]: int(r["n"]) for _, r in df.iterrows()}


def us_gaps(conn) -> list:
    """What would make the US numbers above misleading."""
    gaps = []
    priced = int(_one(conn, "SELECT COUNT(DISTINCT symbol) FROM market_prices"
                            " WHERE market = 'US'"))
    listed = int(_one(conn, "SELECT COUNT(*) FROM instruments"
                            " WHERE market = 'US'"))
    if listed and priced < listed:
        gaps.append(f"{listed - priced:,} of {listed:,} US instruments have no "
                    "bars — run us_pipeline.us_backfill")
    if not int(_one(conn, "SELECT COUNT(*) FROM instruments"
                          " WHERE market = 'US' AND cik IS NOT NULL")):
        gaps.append("no US instrument carries a CIK — fundamentals cannot join "
                    "to prices; run us_pipeline.us_main")
    if not int(_one(conn, "SELECT COUNT(*) FROM instruments"
                          " WHERE market = 'US' AND delisting_date IS NOT NULL")):
        gaps.append("no US instrument is marked delisted — the universe is "
                    "survivorship-biased; run us_pipeline.us_listing")
    states = storage.read_sql(
        "SELECT adjust_state, COUNT(*) AS n FROM us_price_cursor"
        " WHERE adjust_state IS NOT NULL GROUP BY adjust_state", conn)
    counts = {r["adjust_state"]: int(r["n"]) for _, r in states.iterrows()}
    if counts:
        gaps.append("adjust_state: " + ", ".join(
            f"{k}={v:,}" for k, v in sorted(counts.items())))
    spent = storage.read_sql(
        "SELECT source, day, spent FROM api_call_budget"
        " ORDER BY day DESC LIMIT 1", conn)
    if not spent.empty:
        row = spent.iloc[0]
        gaps.append(f"metered budget: {row['spent']} requests spent on "
                    f"{row['day']} ({row['source']})")
    return gaps


def crypto_gaps(conn) -> list:
    """What would make the crypto numbers above misleading."""
    gaps = []
    dead = int(_one(conn, "SELECT COUNT(*) FROM instruments"
                          " WHERE market = 'CRYPTO' AND delisting_date IS NOT NULL"))
    total = int(_one(conn, "SELECT COUNT(*) FROM instruments"
                           " WHERE market = 'CRYPTO'"))
    if total and not dead:
        # The archive holds thousands of pairs and hundreds of them stopped
        # years ago. None marked dead means the enumeration came from somewhere
        # that only knows the living, which is the bias the pipeline exists to
        # avoid — or that the ingest never finished.
        gaps.append("no crypto pair is marked delisted — either the ingest did "
                    "not finish or the universe is survivorship-biased")
    if total and total < 500:
        gaps.append(f"only {total:,} crypto pairs on the plane; the Binance "
                    "archive holds thousands — the ingest looks partial")
    return gaps


GAP_CHECKS = {"US": us_gaps, "CRYPTO": crypto_gaps}


def collect(conn, only: str = None, db_path=None) -> dict:
    """Every market's summary, plus its gaps. Read-only."""
    markets = storage.read_sql(
        "SELECT DISTINCT market FROM market_prices ORDER BY market", conn)
    names = list(markets["market"]) if not markets.empty else []
    # A market with instruments but no bars is exactly the state worth
    # reporting, so the two sources of names are unioned rather than joined.
    registered = storage.read_sql(
        "SELECT DISTINCT market FROM instruments ORDER BY market", conn)
    for name in (list(registered["market"]) if not registered.empty else []):
        if name != B3 and name not in names:
            names.append(name)

    out = []
    if only in (None, B3):
        out.append(b3_summary(conn))
    for name in sorted(names):
        if only and name != only:
            continue
        summary = market_summary(conn, name)
        check = GAP_CHECKS.get(name)
        summary["gaps"] = check(conn) if check else []
        out.append(summary)
    return {"target": storage.describe_target(db_path), "markets": out}


def render(report: dict) -> str:
    lines = [f"plane: {report['target']}", ""]
    for m in report["markets"]:
        lines.append(f"── {m['market']} ({m['table']})")
        lines.append(f"   symbols     {m['symbols']:,}")
        lines.append(f"   rows        {m['rows']:,}")
        lines.append(f"   bars        {m['first_bar']} .. {m['last_bar']}")
        lines.append(f"   instruments {m['instruments']:,} "
                     f"({m['delisted']:,} delisted)")
        if m.get("unconverted_brl"):
            lines.append(f"   no BRL      {m['unconverted_brl']:,} rows")
        for source, n in (m.get("sources") or {}).items():
            lines.append(f"   source      {source}: {n:,}")
        for gap in m.get("gaps") or []:
            lines.append(f"   ! {gap}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--market", default=None,
                    help="only this market (B3, US, CRYPTO)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    with storage.get_connection(args.db_path) as conn:
        report = collect(conn, args.market, args.db_path)
    print(json.dumps(report, indent=2, default=str) if args.as_json
          else render(report))


if __name__ == "__main__":
    main()
