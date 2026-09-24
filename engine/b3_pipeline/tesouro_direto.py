"""Daily marks for Tesouro Direto titles, which COTAHIST does not carry.

A Tesouro title lands in the same hole as a CDB: renda fixa, no ticker, no
exchange, so B3's extract reports the aplicação and the resgate and nothing in
between. Unlike a CDB it is not bilateral bank paper — the Tesouro publishes a
price for every title on every business day, free and unauthenticated, in one
file. So for exactly these holdings the modelled tail that `accrue_rf` draws
past the last mark, under an indexer the tenant had to type in, is replaceable
with the issuer's own published number.

Stored in `external_prices` under the same name B3 writes in the Movimentação
extract — "TESOURO IPCA+ 2045" — which is what `load_b3._rf_code` already
derives, so the platform joins on it with no mapping table in between.

Which of the file's three PU columns: `PU Venda Manha`. They are quoted from the
investor's side — buy at `PU Compra`, sell back at `PU Venda`, the spread
running against the investor, which is the only reading of the two that is not a
free lunch — and what a held position is worth is what it could be sold for.
`PU Base` equals `PU Venda` on every recent row and differs by cents on the
oldest ones.

These are morning quotes and a fill happens intraday, so a tenant's own mark and
this file's PU for the same day differ: checked against a real book, by up to
~3% on a 2045 title, which is a quarter-point of rate acting on twenty-one years
of duration. That is the same relationship COTAHIST's close has to an equity
fill. Cost basis still comes from the extract; only the mark comes from here.
"""
from __future__ import annotations

import datetime
import io
import logging

import pandas as pd
import requests

from .external_prices import level_rows

logger = logging.getLogger(__name__)

# Marks this loader's rows in the shared table. storage's resume helpers scope on
# it so neither the B3 ETF backfill nor the BCB series mistakes these dates for
# their own — see storage.stored_external_dates.
SOURCE = "TESOURO_DIRETO"

# Tesouro Transparente's CKAN resource. Stable id, ~14 MB, no auth, UTF-8,
# semicolon-delimited, comma decimals, rewritten daily with the full history.
URL = ("https://www.tesourotransparente.gov.br/ckan/dataset/"
       "df56aa42-484a-4a59-8184-7676580c81e3/resource/"
       "796d2059-14e9-44e3-80c9-2d9e30b405c1/download/PrecoTaxaTesouroDireto.csv")

# The file reaches back to 2002. Cut at the same epoch as the BCB series
# (index_series.INDEX_SERIES_START) — earlier rows are ~40k more inserts for
# paper that matured before any book this platform indexes was opened.
TESOURO_START = datetime.date(2010, 1, 1)

# Only the four fields that survive into `external_prices` are read; naming them
# also means a renamed column fails loudly at read_csv rather than silently
# yielding an empty feed.
_COLUMNS = {
    "Tipo Titulo": "tipo",
    "Data Vencimento": "vencimento",
    "Data Base": "date",
    "PU Venda Manha": "close",
}


def ticker_for(tipo: str, vencimento: pd.Timestamp) -> str:
    """The name B3's Movimentação extract gives this title.

    "Tesouro IPCA+" maturing 2045-05-15 is "TESOURO IPCA+ 2045" there, upper-cased
    by `load_b3._rf_code`. The maturity *year* is all B3 carries, which is what
    makes `_drop_ambiguous` necessary.
    """
    return f"{tipo.strip()} {vencimento.year}".upper()


def _drop_ambiguous(df: pd.DataFrame) -> pd.DataFrame:
    """Drop titles whose B3 name cannot identify a single maturity.

    Until 2013 the Tesouro issued several Prefixados a year, so "Tesouro
    Prefixado 2010" names two different papers — and B3's extract writes exactly
    that string, so it cannot tell them apart either. Picking one would price a
    holding off the wrong bond and the chart would never say so, which is worse
    than not drawing it: the whole group goes, with a line in the log. Every
    title issued since is unique within its maturity year.
    """
    per_name = df.groupby("ticker")["vencimento"].nunique()
    ambiguous = set(per_name[per_name > 1].index)
    if not ambiguous:
        return df
    logger.warning(
        f"Dropping {len(ambiguous)} title(s) whose name maps to several "
        f"maturities: {', '.join(sorted(ambiguous))}")
    return df[~df["ticker"].isin(ambiguous)]


def parse(payload: bytes) -> pd.DataFrame:
    """The published CSV as (ticker, vencimento, date, close), one row per day."""
    df = pd.read_csv(io.BytesIO(payload), sep=";", decimal=",",
                     encoding="utf-8", usecols=list(_COLUMNS))
    df = df.rename(columns=_COLUMNS)
    for col in ("vencimento", "date"):
        df[col] = pd.to_datetime(df[col], format="%d/%m/%Y", errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["tipo", "vencimento", "date", "close"])
    df["ticker"] = [ticker_for(t, v) for t, v in zip(df["tipo"], df["vencimento"])]
    return _drop_ambiguous(df)


def fetch(url: str = URL, timeout: int = 120) -> bytes:
    """The raw file. Split from `parse` so tests never need the network."""
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def build(start: datetime.date = TESOURO_START, url: str = URL) -> pd.DataFrame:
    """Marks from `start` onward, in `external_prices` shape.

    `start` is inclusive and the caller passes the latest date already stored, so
    that day is re-fetched and upserted onto itself. Resuming from the maximum is
    safe here in a way it is not for the B3 ETF feed (main.py step 5b): this file
    carries its whole history on every request, so a day a network error skipped
    is still there tomorrow rather than rolled off a 14-month window.
    """
    df = parse(fetch(url))
    df = df[df["date"].dt.date >= start]
    if df.empty:
        logger.warning(f"Tesouro Direto file had nothing from {start} onward")
        return pd.DataFrame()

    out = level_rows(df["ticker"].to_numpy(), df["date"].to_numpy(),
                     df["close"].to_numpy(), SOURCE)
    logger.info(f"Tesouro Direto: {len(out):,} marks over "
                f"{out['ticker'].nunique()} titles, "
                f"{df['date'].min():%Y-%m-%d} to {df['date'].max():%Y-%m-%d}")
    return out
