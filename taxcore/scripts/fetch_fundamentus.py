"""Fetch the full B3 screener from Fundamentus and save as a dated snapshot.

Usage: python -m scripts.fetch_fundamentus
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

try:
    import fundamentus
except ImportError:
    print("ERROR: 'fundamentus' package not installed. Run: pip install -e .", file=sys.stderr)
    sys.exit(1)


SNAPSHOTS_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshots"


## Map Fundamentus HTML column names → short snake_case names used by the rest
## of the project. We do this ourselves rather than calling get_resultado()
## because the upstream package's rename is hardcoded against an older schema
## (it still expects 'Dív.Brut/ Patrim.', which the site removed in favor of
## 'Dív.Líq/ Patrim.'). Keeping the mapping local also lets us survive future
## upstream column renames with a single edit here.
COLUMN_MAP = {
    "Cotação":          "cotacao",
    "P/L":              "pl",
    "P/VP":             "pvp",
    "PSR":              "psr",
    "Div.Yield":        "dy",
    "P/Ativo":          "pa",
    "P/Cap.Giro":       "pcg",
    "P/EBIT":           "pebit",
    "P/Ativ Circ.Liq":  "pacl",
    "EV/EBIT":          "evebit",
    "EV/EBITDA":        "evebitda",
    "Mrg Bruta":        "mrgbruta",
    "Mrg Ebit":         "mrgebit",
    "Mrg. Líq.":        "mrgliq",
    "Liq. Corr.":       "liqc",
    "ROIC":             "roic",
    "ROE":              "roe",
    "Liq.2meses":       "liq2m",
    "Patrim. Líq":      "patrliq",
    "Dív.Brut/ Patrim.": "divbpatr",   # legacy column, may not be present
    "Dív.Líq/ Patrim.":  "divlpatr",   # current column on Fundamentus
    "Cresc. Rec.5a":    "c5y",
}


def fetch_snapshot() -> pd.DataFrame:
    """Pull the full screener result. Columns are normalized (lowercase, no accents).

    Uses the raw Fundamentus result and applies our own column rename, so we
    don't break when upstream adds/removes columns — only the mapping above
    needs updating.
    """
    raw = fundamentus.get_resultado_raw()
    raw = raw.reset_index()  # 'papel' becomes a column instead of the index
    rename = {k: v for k, v in COLUMN_MAP.items() if k in raw.columns}
    df = raw.rename(columns=rename)
    # Lowercase any remaining columns (e.g. 'papel') so consumers can rely on a
    # uniform style. Unknown columns are kept verbatim (lowercased).
    df.columns = [c if c in COLUMN_MAP.values() else c.lower() for c in df.columns]
    # Some columns arrive as strings with '-' as the missing-value marker (the
    # site uses it for cells where the metric isn't applicable, e.g. financials
    # for a holding company). Coerce all known-numeric columns so downstream
    # comparisons don't blow up. Anything non-numeric becomes NaN.
    NUMERIC_COLS = {v for v in COLUMN_MAP.values() if v != "papel"}
    for col in NUMERIC_COLS & set(df.columns):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    missing = [src for src in COLUMN_MAP if src not in raw.columns]
    if missing:
        print(f"Note: Fundamentus did not return these columns: {missing}", file=sys.stderr)
    return df


def save_snapshot(df: pd.DataFrame, snapshot_date: date | None = None) -> Path:
    snapshot_date = snapshot_date or date.today()
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    out = SNAPSHOTS_DIR / f"{snapshot_date.isoformat()}.csv"
    if out.exists():
        print(f"Snapshot already exists: {out} (not overwriting)")
        return out
    df.to_csv(out, index=False)
    print(f"Saved {len(df)} rows → {out}")
    return out


def main() -> int:
    print("Fetching Fundamentus screener…")
    df = fetch_snapshot()
    save_snapshot(df)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
