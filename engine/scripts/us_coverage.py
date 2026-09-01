#!/usr/bin/env python3
"""What the SEC ingest actually reached, measured rather than assumed.

`engine/us_pipeline/` was written and tested against synthetic fixtures in the
documented `companyfacts` shape. The tests cover the shape and the refusals;
what they cannot cover is how much of the real universe the tag choices reach.
The tag preference lists in `sec_parser` are judgement — filers use different
concepts for the same line depending on when they adopted ASC 606 — and the
current lists are reasonable but unmeasured.

So: run the ingest once against real EDGAR, then run this. It touches no
network. The numbers it prints are the ones #41 asks for, and two of them are
decisions waiting on evidence rather than statistics:

  * a low `ebitda` fill rate means somebody writes an EV/EBITDA screen and gets
    a tiny universe back without understanding why — the metric is computed
    (`OperatingIncomeLoss` + D&A) and left null when a component is missing, on
    purpose;
  * `net_debt` has the most incomplete tag list of the five, and the same
    applies.

`cik_fallback` is the size of what is left of #38: filers whose facts are on the
plane under a `CIK##########` placeholder because no ticker could be resolved
for them. Measured before and after a `submissions.zip` pass, it says whether
that source was worth its gigabyte.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from b3_pipeline import storage

logger = logging.getLogger(__name__)

METRICS = ["revenue", "net_income", "ebitda", "total_assets", "equity",
           "net_debt", "shares_outstanding", "net_income_ttm"]

# Below this, a metric that ought to be near-universal is a missing tag rather
# than a fact about the market.
EXPECTED_UNIVERSAL = ("revenue", "net_income", "total_assets", "equity")
UNIVERSAL_FLOOR = 0.90


def fill_rates(conn) -> dict:
    """Share of fact rows carrying each metric."""
    counted = ", ".join(
        f"COUNT({m}) AS {m}" for m in METRICS)
    row = storage.read_sql(
        f"SELECT COUNT(*) AS rows, {counted} FROM fundamentals_pit_us",
        conn).iloc[0]
    total = int(row["rows"])
    return {m: (int(row[m]) / total if total else 0.0) for m in METRICS}


def measure(conn) -> dict:
    """The whole report, as data. Rendering is a separate concern."""
    totals = storage.read_sql(
        "SELECT COUNT(*) AS rows, COUNT(DISTINCT cik) AS filers,"
        " MIN(period_end) AS first_period, MAX(period_end) AS last_period"
        " FROM fundamentals_pit_us", conn).iloc[0]
    instruments = storage.read_sql(
        "SELECT COUNT(*) AS n,"
        " SUM(CASE WHEN symbol LIKE 'CIK%' THEN 1 ELSE 0 END) AS placeholders,"
        " SUM(CASE WHEN delisting_date IS NOT NULL THEN 1 ELSE 0 END) AS delisted"
        " FROM instruments WHERE market = 'US'", conn).iloc[0]
    pit = storage.read_sql(
        "SELECT COUNT(*) AS pairs, COUNT(DISTINCT cik) AS ciks"
        " FROM us_ticker_pit", conn).iloc[0]

    n_instruments = int(instruments["n"] or 0)
    placeholders = int(instruments["placeholders"] or 0)
    return {
        "rows": int(totals["rows"] or 0),
        "filers": int(totals["filers"] or 0),
        "period": [totals["first_period"], totals["last_period"]],
        "fill_rate": fill_rates(conn),
        "instruments": n_instruments,
        "delisted": int(instruments["delisted"] or 0),
        "cik_fallback": {
            "count": placeholders,
            "pct": placeholders / n_instruments if n_instruments else 0.0,
        },
        "ticker_pit": {"pairs": int(pit["pairs"] or 0),
                       "ciks": int(pit["ciks"] or 0)},
    }


def warnings(report: dict) -> list:
    """The findings that are decisions, not statistics."""
    out = []
    for metric in EXPECTED_UNIVERSAL:
        rate = report["fill_rate"].get(metric, 0.0)
        if rate < UNIVERSAL_FLOOR:
            out.append(
                f"{metric} is on {rate:.1%} of rows. It should be close to "
                f"universal — the tag preference list in sec_parser is missing "
                f"a concept filers actually use.")
    if report["cik_fallback"]["count"]:
        out.append(
            f"{report['cik_fallback']['count']:,} instruments "
            f"({report['cik_fallback']['pct']:.1%}) still sit under a "
            "CIK placeholder: their fundamentals cannot join any price series.")
    if report["instruments"] and not report["delisted"]:
        out.append(
            "no US instrument carries a delisting date. Either the listing "
            "ingest has not run or the plane is dropping its dead somewhere "
            "upstream — a survivorship-free backtest is impossible either way.")
    return out


def render(report: dict) -> str:
    lines = [
        f"US fundamentals: {report['rows']:,} facts over "
        f"{report['filers']:,} filers, {report['period'][0]} .. "
        f"{report['period'][1]}",
        "",
        "fill rate by metric",
    ]
    for metric, rate in report["fill_rate"].items():
        bar = "#" * int(rate * 40)
        lines.append(f"  {metric:<20} {rate:6.1%}  {bar}")
    lines += [
        "",
        f"instruments        {report['instruments']:,} "
        f"({report['delisted']:,} delisted)",
        f"CIK placeholders   {report['cik_fallback']['count']:,} "
        f"({report['cik_fallback']['pct']:.1%})",
        f"ticker history     {report['ticker_pit']['pairs']:,} pairs over "
        f"{report['ticker_pit']['ciks']:,} CIKs",
    ]
    found = warnings(report)
    if found:
        lines += ["", "worth acting on:"]
        lines += [f"  - {w}" for w in found]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    with storage.get_connection(args.db_path) as conn:
        report = measure(conn)
    print(json.dumps(report, indent=2, default=str) if args.json
          else render(report))


if __name__ == "__main__":
    main()
