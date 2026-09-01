"""What each market plane actually holds, for GET /market/status.

Extracted from routes.py because none of it is routing: it is plain SQL over
ENGINE_DATABASE_URL, and it grew a per-market half when the plane stopped being
B3-only.

The per-market block is the honest-gaps surface for the new planes. It reports
what the coverage is rather than implying completeness — how far back the archive
goes, how many instruments are in it, and **how many of those are dead**, which
is the number that says whether the plane is survivorship-free at all. A market
whose `delisted` count is zero after a real backfill is a market that is quietly
dropping its corpses somewhere.
"""

_EMPTY_PLANE = {
    "prices": {"rows": 0, "tickers": 0, "min_date": None, "max_date": None},
    "tables": {},
    "markets": {},
}

# (table, date column) pairs reported as a plain rows/freshness line.
_TABLES = (
    ("corporate_actions", "event_date"),
    ("fundamentals_pit", "filing_date"),
    ("fundamentals_pit_us", "filing_date"),
)


def _iso(value):
    return value.isoformat() if value else None


async def _b3_prices(pool) -> dict:
    row = await pool.fetchrow(
        "SELECT count(*) AS rows, count(DISTINCT ticker) AS tickers, "
        "min(date) AS min_date, max(date) AS max_date FROM prices")
    return {"rows": row["rows"], "tickers": row["tickers"],
            "min_date": _iso(row["min_date"]), "max_date": _iso(row["max_date"])}


async def _tables(pool) -> dict:
    out = {}
    for name, datecol in _TABLES:
        try:
            row = await pool.fetchrow(
                f"SELECT count(*) AS rows, max({datecol}) AS fresh FROM {name}")
        except Exception:                               # noqa: BLE001
            # A plane created by an older engine has not got the table yet. That
            # is "not present", not an outage, and it must not take the whole
            # status response down with it.
            continue
        out[name] = {"rows": row["rows"], "fresh": _iso(row["fresh"])}
    return out


async def _markets(pool) -> dict:
    """Per-market coverage off `instruments` + `market_prices`.

    B3 prices live in `prices`, not `market_prices`, so its price window is read
    from there; every other market reads its own rows. Both halves are LEFT-ish:
    a market with instruments but no prices yet is a real state during a first
    backfill, and it should show as such rather than vanish — and so is the
    reverse, which this used to get wrong by iterating the instruments rows and
    only looking prices up. A market with bars and an empty security master
    disappeared from the response entirely.
    """
    try:
        rows = await pool.fetch(
            "SELECT market, count(*) AS instruments,"
            " count(*) FILTER (WHERE delisting_date IS NOT NULL) AS delisted,"
            " min(first_seen) AS first_seen, max(last_seen) AS last_seen"
            " FROM instruments GROUP BY market ORDER BY market")
        priced = await pool.fetch(
            "SELECT market, count(*) AS rows, min(date) AS min_date,"
            " max(date) AS max_date, count(*) FILTER (WHERE close_brl IS NULL)"
            " AS unconverted FROM market_prices GROUP BY market")
        adjust = await _adjust_states(pool)
    except Exception:                                   # noqa: BLE001
        return {}

    by_instruments = {r["market"]: r for r in rows}
    by_price = {r["market"]: r for r in priced}
    out = {}
    for name in sorted(set(by_instruments) | set(by_price)):
        out[name] = _plane(name, by_instruments.get(name), by_price.get(name),
                           adjust)
    return out


def _plane(name, r, px, adjust: dict) -> dict:
    """One market's block. Either half may be missing and both are real states."""
    block = {
        "instruments": r["instruments"] if r else 0,
        # The survivorship number. Zero here after a real backfill means the
        # plane is dropping its dead somewhere upstream.
        "delisted": r["delisted"] if r else 0,
        "first_seen": _iso(r["first_seen"]) if r else None,
        "last_seen": _iso(r["last_seen"]) if r else None,
        "price_rows": px["rows"] if px else 0,
        "min_date": _iso(px["min_date"]) if px else None,
        "max_date": _iso(px["max_date"]) if px else None,
        # Bars the plane could not convert to BRL — no PTAX for the date, or
        # a quote asset with no route to the real. Reported, not hidden.
        "unconverted_brl": px["unconverted"] if px else 0,
        # Where a survivorship-free backtest can honestly start. Before this
        # date the plane has listing intervals but no bars.
        "price_coverage_start": _iso(px["min_date"]) if px else None,
    }
    if name == "US":
        # What `adj_close` on this plane actually is, per symbol. The free daily
        # series is unadjusted, so a total-return comparison is only supported
        # where the corporate-actions endpoints answered — and a product that
        # did not say so would be claiming a number it does not have.
        block["adj_close"] = adjust
        block["benchmark"] = {"ticker": "$SPX", "proxy": "SPY",
                              "note": "ETF proxy, BRL, total return"}
    return block


async def _adjust_states(pool) -> dict:
    """How many US symbols carry each adjustment state.

    Its own query and its own failure: `us_price_cursor` arrives with the price
    ingest, and a plane that predates it should report the rest of the block
    rather than nothing.
    """
    try:
        rows = await pool.fetch(
            "SELECT adjust_state, count(*) AS n FROM us_price_cursor"
            " WHERE adjust_state IS NOT NULL GROUP BY adjust_state")
    except Exception:                                   # noqa: BLE001
        return {}
    return {r["adjust_state"]: r["n"] for r in rows}


async def status(pool) -> dict:
    """The uncached plane summary."""
    return {"prices": await _b3_prices(pool),
            "tables": await _tables(pool),
            "markets": await _markets(pool)}
