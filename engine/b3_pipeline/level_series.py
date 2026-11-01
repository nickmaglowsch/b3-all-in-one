"""Steps 5c–5e of the pipeline: third-party level series in `external_prices`.

Moved out of main.py, which was already past the module-size gate, when the
ANBIMA step (5e) joined them. main.run_pipeline calls `fetch` after step 5b.
"""
import logging
from datetime import datetime

from . import anbima_series, ibov_series, index_series, storage, tesouro_direto

logger = logging.getLogger(__name__)


def fetch(conn) -> None:
    """Steps 5c–5e: third-party level series, each one allowed to fail alone.

    Same posture as 5b for every one of them: an unreachable feed leaves what it
    prices where it was before the step existed — open contracts unpriced and
    disclosed, the benchmark overlay disclosed as unavailable, Tesouro titles
    accrued if the tenant entered terms and disclosed as unpriced if not.
    """
    steps = (
        # The CDI and IPCA levels renda fixa accrues against. Rewritten whole
        # each run from a fixed epoch, so a stored level never depends on the
        # window that happened to fetch it — no resume state, nothing to heal.
        ("5c", "BCB index series (CDI, IPCA, Selic)", "BCB index series",
         lambda: index_series.build(datetime.now().date())),
        # The Ibovespa, from B3's own publication. Same whole-rewrite posture as
        # 5c, and for the same reason — one request per year, no resume state.
        ("5c2", "Ibovespa index series", "Ibovespa series",
         lambda: ibov_series.build(datetime.now().date())),
        # Tesouro Direto. Renda fixa with no ticker, like a CDB — except the
        # issuer publishes a daily price for every title, so these need no
        # accrual model and no indexer from the tenant at all.
        #
        # Resumes from the latest stored date, which step 5b deliberately does
        # not: this file carries its whole history on every request, so a day a
        # network error skipped is still there tomorrow. Nothing rolls off.
        ("5d", "Tesouro Direto prices", "Tesouro Direto",
         lambda: tesouro_direto.build(
             storage.max_external_price_date(conn, tesouro_direto.SOURCE)
             or tesouro_direto.TESOURO_START)),
    )
    for step, what, name, build in steps:
        logger.info("")
        logger.info(f"Step {step}/9: Fetching {what}...")
        try:
            rows = build()
            if not rows.empty:
                storage.upsert_external_prices(conn, rows)
        except Exception as e:
            logger.error(f"{name} fetch failed, continuing: {e}")

    # 5e stores as it goes — a first run is ~4,300 requests — so it writes its
    # own rows instead of handing one frame back like the steps above. Same
    # posture: an unreachable ANBIMA leaves the renda-fixa ETFs with their own
    # ~14 months and the benchmarks disclosed as unavailable.
    logger.info("")
    logger.info("Step 5e/9: Fetching ANBIMA fixed-income indexes...")
    try:
        anbima_series.ingest(conn)
    except Exception as e:
        logger.error(f"ANBIMA index fetch failed, continuing: {e}")
