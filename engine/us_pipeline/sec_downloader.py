"""Fetching from SEC EDGAR.

Two entry points, for the two shapes the same data comes in:

  * `companyfacts.zip` — every filer's complete fact history in one archive
    (~1.5 GB). The right way to build the plane from nothing, and the only one
    that gets the companies that stopped filing without knowing their CIKs in
    advance. Streamed member by member; it is never held in memory whole.
  * `companyfacts/CIK##########.json` — one company, for the incremental pass.

SEC requires a declaring User-Agent on every request and returns 403 without one.
`SEC_USER_AGENT` carries it. The default identifies the project by a URL that
actually resolves rather than by an invented mailbox — a fabricated address is
worse than none, because it works right up until it gets the host blocked and
nobody can be told. An operator running this against their own domain should set
the variable to a real contact of theirs.

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
# The other bulk archive. Note the path is not under `xbrl/` like the facts one
# — EDGAR's bulk-data layout is not uniform, so this is worth a HEAD before a
# gigabyte-and-change download.
SUBMISSIONS_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"

_PAUSE = 0.12          # ~8 req/s against a published limit of 10
_TIMEOUT = 120

# A real, resolvable identifier for this project. Not an email, because inventing
# one would name a mailbox nobody reads; a repository URL is a contact channel
# that exists and that a SEC administrator can act on.
DEFAULT_USER_AGENT = "Quantax/1.0 (+https://github.com/nickmaglowsch/b3-all-in-one)"


def user_agent() -> str:
    """The declaring User-Agent EDGAR requires.

    Defaults so the scheduled job runs on a box nobody configured; override with
    a contact of your own when running this against your own domain.
    """
    return os.environ.get("SEC_USER_AGENT", "").strip() or DEFAULT_USER_AGENT


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


def iter_bulk_submissions(zip_path: str):
    """Yield (cik, submission document) from a downloaded `submissions.zip`.

    Same streaming shape as `iter_bulk_facts`, and same reason. The members that
    matter are `CIK##########.json`; the archive also carries `*-submissions-
    ###.json` continuation files holding older filing pages, which repeat the
    entity metadata and would double-count.
    """
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            base = name.rsplit("/", 1)[-1]
            if not base.startswith("CIK") or not base.endswith(".json") \
                    or "-submissions-" in base:
                continue
            try:
                with zf.open(name) as fh:
                    doc = json.load(fh)
            except (json.JSONDecodeError, zipfile.BadZipFile) as e:
                logger.warning(f"{name}: unreadable ({e})")
                continue
            yield str(doc.get("cik", "")).zfill(10), doc


def download_bulk(url: str, dest: str) -> str:
    """Fetch one of EDGAR's bulk archives to `dest`, streamed to disk."""
    logger.info(f"downloading {url} -> {dest}")
    with requests.get(url, stream=True, timeout=_TIMEOUT,
                      headers={"User-Agent": user_agent()}) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            fh.writelines(r.iter_content(chunk_size=1 << 20))
    if not zipfile.is_zipfile(dest):
        # EDGAR answers 200 with an HTML error page when it throttles, so a
        # successful status code is not evidence the bytes are an archive.
        raise RuntimeError(
            f"{dest} is not a zip — EDGAR most likely answered with an error "
            "page. Check SEC_USER_AGENT and the request rate.")
    logger.info(f"downloaded {os.path.getsize(dest):,} bytes")
    return dest


def download_bulk_facts(dest: str) -> str:
    """Fetch `companyfacts.zip` to `dest`."""
    return download_bulk(BULK_FACTS_URL, dest)


def download_bulk_submissions(dest: str) -> str:
    """Fetch `submissions.zip` to `dest`."""
    return download_bulk(SUBMISSIONS_URL, dest)


def read_bulk_member(blob: bytes) -> dict:
    """One member's JSON, for tests that build an archive in memory."""
    return json.load(io.BytesIO(blob))
