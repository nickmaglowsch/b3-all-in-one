"""What a fundamentals row means on the date it is read.

Three rules the point-in-time frames (`data._pit_frame`, the sp500_b3 family's
snapshots, `fundamentals.attach`) share:

* **Newest period, then latest version** (`latest_period_rows`): a late
  restatement of an older quarter is history, not the current number.
* **Income is year-to-date on B3** (`ttm_from_ytd`): CVM's ITR lines are
  Q1 = 3 months, Q2 = 6, Q3 = 9, so anything set against a price needs TTM.
* **Share counts are on the filing's basis** (`split_multiplier_frame`): a count
  filed before a desdobramento must be multiplied up before it meets the
  rebalance-date close.

Pure pandas apart from `load_stock_actions`.
"""
from __future__ import annotations

import bisect

import numpy as np
import pandas as pd

from b3_pipeline import storage

# Per market, the extra columns `data._query_fundamentals_pit` selects: the
# document family (see latest_period_rows) and, on B3, the fiscal slot a YTD
# figure belongs to (see ttm_from_ytd). Constants only — interpolated into SQL.
PIT_EXTRA_COLUMNS = {
    "B3": ("CASE WHEN f.doc_type IN ('DFP', 'ITR') THEN 'FS' ELSE f.doc_type END"
           " AS doc_family, f.doc_type, f.fiscal_year, f.quarter, f.cnpj"),
    # Every SEC fact is a financial-statement figure; one family.
    "US": "'FS' AS doc_family",
}

# The CVM documents whose rows are periodic statements (one per period_end).
# FRE/IPE rows are a different family: their period_end is the form's reference
# date, not a fiscal period, so they must not compete with a quarter's.
STATEMENT_DOCS = ("DFP", "ITR")

_EPOCH, _DAY = pd.Timestamp("1970-01-01"), pd.Timedelta(days=1)


def latest_period_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows that were already superseded by a newer period when filed.

    Point-in-time means "the newest period known on the date, in its latest
    version" — not "the most recently filed row". CVM re-apresentações of an
    earlier quarter are routine: Q1 filed in May, Q2 in August, Q1 v2 in
    September. Keyed on filing_date alone, the September restatement became the
    visible value and Q1's balance replaced Q2's until Q3 was filed.

    A row survives when its period_end is at least the latest period_end
    already filed for that ticker and document family, so a restatement of the
    *current* period still overrides it and a restatement of an older one is
    history. Families: DFP/ITR are one ("FS"); FRE, IPE and the like are their
    own, because their period_end is a form reference date. Without a family
    column every row is one family. Rows without a period_end are kept.

    Callers drop the NaN rows of the metric first, so "newest period" is per
    metric — a newer filing that lacks the metric does not hide an older value.

    Returns the rows sorted by (ticker, filing_date, period_end,
    filing_version): a "last" per (ticker, filing_date) is then the newest
    period's latest version.
    """
    if df.empty or "period_end" not in df.columns:
        return df
    if "doc_family" in df.columns:
        fam = df["doc_family"]
    elif "doc_type" in df.columns:
        fam = df["doc_type"].where(~df["doc_type"].isin(STATEMENT_DOCS), "FS")
    else:
        fam = pd.Series("FS", index=df.index)
    tmp = df.assign(_pe=pd.to_datetime(df["period_end"]), _fam=fam.fillna("FS").astype(str))
    order = ["ticker", "filing_date", "_pe"]
    if "filing_version" in tmp.columns:
        order.append("filing_version")
    tmp = tmp.sort_values(order, kind="stable")
    running = tmp.groupby(["ticker", "_fam"], sort=False)["_pe"].cummax()
    keep = tmp["_pe"].isna() | (tmp["_pe"] >= running)
    return tmp.loc[keep].drop(columns=["_pe", "_fam"])


def _ytd_slots(ordered: pd.DataFrame, key: str, metric: str) -> tuple[dict, dict]:
    """As-of lookups: (key, fy) -> annual and (key, fy, q) -> YTD, each a pair of
    parallel lists (filing dates ascending, values)."""
    annual: dict = {}
    ytd: dict = {}
    for r in ordered.itertuples():
        k, fy = getattr(r, key), int(r.fiscal_year)
        if r.doc_type == "DFP":
            slot = annual.setdefault((k, fy), ([], []))
        elif pd.notna(r.quarter):
            slot = ytd.setdefault((k, fy, int(r.quarter)), ([], []))
        else:
            continue
        slot[0].append(r.filing_date)
        slot[1].append(float(getattr(r, metric)))
    return annual, ytd


def _asof(slot, when):
    """The value filed last on or before `when`, or None."""
    if slot is None:
        return None
    i = bisect.bisect_right(slot[0], when)
    return slot[1][i - 1] if i else None


def _ttm_row(r, key: str, metric: str, annual: dict, ytd: dict):
    value = float(getattr(r, metric))
    if r.doc_type == "DFP":
        return value
    if pd.isna(r.quarter):
        return None
    k, fy, q = getattr(r, key), int(r.fiscal_year), int(r.quarter)
    prior_annual = _asof(annual.get((k, fy - 1)), r.filing_date)
    prior_ytd = _asof(ytd.get((k, fy - 1, q)), r.filing_date)
    if prior_annual is None or prior_ytd is None:
        return None
    return value + prior_annual - prior_ytd


def ttm_from_ytd(df: pd.DataFrame, metric: str) -> pd.Series:
    """Trailing-twelve-month value of a YTD income metric, for every row of `df`.

      DFP (fiscal year Y):  TTM = annual(Y)
      ITR (Y, quarter q):   TTM = YTD(Y, q) + annual(Y-1) - YTD(Y-1, q)

    — the formula `cvm_main.compute_net_income_ttm` applies to net income, for
    any metric. Prior-year components are taken as of the row's own filing date
    (latest version filed by then), so a later restatement never leaks into an
    earlier TTM. Missing components leave NaN; nothing is fabricated. Every
    version gets its TTM, not only the latest — the original filing is what the
    market saw until the restatement landed.

    Needs columns doc_type, fiscal_year, quarter, filing_date and `metric`
    (filing_version orders same-day filings); groups by `cnpj` when present,
    else by `ticker`. Rows of any other document type are NaN.
    """
    out = pd.Series(np.nan, index=df.index, dtype=float)
    if df.empty or not {"doc_type", "fiscal_year", "quarter", "filing_date",
                        metric}.issubset(df.columns):
        return out
    key = "cnpj" if "cnpj" in df.columns else "ticker"
    rows = df[df["doc_type"].isin(STATEMENT_DOCS) & df[metric].notna()
              & df["fiscal_year"].notna()]
    order = ["filing_date"] + (["filing_version"] if "filing_version" in rows.columns else [])
    ordered = rows.assign(filing_date=pd.to_datetime(rows["filing_date"])).sort_values(
        order, kind="stable")
    annual, ytd = _ytd_slots(ordered, key, metric)
    for r in ordered.itertuples():
        v = _ttm_row(r, key, metric, annual, ytd)
        if v is not None:
            out.at[r.Index] = v
    return out


def share_count_anchor(df: pd.DataFrame) -> pd.Series:
    """The date a row's share count is stated at: shares_asof where stored (a
    DFP/ITR's count is the latest FRE's, at that FRE's filing date, see
    cvm_main._propagate_fre_shares), else the filing date for FRE and
    period_end for the rest.

    The FRE's period_end is the form's reference year (CIEL's FRE "2014" is
    2014-01-01, filed 2015-03), but its capital section is restated at each
    re-filing, so the count is the one on the filing date. Anchored at
    period_end, every split of the year in between was applied to a count that
    already had it — CIEL's 2014 1:2 twice, a R$144bn Cielo in 2015. Prod audit
    2026-11: filing_date reconciles the FRE count with the next statement for
    482 of 521 FREs straddling a split, period_end for 34.

    The DFP/ITR copy is the same count: anchored at its own period_end, DIRR's
    ITR 2025-09-30 held the pre-split count of an FRE filed before the Aug-2025
    1:3 split and the split was never applied (and an FRE filed after a split,
    copied into a statement whose period_end precedes it, applied it twice).
    """
    anchor = pd.to_datetime(df["period_end"])
    if {"doc_type", "filing_date"} <= set(df.columns):
        anchor = anchor.where(df["doc_type"] != "FRE", pd.to_datetime(df["filing_date"]))
    if "shares_asof" in df.columns:
        anchor = pd.to_datetime(df["shares_asof"]).fillna(anchor)
    return anchor


def period_end_days(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """`df` with `metric` replaced by its row's share-count anchor (float days
    since 1970-01-01, see share_count_anchor) wherever the metric is present.

    Fed to `data._pit_frame`, the result is the anchor of the very row that
    supplies each cell of the metric's frame — what a share count needs.
    """
    days = (share_count_anchor(df) - _EPOCH) / _DAY
    return df.assign(**{metric: days.where(df[metric].notna())})


# ── Share counts on the price's basis (B3 splits / bonus shares) ──────────────

def load_stock_actions(db_path: str) -> pd.DataFrame:
    """Load stock_actions (ticker, ex_date, action_type, factor), dates parsed.

    stock_actions is keyed by isin_code; map to tickers via the distinct
    (isin_code, ticker) pairs observed in prices. An ISIN that traded under
    multiple tickers yields one row per ticker (the caller filters by ticker).
    """
    sql = """
        SELECT DISTINCT p.ticker, s.ex_date, s.action_type, s.factor
        FROM stock_actions s
        JOIN (SELECT DISTINCT isin_code, ticker FROM prices
              WHERE isin_code != 'UNKNOWN') p
          ON p.isin_code = s.isin_code
    """
    with storage.get_connection(db_path) as conn:
        df = storage.read_sql(sql, conn)
    df["ex_date"] = pd.to_datetime(df["ex_date"])
    return df


def action_factors(actions: pd.DataFrame) -> np.ndarray:
    """New-shares-per-old-share for each stock_actions row.

    STOCK_SPLIT / REVERSE_SPLIT: the factor is directly new/old (2.0, 0.1).
    BONUS_SHARES: the factor is a percentage, so 1 + F/100.
    """
    f = actions["factor"].to_numpy(dtype=float)
    return np.where(actions["action_type"].to_numpy() == "BONUS_SHARES", 1.0 + f / 100.0, f)


def split_multiplier_frame(actions: pd.DataFrame, anchor: pd.DataFrame) -> pd.DataFrame:
    """Share-count multiplier per cell: the stock actions with ex_date in
    (anchor, row date], compounded — the frame form of sp500_b3.share_multiplier.

    `anchor` is the period_end behind each share count, as float days since
    1970-01-01 (see period_end_days); its index is the rebalance grid and its
    columns are tickers. A share count filed before a 1:10 desdobramento is a
    tenth of what trades at the rebalance; the unadjusted close is already on
    the new basis, so their product was a tenth of the company. Multiplying the
    count by this frame puts it on the close's basis. Cells without an anchor,
    and tickers without actions, get 1.0. Non-positive or missing factors are
    skipped rather than zeroing a count.
    """
    out = pd.DataFrame(1.0, index=anchor.index, columns=anchor.columns)
    if actions is None or actions.empty or anchor.empty:
        return out
    a = actions[actions["ticker"].isin(anchor.columns)]
    f = action_factors(a)
    ok = np.isfinite(f) & (f > 0)
    a = a.loc[ok].assign(_logf=np.log(f[ok]))
    t_days = ((pd.DatetimeIndex(anchor.index) - _EPOCH) / _DAY).to_numpy(dtype=float)
    for tkr, g in a.groupby("ticker", sort=False):
        g = g.sort_values("ex_date")
        ex = ((pd.DatetimeIndex(g["ex_date"]) - _EPOCH) / _DAY).to_numpy(dtype=float)
        logc = np.r_[0.0, np.cumsum(g["_logf"].to_numpy())]
        anc = anchor[tkr].to_numpy(dtype=float)
        has = ~np.isnan(anc)
        it = np.searchsorted(ex, t_days, side="right")
        ia = np.searchsorted(ex, np.where(has, anc, -np.inf), side="right")
        out[tkr] = np.where(has & (ia <= it), np.exp(logc[it] - logc[ia]), 1.0)
    return out
