#!/usr/bin/env python3
"""Capture real vendor samples as golden files. **Run this outside the sandbox.**

Everything in `us_pipeline` was written against the documented shapes, with
parsers that read columns by name and fail loudly when one moves — the
parser-drift mitigation `spec/00-overview.md` asks for under risk 4. This script
is the other half of it: run it once on a machine with egress and a real key, and
`tests/test_us_fixtures.py` starts driving those same parsers from real files
instead of synthetic ones. Until then that suite skips, so nothing is blocked and
nothing pretends to be verified either.

The build environment this was written in reaches none of these hosts: the egress
proxy refuses `alphavantage.co` and `sec.gov` alike at the CONNECT tunnel, so a
sample could not be fetched here at any point.

    export ALPHAVANTAGE_API_KEY=...
    python engine/scripts/capture_us_fixtures.py --out engine/tests/fixtures/us

Files are truncated on purpose. They pin the *shape* — the header, the column
names, the date format, the units — and a few hundred rows do that as well as a
million while staying reviewable in a diff.

The most valuable capture is the one that is not a payload at all. This vendor
answers HTTP 200 with a JSON envelope when it throttles, when the key is wrong
and when an endpoint is premium, and **which key carries which condition is the
one thing in the client that could not be written from documentation**. Run with
`--probe-refusal` once the day's quota is spent and the envelope lands in
`refusal.json`; `alphavantage._classify` is what it corrects.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from us_pipeline import alphavantage as av

logger = logging.getLogger(__name__)

# One sample per shape a parser reads, and one symbol that is definitely dead.
# `head` is the number of data rows kept.
CSV_TARGETS = [
    ("listing_status_active.csv", "LISTING_STATUS", {"state": "active"}, 200),
    ("listing_status_delisted.csv", "LISTING_STATUS", {"state": "delisted"}, 200),
    ("time_series_daily_IBM.csv", "TIME_SERIES_DAILY",
     {"symbol": "IBM", "outputsize": "full"}, 60),
    ("time_series_daily_SPY.csv", "TIME_SERIES_DAILY",
     {"symbol": "SPY", "outputsize": "full"}, 60),
    # The question no documentation answers: does the vendor serve history for a
    # symbol that no longer trades? If it does not, the US plane's universe is
    # survivorship-free and its *prices* are not, which is a different claim and
    # has to be said out loud rather than discovered by a user.
    ("time_series_daily_DELISTED.csv", "TIME_SERIES_DAILY",
     {"symbol": "LEHMQ", "outputsize": "full"}, 60),
]

JSON_TARGETS = [
    ("splits_AAPL.json", "SPLITS", {"symbol": "AAPL"}),
    ("dividends_AAPL.json", "DIVIDENDS", {"symbol": "AAPL"}),
    ("splits_SPY.json", "SPLITS", {"symbol": "SPY"}),
    ("dividends_SPY.json", "DIVIDENDS", {"symbol": "SPY"}),
]


def _truncate(text: str, rows: int) -> str:
    """Header plus `rows` data lines."""
    lines = text.splitlines()
    return "\n".join(lines[:rows + 1]) + "\n"


def capture(client, out_dir: Path) -> dict:
    """Fetch every sample, recording what each endpoint actually did.

    One refusal never costs the others: a premium endpoint or an exhausted quota
    is itself a finding, and the run should report all of them rather than stop
    at the first.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {}

    for name, function, params, rows in CSV_TARGETS:
        try:
            body = client.get_csv(function, **params)
        except av.AlphaVantageError as exc:
            result[name] = f"{type(exc).__name__}: {exc}"
            continue
        (out_dir / name).write_text(_truncate(body, rows))
        result[name] = f"ok ({len(body.splitlines()) - 1} rows, truncated to {rows})"

    for name, function, params in JSON_TARGETS:
        try:
            payload = client.get_json(function, **params)
        except av.AlphaVantageError as exc:
            result[name] = f"{type(exc).__name__}: {exc}"
            continue
        (out_dir / name).write_text(json.dumps(payload, indent=2)[:200_000])
        result[name] = f"ok ({len(payload.get('data') or [])} events)"

    return result


def probe_refusal(client, out_dir: Path) -> str:
    """Provoke a refusal envelope and keep it verbatim.

    Deliberately a separate flag. It is worth doing once, with a spent quota,
    because `alphavantage._classify` maps "Error Message" to a permanent refusal
    and "Note"/"Information" to a retryable one — and if a *bad key* were to
    arrive as "Information", the client would treat it as retryable and burn the
    budget on it forever.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        client.get_csv("TIME_SERIES_DAILY", symbol="IBM")
    except av.AlphaVantageError as exc:
        (out_dir / "refusal.txt").write_text(f"{type(exc).__name__}\n{exc}\n")
        return f"captured: {type(exc).__name__}"
    return "no refusal — the quota is not spent yet, so nothing was captured"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="engine/tests/fixtures/us",
                    help="where to write the samples")
    ap.add_argument("--probe-refusal", action="store_true",
                    help="provoke and record a refusal envelope instead of "
                         "capturing payloads")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    out_dir = Path(args.out)
    # No database here: this runs on a laptop, and the durable budget is a
    # production concern. A connection-less client has no local ceiling, so the
    # vendor's own refusal is the ceiling — and capturing that is half the point.
    try:
        client = av.Client(None)
    except av.MissingApiKey as exc:
        raise SystemExit(str(exc)) from None

    if args.probe_refusal:
        print(probe_refusal(client, out_dir))
        return

    outcomes = capture(client, out_dir)
    for name, outcome in outcomes.items():
        print(f"{name:<34} {outcome}")

    captured = sum(1 for o in outcomes.values() if o.startswith("ok"))
    if not captured:
        raise SystemExit(
            "\nNothing was captured. Every request was refused — check the key, "
            "and check that this machine can reach alphavantage.co at all "
            "(a proxy that refuses the CONNECT tunnel looks exactly like this).")
    print(f"\n{captured}/{len(outcomes)} captured to {out_dir}. Review them, then "
          f"commit the ones you are happy to keep — tests/test_us_fixtures.py "
          f"picks up whatever is there and skips whatever is not.")


if __name__ == "__main__":
    main()
