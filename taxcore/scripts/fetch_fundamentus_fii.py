"""Fetch the FII screener from Fundamentus and save as a dated snapshot.

Fundamentus serves FIIs on a separate page from equities (fii_resultado.php
vs resultado.php), with a different schema (no P/L, ROE, etc. — instead FFO
Yield, Dividend Yield, Cap Rate, Vacância). Equity snapshots in
data/snapshots/ are not the right place for them, so FII snapshots go to
data/fii_snapshots/.

Usage: python -m scripts.fetch_fundamentus_fii
"""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pandas as pd
import requests

FII_URL = "https://www.fundamentus.com.br/fii_resultado.php"
HDR = {
    "User-agent": "Mozilla/5.0 (Windows; U; Windows NT 6.1; rv:2.2) Gecko/20110201",
    "Accept": "text/html, text/plain, text/css, text/sgml, */*;q=0.01",
    "Accept-Encoding": "gzip, deflate",
}

SNAPSHOTS_DIR = Path(__file__).resolve().parent.parent / "data" / "fii_snapshots"

# Map FII page column names → snake_case. Keep the same style as the equity
# fetcher so consumers can branch by columns rather than by source.
COLUMN_MAP = {
    "Papel":           "papel",
    "Segmento":        "segmento",
    "Cotação":         "cotacao",
    "FFO Yield":       "ffo_yield",
    "Dividend Yield":  "dy",
    "P/VP":            "pvp",
    "Valor de Mercado": "valor_mercado",
    "Liquidez":        "liquidez",
    "Qtd de imóveis":  "qtd_imoveis",
    "Preço do m2":     "preco_m2",
    "Aluguel por m2":  "aluguel_m2",
    "Cap Rate":        "cap_rate",
    "Vacância Média":  "vacancia",
}


def _pct_to_float(s: pd.Series) -> pd.Series:
    """Convert '12,89%' style strings to 0.1289."""
    return (
        s.astype(str)
        .str.replace("%", "", regex=False)
        .str.replace(".", "", regex=False)  # thousand separators (rare here)
        .str.replace(",", ".", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
        / 100.0
    )


def fetch_snapshot() -> pd.DataFrame:
    r = requests.get(FII_URL, headers=HDR, timeout=30)
    r.raise_for_status()
    raw = pd.read_html(io.StringIO(r.text), decimal=",", thousands=".")[0]
    rename = {k: v for k, v in COLUMN_MAP.items() if k in raw.columns}
    df = raw.rename(columns=rename)
    # Percent columns arrive as strings like '12,89%'; convert to floats in [0,1].
    for col in ("ffo_yield", "dy", "cap_rate", "vacancia"):
        if col in df.columns:
            df[col] = _pct_to_float(df[col])
    df["papel"] = df["papel"].astype(str).str.strip().str.upper()
    return df


def save_snapshot(df: pd.DataFrame, snapshot_date: date | None = None) -> Path:
    snapshot_date = snapshot_date or date.today()
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    out = SNAPSHOTS_DIR / f"{snapshot_date.isoformat()}.csv"
    if out.exists():
        print(f"FII snapshot already exists: {out} (not overwriting)")
        return out
    df.to_csv(out, index=False)
    print(f"Saved {len(df)} FII rows → {out}")
    return out


def main() -> int:
    print("Fetching Fundamentus FII screener…")
    df = fetch_snapshot()
    save_snapshot(df)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
