"""Clube do Valor "S-Rank" — the FII version of the cheapest-stocks screen.

A separate asset class from equities (different snapshot, different metrics), so
this lives apart from methodologies/cheapest.py. Mechanical, no weights to tune.

Etapa 1 — Filtros de Eliminação:
  1. Liquidez:    drop funds below ~R$200k average daily liquidity.
  2. Tipo/Mandato: drop incorporação/desenvolvimento funds (build-to-sell/rent) —
                   risk/volatility misaligned with a long-term income strategy.
  3. Monoativos:  drop single-property (qtd_imoveis == 1) or single-tenant funds —
                   concentrated vacancy risk. (qtd_imoveis == 0 = paper/CRI funds,
                   NOT mono-property, so they're kept.)
  4. Prazo:       drop fixed-term funds (prazo de duração determinado).

Etapa 2 — Fatores de Ranqueamento (the S-Rank core), two equal-weight factors:
  - P/VP ascending  (cheapest vs NAV first)
  - DY (12m) descending (highest yield first)
  Each fund gets its position in both lists; the two positions are summed
  (Greenblatt rank-sum) and the lowest totals win. Top 15 form the portfolio.

Mandato and prazo are not in the Fundamentus FII snapshot — they come from CVM
(scripts/fetch_fii_metadata.py → data/fii_metadata.csv), bridged ISIN→ticker at
~best-effort coverage. Funds with no metadata can't be screened on tipo/prazo and
pass those two filters by default (logged by the runner). single_tenant is a
manual column in that file.

Output: 'srank' (lower = cheaper+higher-yield = better), with pvp_rank/dy_rank.
"""
from __future__ import annotations

import pandas as pd

# Etapa 1.1 — CdV uses ~R$200k average daily liquidity (12m). Fundamentus'
# 'liquidez' is a recent daily volume; close enough for the floor.
MIN_LIQUIDEZ_DAILY = 200_000
# Large funds occasionally show liquidez = 0 from a transient Fundamentus query
# bug (e.g. IRDM11 at R$2B+); bypass the floor for them. Data guard, not a rule.
LIQUIDITY_BYPASS_VALOR_MERCADO = 100_000_000

# Yield-trap detection (not in CdV's spec; the rank-sum rewards distressed
# deep-discount paper FIIs, so we skip the worst offenders — see is_trap()).
SUSPICIOUS_DY = 0.20            # DY above this is often a one-off or a falling cota (informational)
DY_OVER_FFO_GAP = 0.03         # DY exceeding FFO yield by this much = paying above cash generation
# A fund whose RECENT annualized distribution (CVM monthly DY run-rate, from
# fetch_fii_metadata) has collapsed below this fraction of its trailing-12m DY
# has cut payouts — the trailing DY the S-Rank ranks on is stale (e.g. DEVA11/
# HCTR11 paying ~0 in 2026 but still showing 22-24% trailing).
DY_CUT_RATIO = 0.50

# Vacancy ceiling for BRICK funds (qtd_imoveis >= 1). Paper funds report 0/100%
# spuriously, so this is gated on owning real property.
MAX_VACANCIA = 0.20


def eliminate(df: pd.DataFrame, meta: pd.DataFrame | None = None):
    """Apply Etapa 1 elimination filters.

    Returns (kept, dropped) where dropped has an 'elim_reason' column.
    `meta` (data/fii_metadata.csv) supplies mandato / prazo / single_tenant; if
    None or a ticker is absent, the tipo and prazo filters are skipped for it.
    """
    out = df.copy()
    if meta is not None and len(meta):
        cols = [c for c in ["ticker", "mandato", "prazo", "single_tenant",
                            "dy_run_rate", "dy_months"] if c in meta.columns]
        out = out.merge(meta[cols], left_on="papel", right_on="ticker", how="left")

    reason = pd.Series("", index=out.index)

    # 1. Liquidez
    liquid = out["liquidez"].fillna(0) >= MIN_LIQUIDEZ_DAILY
    if "valor_mercado" in out.columns:
        liquid = liquid | (out["valor_mercado"].fillna(0) >= LIQUIDITY_BYPASS_VALOR_MERCADO)
    reason = reason.mask((reason == "") & ~liquid, "liquidez<200k")

    # 2. Tipo / Mandato — desenvolvimento / incorporação
    if "mandato" in out.columns:
        is_dev = out["mandato"].fillna("").str.startswith("Desenvolvimento")
        reason = reason.mask((reason == "") & is_dev, "desenvolvimento")

    # 4. Prazo determinado
    if "prazo" in out.columns:
        is_term = out["prazo"].fillna("") == "Determinado"
        reason = reason.mask((reason == "") & is_term, "prazo_determinado")

    # 3. Monoativos — single property OR single tenant (paper funds qtd==0 exempt)
    mono = out["qtd_imoveis"].fillna(-1) == 1
    if "single_tenant" in out.columns:
        mono = mono | out["single_tenant"].fillna("").astype(str).str.strip().str.lower().isin(
            {"1", "true", "yes", "sim", "y", "s"})
    reason = reason.mask((reason == "") & mono, "monoativo")

    # 5. Vacância (enhancement, not in CdV's spec) — brick funds only.
    if "vacancia" in out.columns:
        high_vac = (out["qtd_imoveis"].fillna(0) >= 1) & (out["vacancia"].fillna(0) > MAX_VACANCIA)
        reason = reason.mask((reason == "") & high_vac, f"vacancia>{MAX_VACANCIA:.0%}")

    out["elim_reason"] = reason
    kept = out[reason == ""].reset_index(drop=True)
    dropped = out[reason != ""].reset_index(drop=True)
    return kept, dropped


def rank(df: pd.DataFrame) -> pd.DataFrame:
    """Etapa 2 — S-Rank: rank-sum of P/VP (asc) + DY (desc). Lowest sum = best."""
    out = df.copy()
    out = out[(out["pvp"].fillna(0) > 0) & out["dy"].notna()].copy()
    out["pvp_rank"] = out["pvp"].rank(ascending=True, method="min")
    out["dy_rank"] = out["dy"].rank(ascending=False, method="min")
    out["srank"] = out["pvp_rank"] + out["dy_rank"]
    return out.sort_values(["srank", "pvp_rank"]).reset_index(drop=True)


def is_unsustainable(df: pd.DataFrame) -> pd.Series:
    """Yield-trap test used for SKIPPING: distribution runs above cash generation.

    DY exceeding FFO yield by more than DY_OVER_FFO_GAP means the fund pays out
    more than it earns — a cut is likely. This also captures negative-FFO funds
    (e.g. DY 60% with FFO −6%), since dy − ffo is then large. A high DY that is
    *covered* by FFO is NOT flagged — that's the yield we're screening for.
    """
    if "ffo_yield" not in df.columns:
        return pd.Series(False, index=df.index)
    return (df["dy"].fillna(0) - df["ffo_yield"].fillna(0)) > DY_OVER_FFO_GAP


def is_cut(df: pd.DataFrame) -> pd.Series:
    """Distribution-cut test: recent CVM DY run-rate collapsed vs trailing-12m DY.

    Needs 'dy_run_rate' (annualized recent monthly DY from fetch_fii_metadata).
    Funds without run-rate data (no CVM match) are NOT flagged — best-effort.
    """
    if "dy_run_rate" not in df.columns:
        return pd.Series(False, index=df.index)
    run = pd.to_numeric(df["dy_run_rate"], errors="coerce")
    dy = df["dy"].fillna(0)
    return run.notna() & (run < DY_CUT_RATIO * dy)


def flag_sustainability(df: pd.DataFrame) -> pd.DataFrame:
    """Yield-trap flags. 'DY>>FFO' and 'DY-cut' trigger a skip (is_trap); 'DY>20%'
    is informational only (high yield is the point of the strategy when covered)."""
    out = df.copy()
    dy = out["dy"].fillna(0)
    flags = pd.Series([[] for _ in range(len(out))], index=out.index)
    unsust = is_unsustainable(out)
    cut = is_cut(out)
    for idx in out.index[unsust]:
        flags[idx].append("DY>>FFO")
    for idx in out.index[cut]:
        flags[idx].append("DY-cut")
    for idx in out.index[dy > SUSPICIOUS_DY]:
        flags[idx].append(f"DY>{SUSPICIOUS_DY:.0%}")
    out["yield_flags"] = flags.map(", ".join)
    out["is_trap"] = unsust | cut
    return out
