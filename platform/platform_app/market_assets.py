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
import time

from platform_app import asset_refs

SEARCH_LIMIT_MAX = 50
_BDR_SUFFIXES = ("31", "32", "33", "34", "35", "39")

# The candidates are the security master's live rows plus the renda-fixa ETFs
# only `external_prices` knows (passed in, see _reference), filtered in SQL to
# what a sleeve can hold — the market's canonical symbol shape, no test issue —
# *before* the LIMIT, so a page is never cut short afterwards. A B3 name matches
# through its CVM root ($9: the roots whose company name holds the query).
_SEARCH_SQL = r"""
WITH cand AS (
    SELECT i.market, i.symbol, i.kind, i.name
    FROM instruments i
    WHERE i.market = ANY($1::text[]) AND i.delisting_date IS NULL
      AND COALESCE(i.is_test_issue, 0) = 0
    UNION ALL
    SELECT 'B3', e.ticker, 'ETF_RF', NULL FROM unnest($6::text[]) AS e(ticker)
    WHERE 'B3' = ANY($1::text[]) AND NOT EXISTS (
        SELECT 1 FROM instruments i WHERE i.market = 'B3' AND i.symbol = e.ticker)
)
SELECT market, symbol, kind, name FROM cand
WHERE symbol ~ (CASE market WHEN 'B3' THEN $7 WHEN 'US' THEN $8 ELSE $10 END)
  AND (symbol LIKE $2 ESCAPE '\' OR name ILIKE $3 ESCAPE '\'
       OR (market = 'B3' AND SUBSTR(symbol, 1, 4) = ANY($9::text[]))
       OR (market = 'CRYPTO' AND symbol = ANY($11::text[])))
ORDER BY symbol = $4 DESC, symbol LIKE $2 ESCAPE '\' DESC, length(symbol), symbol, market
LIMIT $5
"""

# What the search reads besides `instruments`, cached: B3 company names by
# ticker root (cvm_companies) and the renda-fixa ETFs outside COTAHIST
# (external_prices). Both change with a daily ingest at most, and walking them
# on every keystroke bought nothing. Per pool, so a test's plane is its own.
# The cost is a lag of up to this long: a renda-fixa ETF B3 starts publishing,
# or a renamed company, is found by its new name only after the cache expires.
# Prices are never cached — every search reads them live.
_REFERENCE_TTL_S = 3600.0

# The crypto plane carries pairs, not names: the coins a person searches by
# name, so "bitcoin" finds BTCUSDT. A short list on purpose — any other pair is
# found by its symbol.
COIN_NAMES = {
    "BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum", "SOLUSDT": "Solana", "BNBUSDT": "BNB",
    "XRPUSDT": "XRP", "ADAUSDT": "Cardano", "DOGEUSDT": "Dogecoin", "LTCUSDT": "Litecoin",
    "DOTUSDT": "Polkadot", "AVAXUSDT": "Avalanche", "LINKUSDT": "Chainlink",
}
_REFERENCE: dict = {}


async def _reference(pool) -> tuple[dict[str, str], list[str]]:
    """({B3 root: company name}, [renda-fixa ETF tickers])."""
    hit = _REFERENCE.get(pool)
    if hit and time.monotonic() - hit[0] < _REFERENCE_TTL_S:
        return hit[1]
    names = await pool.fetch(
        "SELECT ticker, COALESCE(NULLIF(b3_trading_name, ''), company_name) AS name "
        "FROM cvm_companies WHERE ticker IS NOT NULL ORDER BY delisting_date DESC NULLS LAST")
    rf = await pool.fetch(
        "SELECT DISTINCT ticker FROM external_prices WHERE ticker NOT LIKE '$%'")
    # The listed company wins a root shared with a delisted one (sorted last).
    ref = ({r["ticker"]: r["name"] for r in names if r["name"]},
           sorted(r["ticker"] for r in rf))
    _REFERENCE.clear()  # one live plane at a time; never grows past it
    _REFERENCE[pool] = (time.monotonic(), ref)
    return ref


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


async def search(pool, q: str, markets: list[str], limit: int) -> list[dict]:
    """Instruments whose symbol starts with `q` or whose name contains it, exact
    symbol first, then symbol prefix, then name; each with its latest BRL close
    (None when the plane has never priced it — shown, but not selectable)."""
    needle = q.strip()
    names, rf = await _reference(pool)
    roots = [root for root, name in names.items() if needle.lower() in name.lower()]
    coins = [pair for pair, name in COIN_NAMES.items() if needle.lower() in name.lower()]
    pat = asset_refs.SYMBOL_PATTERN
    rows = await pool.fetch(_SEARCH_SQL, markets, _like(needle.upper()) + "%",
                            f"%{_like(needle)}%", needle.upper(), limit, rf,
                            pat["B3"], pat["US"], roots, pat["CRYPTO"], coins)
    prices = await latest_closes(pool, [(r["symbol"], r["market"]) for r in rows])
    out = []
    for r in rows:
        b3_name = names.get(r["symbol"][:4]) if r["market"] == "B3" else None
        coin = COIN_NAMES.get(r["symbol"]) if r["market"] == "CRYPTO" else None
        close, day = prices.get((r["symbol"], r["market"]), (None, None))
        out.append({
            "ticker": r["symbol"], "market": r["market"], "name": r["name"] or b3_name or coin,
            "asset_class": asset_class(r["market"], r["symbol"], r["kind"], bool(b3_name)),
            "price_brl": close, "price_date": day.isoformat() if day else None,
        })
    return out


# Latest positive close per name, one index probe each: every price table is
# keyed (…, symbol, date), so ORDER BY date DESC LIMIT 1 per ticker reads the
# newest rows only, where DISTINCT ON would walk every name's whole history.
_LATEST_SQL = """
SELECT t.symbol, p.close, p.date FROM unnest($1::text[]) AS t(symbol)
CROSS JOIN LATERAL (
    SELECT {close} AS close, date FROM {table}
    WHERE {key} = t.symbol{scope} AND {close} > 0
    ORDER BY date DESC LIMIT 1
) p
"""


async def _closes(pool, table: str, symbols: list[str], market: str) -> dict:
    if table == "market_prices":
        sql = _LATEST_SQL.format(close="close_brl", table=table, key="symbol",
                                 scope=" AND market = $2")
        rows = await pool.fetch(sql, symbols, market)
    else:
        sql = _LATEST_SQL.format(close="close", table=table, key="ticker", scope="")
        rows = await pool.fetch(sql, symbols)
    return {(r["symbol"], market): (float(r["close"]), r["date"]) for r in rows}


async def latest_closes(pool, refs: list[tuple[str, str]]
                        ) -> dict[tuple[str, str], tuple[float, datetime.date]]:
    """{(ticker, market): (latest close in BRL, its date)}; unpriced refs absent.
    A B3 code is read from `prices`, then from `external_prices` for what
    COTAHIST does not carry — no lot-shape filter, so a BDR is priced if the
    plane has it at all."""
    out: dict = {}
    for market in asset_refs.MARKETS:
        symbols = sorted({t for t, m in refs if m == market})
        tables = ("prices", "external_prices") if market == "B3" else ("market_prices",)
        for table in tables:
            if todo := [t for t in symbols if (t, market) not in out]:
                out |= await _closes(pool, table, todo, market)
    return out
