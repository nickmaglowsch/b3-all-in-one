"""Realização — the preço médio walk that turns a timeline into realised results.

One walk, shared by the IRPF report's Renda Variável grid, its Bens e Direitos
snapshots and the TLH planner, so the three cannot disagree about a sale.

Within a day the walk works per (ticker, broker), not row by row:

- **Day trade** is the quantity bought *and* sold of the same ticker on the same
  day at the same intermediary (Lei 9.959/2000, art. 8º, § 1º, I, as amended by
  Lei 12.350/2010). Its result is the day's average sale price against the day's
  average purchase price — B3 extracts carry no execution time, so the art. 65
  pairing of first buy with first sell cannot be reproduced; the average is exact
  whenever the day's bought and sold quantities match. It never touches the
  swing position's preço médio, whatever the order of the rows.
- What is left — the excess of buys, then the excess of sells — goes through the
  ordinary preço médio walk. Buys first: a same-day sell listed before its buy is
  no longer dropped as a sale of nothing.
- A sale of more than the walk holds (`descoberto`) keeps its **full** value in
  the month's gross — the isenção looks at alienações, not at what the walk
  could cost (Lei 11.033/2004, art. 3º, I) — books a result only for what was
  held, and is flagged. That almost always means the history is incomplete (a
  buy before the extract window, a subscription, a transfer), so the uncovered
  part is never given a cost of zero or a short-sale result against some later,
  unrelated buy.
"""
from __future__ import annotations

import pandas as pd

from scripts.apuracao import REGRAS, darf_mensal, monthly_apuracao

COLS = ["date", "ticker", "broker", "qty", "sell_price", "avg_cost",
        "sell_value", "pnl", "daytrade", "descoberto"]
GRID_CLASSES = ("AÇÃO", "FII", "BDR", "ETF_RV", "ETF_RF", "DT")
# Asset classes whose day trades are apurados on the day-trade line. A FII day
# trade stays on the FII line, which the IRPF program apurates as one 20% table.
DT_CLASSES = ("AÇÃO", "BDR", "ETF_RV")
_TRADES = ("buy", "sell")


class _Posicao:
    """Quantity and total cost of one ticker's swing position."""

    def __init__(self) -> None:
        self.qty = 0.0
        self.cost = 0.0

    def comprar(self, qty: float, valor: float) -> None:
        self.qty += qty
        self.cost += valor

    def vender(self, qty: float, preco: float) -> tuple[float, float, float]:
        """(quantity covered, preço médio, result) — the rest is descoberto."""
        coberto = min(qty, max(self.qty, 0.0))
        avg = self.cost / self.qty if self.qty > 0 else 0.0
        self.cost -= coberto * avg
        self.qty -= coberto
        if self.qty <= 1e-9:  # closed: no centavo of cost left on zero shares
            self.qty = self.cost = 0.0
        return coberto, avg, coberto * (preco - avg)

    def evento(self, r) -> None:
        if r["kind"] == "qty_event":
            # `amount` is the custo atribuído of a bonificação (Lei 9.249/95,
            # art. 10, parágrafo único) and zero for desdobro / grupamento.
            self.qty += r["qty"]
            self.cost += r["amount"]
        elif r["kind"] == "amort":
            self.cost = max(0.0, self.cost - r["amount"])


def _broker(r) -> str:
    b = r.get("broker", "")
    return b.strip() if isinstance(b, str) else ""


def _por_corretora(trades: pd.DataFrame) -> dict[str, list[float]]:
    """broker → [qty bought, value bought, qty sold, value sold] for one day."""
    acc: dict[str, list[float]] = {}
    for _, r in trades.iterrows():
        a = acc.setdefault(_broker(r), [0.0, 0.0, 0.0, 0.0])
        i = 0 if r["kind"] == "buy" else 2
        a[i] += float(r["qty"])
        a[i + 1] += float(r["amount"])
    return acc


def _venda(date, ticker, broker, qty, preco, avg, pnl, daytrade, descoberto):
    return {"date": date, "ticker": ticker, "broker": broker, "qty": qty,
            "sell_price": preco, "avg_cost": avg,
            "sell_value": (qty + descoberto) * preco, "pnl": pnl,
            "daytrade": daytrade, "descoberto": descoberto}


def _realizar_dia(ticker, date, dia: pd.DataFrame, pos: _Posicao) -> list[dict]:
    is_trade = dia["kind"].isin(_TRADES)
    out, vendas = [], []
    for broker, (bq, bv, sq, sv) in _por_corretora(dia[is_trade]).items():
        dt = min(bq, sq)
        pb = bv / bq if bq else 0.0
        ps = sv / sq if sq else 0.0
        if dt > 0:
            out.append(_venda(date, ticker, broker, dt, ps, pb, dt * (ps - pb), True, 0.0))
        if bq > dt:
            pos.comprar(bq - dt, (bq - dt) * pb)
        if sq > dt:
            vendas.append((broker, sq - dt, ps))
    for broker, qty, ps in vendas:
        coberto, avg, pnl = pos.vender(qty, ps)
        out.append(_venda(date, ticker, broker, coberto, ps, avg, pnl, False, qty - coberto))
    for _, ev in dia[~is_trade].iterrows():
        pos.evento(ev)
    return out


def realizar(tl: pd.DataFrame) -> pd.DataFrame:
    """Every sale in the timeline, all years: one row per (ticker, day, broker,
    day trade or not). `sell_value` is the full proceeds; `qty` and `pnl` cover
    only what the walk held; `descoberto` is the quantity it did not."""
    rows: list[dict] = []
    for ticker, group in tl.groupby("ticker", sort=True):
        pos = _Posicao()
        for date, dia in group.groupby("date", sort=True):
            rows += _realizar_dia(ticker, date, dia, pos)
    out = pd.DataFrame(rows, columns=COLS)
    out["date"] = pd.to_datetime(out["date"])  # typed even when there is no sale
    return out


def realized_per_month(tl: pd.DataFrame, year: int) -> pd.DataFrame:
    """The year's sales — the shape irpf_2026 has always exported."""
    r = realizar(tl)
    return r[r["date"].dt.year == year].reset_index(drop=True)


def snapshot_positions(tl: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Walk timeline up to cutoff and return (ticker, qty, total_cost, broker)."""
    out = []
    for ticker, group in tl[tl["date"] <= cutoff].groupby("ticker"):
        pos = _Posicao()
        for date, dia in group.groupby("date", sort=True):
            _realizar_dia(ticker, date, dia, pos)
        if pos.qty <= 1e-6:
            continue
        # Most-recent non-empty broker for this ticker on or before cutoff.
        buys = group[group["kind"] == "buy"]
        brokers = [b for b in (_broker(r) for _, r in buys.iterrows())
                   if b and b != "override"]
        out.append({"ticker": ticker, "qty": round(pos.qty, 6),
                    "total_cost": round(pos.cost, 2),
                    "broker": brokers[-1] if brokers else ""})
    return pd.DataFrame(out)


def linha_grade(realized: pd.DataFrame, classe_de) -> pd.Series:
    cls = realized["ticker"].map(classe_de)
    dt = realized["daytrade"].astype(bool) & cls.isin(DT_CLASSES)
    return cls.where(~dt, "DT")


def grade_mensal(realized: pd.DataFrame, classe_de, ate_ano: int) -> pd.DataFrame:
    """Every month from January of the first sale through December of `ate_ano`,
    with ``{class}_gross`` / ``{class}_pnl`` per grid class — the frame
    `monthly_apuracao` reads. All of history, because the carry has no expiry."""
    r = realized[realized["date"].dt.year <= ate_ano].assign(
        year=lambda d: d["date"].dt.year, month=lambda d: d["date"].dt.month)
    r["linha"] = linha_grade(r, classe_de) if not r.empty else []
    first = int(r["year"].min()) if not r.empty else ate_ano
    idx = pd.MultiIndex.from_product(
        [range(first, ate_ano + 1), range(1, 13)], names=["year", "month"])
    grade = pd.DataFrame(index=idx)
    for cls in GRID_CLASSES:
        sub = r[r["linha"] == cls].groupby(["year", "month"])
        grade[f"{cls}_gross"] = sub["sell_value"].sum().reindex(idx, fill_value=0.0).round(2)
        grade[f"{cls}_pnl"] = sub["pnl"].sum().reindex(idx, fill_value=0.0).round(2)
    return grade.reset_index()


# IRRF "dedo-duro" (Lei 11.033/2004, art. 2º, §§ 1º, 4º, 5º): 0,005% of every
# swing sale in bolsa, not withheld when the month's total at the intermediary is
# R$ 1,00 or less. Day trade: 1% of the day's positive result (Lei 9.959/2000,
# art. 8º), and the 0,005% does not apply to it (Lei 11.033, art. 2º, § 3º).
IRRF_SWING = 0.00005
IRRF_DAYTRADE = 0.01
IRRF_DISPENSA = 1.0
_LINHA_IRRF = {"AÇÃO": "AÇÃO", "BDR": "BDR_ETF", "ETF_RV": "BDR_ETF", "FII": "FII",
               "DT": "DAYTRADE"}


def irrf_mensal(realized: pd.DataFrame, classe_de) -> dict:
    """``{(year, month): {apuração line: IRRF}}`` estimated from the sales.

    B3 extracts do not carry the IRRF itself; this is what the intermediary is
    required to withhold on them. The informe de rendimentos and the notas de
    corretagem are the authority — the report says so.
    """
    if realized.empty:
        return {}
    r = realized.assign(linha=linha_grade(realized, classe_de).map(_LINHA_IRRF),
                        year=realized["date"].dt.year, month=realized["date"].dt.month)
    r = r[r["linha"].notna()]
    out: dict = {}
    swing = r[~r["daytrade"].astype(bool)]
    for (y, m, _b), g in swing.groupby(["year", "month", "broker"]):
        if g["sell_value"].sum() * IRRF_SWING <= IRRF_DISPENSA:
            continue
        mes = out.setdefault((int(y), int(m)), {})
        for linha, v in g.groupby("linha")["sell_value"].sum().items():
            mes[linha] = mes.get(linha, 0.0) + round(v * IRRF_SWING, 2)
    dt = r[r["daytrade"].astype(bool)]
    for (d, _b, linha), g in dt.groupby(["date", "broker", "linha"]):
        ganho = max(0.0, float(g["pnl"].sum()))
        mes = out.setdefault((d.year, d.month), {})
        mes[linha] = mes.get(linha, 0.0) + round(ganho * IRRF_DAYTRADE, 2)
    return out


def apurar(tl: pd.DataFrame, ano: int, classe_de) -> dict:
    """The year's Renda Variável apuração, computed over the whole history.

    Returns the year's grid, the year's apuração rows, the year's DARFs, the
    carry each bucket brought into January, and the year's sales.
    """
    realized = realizar(tl)
    grade = grade_mensal(realized, classe_de, ano)
    historico = monthly_apuracao(grade)
    darfs = darf_mensal(historico, irrf_mensal(realized, classe_de))
    # Keyed by bucket, not by line: ações and BDR/ETF share one.
    anterior = {REGRAS[r["classe"]].grupo or r["classe"]: r["carry"]
                for r in historico if r["year"] == ano - 1}
    return {
        "grade": grade[grade["year"] == ano].reset_index(drop=True),
        "apuracao": [r for r in historico if r["year"] == ano],
        "darf": [d for d in darfs if d["year"] == ano],
        "carry_inicial": anterior,
        "realized": realized[realized["date"].dt.year == ano].reset_index(drop=True),
    }
