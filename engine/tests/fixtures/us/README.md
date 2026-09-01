# Real vendor samples for the US plane

Empty in git, on purpose, and it is not a placeholder for something forgotten.

Everything under `us_pipeline/av_parsers.py` was written against the vendor's
documented shapes, with columns read by name and a loud `ValueError` when one
moves. That is the parser-drift mitigation `spec/00-overview.md` asks for under
risk 4, and it is half of one: it catches drift *at runtime*, on the day the
vendor changes something, in production.

This directory is the other half — the same parsers driven from real files, in
CI, before anyone ships. It is empty because the environment this was built in
cannot reach the vendor at all: the egress proxy refuses `alphavantage.co` and
`sec.gov` alike at the CONNECT tunnel, so a sample was never fetchable here.

## Filling it

On a machine with egress and a free key:

    export ALPHAVANTAGE_API_KEY=...
    python engine/scripts/capture_us_fixtures.py --out engine/tests/fixtures/us

Review what lands, then commit what you are happy to keep. `test_us_fixtures.py`
picks up whatever is here and skips whatever is not, so a partial capture is
useful and CI stays green either way.

## What each file answers

| file | the question it settles |
|---|---|
| `listing_status_active.csv` | the column names and the date format of the universe feed |
| `listing_status_delisted.csv` | how far back the delisted set reaches, and whether `delistingDate` is a last-trade or a settlement date |
| `time_series_daily_IBM.csv` | the daily bar shape, and that `volume` really is a share count |
| `time_series_daily_DELISTED.csv` | **whether the vendor serves history for a symbol that no longer trades.** If it does not, the plane's universe is survivorship-free and its prices are not — a materially weaker claim, and the one that keeps a paid feed on the table |
| `splits_AAPL.json` | whether `split_factor` is new-over-old. Backwards, Apple's 2020 split becomes a +300% day |
| `dividends_AAPL.json` | that `ex_dividend_date` is present and is the ex-date |
| `refusal.txt` | which JSON key carries which refusal. The client treats `Error Message` as permanent and `Note`/`Information` as retryable; a bad key arriving as `Information` would burn the budget forever |

The files are truncated to a few hundred rows. They pin shape, not content.
