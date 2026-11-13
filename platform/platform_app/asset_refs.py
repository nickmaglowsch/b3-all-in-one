"""How a listed asset is spelled, per market plane, and the one spelling kept.

A sleeve can hold one instrument directly, and the three planes name theirs
differently: B3 by trading code (`PETR4`, `IVVB11`, the BDR `AAPL34`), the US by
symbol (`SPY`, `AAPL`), crypto by Binance spot pair (`BTCUSDT`). The same code can
also arrive in a second spelling — `IVVB11.SA` from the yfinance era, `PETR4F`
from the fractional market, `btc` typed by a person — and two spellings of one
asset must never become two sleeves, or one sleeve the plane cannot price.

So every ticker is read together with its market and folded onto the symbol the
plane itself stores (engine `markets.plane_symbol`, and the B3 position book,
which carries bare codes). A live sleeve, a search result and a rebalance trade
all use that form. Saved carteiras keep the built-ins' `IVVB11.SA` spelling on
top of it (strategy_specs), because that is what the engine spec has always held.
"""
from __future__ import annotations

import re
from typing import Literal

Market = Literal["B3", "US", "CRYPTO"]
MARKETS: tuple[str, ...] = ("B3", "US", "CRYPTO")
# How a market is named to the user, in the pt-BR the UI and the API messages use.
MARKET_LABEL = {"B3": "B3", "US": "EUA", "CRYPTO": "cripto"}

# A B3 trading code — a four-character root and a one- or two-digit class
# (PETR4, IVVB11, AAPL34). `^BVSP` and `$IBOV` fail it on purpose: indices are not
# tradable, and the plane's `$` namespace is stored series (CDI, IPCA).
B3_TICKER = re.compile(r"[A-Z0-9]{4}[0-9]{1,2}")
_FRACTIONAL = re.compile(r"[A-Z0-9]{4}[0-9]{1,2}F")
# The US directory's symbols: letters first, then letters, digits and the class
# separators (BRK.B). Whether the plane prices one is the plane's to say. The
# hyphen goes last in the class so the pattern reads the same in Postgres.
_US_SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,9}")
# The crypto plane holds Binance spot pairs quoted in USDT and nothing else
# (markets.CRYPTO.ticker_sql), so a bare coin means its USDT pair.
CRYPTO_QUOTE = "USDT"
_CRYPTO_PAIR = re.compile(rf"[A-Z0-9]{{2,20}}{CRYPTO_QUOTE}")


# What a malformed B3 code's message suggests for a US or crypto asset — where
# one can go. A saved carteira passes its own: there, nowhere.
LIVE_HINT = "um ativo dos EUA ou cripto leva `market`: US ou CRYPTO"


def b3_code(raw: str, off_b3_hint: str = LIVE_HINT) -> str:
    """A B3 trading code, bare: case, the `.SA` suffix and the fractional
    market's F folded away. Raises ValueError for an index or anything not
    shaped like a code."""
    t = raw.strip().upper().removesuffix(".SA")
    # PETR4F is the fractional market's code for the PETR4 share. The plane
    # keeps only the round-lot market (the parser drops TPMERC 020), and a
    # sleeve holds amounts, not lots — so it is the same sleeve.
    if _FRACTIONAL.fullmatch(t):
        t = t[:-1]
    if t.startswith(("^", "$")):
        raise ValueError(f"ticker {raw!r} é um índice, não um ativo negociável — "
                         "use o ETF que o acompanha (BOVA11 acompanha o Ibovespa)")
    if not B3_TICKER.fullmatch(t):
        raise ValueError(f"ticker {raw!r} não é um código da B3: quatro caracteres e "
                         f"uma classe de um ou dois dígitos, como PETR4 ou IVVB11 "
                         f"({off_b3_hint})")
    return t


def _us_symbol(raw: str) -> str:
    t = raw.strip().upper()
    if not _US_SYMBOL.fullmatch(t):
        raise ValueError(f"ticker {raw!r} não é um símbolo dos EUA (ex.: SPY, AAPL)")
    return t


def _crypto_pair(raw: str) -> str:
    t = raw.strip().upper()
    if not t.endswith(CRYPTO_QUOTE):
        t += CRYPTO_QUOTE
    if not _CRYPTO_PAIR.fullmatch(t):
        raise ValueError(f"ticker {raw!r} não é um par cripto cotado em "
                         f"{CRYPTO_QUOTE} (ex.: BTCUSDT, ou só BTC)")
    return t


_CANONICAL = {"B3": b3_code, "US": _us_symbol, "CRYPTO": _crypto_pair}

# Each market's canonical symbol shape, anchored, as a regex Python and Postgres
# read alike: the search filters the plane by it in SQL (market_assets), before
# its LIMIT, so a page is never cut short by names no sleeve could hold.
SYMBOL_PATTERN = {m: f"^({rx.pattern})$" for m, rx in
                  (("B3", B3_TICKER), ("US", _US_SYMBOL), ("CRYPTO", _CRYPTO_PAIR))}


def canonical(raw: str, market: str = "B3") -> str:
    """The plane's own symbol for `raw` on `market`. Raises ValueError on a
    malformed ticker or an unknown market."""
    if market not in _CANONICAL:
        raise ValueError(f"mercado {market!r} desconhecido: use B3, US ou CRYPTO")
    return _CANONICAL[market](raw)


def label(ticker: str, market: str) -> str:
    """`SPY (EUA)` — a ticker as a message names it; B3 needs no tag."""
    return ticker if market == "B3" else f"{ticker} ({MARKET_LABEL[market]})"
