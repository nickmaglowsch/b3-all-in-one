"""Fetch & cache each ticker's last processed balance-sheet date (freshness).

The Fundamentus screener snapshot carries no report date, so we read
'Ult_balanco_processado' from each ticker's detail page (fundamentus.get_papel)
and cache it in a per-snapshot sidecar:

    data/balance_dates/<snapshot-date>.csv   (ticker, ult_balanco_processado, fetched_at)

Per-snapshot (not a rolling cache) so re-running an old snapshot reproduces the
exact freshness verdicts it had on the day. HTTP responses are additionally
cached by the `fundamentus` package (http_cache.sqlite).
"""
from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    import fundamentus
except ImportError:  # pragma: no cover
    print("ERROR: 'fundamentus' package not installed. Run: pip install -e .", file=sys.stderr)
    raise

# The 'fundamentus' package logs an INFO line per detail fetch — far too noisy
# when we're fetching hundreds. Quiet its loggers to WARNING.
for _name in ("detalhes", "detalhes.get_papel", "logging", "fundamentus"):
    logging.getLogger(_name).setLevel(logging.WARNING)

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "balance_dates"

_MISSING = {None, "", "-", "nan", "NaN"}


def _fetch_one(ticker: str) -> tuple[str, str | None]:
    try:
        df = fundamentus.get_papel(ticker)
        if df is None or df.empty:
            return ticker, None
        val = df.iloc[0].to_dict().get("Ult_balanco_processado")
        val = None if val is None else str(val).strip()
        return ticker, (None if val in _MISSING else val)
    except Exception:  # network/parse failure → treated as 'no-date' downstream
        return ticker, None


def load_or_fetch(
    tickers,
    snapshot_date: str,
    *,
    refresh: bool = False,
    max_workers: int = 5,
) -> dict[str, str | None]:
    """Return {ticker -> last balance date string or None}, using the sidecar.

    Only tickers absent from the sidecar are fetched (unless refresh=True, which
    re-fetches everything). The sidecar is rewritten with the union.
    """
    DIR.mkdir(parents=True, exist_ok=True)
    sidecar = DIR / f"{snapshot_date}.csv"
    tickers = list(dict.fromkeys(tickers))  # de-dup, keep order

    cached: dict[str, str | None] = {}
    if sidecar.exists() and not refresh:
        prev = pd.read_csv(sidecar, dtype=str)
        for t, d in zip(prev["ticker"], prev["ult_balanco_processado"]):
            cached[t] = None if pd.isna(d) else d

    to_fetch = list(tickers) if refresh else [
        t for t in tickers if t not in cached or cached.get(t) is None
    ]

    if to_fetch:
        print(f"Fetching balance dates for {len(to_fetch)} tickers "
              f"(≤{max_workers} concurrent)...", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for ticker, bal in ex.map(_fetch_one, to_fetch):
                cached[ticker] = bal
        now = datetime.now().isoformat(timespec="seconds")
        rows = [{"ticker": t, "ult_balanco_processado": cached.get(t), "fetched_at": now}
                for t in sorted(cached)]
        pd.DataFrame(rows).to_csv(sidecar, index=False)
        print(f"Wrote {sidecar.relative_to(ROOT)} ({len(rows)} tickers)", file=sys.stderr)

    return {t: cached.get(t) for t in tickers}
