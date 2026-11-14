"""Realização — the sales a timeline realised, and the apuração they feed.

One walk (`preco_medio.caminhar`), shared by the IRPF report's Renda Variável
grid, its Bens e Direitos snapshots, the TLH planner and positions.csv, so none
of them can disagree about a sale or a preço médio. The day-trade, descoberto
and órfão rules are documented there.
"""
from __future__ import annotations

import pandas as pd

from scripts.apuracao import REGRAS, darf_mensal, monthly_apuracao
from scripts.preco_medio import caminhar

COLS = ["date", "ticker", "broker", "qty", "sell_price", "avg_cost",
        "sell_value", "pnl", "daytrade", "descoberto"]
GRID_CLASSES = ("AÇÃO", "FII", "BDR", "ETF_RV", "ETF_RF", "DT")
# Asset classes whose day trades are apurados on the day-trade line. A FII day
# trade stays on the FII line, which the IRPF program apurates as one 20% table.
DT_CLASSES = ("AÇÃO", "BDR", "ETF_RV")


def _realizar(tl: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """(every sale, every órfão event) in the timeline, all years."""
    rows: list[dict] = []
    orfaos: list[dict] = []
    for _ticker, _g, _pos, vendas, orf in caminhar(tl):
        rows += vendas
        orfaos += orf
    out = pd.DataFrame(rows, columns=COLS)
    out["date"] = pd.to_datetime(out["date"])  # typed even when there is no sale
    return out, orfaos


def realizar(tl: pd.DataFrame) -> pd.DataFrame:
    """Every sale in the timeline, all years: one row per (ticker, day, broker,
    day trade or not). `sell_value` is the full proceeds; `qty` and `pnl` cover
    only what the walk held; `descoberto` is the quantity it did not."""
    return _realizar(tl)[0]


def realized_per_month(tl: pd.DataFrame, year: int) -> pd.DataFrame:
    """The year's sales — the shape irpf_2026 has always exported."""
    r = realizar(tl)
    return r[r["date"].dt.year == year].reset_index(drop=True)


def snapshot_positions(tl: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Walk timeline up to cutoff and return (ticker, qty, total_cost, broker)."""
    out = []
    for ticker, group, pos, _vendas, _orfaos in caminhar(tl[tl["date"] <= cutoff]):
        if pos.qty <= 1e-6:
            continue
        # Most-recent non-empty broker for this ticker on or before cutoff.
        buys = group.loc[group["kind"] == "buy", "broker"]
        brokers = [b.strip() for b in buys if isinstance(b, str)
                   and b.strip() and b.strip() != "override"]
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
    carry each bucket brought into January, the year's sales and the year's
    órfão events (preco_medio: quantity the loaded history cannot explain).
    """
    realized, orfaos = _realizar(tl)
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
        "orfaos": [o for o in orfaos if o["date"].year == ano],
    }
