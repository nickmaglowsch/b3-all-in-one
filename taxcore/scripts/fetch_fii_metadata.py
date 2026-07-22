"""Fetch FII classification metadata (mandato, prazo de duração) from CVM Dados Abertos.

The Fundamentus FII screener has no mandato or duration fields, but CdV's S-Rank
needs them to eliminate (a) incorporação/desenvolvimento funds and (b) fixed-term
(prazo determinado) funds. CVM's monthly FII informe carries both, authoritatively
and free:

    https://dados.cvm.gov.br/dados/FII/DOC/INF_MENSAL/DADOS/inf_mensal_fii_<YEAR>.zip
      └─ inf_mensal_fii_geral_<YEAR>.csv  (Mandato, Prazo_Duracao, Segmento_Atuacao,
                                           Tipo_Gestao, Codigo_ISIN, CNPJ, ...)

Caveats handled here:
  - CVM keys records by CNPJ/ISIN, not B3 ticker. We bridge via the ISIN issuer
    code (BR**XXXX**CTF... → XXXX + '11'), which resolves ~65-70% of listed funds.
    Unmapped funds simply carry no mandato/prazo (best-effort — logged).
  - `Mandato` is populated in older years but blank in 2026, while mandate is
    static; `Prazo_Duracao` is current. So we scan several recent years and take
    each fund's LATEST NON-NULL value per field.
  - 'single_tenant' (monoinquilino) is not in any feed — it's a manual column,
    preserved across regenerations for hand-curation.

Output: data/fii_metadata.csv (ticker, mandato, prazo, segmento_cvm, tipo_gestao,
single_tenant, cnpj, isin, updated_at). Re-run semi-annually (the rebalance cadence).

Usage:
    python -m scripts.fetch_fii_metadata                 # current + 2 prior years
    python -m scripts.fetch_fii_metadata --years 2026 2025 2024 2023
"""
from __future__ import annotations

import argparse
import io
import sys
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "fii_metadata.csv"
# Hand-maintained truth applied ON TOP of CVM: Investidor10-confirmed development
# funds that CVM's coarse ANBIMA mandato mis-buckets as 'Híbrido' (e.g. TGAR11,
# MFII11), plus manual single_tenant (monoinquilino) flags. Investidor10's
# /fiis/tipos/desenvolvimento/ page can't be flat-scraped reliably (its sidebar
# mixes in unrelated funds), so this list is curated. Refresh occasionally.
OVERRIDES = ROOT / "data" / "fii_metadata_overrides.csv"
CVM_URL = "https://dados.cvm.gov.br/dados/FII/DOC/INF_MENSAL/DADOS/inf_mensal_fii_{year}.zip"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) b3-portfolio/1.0"}

KEEP = ["CNPJ_Fundo_Classe", "Codigo_ISIN", "Mandato", "Prazo_Duracao",
        "Segmento_Atuacao", "Tipo_Gestao", "Data_Referencia"]

# Recent-distribution run-rate: annualize the last N months of CVM's monthly DY
# to catch funds that have CUT distributions (trailing-12m DY stays high for a
# while after a cut, masking the trap — e.g. DEVA11/HCTR11 paying ~0 in 2026).
RUN_RATE_MONTHS = 6
MIN_RUN_RATE_MONTHS = 3


def _zip_for_year(year: int) -> bytes | None:
    url = CVM_URL.format(year=year)
    try:
        r = requests.get(url, headers=HEADERS, timeout=120)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"  {year}: download failed ({e})", file=sys.stderr)
        return None


def _read_member(content: bytes, needle: str) -> pd.DataFrame | None:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name = next((n for n in z.namelist() if needle in n), None)
        if not name:
            return None
        with z.open(name) as fh:
            return pd.read_csv(fh, sep=";", encoding="latin1", dtype=str)


def _load_year(content: bytes, year: int) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Return (geral[KEEP], complemento[cnpj, date, monthly DY]) for a year's zip."""
    g = _read_member(content, "geral")
    if g is not None:
        g = g[[c for c in KEEP if c in g.columns]].copy()
    c = _read_member(content, "complemento")
    if c is not None:
        cc = [x for x in ["CNPJ_Fundo_Classe", "Data_Referencia",
                          "Percentual_Dividend_Yield_Mes"] if x in c.columns]
        c = c[cc].copy()
    print(f"  {year}: {0 if g is None else len(g)} geral rows", file=sys.stderr)
    return g, c


def _run_rate(comp: pd.DataFrame) -> pd.DataFrame:
    """Per-CNPJ annualized DY from the most recent RUN_RATE_MONTHS months."""
    comp = comp.copy()
    comp["Data_Referencia"] = pd.to_datetime(comp["Data_Referencia"], errors="coerce")
    comp["dy_m"] = pd.to_numeric(comp["Percentual_Dividend_Yield_Mes"], errors="coerce")
    comp = comp.dropna(subset=["Data_Referencia", "dy_m"]).sort_values("Data_Referencia")
    rows = []
    for cnpj, g in comp.groupby("CNPJ_Fundo_Classe"):
        recent = g.tail(RUN_RATE_MONTHS)
        n = len(recent)
        if n < MIN_RUN_RATE_MONTHS:
            continue
        rows.append({"CNPJ_Fundo_Classe": cnpj,
                     "dy_run_rate": round(recent["dy_m"].sum() * 12.0 / n, 4),
                     "dy_months": n})
    return pd.DataFrame(rows)


def isin_to_ticker(isin: pd.Series) -> pd.Series:
    """Bridge ISIN → B3 ticker via the issuer code: BR**XXXX**CTF... → XXXX + '11'."""
    return isin.str[2:6].str.upper() + "11"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--years", type=int, nargs="+",
                   help="CVM informe years to scan (default: current + 2 prior)")
    args = p.parse_args(argv[1:])
    years = args.years or [date.today().year - d for d in (0, 1, 2)]

    print(f"Fetching CVM FII informe for years: {years}", file=sys.stderr)
    gframes, cframes = [], []
    for y in years:
        content = _zip_for_year(y)
        if content is None:
            continue
        g, c = _load_year(content, y)
        if g is not None:
            gframes.append(g)
        if c is not None:
            cframes.append(c)
    if not gframes:
        raise SystemExit("No CVM data could be downloaded.")
    allrows = pd.concat(gframes, ignore_index=True)
    allrows["Data_Referencia"] = pd.to_datetime(allrows["Data_Referencia"], errors="coerce")
    run = _run_rate(pd.concat(cframes, ignore_index=True)) if cframes else pd.DataFrame(
        columns=["CNPJ_Fundo_Classe", "dy_run_rate", "dy_months"])
    allrows = allrows.sort_values("Data_Referencia")

    # Latest NON-NULL value per fund per field (mandate is static; prazo current).
    g = allrows.groupby("CNPJ_Fundo_Classe", as_index=False)
    meta = g.agg({
        "Codigo_ISIN": "last",
        "Mandato": lambda s: s.dropna().iloc[-1] if s.notna().any() else pd.NA,
        "Prazo_Duracao": lambda s: s.dropna().iloc[-1] if s.notna().any() else pd.NA,
        "Segmento_Atuacao": lambda s: s.dropna().iloc[-1] if s.notna().any() else pd.NA,
        "Tipo_Gestao": lambda s: s.dropna().iloc[-1] if s.notna().any() else pd.NA,
    })
    # Only B3-issued ISINs (BR**XXXX**CTF...) bridge to a real ticker.
    meta = meta[meta["Codigo_ISIN"].fillna("").str.match(r"^BR[A-Z0-9]{4}CTF")]
    meta["ticker"] = isin_to_ticker(meta["Codigo_ISIN"])
    meta = meta.dropna(subset=["ticker"])
    # One row per ticker (collisions are rare; keep the most recently-referenced).
    meta = meta.drop_duplicates(subset=["ticker"], keep="last")
    meta = meta.merge(run, on="CNPJ_Fundo_Classe", how="left")

    out = pd.DataFrame({
        "ticker": meta["ticker"],
        "mandato": meta["Mandato"],
        "prazo": meta["Prazo_Duracao"],
        "segmento_cvm": meta["Segmento_Atuacao"],
        "tipo_gestao": meta["Tipo_Gestao"],
        "dy_run_rate": meta["dy_run_rate"],       # annualized recent CVM monthly DY
        "dy_months": meta["dy_months"],
        "single_tenant": "",                      # manual; preserved via overrides
        "cnpj": meta["CNPJ_Fundo_Classe"],
        "isin": meta["Codigo_ISIN"],
        "updated_at": date.today().isoformat(),
    }).sort_values("ticker")

    # Apply hand-maintained overrides on top of CVM (non-empty fields win;
    # tickers absent from CVM are appended).
    if OVERRIDES.exists():
        ov = pd.read_csv(OVERRIDES, dtype=str).fillna("")
        out = out.set_index("ticker")
        n_new = n_set = 0
        for _, row in ov.iterrows():
            tk = row["ticker"]
            if tk not in out.index:
                out.loc[tk] = ""
                n_new += 1
            for field in ("mandato", "prazo", "single_tenant"):
                if field in ov.columns and str(row.get(field, "")).strip():
                    out.at[tk, field] = row[field]
                    n_set += 1
        out = out.reset_index()
        out["updated_at"] = out["updated_at"].replace("", date.today().isoformat())
        print(f"Applied overrides from {OVERRIDES.name}: {len(ov)} rows "
              f"({n_new} new tickers, {n_set} fields set).", file=sys.stderr)

    out = out.sort_values("ticker")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)

    n_dev = int(out["mandato"].fillna("").str.startswith("Desenvolvimento").sum())
    n_det = int((out["prazo"] == "Determinado").sum())
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(out)} tickers "
          f"({n_dev} desenvolvimento, {n_det} prazo determinado)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
