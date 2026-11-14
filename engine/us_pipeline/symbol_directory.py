"""The US security master, built forward from a daily listing snapshot.

Nasdaq publishes the consolidated symbol directory — every symbol traded on any
US exchange that day — as a pipe-delimited file. Captured daily, a symbol's
appearance and disappearance across those captures *is* its listing interval,
which is what makes the US plane survivorship-free going forward. There is no
free historical archive of these files, so the record starts the day the capture
starts and thickens from there; `GET /market/status` reports that boundary rather
than implying coverage the plane does not have.

The inference is the opposite direction from crypto's, and needs a guard crypto
does not. There, the bucket is the complete history and an archive that stops IS
the delisting. Here, absence from *one* download is not evidence of anything: a
truncated response, a bad day at the exchange's web server, or a proxy error page
would read as "every symbol delisted at once". So a snapshot is refused unless it
looks like a real market, and a delisting is only ever recorded from a snapshot
that passed.

Columns are read by header name, not position. The file has carried the same
twelve columns for years, but a parser that indexes by number turns an inserted
column into silently wrong data, while one that indexes by name turns it into a
loud KeyError.
"""

import io
import logging

import pandas as pd
from b3_pipeline import storage

from . import us_lifecycle as lifecycle

logger = logging.getLogger(__name__)

MARKET = "US"
SOURCE = "nasdaqtrader-symbol-directory"

DIRECTORY_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"

# The consolidated file lists every US-listed symbol; it has not been below five
# thousand rows in years. A snapshot smaller than this is a truncated download or
# an error page, and applying it would delist most of the market in one run.
MIN_PLAUSIBLE_SYMBOLS = 5_000

_EXCHANGE = {"A": "NYSE MKT", "N": "NYSE", "P": "NYSE ARCA", "Q": "NASDAQ",
             "V": "IEX", "Z": "CBOE BZX"}


def parse_directory(text: str) -> pd.DataFrame:
    """The pipe-delimited directory -> [symbol, name, venue, is_etf, is_test].

    The last line is a `File Creation Time:` footer, not a symbol; it survives
    the split and is dropped by the missing-symbol filter below.
    """
    df = pd.read_csv(io.StringIO(text), sep="|", dtype=str).fillna("")
    missing = {"Symbol", "Security Name", "Listing Exchange", "ETF",
               "Test Issue"} - set(df.columns)
    if missing:
        raise ValueError(
            f"symbol directory is missing {sorted(missing)} — the file's shape "
            f"changed. Columns seen: {sorted(df.columns)}")

    df = df[df["Symbol"].str.strip().ne("")]
    df = df[~df["Symbol"].str.contains("File Creation Time", na=False)]
    return pd.DataFrame({
        "symbol": df["Symbol"].str.strip(),
        "name": df["Security Name"].str.strip(),
        "venue": df["Listing Exchange"].str.strip().map(_EXCHANGE).fillna(
            df["Listing Exchange"].str.strip()),
        "is_etf": df["ETF"].str.strip().eq("Y"),
        "is_test": df["Test Issue"].str.strip().eq("Y"),
    }).reset_index(drop=True)


def fetch_directory() -> pd.DataFrame:
    """Today's consolidated directory.

    Declares itself, like the SEC client does. Nasdaq does not publish a rule
    the way the SEC does, but a bare client is what gets blocked first and an
    unidentified scraper is not what this project wants to be — the same
    reasoning that gave `sec_downloader` a User-Agent that resolves.
    """
    import requests

    from .sec_downloader import user_agent

    r = requests.get(DIRECTORY_URL, timeout=60,
                     headers={"User-Agent": user_agent()})
    r.raise_for_status()
    return parse_directory(r.text)


def _known(conn) -> dict:
    """Every US instrument the plane holds, live or not, as `{symbol: row}`.

    Not just the live ones. Reading only those is what made a delisted symbol
    that reappeared look like a first sighting: its real `first_seen` was never
    read, and its delisting never cleared.
    """
    df = storage.read_sql(
        "SELECT symbol, first_seen, last_seen, delisting_date, delist_reason"
        " FROM instruments WHERE market = ?", conn, params=[MARKET])
    return {r["symbol"]: r for r in df.to_dict("records")}


def apply_snapshot(conn, snapshot: pd.DataFrame, as_of) -> dict:
    """Fold one day's directory into `instruments`.

    Present today: alive, `last_seen` moved to today, `first_seen` kept from the
    first sighting — and, if this directory had recorded the symbol delisted,
    revived (`us_lifecycle.observe_present`). Test issues are recorded but
    flagged, never silently dropped — a symbol that vanishes from the file
    because it was a test issue and a symbol that vanishes because it delisted
    must not look the same later.

    Previously alive and absent today: delisted, dated at the last day it *was*
    seen rather than today, because that is the last day the plane can prove it
    traded.

    Refuses an implausibly small snapshot rather than delisting the market.

    Every write is narrow on purpose: `upsert_instruments` touches only the
    columns it is handed, so this job never restates the CIK the SEC ingest
    owns or the listing interval the listing-status feed owns.
    """
    if len(snapshot) < MIN_PLAUSIBLE_SYMBOLS:
        raise ValueError(
            f"symbol directory has {len(snapshot):,} rows, below the "
            f"{MIN_PLAUSIBLE_SYMBOLS:,} floor — treating this as a truncated "
            "download rather than a market-wide delisting. Nothing was written.")

    as_of = pd.Timestamp(as_of).date()
    known = _known(conn)
    present = [{
        "market": MARKET, "symbol": r["symbol"], "currency": "USD",
        "name": r["name"], "venue": r["venue"],
        "kind": "ETF" if r["is_etf"] else "EQUITY",
        "is_test_issue": int(r["is_test"]),
        **lifecycle.observe_present(known.get(r["symbol"]), as_of),
    } for r in snapshot.to_dict("records")]
    revived = sum("delisting_date" in row for row in present)

    listed = set(snapshot["symbol"])
    absent = {s: lifecycle.observe_absent(prior)
              for s, prior in known.items() if s not in listed}
    gone = [{"market": MARKET, "symbol": s, "currency": "USD", **change}
            for s, change in absent.items() if change]
    lifecycle.write(conn, present + gone)
    logger.info(f"{as_of}: {len(present):,} listed, {len(gone):,} newly delisted, "
                f"{revived:,} revived")
    return {"listed": len(present), "delisted": len(gone), "revived": revived,
            "as_of": str(as_of)}


def run_pipeline(db_path=None, as_of=None) -> dict:
    """Capture today's directory into the security master.

    Daily, and not skippable: there is no free historical archive of these files,
    so a day not captured is a day of listing history that cannot be recovered —
    the same property that makes B3's daily file a daily obligation.
    """
    as_of = as_of or pd.Timestamp.today().date()
    logger.info(f"US symbol directory -> {storage.describe_target(db_path)}")
    snapshot = fetch_directory()
    with storage.get_connection(db_path) as conn:
        storage.init_db(conn)
        return apply_snapshot(conn, snapshot, as_of)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--as-of", default=None,
                    help="date to stamp the snapshot with (default: today)")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_pipeline(args.db_path, args.as_of)


if __name__ == "__main__":
    main()
