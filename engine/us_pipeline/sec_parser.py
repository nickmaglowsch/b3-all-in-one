"""SEC XBRL `companyfacts` -> point-in-time fundamentals rows.

The input is one company's complete fact history:

    {"cik": 320193, "entityName": "Apple Inc.",
     "facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        {"start": "...", "end": "...", "val": 1, "accn": "...",
         "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-03"}, ...]}}}}}

One row per (accession, period_end). That grouping is the whole point-in-time
mechanism: when a 10-K restates the prior year, those restated facts arrive under
the *new* accession with the new `filed` date, so both versions survive and a
backtest dated before the restatement still reads the original. Nothing is
deduplicated across accessions — the same rule `fundamentals_pit` follows for CVM
filing versions.

Tag choice is a judgement call and is made once, here, as an ordered preference
list per metric. Filers use different concepts for the same line (a company that
adopted ASC 606 reports revenue under
RevenueFromContractWithCustomerExcludingAssessedTax, an older filing under
Revenues), so the first tag present wins and the rest are fallbacks.

Two metrics are computed rather than reported, because US GAAP has no tag for
them, and both are NULL when a component is missing rather than being guessed:

  * **EBITDA** = operating income + depreciation & amortisation. Not "net income
    plus back-of-the-envelope adjustments" — that is a different number.
  * **net debt** = total debt - cash & equivalents. A filer that reports no debt
    tag at all gets NULL, not zero: "no debt reported" and "zero debt" are not
    the same claim, and a screen ranking on EV/EBITDA would treat them very
    differently.
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)

MARKET = "US"
SOURCE = "sec-companyfacts"

# Only the periodic reports. An 8-K or an S-1 carries facts too, but not a
# comparable set, and mixing them makes a quarter's numbers depend on whether the
# company happened to file a prospectus that month.
FORMS = ("10-K", "10-Q", "20-F", "40-F")

# Ordered preference per metric: the first tag with a value for the period wins.
_DURATION_TAGS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"),
    "net_income": ("NetIncomeLoss", "ProfitLoss",
                   "NetIncomeLossAvailableToCommonStockholdersBasic"),
    "_operating_income": ("OperatingIncomeLoss",),
    "_dep_amort": ("DepreciationDepletionAndAmortization",
                   "DepreciationAmortizationAndAccretionNet",
                   "DepreciationAndAmortization"),
}

# Balance-sheet items: reported at an instant, not over a period.
_INSTANT_TAGS = {
    "total_assets": ("Assets",),
    "equity": ("StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "shares_outstanding": ("CommonStockSharesOutstanding",
                           "CommonStockSharesIssued",
                           "EntityCommonStockSharesOutstanding"),
    "_cash": ("CashAndCashEquivalentsAtCarryingValue",
              "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
    "_debt_long": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "_debt_short": ("LongTermDebtCurrent", "ShortTermBorrowings",
                    "DebtCurrent"),
}

METRICS = ("revenue", "net_income", "ebitda", "total_assets", "equity",
           "net_debt", "shares_outstanding")


def _facts(doc: dict, tag: str) -> list:
    """Every reported observation of one tag, across taxonomies and units.

    Shares are reported in `shares`, money in `USD`; taking every unit and
    letting the tag decide keeps one lookup path for both.
    """
    out = []
    for taxonomy in doc.get("facts", {}).values():
        entry = taxonomy.get(tag)
        if not entry:
            continue
        for observations in entry.get("units", {}).values():
            out.extend(observations)
    return out


def _collect(doc: dict, tags: dict) -> dict:
    """{(accn, end): {metric: value}} for one group of tags.

    Later tags never overwrite earlier ones for the same (accn, end): the
    preference order in the tag lists is what decides, so a fallback only fills a
    hole.
    """
    found: dict = {}
    for metric, names in tags.items():
        for tag in names:
            for f in _facts(doc, tag):
                if f.get("form") not in FORMS or f.get("val") is None:
                    continue
                key = (f.get("accn"), f.get("end"))
                slot = found.setdefault(key, {})
                if metric in slot:
                    continue
                slot[metric] = float(f["val"])
                slot["_filed"] = f.get("filed")
                slot["_form"] = f.get("form")
                slot["_fy"] = f.get("fy")
                slot["_fp"] = f.get("fp")
    return found


def _merge(a: dict, b: dict) -> dict:
    """Union of two {(accn, end): {...}} maps, `a` winning on conflict."""
    out = {k: dict(v) for k, v in a.items()}
    for key, values in b.items():
        slot = out.setdefault(key, {})
        for name, value in values.items():
            slot.setdefault(name, value)
    return out


def _derived(slot: dict) -> tuple:
    """(ebitda, net_debt) for one period, or None where a component is missing.

    NULL rather than a partial figure, deliberately. An EBITDA computed without
    D&A is operating income wearing a different name, and a net debt computed
    from cash alone would report every company as deeply net-cash.
    """
    op, da = slot.get("_operating_income"), slot.get("_dep_amort")
    ebitda = op + da if op is not None and da is not None else None

    debt = [slot.get("_debt_long"), slot.get("_debt_short")]
    cash = slot.get("_cash")
    net_debt = (sum(d for d in debt if d is not None) - cash
                if any(d is not None for d in debt) and cash is not None else None)
    return ebitda, net_debt


def parse_companyfacts(doc: dict, ticker=None) -> pd.DataFrame:
    """One company's facts -> `fundamentals_pit_us` rows.

    Returns an empty frame for a company with no usable periodic facts — a real
    state for a filer that has only ever filed an S-1, and not an error.

    `ticker` is either a string or a callable from a filing date to the ticker
    the market knew then. The callable form is what lets a company that renamed
    itself produce two labelled columns that each line up with their own price
    history, instead of one column under today's name that lines up with half.
    """
    at = ticker if callable(ticker) else (lambda _filed: ticker)
    cik = str(doc.get("cik", "")).zfill(10)
    periods = _merge(_collect(doc, _DURATION_TAGS), _collect(doc, _INSTANT_TAGS))

    rows = []
    for (accn, end), slot in periods.items():
        filed = slot.get("_filed")
        if not accn or not end or not filed:
            # Without an accession and a filing date the fact cannot be placed in
            # time, which is the only thing that makes this table point-in-time.
            continue
        ebitda, net_debt = _derived(slot)
        rows.append({
            "fact_id": f"{accn}:{end}",
            "cik": cik,
            "ticker": at(filed),
            "period_end": end,
            "filing_date": filed,
            "accn": accn,
            "form": slot.get("_form"),
            "fiscal_year": slot.get("_fy"),
            "fiscal_period": slot.get("_fp"),
            "revenue": slot.get("revenue"),
            "net_income": slot.get("net_income"),
            "ebitda": ebitda,
            "total_assets": slot.get("total_assets"),
            "equity": slot.get("equity"),
            "net_debt": net_debt,
            "shares_outstanding": slot.get("shares_outstanding"),
        })
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # A row carrying nothing but its keys is an accession that reported none of
    # the metrics we read. Storing it would put a filing_date on the plane with
    # no numbers behind it, which forward-fills as "the company reported and we
    # know nothing", where the truth is "we did not read what it reported".
    return df.dropna(subset=list(METRICS), how="all").reset_index(drop=True)
