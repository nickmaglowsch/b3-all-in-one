"""Hard filters applied before any ranking. Reject obvious junk, illiquid names, losses."""
from __future__ import annotations

import pandas as pd

from methodologies._util import debt_col


# Minimum 2-month liquidity (BRL) — avoid names you can't realistically trade
MIN_LIQ_2M = 1_000_000

# Reject extreme or nonsensical multiples
MAX_PL = 50.0
# Leverage ceiling. Fundamentus used to expose 'Dív.Brut/ Patrim.' (gross debt /
# equity) as 'divbpatr'; the site now exposes 'Dív.Líq/ Patrim.' (net debt /
# equity) as 'divlpatr'. Net debt subtracts cash from gross, so a 2.0 ceiling
# is conservative on the new column too. We accept either column for backward
# compatibility with older snapshots.
MAX_DEBT_EQUITY = 2.0


def apply(df: pd.DataFrame, *, allow_units: bool = False) -> pd.DataFrame:
    """Return a filtered copy of df with only investable names."""
    out = df.copy()

    if not allow_units:
        # Tickers ending in '11' are typically FIIs, ETFs, BDR units, or pref+ord units.
        # Different analysis framework. Excluded by default.
        out = out[~out["papel"].str.endswith("11")]

    # Liquidity floor
    if "liq2m" in out.columns:
        out = out[out["liq2m"] >= MIN_LIQ_2M]

    # P/L sanity: must be positive (not a loss-maker), not a bubble multiple
    if "pl" in out.columns:
        out = out[(out["pl"] > 0) & (out["pl"] < MAX_PL)]

    # Profitability floor
    if "roe" in out.columns:
        out = out[out["roe"] > 0]

    # Leverage ceiling — accept whichever debt/equity column is present.
    dcol = debt_col(out)
    if dcol is not None:
        out = out[out[dcol] < MAX_DEBT_EQUITY]

    return out.reset_index(drop=True)
