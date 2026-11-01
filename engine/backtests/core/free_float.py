"""
Point-in-time free float per ticker root, from free_float_pit (CVM FRE, see
b3_pipeline.free_float).

    ff = load_free_float_pit(db_path)
    asof(ff, pd.Timestamp("2020-06-30"))   # root -> free_float_pct (0-100) + dates

Known-as-of is the RECEIPT date of the version carrying the value, never the
reference date. Among versions received by then the newest reference year wins,
then the highest version — so a late resubmission of an old year (CVM has
2017 versions received in 2024) never overrides a newer year.

Roots join by CNPJ through company_tickers_pit ∪ cvm_companies.ticker_root,
the same join as data.load_sector_pit: a rename (ELET -> AXIA) keeps its history.

Gaps: no FRE for BDRs, ETFs, FIIs and foreign issuers; companies leaving item
15.3 blank (pct 0, stored as NULL) are skipped here, so asof falls back to their
last real value — check ref_date for staleness. The value is per COMPANY (all
classes): units (BPAC11, TAEE11, KLBN11) and dual-class names get the total;
pct_free_on / pct_free_pn are there for class-level weighting. Banks file FRE
like everyone else.

STALENESS — read before choosing a rebalance date. CVM publishes only the
latest version of each company-year, and that version's median receipt is
~May of the FOLLOWING year (76% arrive after March). Measured on prod, top-100
roots x 16 years: on 31 Mar, 1,266 of 1,600 reads are 2+ years old (only 124
are the prior year); on 30 Jun, 1,438 are the prior year and 32 are 2+ years
old. So a March rebalance sees ~2-year-old free float; read on June 30 or later.
Do NOT date values by earlier versions' receipts to make this look fresher:
the value shipped is the latest version's, so that would leak restatements.
From the first weekly ingest on, new versions are stored as they arrive and
this lag shrinks for future dates only.

DEFINITION — CVM's "ações em circulação" excludes only the controller, its
related persons, managers and treasury. A NON-controlling government stake
counts as float (AXIA ~97.8%, SBSP 100% after privatization), unlike B3's
index free float. BESST/TevaIAFD wiring should override or haircut such names.
"""
from __future__ import annotations

import pandas as pd

from b3_pipeline import storage

_SQL = """
    SELECT DISTINCT t.root, f.cnpj, f.ref_date, f.version, f.receipt_date,
           f.pct_free_total AS free_float_pct, f.pct_free_on, f.pct_free_pn
    FROM free_float_pit f
    JOIN (SELECT cnpj, ticker_root AS root FROM company_tickers_pit UNION
          SELECT cnpj, ticker_root FROM cvm_companies) t ON t.cnpj = f.cnpj
    WHERE t.root IS NOT NULL AND f.pct_free_total IS NOT NULL"""


def load_free_float_pit(db_path) -> pd.DataFrame:
    """[root, cnpj, ref_date, version, receipt_date, free_float_pct, pct_free_on,
    pct_free_pn], every stored version, dates as Timestamps."""
    with storage.get_connection(db_path) as conn:
        out = storage.read_sql(_SQL, conn)
    for col in ("ref_date", "receipt_date"):
        out[col] = pd.to_datetime(out[col])
    return out


def asof(ff: pd.DataFrame, when) -> pd.DataFrame:
    """Latest free float known on `when`, one row per root (index = root).

    ponytail: one root on several CNPJs (re-registrations) takes the newest
    filing of any of them."""
    known = ff[ff["receipt_date"] <= pd.Timestamp(when)]
    known = known.sort_values(["ref_date", "version", "receipt_date"])
    return known.drop_duplicates("root", keep="last").set_index("root")
