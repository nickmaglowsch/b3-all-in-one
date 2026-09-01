"""Tests for the Binance archive reader.

No network. The kline zips are built in-process from the same 12-column, headerless
CSV shape the real archive publishes — verified against
`SRMUSDT-1d-2020-08.zip`, whose first row is
`1597104000000,0.11,5.00,0.11,1.56290000,60097839.17,1597190399999,78655176.41,...`
— so a change to that shape breaks these tests rather than production.

The S3 listing XML is likewise a trimmed copy of what ListBucket actually returns,
including the `IsTruncated` flag that the pagination depends on.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from crypto_pipeline import binance_archive as archive

_S3 = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'


def _kline_zip(rows: list) -> bytes:
    csv = "\n".join(",".join(str(v) for v in r) for r in rows) + "\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("BTCUSDT-1d-2020-08.csv", csv)
    return buf.getvalue()


def _row(open_time, o=1.0, h=2.0, low=0.5, c=1.5, base_vol=10.0, quote_vol=100.0):
    """A kline row in archive column order. Column 5 is base volume, 7 is quote."""
    return [open_time, o, h, low, c, base_vol, open_time + 86_399_999,
            quote_vol, 123, 5.0, 50.0, 0]


class _Resp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


# ── parse_klines ──────────────────────────────────────────────────────────────

def test_parses_the_headerless_twelve_column_shape():
    df = archive.parse_klines(_kline_zip([_row(1597104000000)]), "BTCUSDT")
    assert len(df) == 1
    row = df.iloc[0]
    assert row["market"] == "CRYPTO" and row["symbol"] == "BTCUSDT"
    assert str(row["date"]) == "2020-08-11"
    assert row["close"] == 1.5


def test_volume_is_quote_volume_not_coin_count():
    """`market_prices.volume` means financial volume everywhere on the plane —
    it is what the liquidity filters read. Column 5 (coins) would be a different
    number in different units and would silently break min_adtv."""
    df = archive.parse_klines(
        _kline_zip([_row(1597104000000, base_vol=10.0, quote_vol=987.0)]), "BTCUSDT")
    assert df.iloc[0]["volume"] == 987.0


def test_microsecond_timestamps_are_detected():
    """Binance moved some 2025 files to microseconds. Assuming milliseconds would
    date every bar to the year 54000, which a date-ranged query returns nothing
    for — a silent empty result, not an error."""
    ms = archive.parse_klines(_kline_zip([_row(1597104000000)]), "X")
    us = archive.parse_klines(_kline_zip([_row(1597104000000000)]), "X")
    assert str(ms.iloc[0]["date"]) == str(us.iloc[0]["date"]) == "2020-08-11"


def test_a_header_row_is_dropped_not_parsed():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.csv", "open_time,open,high,low,close,volume,close_time,"
                             "quote_volume,count,tb,tq,ignore\n"
                             + ",".join(str(v) for v in _row(1597104000000)) + "\n")
    df = archive.parse_klines(buf.getvalue(), "X")
    assert len(df) == 1


def test_adjusted_close_equals_close_for_spot_pairs():
    """Spot pairs have no splits and no dividends. Storing adj_close rather than
    leaving it NULL keeps every reader that asks for it working with no
    market-specific COALESCE."""
    df = archive.parse_klines(_kline_zip([_row(1597104000000, c=7.25)]), "X")
    assert df.iloc[0]["adj_close"] == df.iloc[0]["close"] == 7.25


def test_empty_file_yields_empty_frame():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.csv", "")
    assert archive.parse_klines(buf.getvalue(), "X").empty


# ── list_symbols / list_months ────────────────────────────────────────────────

def _listing(prefixes, truncated=False):
    body = "".join(
        f"<CommonPrefixes><Prefix>data/spot/monthly/klines/{p}/</Prefix></CommonPrefixes>"
        for p in prefixes)
    return (f'<?xml version="1.0"?><ListBucketResult {_S3}>'
            f"<IsTruncated>{str(truncated).lower()}</IsTruncated>{body}"
            "</ListBucketResult>").encode()


def test_symbol_listing_follows_pagination(monkeypatch):
    """ListBucket truncates at 1000 keys and the spot archive is well past that.
    An unpaged call returns an alphabetical prefix of the universe — a
    survivorship bias of its own, just sorted differently."""
    pages = [_listing(["AAAUSDT", "BBBUSDT"], truncated=True),
             _listing(["CCCUSDT"], truncated=False)]
    calls = []

    def fake_get(url, params=None):
        calls.append(params.get("marker"))
        return _Resp(pages[len(calls) - 1])

    monkeypatch.setattr(archive, "_get", fake_get)
    assert archive.list_symbols() == ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    assert calls[0] == "" and calls[1].endswith("BBBUSDT/")


def test_symbol_listing_stops_on_an_empty_page(monkeypatch):
    """A truncated flag with no prefixes must not loop forever."""
    monkeypatch.setattr(archive, "_get",
                        lambda url, params=None: _Resp(_listing([], truncated=True)))
    assert archive.list_symbols() == []


def test_month_listing_extracts_year_month_from_keys(monkeypatch):
    keys = "".join(
        f"<Contents><Key>data/spot/monthly/klines/SRMUSDT/1d/SRMUSDT-1d-{m}.zip</Key></Contents>"
        for m in ("2020-08", "2020-09", "2022-11"))
    monkeypatch.setattr(archive, "_get", lambda url, params=None: _Resp(
        (f'<?xml version="1.0"?><ListBucketResult {_S3}>'
         f"<IsTruncated>false</IsTruncated>{keys}</ListBucketResult>").encode()))
    assert archive.list_months("SRMUSDT") == ["2020-08", "2020-09", "2022-11"]


def test_month_listing_ignores_checksum_siblings(monkeypatch):
    """Every zip has a .CHECKSUM beside it; counting those would double the
    month list and make a pair look twice as long-lived as it was."""
    monkeypatch.setattr(archive, "_get", lambda url, params=None: _Resp(
        (f'<?xml version="1.0"?><ListBucketResult {_S3}><IsTruncated>false</IsTruncated>'
         "<Contents><Key>data/spot/monthly/klines/X/1d/X-1d-2020-08.zip</Key></Contents>"
         "<Contents><Key>data/spot/monthly/klines/X/1d/X-1d-2020-08.zip.CHECKSUM</Key></Contents>"
         "</ListBucketResult>").encode()))
    assert archive.list_months("X") == ["2020-08"]


def test_missing_month_is_empty_not_an_error(monkeypatch):
    """A gap in one pair's archive must not abort the other 3,694."""
    import requests

    class _E(requests.HTTPError):
        def __init__(self):
            super().__init__(response=type("R", (), {"status_code": 404})())

    def boom(url, params=None):
        raise _E()

    monkeypatch.setattr(archive, "_get", boom)
    assert archive.fetch_month("X", "1999-01").empty


def test_non_404_errors_propagate(monkeypatch):
    """A 503 is not "this month does not exist" — swallowing it would silently
    truncate a pair's history and reintroduce the bias."""
    import requests

    class _E(requests.HTTPError):
        def __init__(self):
            super().__init__(response=type("R", (), {"status_code": 503})())

    monkeypatch.setattr(archive, "_get", lambda url, params=None: (_ for _ in ()).throw(_E()))
    with pytest.raises(requests.HTTPError):
        archive.fetch_month("X", "2020-08")
