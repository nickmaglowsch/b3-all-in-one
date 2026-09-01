"""The US benchmark, as a labelled ETF proxy in reais.

`markets.MARKETS["US"].benchmark` has pointed at `$SPX` since the US plane was
built and nothing has ever written it, so a US backtest ran without a ruler:
`download_benchmark` returned an empty series and `_plane_series` correctly
refused to fabricate a flat line, which is honest and not much use.

Three decisions are baked into this module and each one is visible in the
stored rows.

**SPY, not the index.** The S&P 500 level has no free feed with a licence this
project can stand behind — it is a licensed product, which is exactly the
posture that keeps yfinance out of the engine. The ETF that tracks it does. So
`$SPX` here is SPY's total return, and the difference is real and disclosed: a
fund fee of about nine basis points a year, some tracking error, and
distributions that are the fund's rather than the index's. `source` says
`ALPHAVANTAGE_SPY_BRL` so nobody has to guess, and `/market/status` reports it.
This is the one place the plane serves a proxy, and it is named as one.

**In BRL.** The numeraire is reais for every market — `metrics.json` already
declares `"currency": "BRL"` precisely because a US book's Sharpe has the dollar
inside it. A benchmark without the same conversion is the wrong ruler, and
nothing in the output would have said so: the strategy would carry the currency
move and the bench would not, and the difference would read as skill. `$IBOV`
already stores points in reais, so this is the same shape, not a special case.

**Total return, in `close`.** `download_benchmark` routes a `$`-prefixed symbol
to `download_index_level`, which reads the `close` column — so the adjusted
series has to live there. `external_prices` has no adjusted column and no
currency columns; it holds one number per day, and for this ticker that number
is a BRL total-return level.
"""

import datetime
import logging

import pandas as pd

from b3_pipeline import fx_series, storage

from . import us_adjust
from .alphavantage import Client
from .av_parsers import parse_time_series_daily
from .us_prices import fetch_actions

logger = logging.getLogger(__name__)

SOURCE = "ALPHAVANTAGE_SPY_BRL"
SPX_TICKER = "$SPX"
PROXY_SYMBOL = "SPY"


def fetch(client: Client, symbol: str = PROXY_SYMBOL) -> tuple:
    """Bars and corporate actions for the proxy. Up to three requests.

    Distributions matter more here than anywhere else on the plane: a benchmark
    without them understates the market by roughly the dividend yield every
    year, and understating the thing a strategy is measured against makes every
    strategy look better than it is.
    """
    bars = parse_time_series_daily(
        client.get_csv("TIME_SERIES_DAILY", symbol=symbol, outputsize="full"),
        symbol)
    splits, dividends = fetch_actions(client, symbol)
    return bars, splits, dividends


def to_external_rows(bars: pd.DataFrame, rates: pd.Series) -> pd.DataFrame:
    """Adjusted bars -> `external_prices` shape, in reais.

    Rows PTAX cannot cover are dropped rather than stored unconverted. This
    table has one number per day and no `fx_rate` column to carry the caveat, so
    a row here is either a BRL level or it is not written — the alternative is a
    dollar number in a series everything else reads as reais.
    """
    if bars.empty:
        return pd.DataFrame()
    out = fx_series.to_brl(bars, rates, columns=("adj_close",))
    out = out.dropna(subset=["adj_close_brl"])
    if out.empty:
        logger.warning("PTAX covers none of the benchmark's dates; nothing written")
        return pd.DataFrame()
    return pd.DataFrame({
        "ticker": SPX_TICKER,
        "isin_code": "UNKNOWN",
        "date": out["date"].to_numpy(),
        # A benchmark level has no bar and no volume of its own. NaN rather
        # than faked from the close, and NaN rather than None for the dtype
        # reason index_series.build documents.
        "open": float("nan"), "high": float("nan"), "low": float("nan"),
        "close": out["adj_close_brl"].to_numpy(),
        "volume": 0.0,
        "source": SOURCE,
    })


def build(client: Client, rates: pd.Series) -> pd.DataFrame:
    bars, splits, dividends = fetch(client)
    adjusted, state = us_adjust.apply(bars, splits, dividends)
    if state != us_adjust.TOTAL_RETURN:
        logger.warning(
            f"{SPX_TICKER} is being built from a {state} series: the benchmark "
            "will understate the market by roughly the dividend yield, which "
            "flatters every strategy measured against it.")
    rows = to_external_rows(adjusted, rates)
    if not rows.empty:
        logger.info(f"{SPX_TICKER}: {len(rows):,} sessions, "
                    f"{rows['close'].iloc[-1]:,.2f} BRL at {rows['date'].iloc[-1]}")
    return rows


def run_pipeline(db_path=None, today=None) -> dict:
    today = today or datetime.date.today()
    logger.info(f"US benchmark -> {storage.describe_target(db_path)}")
    with storage.get_connection(db_path) as conn:
        storage.init_db(conn)
        fx = fx_series.build(today)
        if not fx.empty:
            storage.upsert_external_prices(conn, fx)
        rates = fx_series.load(conn)

        rows = build(Client(conn, today=today), rates)
        written = storage.upsert_external_prices(conn, rows) if not rows.empty else 0
        return {"ticker": SPX_TICKER, "sessions": int(len(rows)),
                "written": int(written), "proxy": PROXY_SYMBOL}


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=None)
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_pipeline(args.db_path)


if __name__ == "__main__":
    main()
