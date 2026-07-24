"""Clube do Valor "Nova Seleção Quantitativa" — the N cheapest stocks of the bolsa.

Bank-inclusive deep-value screen. Non-financials are ranked by EBIT/EV; banks
(which have no EBIT) are injected by lowest P/L + P/VP into the same ranking.
See methodologies/cheapest.py for the full method.

Usage:
    python -m scripts.rank_cheapest                 # top 20 clean, drawn from a top-30 pool
    python -m scripts.rank_cheapest --pool 40 --top 25
    python -m scripts.rank_cheapest --no-trap-filter # raw top-N, keep value traps in
    python -m scripts.rank_cheapest --liq 200000    # override daily-liquidity floor
    python -m scripts.rank_cheapest --max-balance-age 135  # tighter freshness window
    python -m scripts.rank_cheapest --refresh-dates  # re-fetch balance dates (ignore sidecar)
    python -m scripts.rank_cheapest --snapshot data/snapshots/2026-06-10.csv

Pipeline: liquidity filter → DROP stale financials (balance older than
--max-balance-age days vs the snapshot date) across the whole universe → rank →
flag value traps (low ROE / distorted P/L / implausible DY) → pool → clean top.
Freshness needs one detail fetch per liquid name, cached in
data/balance_dates/<snapshot-date>.csv.

Analysis only — not a buy/sell recommendation.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from methodologies import cheapest
from scripts import balance_dates
from scripts._common import SNAPSHOTS, latest_csv_path

ROOT = Path(__file__).resolve().parent.parent
SECTORS = ROOT / "data" / "sectors_cache.csv"


def latest_snapshot() -> Path:
    snap = latest_csv_path(SNAPSHOTS)
    if snap is None:
        raise SystemExit("No snapshots in data/snapshots/. Run fetch_fundamentus.py first.")
    return snap


def load_sectors() -> dict[str, str]:
    if not SECTORS.exists():
        return {}
    s = pd.read_csv(SECTORS)
    return dict(zip(s["ticker"], s["subsetor"].fillna(s["setor"])))


def _render(frame: pd.DataFrame, sectors: dict[str, str], *, with_flags: bool) -> str:
    view = frame.copy()
    view.insert(1, "setor", view["papel"].map(sectors).fillna("—"))
    cols = ["papel", "setor", "leg", "cheapness", "cotacao",
            "pl", "pvp", "evebit", "roe", "dy"]
    if with_flags:
        cols.append("trap_flags")
    cols = [c for c in cols if c in view.columns]
    view = view[cols].copy()
    for c in view.columns:
        if pd.api.types.is_numeric_dtype(view[c]):
            view[c] = view[c].round(3)
    if with_flags and "trap_flags" in view.columns:
        view["trap_flags"] = view["trap_flags"].replace("", "—")
    return tabulate(view, headers="keys", tablefmt="github", showindex=False)


def _report_dropped(dropped: pd.DataFrame) -> None:
    """Print the names excluded for stale/unknown financials (transparency)."""
    if dropped.empty:
        return
    stale = dropped[dropped["freshness_reason"].str.startswith("stale")]
    nodate = dropped[dropped["freshness_reason"] == "no-date"]
    if len(stale):
        view = stale.sort_values("balance_age_days", ascending=False)[
            ["papel", "balance_date", "balance_age_days"]].copy()
        view["balance_date"] = pd.to_datetime(view["balance_date"]).dt.date.astype(str)
        print(f"\n── Excluded for stale financials ({len(stale)}) ──\n")
        print(tabulate(view, headers=["papel", "últ. balanço", "idade (dias)"],
                       tablefmt="github", showindex=False))
    if len(nodate):
        names = ", ".join(sorted(nodate["papel"]))
        print(f"\n── Excluded — no usable balance date ({len(nodate)}) ──")
        print(f"{names}\n(try --refresh-dates if a fetch glitched)")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=20,
                   help="size of the final clean list (default 20)")
    p.add_argument("--pool", type=int, default=30,
                   help="candidate pool to draw from before removing traps (default 30)")
    p.add_argument("--no-trap-filter", action="store_true",
                   help="don't drop value traps — just show the raw top-`pool`")
    p.add_argument("--liq", type=float, default=cheapest.MIN_LIQ_DAILY,
                   help=f"daily-liquidity floor in BRL (default {cheapest.MIN_LIQ_DAILY:,.0f})")
    p.add_argument("--snapshot", type=str, help="path to specific snapshot CSV (default: latest)")
    p.add_argument("--max-balance-age", type=int, default=cheapest.MAX_BALANCE_AGE_DAYS,
                   help=f"drop names whose last balance is older than this many days vs the "
                        f"snapshot date (default {cheapest.MAX_BALANCE_AGE_DAYS}); "
                        f"0 disables the freshness filter")
    p.add_argument("--refresh-dates", action="store_true",
                   help="re-fetch balance dates, ignoring the per-snapshot sidecar")
    p.add_argument("--max-workers", type=int, default=5,
                   help="concurrent detail fetches for balance dates (default 5)")
    p.add_argument("--as-of", type=str,
                   help="reference date for freshness (YYYY-MM-DD); default: parsed from snapshot filename")
    args = p.parse_args(argv[1:])

    snap = Path(args.snapshot) if args.snapshot else latest_snapshot()
    df = pd.read_csv(snap)
    print(f"Snapshot: {snap.name} ({len(df)} tickers)", file=sys.stderr)

    # ---- Financial-statement freshness: hard pre-rank filter over the whole
    # liquidity-passing universe (a stale balance poisons the multiples we rank on).
    if args.max_balance_age and args.max_balance_age > 0:
        ref_date = pd.to_datetime(args.as_of or snap.stem, errors="coerce")
        if pd.isna(ref_date):
            ref_date = pd.Timestamp.today().normalize()
            print(f"Warning: could not derive a reference date from '{snap.stem}'; "
                  f"using today ({ref_date.date()}). Pass --as-of to fix.", file=sys.stderr)

        universe = df[df["liq2m"].fillna(0) >= args.liq].copy()
        dates = balance_dates.load_or_fetch(
            universe["papel"].tolist(), str(ref_date.date()),
            refresh=args.refresh_dates, max_workers=args.max_workers)
        annotated = cheapest.annotate_freshness(
            universe, dates, ref_date, max_age_days=args.max_balance_age)
        df, dropped = cheapest.split_by_freshness(annotated)

        n_stale = int((dropped["freshness_reason"].str.startswith("stale")).sum())
        n_nodate = int((dropped["freshness_reason"] == "no-date").sum())
        print(f"Freshness filter (vs {ref_date.date()}, >{args.max_balance_age}d): "
              f"kept {len(df)}/{len(universe)}, dropped {n_stale} stale + {n_nodate} no-date",
              file=sys.stderr)
        _report_dropped(dropped)

    ranked = cheapest.flag_traps(cheapest.rank(df, liq_floor=args.liq))
    n_nonfin = int((ranked["leg"] == "EBIT/EV").sum())
    n_banks = int((ranked["leg"] == "P/L+P/VP").sum())
    print(f"Survived filters: {len(ranked)} ({n_nonfin} non-fin, {n_banks} bank/EBIT-less leg)",
          file=sys.stderr)

    sectors = load_sectors()
    pool = ranked.head(args.pool).copy()

    if args.no_trap_filter:
        n_banks_pool = int((pool["leg"] == "P/L+P/VP").sum())
        print(f"\nAs {len(pool)} Ações Mais Baratas — CdV Nova Seleção Quantitativa (raw)")
        print(f"({n_banks_pool} banks/EBIT-less names)\n")
        print(_render(pool, sectors, with_flags=True))
        return 0

    # Clean list: cheapest non-trap names. Drawn from the full ranking so we can
    # always backfill to `top` even if the top-`pool` is trap-heavy.
    clean = ranked[~ranked["is_trap"]].head(args.top).copy()
    traps_in_pool = pool[pool["is_trap"]]

    print(f"\n── Candidate pool: top {len(pool)} by cheapness "
          f"({len(traps_in_pool)} flagged as value traps) ──\n")
    print(_render(pool, sectors, with_flags=True))

    backfilled = (clean["cheapness"].min() < pool["cheapness"].min()) if len(clean) else False
    note = " (backfilled past the pool to reach the target count)" if backfilled else ""
    n_banks_clean = int((clean["leg"] == "P/L+P/VP").sum())
    print(f"\n── Clean {len(clean)}: value traps removed{note} "
          f"({n_banks_clean} banks) ──\n")
    print(_render(clean, sectors, with_flags=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
