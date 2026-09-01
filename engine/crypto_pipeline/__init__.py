"""Crypto market plane, built from Binance's public data archive.

The archive is the crypto equivalent of COTAHIST, and for the same structural
reason: it is a bulk historical dump rather than a query against what is listed
today, so it keeps the pairs that died. Verified against it — `SRMUSDT` (Serum)
and `WAVESUSDT` were both delisted from the exchange and both still download.
Listing the bucket enumerates every pair that ever traded, alive or dead, which
is exactly what a survivorship-free universe needs and exactly what the
exchange's own `exchangeInfo` endpoint will not tell you.
"""
