"""Listed assets on the market planes: search them, and price them in reais.

Plain SQL over ENGINE_DATABASE_URL, like market_planes. Two readers: the sleeve
picker's search (GET /market/assets) and the live sleeves' save, which refuses a
ticker the plane cannot price rather than storing a sleeve the rebalance plan
would then skip without a word.

Where each market keeps what:

  B3      `instruments` (the security master, backfilled from `prices`), names
          from `cvm_companies` by ticker root; closes in `prices`. Renda-fixa
          ETFs are outside COTAHIST: they are only in `external_prices`.
  US      `instruments` (name, kind ETF/EQUITY); closes in `market_prices`,
  CRYPTO  already converted to BRL at ingest (`close_brl`).

The price is the raw close in reais — what a share costs today, the number a
trade is sized with — and engine/scripts/weights_pricing.py reads the same
columns for the rebalance trades, so the save and the plan agree on it.
"""
from __future__ import annotations

import datetime

from platform_app import asset_refs

SEARCH_LIMIT_MAX = 50
_BDR_SUFFIXES = ("31", "32", "33", "34", "35", "39")

# One row per searchable instrument. `company` says a B3 root is a CVM-listed
# company: a *11 of a company is a unit, a *11 of nobody is a fund (FII or ETF).
_SEARCH_SQL = r"""
WITH names AS (
    SELECT DISTINCT ON (ticker) ticker AS root,
           COALESCE(NULLIF(b3_trading_name, ''), company_name) AS name
    FROM cvm_companies WHERE ticker IS NOT NULL
    ORDER BY ticker, delisting_date NULLS FIRST
), cand AS (
    SELECT i.market, i.symbol, i.kind, COALESCE(i.name, n.name) AS name,
           n.root IS NOT NULL AS company
    FROM instruments i
    LEFT JOIN names n ON i.market = 'B3' AND n.root = SUBSTR(i.symbol, 1, 4)
    WHERE i.market = ANY($1::text[]) AND i.delisting_date IS NULL
      AND COALESCE(i.is_test_issue, 0) = 0
    UNION ALL
    SELECT 'B3', e.ticker, 'ETF_RF', NULL, FALSE
    FROM (SELECT DISTINCT ticker FROM external_prices
          WHERE ticker NOT LIKE '$%') e
    WHERE 'B3' = ANY($1::text[]) AND NOT EXISTS (
        SELECT 1 FROM instruments i WHERE i.market = 'B3' AND i.symbol = e.ticker)
)
SELECT market, symbol, kind, name, company FROM cand
WHERE symbol LIKE $2 ESCAPE '\' OR name ILIKE $3 ESCAPE '\'
ORDER BY symbol = $4 DESC, symbol LIKE $2 ESCAPE '\' DESC, length(symbol), symbol, market
LIMIT $5
"""


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def asset_class(market: str, symbol: str, kind: str | None, company: bool) -> str:
    """What the instrument is, as far as the plane says: STOCK, UNIT, FUND (a
    B3 *11 of no listed company — a FII or an ETF; the plane cannot tell which),
    ETF_RF, BDR, ETF or CRYPTO. A label for the picker, not a tax class — taxcore
    classifies a trade by its own lists."""
    if market == "CRYPTO":
        return "CRYPTO"
    if market == "US":
        return "ETF" if kind == "ETF" else "STOCK"
    if kind == "ETF_RF":
        return "ETF_RF"
    if symbol.endswith(_BDR_SUFFIXES):
        return "BDR"
    if symbol.endswith("11"):
        return "UNIT" if company else "FUND"
    return "STOCK"


def _searchable(market: str, symbol: str) -> bool:
    """Only what a sleeve can hold, in the spelling it stores: a symbol the
    sleeve form would refuse or respell (a US preferred's `BAC$K`) is not
    offered, rather than offered and then refused on save."""
    try:
        return asset_refs.canonical(symbol, market) == symbol
    except ValueError:
        return False


async def search(pool, q: str, markets: list[str], limit: int) -> list[dict]:
    """Instruments whose symbol starts with `q` or whose name contains it, exact
    symbol first, then symbol prefix, then name; each with its latest BRL close
    (None when the plane has never priced it — shown, but not selectable)."""
    needle = q.strip()
    rows = await pool.fetch(_SEARCH_SQL, markets, _like(needle.upper()) + "%",
                            f"%{_like(needle)}%", needle.upper(), limit)
    found = [r for r in rows if _searchable(r["market"], r["symbol"])]
    prices = await latest_closes(pool, [(r["symbol"], r["market"]) for r in found])
    out = []
    for r in found:
        close, day = prices.get((r["symbol"], r["market"]), (None, None))
        out.append({
            "ticker": r["symbol"], "market": r["market"], "name": r["name"],
            "asset_class": asset_class(r["market"], r["symbol"], r["kind"], r["company"]),
            "price_brl": close, "price_date": day.isoformat() if day else None,
        })
    return out


async def _b3_closes(pool, tickers: list[str]) -> dict:
    """Latest close per B3 code: `prices` first, then `external_prices` for what
    COTAHIST does not carry. No lot-shape filter — a BDR is priced if the plane
    has it at all."""
    out: dict = {}
    for table in ("prices", "external_prices"):
        todo = [t for t in tickers if (t, "B3") not in out]
        if not todo:
            break
        rows = await pool.fetch(
            f"SELECT DISTINCT ON (ticker) ticker, close, date FROM {table} "  # noqa: S608
            "WHERE ticker = ANY($1::text[]) AND close IS NOT NULL AND close > 0 "
            "ORDER BY ticker, date DESC", todo)
        out |= {(r["ticker"], "B3"): (float(r["close"]), r["date"]) for r in rows}
    return out


async def _market_closes(pool, market: str, symbols: list[str]) -> dict:
    rows = await pool.fetch(
        "SELECT DISTINCT ON (symbol) symbol, close_brl, date FROM market_prices "
        "WHERE market = $1 AND symbol = ANY($2::text[]) AND close_brl IS NOT NULL "
        "AND close_brl > 0 ORDER BY symbol, date DESC", market, symbols)
    return {(r["symbol"], market): (float(r["close_brl"]), r["date"]) for r in rows}


async def latest_closes(pool, refs: list[tuple[str, str]]
                        ) -> dict[tuple[str, str], tuple[float, datetime.date]]:
    """{(ticker, market): (latest close in BRL, its date)}; unpriced refs absent."""
    out: dict = {}
    for market in asset_refs.MARKETS:
        symbols = sorted({t for t, m in refs if m == market})
        if not symbols:
            continue
        if market == "B3":
            out |= await _b3_closes(pool, symbols)
        else:
            out |= await _market_closes(pool, market, symbols)
    return out
