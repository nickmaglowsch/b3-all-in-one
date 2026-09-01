"""Fetching from SEC EDGAR.

Two entry points, for the two shapes the same data comes in:

  * `companyfacts.zip` — every filer's complete fact history in one archive
    (~1.5 GB). The right way to build the plane from nothing, and the only one
    that gets the companies that stopped filing without knowing their CIKs in
    advance. Streamed member by member; it is never held in memory whole.
  * `companyfacts/CIK##########.json` — one company, for the incremental pass.

SEC requires a declaring User-Agent with a contact address on every request and
returns 403 without one. `SEC_USER_AGENT` carries it; there is no default,
because a made-up address is worse than a clear failure — it would work until it
got the whole VPS blocked.

The rate limit is 10 requests/second, published and enforced. `_PAUSE` keeps a
single-threaded caller under it with room to spare; do not parallelise this
without a shared limiter.
"""

import io
import json
import logging
import os
import time
import zipfile

import requests

logger = logging.getLogger(__name__)

BULK_FACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:0>10}.json"
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

_PAUSE = 0.12          # ~8 req/s against a published limit of 10
_TIMEOUT = 120


def user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        raise RuntimeError(
            "SEC_USER_AGENT is unset. EDGAR requires a declaring User-Agent with "
            "a contact address (e.g. 'Quantax ingest contato@exemplo.com.br') and "
            "answers 403 without one. There is no default on purpose: a made-up "
            "address would work right up until it got this host blocked.")
    return ua


def _get(url: str) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": user_agent(),
                                   "Accept-Encoding": "gzip, deflate"},
                     timeout=_TIMEOUT)
    r.raise_for_status()
    time.sleep(_PAUSE)
    return r


def fetch_company_tickers() -> dict:
    """{CIK (10-digit, zero-padded): ticker} for currently listed filers.

    Current only — this file is a snapshot of who trades today, so it is a
    convenience for labelling, never the universe. A company that delisted in
    2009 is absent here and present in the bulk facts, which is exactly the
    asymmetry that makes the fundamentals survivorship-free and this file not.
    """
    payload = _get(COMPANY_TICKERS_URL).json()
    return {str(row["cik_str"]).zfill(10): row["ticker"]
            for row in payload.values() if row.get("ticker")}


def fetch_company_facts(cik: str) -> dict:
    """One filer's complete fact history."""
    return _get(COMPANY_FACTS_URL.format(cik=str(cik).lstrip("0"))).json()


def iter_bulk_facts(zip_path: str):
    """Yield (cik, facts document) from a downloaded `companyfacts.zip`.

    Streamed one member at a time. The archive holds a JSON per filer and
    expands to tens of gigabytes; reading it whole is not an option on the box
    this runs on.
    """
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            try:
                with zf.open(name) as fh:
                    doc = json.load(fh)
            except (json.JSONDecodeError, zipfile.BadZipFile) as e:
                # One malformed member must not cost the other ten thousand.
                logger.warning(f"{name}: unreadable ({e})")
                continue
            yield str(doc.get("cik", "")).zfill(10), doc


def download_bulk_facts(dest: str) -> str:
    """Fetch `companyfacts.zip` to `dest`, streamed to disk."""
    logger.info(f"downloading {BULK_FACTS_URL} -> {dest}")
    with requests.get(BULK_FACTS_URL, stream=True, timeout=_TIMEOUT,
                      headers={"User-Agent": user_agent()}) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    if not zipfile.is_zipfile(dest):
        # EDGAR answers 200 with an HTML error page when it throttles, so a
        # successful status code is not evidence the bytes are an archive.
        raise RuntimeError(
            f"{dest} is not a zip — EDGAR most likely answered with an error "
            "page. Check SEC_USER_AGENT and the request rate.")
    logger.info(f"downloaded {os.path.getsize(dest):,} bytes")
    return dest


def read_bulk_member(blob: bytes) -> dict:
    """One member's JSON, for tests that build an archive in memory."""
    return json.load(io.BytesIO(blob))
