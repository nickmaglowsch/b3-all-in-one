"""Hard filters for FIIs. Reject illiquid, defective, or unsustainable yields."""
from __future__ import annotations

import pandas as pd


# Minimum 2-month liquidity (BRL) — Fundamentus' 'liquidez' is daily volume.
# 100k/day is roughly the lowest threshold a retail investor can trade
# without moving the price meaningfully.
MIN_LIQUIDEZ_DAILY = 100_000

# Funds above this market cap are treated as having real liquidity even if
# Fundamentus reports liquidez = 0 today (transient query bug we've seen
# bite IRDM11 and others — R$1B+ funds always trade).
LIQUIDITY_BYPASS_VALOR_MERCADO = 100_000_000

# DY is reported as a fraction (0.10 = 10%). MIN_DY catches defective /
# zombie funds (DY=0 or near-zero). We don't want to filter out merely
# *low*-yielding funds: young funds (e.g. KNCA11) can be ramping
# distributions and still belong in the universe. MAX_DY catches cotas in
# freefall where the denominator collapsed.
MIN_DY = 0.02
MAX_DY = 0.30


def apply(df: pd.DataFrame, *, segment: str | None = None) -> pd.DataFrame:
    """Return a filtered copy of df with only investable FIIs.

    `segment` (optional): case-insensitive substring match against the
    Fundamentus 'segmento' column. Useful for sector-specific ranking.
    """
    out = df.copy()

    if segment:
        out = out[out["segmento"].str.contains(segment, case=False, na=False)]

    if "liquidez" in out.columns:
        # Bypass the liquidity floor for large funds — Fundamentus
        # occasionally reports liquidez = 0 transiently for funds that
        # actually trade fine (e.g. IRDM11 at R$2B+ mkt cap).
        liquid_enough = out["liquidez"] >= MIN_LIQUIDEZ_DAILY
        if "valor_mercado" in out.columns:
            liquid_enough = liquid_enough | (
                out["valor_mercado"] >= LIQUIDITY_BYPASS_VALOR_MERCADO
            )
        out = out[liquid_enough]

    if "dy" in out.columns:
        out = out[(out["dy"] >= MIN_DY) & (out["dy"] <= MAX_DY)]

    if "pvp" in out.columns:
        # NaN / zero / negative P/VP usually means missing or distressed data
        out = out[out["pvp"] > 0]

    return out.reset_index(drop=True)
