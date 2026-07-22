"""Clube do Valor — "Nova Seleção Quantitativa" (As 20 Ações Mais Baratas da Bolsa).

A faithful, bank-inclusive reproduction of CdV's deep-value screen:

  Passo 1 (Liquidez):    drop names below a daily-liquidity floor (~R$200k/day),
                         so the basket is actually tradable.
  Passo 2 (Saúde):       drop consistent loss-makers (require positive earnings;
                         non-financials also need a positive EBIT margin).
  Passo 3 (Múltiplos):   rank NON-FINANCIALS by EBIT/EV (cheapest first) and
                         INJECT the cheapest BANKS — ranked by lowest P/L + P/VP —
                         into the SAME ranking.
  Passo 4 (Top N):       take the top of the integrated list. The big banks
                         (BBAS3, BBDC4, ITUB4, ...) enter automatically whenever
                         they trade at historically low multiples — no manager bias.

Why two legs
------------
Banks don't report EBIT, so EV/EBIT and EBIT margin are undefined — Fundamentus
returns 0 for every financial. A pure EV/EBIT screen therefore *structurally*
excludes every bank, no matter how cheap. CdV's fix is to value the EBIT-less
names (banks, insurers, some holdings) on P/L and P/VP instead, then normalise
each leg to a common cheapness percentile in [0, 1] so the two legs compete
head-to-head in one integrated ranking. A genuinely cheap bank sits at the top
of its leg (percentile ≈ 1.0) and therefore surfaces near the top of the merged
list — exactly the "enters automatically when historically cheap" behaviour.

The output 'cheapness' column is in [0, 1]; higher = cheaper = better.
'leg' tells you which engine surfaced the name ('EBIT/EV' or 'P/L+P/VP').
"""
from __future__ import annotations

import pandas as pd

from methodologies._util import pct_rank as _pct_rank


# Passo 1: CdV uses roughly R$200k of daily liquidity. Fundamentus' 'liq2m'
# is the average daily financial volume (BRL) over the trailing 2 months.
MIN_LIQ_DAILY = 200_000

# ---- Financial-statement freshness -----------------------------------------
# We only rank companies whose financials are up to date — a stale balance
# poisons every multiple (P/L, EV/EBIT, ...) we rank on, so stale names must be
# dropped BEFORE percentiles are computed, across the whole liquidity-passing
# universe. Brazilian issuers file quarterly ITRs (~45d after quarter-end) and
# an annual DFP; a balance older than ~2 quarters means a missed filing
# (distress, suspension, delisting). Measured against the SNAPSHOT date so old
# snapshots stay reproducible.
MAX_BALANCE_AGE_DAYS = 180

# ---- Value-trap guardrails -------------------------------------------------
# The screen buys "cheap" mechanically. These thresholds flag names whose
# cheapness more likely reflects trouble than a bargain, so a human can drop
# them. They do NOT change the ranking — they only annotate it. Tuned against
# the 2026-06 list (QUAL3, HBRE3, GOAU3/4) but exposed as constants so the user
# can override.
MIN_ROE_TRAP = 0.08   # ROE below 8% → cheapness is poor returns, not a discount
MAX_PL_TRAP = 20.0    # P/L above 20 despite cheap EV/EBIT → depressed/distorted earnings
MAX_DY_TRAP = 0.18    # DY above 18% → likely one-off payout or price in freefall


def rank(df: pd.DataFrame, *, liq_floor: float = MIN_LIQ_DAILY) -> pd.DataFrame:
    """Run the CdV integrated screen. Returns every surviving name, cheapest first.

    Columns added: leg, cheapness, plus pl_rank/pvp_rank for the bank leg.
    Caller slices .head(N) for the "top N mais baratas".
    """
    out = df.copy()

    # ---- Passo 1: liquidity floor ----
    if "liq2m" in out.columns:
        out = out[out["liq2m"].fillna(0) >= liq_floor]

    # ---- Passo 2: financial health — positive trailing earnings ----
    # P/L > 0 rejects loss-makers (negative earnings ⇒ negative or absent P/L).
    out = out[out["pl"].fillna(0) > 0]

    # ---- Leg split ----
    # Non-financials have a usable EBIT (positive EV/EBIT and EBIT margin).
    # EBIT-less survivors with positive earnings are banks/insurers/holdings —
    # they can only be valued on P/L and P/VP.
    evebit = out["evebit"].fillna(0)
    mrgebit = out["mrgebit"].fillna(0)
    is_nonfin = (evebit > 0) & (mrgebit > 0)

    nonfin = out[is_nonfin].copy()
    banks = out[~is_nonfin].copy()
    # The bank leg needs valid book value; drop anything without a real P/VP.
    banks = banks[banks["pvp"].fillna(0) > 0]
    if "roe" in banks.columns:
        banks = banks[banks["roe"].fillna(0) > 0]  # still part of the health screen

    # ---- Passo 3a: non-financials ranked by EBIT/EV (cheapest = lowest EV/EBIT) ----
    nonfin["leg"] = "EBIT/EV"
    nonfin["cheapness"] = _pct_rank(nonfin["evebit"], ascending=True)
    nonfin["pl_rank"] = pd.NA
    nonfin["pvp_rank"] = pd.NA

    # ---- Passo 3b: banks ranked by lowest P/L + P/VP (rank-sum) ----
    banks["leg"] = "P/L+P/VP"
    if len(banks):
        pl_rank = banks["pl"].rank(ascending=True, method="average")
        pvp_rank = banks["pvp"].rank(ascending=True, method="average")
        banks["pl_rank"] = pl_rank.round(0).astype("Int64")
        banks["pvp_rank"] = pvp_rank.round(0).astype("Int64")
        # Mean of the two cheapness percentiles == rank-sum normalised to [0, 1].
        banks["cheapness"] = (
            _pct_rank(banks["pl"], ascending=True)
            + _pct_rank(banks["pvp"], ascending=True)
        ) / 2.0

    # ---- Passo 3c: inject into one ranking ----
    combined = pd.concat([nonfin, banks], ignore_index=True)
    return combined.sort_values(
        ["cheapness", "leg"], ascending=[False, True]
    ).reset_index(drop=True)


def annotate_freshness(
    df: pd.DataFrame,
    balance_dates: dict,
    ref_date,
    *,
    max_age_days: int = MAX_BALANCE_AGE_DAYS,
) -> pd.DataFrame:
    """Add balance-sheet freshness columns to a copy of df.

    Args:
        df: rows with a 'papel' column.
        balance_dates: {ticker -> last balance date (parseable) or None}.
        ref_date: the date to measure age against (the snapshot date).
        max_age_days: a balance older than this is 'stale'.

    Adds:
        balance_date     — parsed last-balance Timestamp (NaT if unknown)
        balance_age_days — (ref_date - balance_date).days (NA if unknown)
        freshness_reason — '' if fresh, else 'stale>{N}d' or 'no-date'
    """
    out = df.copy()
    ref = pd.Timestamp(ref_date)
    dates, ages, reasons = [], [], []
    for ticker in out["papel"]:
        bd = pd.to_datetime(balance_dates.get(ticker), errors="coerce")
        if pd.isna(bd):
            dates.append(pd.NaT)
            ages.append(pd.NA)
            reasons.append("no-date")
            continue
        age = (ref - bd).days
        dates.append(bd)
        ages.append(age)
        reasons.append(f"stale>{max_age_days}d" if age > max_age_days else "")
    out["balance_date"] = dates
    out["balance_age_days"] = pd.array(ages, dtype="Int64")
    out["freshness_reason"] = reasons
    return out


def split_by_freshness(annotated: pd.DataFrame):
    """Return (fresh, dropped) frames from an annotate_freshness() result."""
    is_fresh = annotated["freshness_reason"] == ""
    return (
        annotated[is_fresh].reset_index(drop=True),
        annotated[~is_fresh].reset_index(drop=True),
    )


def flag_traps(
    ranked: pd.DataFrame,
    *,
    min_roe: float = MIN_ROE_TRAP,
    max_pl: float = MAX_PL_TRAP,
    max_dy: float = MAX_DY_TRAP,
) -> pd.DataFrame:
    """Annotate a ranked frame with value-trap reasons.

    Adds 'trap_flags' (comma-joined reason codes, '' if clean) and 'is_trap'
    (bool). Purely mechanical: low ROE, distorted P/L, or implausible DY.
    """
    out = ranked.copy()
    roe = out["roe"].fillna(0) if "roe" in out.columns else 0.0
    pl = out["pl"].fillna(0) if "pl" in out.columns else 0.0
    dy = out["dy"].fillna(0) if "dy" in out.columns else 0.0

    checks = {
        f"ROE<{min_roe:.0%}": roe < min_roe,
        f"P/L>{max_pl:.0f}": pl > max_pl,
        f"DY>{max_dy:.0%}": dy > max_dy,
    }
    flags = pd.Series([[] for _ in range(len(out))], index=out.index)
    for label, mask in checks.items():
        for idx in out.index[mask]:
            flags[idx].append(label)
    out["trap_flags"] = flags.map(", ".join)
    out["is_trap"] = out["trap_flags"].str.len() > 0
    return out
