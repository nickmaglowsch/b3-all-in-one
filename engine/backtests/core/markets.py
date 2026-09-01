"""What differs between the markets the bench can trade.

Everything B3-specific that used to be welded into the loaders and the feature
builders lives here as data, so adding a market is a table entry rather than a
branch in six functions.

Three things about this registry are load-bearing and easy to get wrong:

**The shape filter and the listing interval are not the same thing.** B3 needs
the ticker-suffix rule because COTAHIST carries BDRs, options and units that no
security master will ever cover for thirty years of history; `instruments` says
*when* something was tradable, `ticker_sql` says *what counts as an instrument at
all*. They compose. That is why B3 keeps its suffix rule instead of being forced
through a master it does not have.

**Split detection must not run on crypto.** `shared_data._detect_and_fix_unrecorded_splits`
fires on a persistent, calm-volume overnight repricing by a clean ratio. On B3
that is an unrecorded grupamento. In crypto a -50% day is a Tuesday, and letting
the detector loose would silently rewrite price history.

**The glitch thresholds are B3-calibrated.** `has_glitch` masks a name whose
return crossed +100%/-45% in the lookback. Applied to crypto that masks
essentially the whole universe, permanently.

The numeraire is BRL for every market. The holder is a Brazilian taxpayer, the
tax engine that scores the backtest works in reais, and the cash sleeve is CDI
whatever the equity sleeve is — so `currency` here describes what the venue
quotes, not what the simulation counts in. A consequence worth stating: a US or
crypto backtest bundles the USD/BRL move into its return. That is correct for
this product's user and not comparable to a native-currency Sharpe.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketSpec:
    key: str
    currency: str                 # what the venue quotes, not the numeraire
    table: str                    # where the daily bars live
    symbol_column: str            # what that table calls the instrument
    ticker_sql: str               # shape filter over the symbol; "" = none
    benchmark: str                # passed to download_benchmark
    aliases: dict                 # caller-facing benchmark name -> stored symbol
    suffix_strip: str | None      # ".SA" on B3, from the yfinance era
    glitch_up: float
    glitch_down: float
    detect_splits: bool
    root_len: int | None          # share-class root width; None = no roots
    default_min_adtv: float
    default_min_price: float
    fundamentals: bool            # is there a PIT fundamentals table for it


# Character-for-character the predicate the two B3 loaders and
# advise_weights._STANDARD_LOT_SQL have always used: standard lots ending in
# 3/4/5/6, plus units ending in 11. BDRs (34, 35) and the rest stay out.
_B3_LOTS = (
    "((LENGTH({col}) = 5 AND SUBSTR({col}, 5, 1) IN ('3', '4', '5', '6'))"
    " OR (LENGTH({col}) = 6 AND SUBSTR({col}, 5, 2) = '11'))"
)

MARKETS = {
    "B3": MarketSpec(
        key="B3", currency="BRL", table="prices", symbol_column="ticker",
        ticker_sql=_B3_LOTS.format(col="ticker"),
        benchmark="^BVSP", aliases={"^BVSP": "$IBOV", "IBOV": "$IBOV"},
        suffix_strip=".SA",
        glitch_up=1.0, glitch_down=-0.45, detect_splits=True, root_len=4,
        default_min_adtv=1_000_000.0, default_min_price=1.0, fundamentals=True,
    ),
    "US": MarketSpec(
        key="US", currency="USD", table="market_prices", symbol_column="symbol",
        ticker_sql="", benchmark="$SPX", aliases={"^GSPC": "$SPX", "SPX": "$SPX"},
        suffix_strip=None,
        glitch_up=1.0, glitch_down=-0.45, detect_splits=False, root_len=None,
        # In BRL, like every threshold on this plane. ~US$200k/day of turnover
        # and a US$1 price — the usual penny-stock floor — at a ~5.5 rate.
        default_min_adtv=1_000_000.0, default_min_price=5.0, fundamentals=True,
    ),
    "CRYPTO": MarketSpec(
        key="CRYPTO", currency="USDT", table="market_prices",
        symbol_column="symbol",
        # Binance lists the same coin against a dozen quote assets; without this
        # a "universe" would hold BTCUSDT, BTCFDUSD and BTCTRY as three names.
        ticker_sql="symbol LIKE '%USDT'",
        benchmark="BTCUSDT", aliases={"BTC": "BTCUSDT"}, suffix_strip=None,
        # A -45% day is not a glitch here and a +100% day is not either.
        glitch_up=5.0, glitch_down=-0.90, detect_splits=False, root_len=None,
        default_min_adtv=1_000_000.0, default_min_price=0.0,
        # No filings, no fundamentals. validate_spec rejects a crypto spec that
        # asks for them rather than handing it silently empty frames.
        fundamentals=False,
    ),
}

DEFAULT = "B3"


def get(key: str | None) -> MarketSpec:
    """The spec for a market key. Unknown keys raise rather than defaulting —
    a typo in a stored strategy spec must not quietly run against B3."""
    resolved = (key or DEFAULT).strip().upper()
    if resolved not in MARKETS:
        raise ValueError(
            f"Unknown market {key!r}. Choose from: {', '.join(sorted(MARKETS))}")
    return MARKETS[resolved]


def keys() -> list:
    return sorted(MARKETS)
