"""ANBIMA's fixed-income indexes, so a renda-fixa ETF has a history before its own.

The ETFs de renda fixa (IMAB11, IRFM11, B5P211, ...) reach the plane through B3's
daily trade file, which only serves ~14 months back (see external_prices.py). A
backtest over a decade cannot hold an instrument it can only see for one year —
but each of these funds is a passive copy of an ANBIMA index, and ANBIMA
publishes those indexes, free and unauthenticated, back to 2009. So the index is
stored, and the engine splices it in front of the ETF's own closes, net of the
fund's fee (backtests/core/stored_series.py, `rf_etf_series`). The indexes are also
benchmarks in their own right: IMA-B is what a Brazilian bond fund is measured
against.

Stored in `external_prices` under reserved `$` tickers, "Número Índice" as
`close`, the same shape as $CDI. `P` stands for ANBIMA's "+" (IMA-B 5+ is
`$IMAB5P`), because "+" is not something a ticker should carry through a URL:

    $IRFM1  IRF-M 1      $IMAB5    IMA-B 5      $IMAS      IMA-S
    $IRFM1P IRF-M 1+     $IMAB5P   IMA-B 5+     $IMAGERAL  IMA-GERAL
    $IRFM   IRF-M        $IMAB     IMA-B
    $IRFMP2 IRF-M P2     $IMAB5P2  IMA-B 5 P2

IMA-C and IMA-GERAL ex-C come in the same answer and are skipped: no ETF tracks
them and nobody is benchmarked against them.

One request answers ONE date. The endpoint takes a range, but answers only its
first day, so the history is ~4,300 sequential requests from INDEX_SERIES_START.
That is why this loader resumes from what is stored instead of rewriting the
whole series every night like index_series does.

The two P2 indexes come from a different page, and ANBIMA serves only their last
week or so (measured 2026-11-01: 23/10/2026 answers, 22/10/2026 says "Não há
dados"). They are stored going forward, from whatever the window still had on
the first run, and are only as long as the nightly run has been running. That
makes them benchmarks only: they start long after the ETFs' own closes do, so
the splice can never reach them (see config.RF_ETF_INDEX).
"""
from __future__ import annotations

import csv
import datetime
import io
import logging
import time

import pandas as pd
import requests

from . import storage
from .external_prices import BackfillResult, FetchFailed, _num, level_rows, store_result

logger = logging.getLogger(__name__)

# Two sources, not one, because the two pages are two feeds with two resume
# stories — the IMA history resumes from its newest stored date, the P2 window
# heals the last few days — and storage's resume helpers scope on this field.
SOURCE_IMA = "ANBIMA_IMA"
SOURCE_P2 = "ANBIMA_P2"

IMA_URL = "https://www.anbima.com.br/informacoes/ima/ima-sh-down.asp"
P2_URL = "https://www.anbima.com.br/informacoes/ima-p2/ima-p2-geral-down.asp"

# ANBIMA's name for the index -> ours. The keys are matched exactly against the
# file's "Índice" column, so a rename there drops the row rather than storing it
# under a guess.
INDEXES = {
    "IRF-M 1": "$IRFM1", "IRF-M 1+": "$IRFM1P", "IRF-M": "$IRFM",
    "IMA-B 5": "$IMAB5", "IMA-B 5+": "$IMAB5P", "IMA-B": "$IMAB",
    "IMA-S": "$IMAS", "IMA-GERAL": "$IMAGERAL",
    "IRF-M P2": "$IRFMP2", "IMA-B 5 P2": "$IMAB5P2",
}
NAMES = {ticker: name for name, ticker in INDEXES.items()}

# Same epoch as the BCB series (index_series.INDEX_SERIES_START). ANBIMA has
# data before it — 2009-01-02 answers — but IRF-M 1 and 1+ only appear in the
# summary from 2011, and nothing on the plane reaches further back than this.
ANBIMA_START = datetime.date(2010, 1, 1)

# How far back each run re-checks the P2 page. ANBIMA keeps about a week of it,
# so anything older is gone whatever we ask; ten days covers the window and the
# weekend around it, which is all a retry could ever recover.
P2_HEAL_DAYS = 10

# Between requests. ANBIMA answers in ~0.25s; this keeps the one-off backfill at
# ~2 requests a second rather than hammering a free public endpoint.
REQUEST_PAUSE_S = 0.2

# Consecutive failed requests after which a run stops asking. A hung or
# rate-limiting ANBIMA would otherwise cost the first backfill ~4,300 timeouts
# — hours — and write every one of them into the failure ledger. Twenty in a row
# is no blip; the next run resumes from the same place.
GIVE_UP_AFTER = 20

# What ANBIMA answers for a date it has nothing for — a weekend, a holiday, a
# P2 date past its window, or today before publication. It is an answer, not a
# failure: the bracketed date in it is not always the one that was asked for
# (a Sunday answers with the last session's date), so only the prefix is read.
_NO_DATA = "Não há dados disponíveis"


class GaveUp(FetchFailed):
    """GIVE_UP_AFTER requests in a row failed. Carries what was fetched before
    that, so the caller can still store it and record the failures."""

    def __init__(self, message: str, result: BackfillResult) -> None:
        super().__init__(message)
        self.result = result


def _form(day: datetime.date, p2: bool) -> dict:
    """The POST body each page's own download button sends."""
    if p2:
        return {"Tipo": "", "DataRef": "", "Pai": "ima", "Dt_Ref_Ver": "",
                "Dt_Ref": f"{day:%d/%m/%Y}", "saida": "csv", "escolha": "2",
                "Idioma": "PT"}
    return {"Tipo": "", "DataRef": "", "Pai": "ima", "escolha": "2", "Idioma": "PT",
            "saida": "csv", "Dt_Ref_Ver": "", "Dt_Ref": "",
            "DataIni": f"{day:%d/%m/%Y}", "DataFim": f"{day:%d/%m/%Y}",
            "Indice": "quadro-resumo", "Consulta": "Ambos"}


def parse(body: bytes) -> pd.DataFrame:
    """One answer -> (ticker, date, close) rows; empty for "Não há dados".

    Latin-1, `;`-delimited, comma decimals with `.` thousands. Line 1 is a banner
    ("TOTAIS - QUADRO RESUMO"), line 2 the header. Anything that is neither the
    no-data sentence nor that table — an HTML error page, a renamed column —
    raises FetchFailed: storing nothing for it would read as a holiday, and the
    day would never be asked for again.
    """
    text = body.decode("latin-1").strip()
    if text.startswith(_NO_DATA):
        return pd.DataFrame(columns=["ticker", "date", "close"])
    _, _, rest = text.partition("\n")
    reader = csv.DictReader(io.StringIO(rest), delimiter=";")
    need = {"Índice", "Data de Referência", "Número Índice"}
    if not need <= set(reader.fieldnames or ()):
        raise FetchFailed(f"unrecognised ANBIMA answer: {text[:80]!r}")
    rows = []
    for row in reader:
        ticker = INDEXES.get((row["Índice"] or "").strip())
        close = _num(row["Número Índice"])
        if ticker is None or not close or close <= 0:
            continue
        rows.append({"ticker": ticker, "close": close,
                     "date": pd.to_datetime(row["Data de Referência"].strip(),
                                            format="%d/%m/%Y")})
    return pd.DataFrame(rows, columns=["ticker", "date", "close"])


def fetch_day(day: datetime.date, p2: bool = False) -> pd.DataFrame:
    """One date's index levels. Empty when ANBIMA has none for it; FetchFailed
    when it did not answer — the same split, for the same reason, as
    external_prices.fetch_day."""
    try:
        resp = requests.post(P2_URL if p2 else IMA_URL, data=_form(day, p2),
                             timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise FetchFailed(f"ANBIMA unavailable for {day}: {e}") from e
    return parse(resp.content)


def backfill(days, p2: bool = False, pause: float = REQUEST_PAUSE_S) -> BackfillResult:
    """`days` in `external_prices` shape, surviving the ones that fail — up to
    GIVE_UP_AFTER in a row, where it raises GaveUp with what it has. Either way
    the result says which days were asked: after a trip, not all of them."""
    frames, failed, attempted, streak = [], {}, [], 0
    for i, day in enumerate(days):
        if i and pause:
            time.sleep(pause)
        attempted.append(day)
        try:
            df = fetch_day(day, p2)
        except FetchFailed as e:
            logger.warning(str(e))
            failed[day] = str(e)
            streak += 1
            if streak >= GIVE_UP_AFTER:
                raise GaveUp(f"ANBIMA failed {streak} requests in a row, last for "
                             f"{day}; stopping this run",
                             _result(frames, failed, attempted, p2)) from e
            continue
        streak = 0
        if not df.empty:
            frames.append(df)
    return _result(frames, failed, attempted, p2)


def _result(frames: list, failed: dict, attempted: list, p2: bool) -> BackfillResult:
    if not frames:
        return BackfillResult(pd.DataFrame(), failed, frozenset(attempted))
    df = pd.concat(frames, ignore_index=True)
    return BackfillResult(
        level_rows(df["ticker"].to_numpy(), df["date"].to_numpy(),
                   df["close"].to_numpy(), SOURCE_P2 if p2 else SOURCE_IMA),
        failed, frozenset(attempted))


def weekdays(start: datetime.date, end: datetime.date) -> list[datetime.date]:
    """Every Monday-to-Friday in [start, end]. Holidays stay in: ANBIMA answers
    them with "Não há dados", which costs one request and settles the day."""
    return [d.date() for d in pd.bdate_range(start, end)] if start <= end else []


if __name__ == "__main__":
    # Live smoke: a session, the Sunday after it, and the P2 page for the same
    # session. Three requests, nothing written.
    logging.basicConfig(level=logging.INFO)
    print(fetch_day(datetime.date(2026, 10, 30)).to_string())
    print(fetch_day(datetime.date(2026, 11, 1)).to_string())
    print(fetch_day(datetime.date(2026, 10, 30), p2=True).to_string())


# Days per upsert in the IMA backfill. The first run is ~4,300 requests (half an
# hour); writing as it goes means a run killed in the middle keeps what it
# fetched, and the next one resumes after the newest stored day.
_CHUNK = 100


def ingest(conn) -> None:
    """Pipeline step 5e: the IMA family, then the two P2 indexes, stored as fetched.

    Unlike the other loaders this one writes, because a first run is too long to
    hand back as one frame: see `_CHUNK`. The caller (level_series) logs a raise
    as the step's failure and moves on.
    """
    today = datetime.date.today()
    _ingest_ima(conn, today)
    _ingest_p2(conn, today)


def _ingest_ima(conn, today: datetime.date) -> None:
    """The IMA history does not roll off, so it resumes from the newest stored
    date — the whole of it on the first run, the last session or two every night
    after — plus whatever the failure ledger says is still owed: resuming from
    the max is what steps over a day a network error skipped, and the ledger is
    what pays it back. A holiday answers "Não há dados" and is settled.
    """
    last = storage.max_external_price_date(conn, SOURCE_IMA)
    pending = storage.pending_external_failures(conn, SOURCE_IMA)
    start = last + datetime.timedelta(days=1) if last else ANBIMA_START
    days = sorted(set(weekdays(start, today)) | pending)
    logger.info(f"ANBIMA IMA: {len(days)} day(s) from {start} ({len(pending)} retried)")
    failed = 0
    for i in range(0, len(days), _CHUNK):
        chunk = days[i:i + _CHUNK]
        gave_up = None
        try:
            result = backfill(chunk)
        except GaveUp as e:
            # Store and record what the chunk got before the breaker tripped,
            # then stop: the caller logs it as this step's failure. The days it
            # never reached were not asked, so their debt stays on the books.
            result, gave_up = e.result, e
        store_result(conn, SOURCE_IMA, result, pending)
        failed += len(result.failed)
        if gave_up:
            raise gave_up
    if failed:
        logger.warning(f"{failed} ANBIMA day(s) failed and are queued for retry")


def _ingest_p2(conn, today: datetime.date) -> None:
    """The P2 page keeps only its last week, so there is nothing to resume and
    nothing a ledger could recover past it: each run fetches the trailing window
    minus what is stored, and a day missed for longer than that is gone.
    """
    since = today - datetime.timedelta(days=P2_HEAL_DAYS)
    stored = storage.stored_external_dates(conn, since, SOURCE_P2)
    result = backfill([d for d in weekdays(since, today) if d not in stored], p2=True)
    if not result.rows.empty:
        storage.upsert_external_prices(conn, result.rows)
    if result.failed:
        logger.warning(f"{len(result.failed)} ANBIMA P2 day(s) failed; ANBIMA keeps "
                       f"about a week of P2, so they are retried until they age out")
