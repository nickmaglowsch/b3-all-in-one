"""Sector exposure breakdown for the current portfolio.

Aggregates market value across sectors for every position in
data/positions.csv, joining prices from the latest equity, FII, and
external-prices snapshots and resolving sectors from:

  - Fundamentus equity detail page (Setor/Subsetor) for ações
  - data/fii_snapshots/<latest>.csv 'segmento' column for FIIs
    (prefixed "FII: " so they don't merge with equity sectors)
  - "ETF/BDR" for external-priced names (e.g. IVVB11)
  - "Unknown" for unpriced positions

The equity sector lookup hits Fundamentus' detail page which is slow
(~1-2s per ticker). Results are cached in data/sectors_cache.csv;
only tickers missing from the cache trigger a fetch.

CLAUDE.md asks for a >25% concentration flag — printed below the table.

Usage:
    python -m scripts.sector_exposure
    python -m scripts.sector_exposure --top 10
    python -m scripts.sector_exposure --no-cache
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from scripts._common import (
    EXTERNAL_PRICES,
    FII_SNAPSHOTS,
    latest_csv as _latest_csv,
    price_lookup as _build_price_lookup,
)

ROOT = Path(__file__).resolve().parent.parent
POSITIONS = ROOT / "data" / "positions.csv"
SECTORS_CACHE = ROOT / "data" / "sectors_cache.csv"


def _load_fii_segments() -> dict[str, str]:
    """Map FII ticker → segmento from the latest FII snapshot."""
    df = _latest_csv(FII_SNAPSHOTS)
    if df is None or "segmento" not in df.columns:
        return {}
    return dict(zip(df["papel"].astype(str), df["segmento"].astype(str)))


def _load_external_tickers() -> set[str]:
    """Set of tickers we know come from external sources (BDR ETFs etc.)."""
    df = _latest_csv(EXTERNAL_PRICES)
    if df is None or "papel" not in df.columns:
        return set()
    return set(df["papel"].astype(str))


def _load_cache() -> pd.DataFrame:
    """Read the sectors cache or return an empty frame with the schema."""
    cols = ["ticker", "setor", "subsetor", "fetched_at"]
    if not SECTORS_CACHE.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(SECTORS_CACHE)
    # Defensive: ensure all expected columns exist even if cache was
    # written by an older version of the script.
    for c in cols:
        if c not in df.columns:
            df[c] = pd.NA
    return df[cols]


def _save_cache(df: pd.DataFrame) -> None:
    SECTORS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(SECTORS_CACHE, index=False)


def _fetch_equity_sector(ticker: str) -> tuple[str | None, str | None]:
    """Hit Fundamentus' detail page for `ticker` and return
    (Setor, Subsetor). Returns (None, None) on any failure — the page
    is flaky for delisted/restructured names.
    """
    import fundamentus  # local import: lets --help run without the lib
    try:
        df = fundamentus.get_papel(ticker)
        if df is None or df.empty:
            return None, None
        row = df.iloc[0]
        setor = row.get("Setor")
        subsetor = row.get("Subsetor")
        # Coerce NaN/empty to None so cache stays clean.
        setor = str(setor).strip() if pd.notna(setor) and str(setor).strip() else None
        subsetor = str(subsetor).strip() if pd.notna(subsetor) and str(subsetor).strip() else None
        return setor, subsetor
    except Exception as e:  # noqa: BLE001 — Fundamentus raises various things
        print(f"  warn: get_papel({ticker}) failed: {e}", file=sys.stderr)
        return None, None


def _resolve_sectors(
    tickers: list[str],
    fii_segments: dict[str, str],
    external_tickers: set[str],
    use_cache: bool,
) -> pd.DataFrame:
    """For each ticker decide its sector label. FIIs and external names
    short-circuit; equities go through the cache. Returns columns:
    ticker, setor, subsetor.
    """
    cache = _load_cache() if use_cache else pd.DataFrame(
        columns=["ticker", "setor", "subsetor", "fetched_at"])
    cached: dict[str, tuple[str | None, str | None]] = {
        r["ticker"]: (
            r["setor"] if pd.notna(r["setor"]) else None,
            r["subsetor"] if pd.notna(r["subsetor"]) else None,
        )
        for _, r in cache.iterrows()
    }

    # Decide what each ticker needs.
    equity_to_fetch: list[str] = []
    rows: list[dict] = []
    for t in tickers:
        if t in fii_segments:
            seg = fii_segments[t] or "Outros"
            rows.append({"ticker": t, "setor": f"FII: {seg}", "subsetor": None})
        elif t in external_tickers:
            rows.append({"ticker": t, "setor": "ETF/BDR", "subsetor": None})
        elif t in cached:
            setor, subsetor = cached[t]
            rows.append({
                "ticker": t,
                "setor": setor if setor else "Unknown",
                "subsetor": subsetor,
            })
        else:
            equity_to_fetch.append(t)

    # Fetch missing equity sectors. Append to cache incrementally so a
    # crash mid-run doesn't lose the work we already did.
    if equity_to_fetch:
        print(
            f"Fetching sector for {len(equity_to_fetch)} ticker(s) from Fundamentus "
            f"(this is slow, ~1-2s each)...",
            file=sys.stderr,
        )
        new_cache_rows: list[dict] = []
        for t in equity_to_fetch:
            setor, subsetor = _fetch_equity_sector(t)
            new_cache_rows.append({
                "ticker": t,
                "setor": setor,
                "subsetor": subsetor,
                "fetched_at": datetime.utcnow().isoformat(timespec="seconds"),
            })
            rows.append({
                "ticker": t,
                "setor": setor if setor else "Unknown",
                "subsetor": subsetor,
            })
        # Merge into cache. If --no-cache was passed we still want to
        # write the fresh data so subsequent runs benefit.
        merged = pd.concat([cache, pd.DataFrame(new_cache_rows)], ignore_index=True)
        merged = merged.drop_duplicates(subset=["ticker"], keep="last")
        _save_cache(merged)

    return pd.DataFrame(rows)


def _print_breakdown(agg: pd.DataFrame, total_mv: float, top_n: int) -> None:
    show = agg.head(top_n).copy()
    show["total_mv"] = show["total_mv"].round(2)
    show["weight_pct"] = show["weight_pct"].round(2)
    print(tabulate(show, headers="keys", tablefmt="github", showindex=False))
    print()
    print(f"Total portfolio value: R$ {total_mv:,.2f}")

    if agg.empty:
        return
    top = agg.iloc[0]
    flag = "  WARNING: >25% concentration" if top["weight_pct"] > 25 else ""
    print(
        f"Top sector: {top['setor']} — {top['weight_pct']:.2f}% "
        f"(R$ {top['total_mv']:,.2f}){flag}"
    )
    if (agg["weight_pct"] > 25).any():
        flagged = agg[agg["weight_pct"] > 25]
        names = ", ".join(
            f"{r['setor']} ({r['weight_pct']:.1f}%)" for _, r in flagged.iterrows()
        )
        print(f"Sectors over 25%: {names}")

    top3 = agg.head(3)
    sentence = "Top 3 sectors: " + ", ".join(
        f"{r['setor']} ({r['weight_pct']:.1f}%)" for _, r in top3.iterrows()
    ) + "."
    print(sentence)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore data/sectors_cache.csv and re-fetch every equity sector.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Show only the top N sectors by weight (default 20).",
    )
    args = parser.parse_args(argv)

    if not POSITIONS.exists():
        print("No positions yet. Run load_b3.py first.", file=sys.stderr)
        return 1
    pos = pd.read_csv(POSITIONS)
    if pos.empty:
        print("Positions file is empty.", file=sys.stderr)
        return 0

    prices = _build_price_lookup()
    df = pos.merge(prices, on="ticker", how="left")
    df["market_value"] = df["qty"] * df["price"]

    fii_segments = _load_fii_segments()
    external_tickers = _load_external_tickers()

    sectors = _resolve_sectors(
        tickers=df["ticker"].astype(str).tolist(),
        fii_segments=fii_segments,
        external_tickers=external_tickers,
        use_cache=not args.no_cache,
    )
    df = df.merge(sectors, on="ticker", how="left")
    df["setor"] = df["setor"].fillna("Unknown")

    # Unpriced positions: include in the breakdown but force their
    # sector to "Unknown" — we have no market_value for them so they
    # contribute zero anyway, but we still want to surface that they're
    # excluded from the percentage math.
    unpriced_mask = df["price"].isna()
    df.loc[unpriced_mask, "setor"] = "Unknown"
    df.loc[unpriced_mask, "market_value"] = 0.0

    agg = (
        df.groupby("setor", as_index=False)
        .agg(n_tickers=("ticker", "count"), total_mv=("market_value", "sum"))
        .sort_values("total_mv", ascending=False)
        .reset_index(drop=True)
    )
    total_mv = agg["total_mv"].sum()
    agg["weight_pct"] = (agg["total_mv"] / total_mv * 100) if total_mv else 0.0

    print("=== Sector exposure ===")
    _print_breakdown(agg, total_mv, args.top)

    unpriced = df[unpriced_mask]
    if not unpriced.empty:
        print()
        print(
            f"Note: {len(unpriced)} unpriced position(s) excluded from MV math: "
            f"{', '.join(unpriced['ticker'].tolist())}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
