"""What load_b3 has to know about B3 that the extracts do not spell out.

Four rules, each one a place where reading the report literally books the wrong
number:

  - **Re-exports.** Two overlapping downloads describe the same negócio twice.
    One download that lists two identical fills describes two negócios.
    `drop_reexported` tells the two apart.
  - **Settlement dates.** Movimentação dates a trade by when it settled (D+2),
    and the month a sale belongs to is decided by when it was made.
    `settlement_to_trade_date` walks back over the B3 calendar.
  - **Fraction auctions.** The debit of a fraction and the cash from its
    auction are two rows in Movimentação and one sale for the Receita.
    `fraction_auction_sales` puts them back together.
  - **Labels nothing books.** A Movimentação label load_b3 has no rule for used
    to vanish without a word. `review_rows` names every one of them.

Kept out of load_b3.py because that module is already past the size the quality
gate lets it grow to.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Re-exported reports
# ---------------------------------------------------------------------------


def drop_reexported(df: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    """Collapse the rows that are one record seen in more than one extract.

    The dedup exists for overlapping downloads: a full-year Negociação on top of
    the two half-year ones it covers, a Movimentação exported twice, the same
    file uploaded twice under two names. Every row of those arrives once per
    copy and must count once.

    But a single report never lists one negócio twice. Two `Compra PETR4 100 @
    30,00` rows on the same day in one file are two executions — two orders, or
    one order filled in equal lots — and a plain drop_duplicates over the key
    deleted the second: R$ 3.000 of cost basis gone, and the later sale of the
    200 read as an oversell. Same for two identical dividends on one ticker,
    which is what a position held at two brokers receives.

    So each row is numbered within its own file and key (the 1st, 2nd... copy of
    that key in that file), and the dedup runs on key + that ordinal. Two
    overlapping files collapse pairwise — first copy against first copy, second
    against second — while the repeats inside one file all survive.

    Broker is still left out of the key, as it always was: B3 has spelled one
    institution two ways across exports, and a broker in the key would turn
    every re-export with a reformatted name into a double count. What that
    leaves uncovered is two identical fills at two brokers that arrive in two
    *separate* per-broker exports — they still collapse to one. A combined
    export (the default) keeps both.
    """
    if df.empty:
        return df
    src = (df["__source_file"].fillna("").astype(str)
           if "__source_file" in df.columns else pd.Series("", index=df.index))
    keyed = df[key].assign(__src=src)
    nth = keyed.groupby(key + ["__src"], dropna=False, sort=False).cumcount()
    return df[~df[key].assign(__nth=nth).duplicated()]


# ---------------------------------------------------------------------------
# B3 calendar: settlement (D+2) back to trade date
# ---------------------------------------------------------------------------

# Days the B3 neither trades nor settles. The national holidays, Carnaval
# Monday and Tuesday, Good Friday, Corpus Christi, and the 24th and 31st of
# December. São Paulo's municipal holidays (25/01, 09/07, and 20/11 while it was
# only municipal) closed the exchange until 2021; from 2022 the B3 opens on
# them, and 20/11 came back in 2024 as a national holiday.
_FIXED = [(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15),
          (12, 24), (12, 25), (12, 31)]
_SP_UNTIL_2021 = [(1, 25), (7, 9), (11, 20)]
# Easter offsets: Carnaval Monday, Carnaval Tuesday, Good Friday, Corpus Christi.
_EASTER_OFFSETS = (-48, -47, -2, 60)

# Settlement cycle for the cash equity market, D+2 since May 2019. The Área do
# Investidor keeps no history from before then, so no extract reaches the D+3
# years.
SETTLEMENT_LAG = 2


def _easter(year: int) -> dt.date:
    """Gregorian Easter Sunday (Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def b3_holidays(years) -> list[dt.date]:
    """Every weekday the B3 was closed in `years`."""
    out: list[dt.date] = []
    for y in years:
        fixed = _FIXED + (_SP_UNTIL_2021 if y <= 2021 else [(11, 20)] if y >= 2024 else [])
        out += [dt.date(y, m, d) for m, d in fixed]
        out += [_easter(y) + dt.timedelta(days=n) for n in _EASTER_OFFSETS]
    return out


def settlement_to_trade_date(dates: pd.Series, lag: int = SETTLEMENT_LAG) -> pd.Series:
    """The trade date of a row Movimentação dates by settlement.

    `lag` B3 business days back. A sale made on 30/01 settles on 03/02, and
    dated by settlement it moves to February: the wrong month for the R$ 20k
    test, the DARF and the order losses are carried in. A settlement date that
    is not itself a business day (only a synthetic extract has one) is first
    rolled forward to the next one.
    """
    d = pd.to_datetime(dates)
    if d.empty:
        return d
    years = range(int(d.dt.year.min()) - 1, int(d.dt.year.max()) + 1)
    hol = np.array(b3_holidays(years), dtype="datetime64[D]")
    back = np.busday_offset(d.to_numpy().astype("datetime64[D]"), -lag,
                            roll="forward", holidays=hol)
    return pd.Series(pd.to_datetime(back), index=dates.index)


# ---------------------------------------------------------------------------
# Fraction auctions (Fração em Ativos + Leilão de Fração)
# ---------------------------------------------------------------------------

FRACAO = "Fração em Ativos"
LEILAO = "Leilão de Fração"
# How far back from the auction its fraction debit may sit. The B3 auctions the
# fractions of a bonificação, desdobro or grupamento within weeks.
AUCTION_WINDOW = pd.Timedelta(days=90)


def fraction_auction_sales(cash_events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Turn each auctioned fraction into the sale it is.

    A corporate action that leaves a fraction of a share has B3 debit the
    fraction ("Fração em Ativos") and, once the issuer auctions it, credit the
    cash ("Leilão de Fração"). Read as two rows, the debit dropped the quantity
    but left its whole cost on the shares that remain, and the cash was nobody's
    sale. It is an alienação: the fraction leaves at its share of the preço
    médio, the auction proceeds are the sale value, and both count in that
    month's gross.

    Each auction is paired with the latest unpaired debit of its ticker in the
    preceding `AUCTION_WINDOW`. A pair becomes one `Venda` row (quantity of the
    debit, value of the auction, dated on the auction) and the debit is retagged
    `other`, so the quantity leaves the position once, through the sale. The
    auction row stays in cash_events as it was. Unpaired rows are left alone:
    a debit whose auction is in an extract not loaded yet still reduces the
    quantity, as before.

    Returns (sale_trades, cash_events).
    """
    ce = cash_events
    if ce.empty or "event_type" not in ce.columns:
        return pd.DataFrame(), ce
    debits = ce[(ce["event_type"] == FRACAO) & (ce["event_category"] == "qty_event")
                & (pd.to_numeric(ce["qty"], errors="coerce") < 0)]
    auctions = ce[(ce["event_type"] == LEILAO)
                  & (pd.to_numeric(ce["amount"], errors="coerce") > 0)]
    paired: list = []
    sales: list[dict] = []
    for _, a in auctions.sort_values("date").iterrows():
        cand = debits[(debits["ticker"] == a["ticker"]) & (debits["date"] <= a["date"])
                      & (debits["date"] >= a["date"] - AUCTION_WINDOW)
                      & ~debits.index.isin(paired)]
        if cand.empty:
            continue
        frac = cand.sort_values("date").iloc[-1]
        paired.append(frac.name)
        qty = -float(frac["qty"])
        sales.append({
            "date": a["date"], "side": "Venda", "market": "Mercado à Vista",
            "maturity": "-", "broker": a["broker"], "ticker": a["ticker"],
            "qty": qty, "price": round(float(a["amount"]) / qty, 6),
            "amount": float(a["amount"]), "signed_qty": -qty,
            "__source_file": a["__source_file"],
        })
    out = ce.copy()
    out.loc[paired, "event_category"] = "other"
    return pd.DataFrame(sales), out


# ---------------------------------------------------------------------------
# Movimentação labels that book nothing
# ---------------------------------------------------------------------------

# Labels that rightly move nothing: custody bookkeeping and the life of a
# subscription right that was never exercised. Received rights cost nothing and
# are not shares of the underlying, so the credit of 128 rights on an MXRF11
# line must not add 128 cotas.
MOV_NOOP = frozenset({
    "Empréstimo",                              # BTC lending (see load_b3 docstring)
    "Transferência",                           # custody move between brokers
    "RETIRADA DE CUSTÓDIA",                    # equity case; renda fixa resolved in load_b3
    "Cobrança de Taxa Semestral",
    "Direito de Subscrição",
    "Direitos de Subscrição - Não Exercido",
})

_SUBSCRIPTION = ("subscrição exercida: é uma compra ao preço de subscrição, que "
                 "nenhum extrato da B3 lança como negócio — inclua uma linha `buy` "
                 "em cost_basis_overrides.csv (data, ticker, quantidade, preço)")
_TRANSFER_IN = ("ativos que entraram ou saíram sem preço (transferência de outra "
                "corretora, cisão, incorporação, conversão de recibo) — informe o "
                "custo em cost_basis_overrides.csv")
# Labels that change a position in a way the extract does not price. They are
# not booked (a guess at their cost would be a silent wrong number), and they
# are not dropped silently either: each one lands in the import summary.
MOV_REVIEW = {
    "Direitos de Subscrição - Exercido": _SUBSCRIPTION,
    "Recibo de Subscrição": _SUBSCRIPTION,
    "Cessão de Direitos": ("venda de direitos de subscrição (custo zero): inclua "
                           "uma linha `sell` em cost_basis_overrides.csv"),
    "Cessão de Direitos - Solicitada": ("venda de direitos de subscrição (custo "
                                        "zero): inclua uma linha `sell` em "
                                        "cost_basis_overrides.csv"),
    "Atualização": _TRANSFER_IN,
    "Incorporação": _TRANSFER_IN,
    "Cisão": _TRANSFER_IN,
}
_UNKNOWN = "rótulo sem regra no importador — a linha foi ignorada; confira se mexe na posição"
_BONUS_NO_COST = ("bonificação sem custo atribuído no extrato — entrou com custo zero; "
                  "confira no aviso aos acionistas o valor por ação capitalizado")


def review_rows(raw: pd.DataFrame, known: set[str]) -> list[dict]:
    """Movimentação rows the import did not, or could not fully, book.

    `raw` is the parsed extract (mov_type, ticker, date, qty, price, amount).
    `known` is every label load_b3 has a rule for. A row is reported when its
    label has neither a rule nor a place in MOV_NOOP, and when a bonificação
    arrives without the per-share cost the company capitalised.
    """
    label = raw["mov_type"]
    unbooked = ~label.isin(known) & ~label.isin(MOV_NOOP)
    bonus = label.str.startswith("Bonificação") & ~(raw["price"] > 0)
    rows = raw[(unbooked | bonus) & raw["ticker"].notna()]
    return [{
        "date": str(pd.Timestamp(r["date"]).date()) if pd.notna(r["date"]) else None,
        "ticker": r["ticker"], "label": r["mov_type"],
        "qty": None if pd.isna(r["qty"]) else float(r["qty"]),
        "price": None if pd.isna(r["price"]) else float(r["price"]),
        "reason": (_BONUS_NO_COST if r["mov_type"].startswith("Bonificação")
                   else MOV_REVIEW.get(r["mov_type"], _UNKNOWN)),
    } for _, r in rows.iterrows()]


def drop_overridden(review: list[dict], overrides: pd.DataFrame) -> list[dict]:
    """Leave out the rows a cost_basis_overrides.csv line already answers.

    Matched on (date, ticker): an override is written for the event, on its
    date, so a row with one beside it has been dealt with.
    """
    if not review or overrides is None or overrides.empty:
        return review
    done = {(str(pd.Timestamp(d).date()), t)
            for d, t in zip(overrides["date"], overrides["ticker"])}
    return [r for r in review if (r["date"], r["ticker"]) not in done]
