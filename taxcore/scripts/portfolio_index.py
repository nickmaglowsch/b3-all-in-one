"""Daily total-return index of the portfolio vs CDI, IBOV and S&P 500.

Reconstructs daily holdings from data/trades.csv (plus corporate qty events
from data/cash_events.csv), values them with Yahoo daily closes, and chains a
time-weighted return (TWR) — so deposits and withdrawals move the NAV but not
the index. Dividends/JCP/rendimentos/amortizações count as return on the day
they hit the account. Everything is indexed to 100 at the start.

Benchmarks:
  CDI      — BCB SGS série 12 (daily CDI, compounded)
  IBOV     — ^BVSP (a total-return index by construction)
  S&P 500  — ^SP500TR converted to BRL at spot (what a BRL investor earned)

Out of scope (excluded, and disclosed in the output): Futuro daytrades,
options premiums, reembolsos, and any ticker Yahoo has no history for.

Usage:
    python -m scripts.portfolio_index                    # inception -> today
    python -m scripts.portfolio_index --start 2023-01-01
    python -m scripts.portfolio_index --out reports/x.png
"""
from __future__ import annotations

import argparse
import re
import sys
import warnings
from pathlib import Path

import pandas as pd

from scripts._common import annualize as _annualize

warnings.filterwarnings("ignore")  # yfinance is noisy; one-shot CLI

ROOT = Path(__file__).resolve().parent.parent
TRADES = ROOT / "data" / "trades.csv"
CASH_EVENTS = ROOT / "data" / "cash_events.csv"
POSITIONS = ROOT / "data" / "positions.csv"
REPORTS = ROOT / "reports"

# dataviz palette (light mode) — validated slots 1-4 + chrome
C_PORTFOLIO, C_CDI, C_IBOV, C_SPX = "#2a78d6", "#008300", "#e87ba4", "#eda100"
SURFACE, INK, INK2, MUTED, GRID, BASELINE = (
    "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7")

# Futures (WIN/WDO daytrades) have no daily-position meaning; options live in
# cash_events as 'premium'. ETF primary-market rows are receipt bookkeeping
# (e.g. HASH11H) that duplicates the real buy load_b3 synthesizes from
# cost_basis_overrides.csv. All excluded from the index and disclosed.
EXCLUDED_MARKETS = {"Futuro", "Mercado Primário de ETF"}
INCOME_CATEGORIES = {"income", "amortization"}

_FRACTIONAL = re.compile(r"^([A-Z0-9]{4}\d{1,2})F$")  # PETR4F/B3SA3F -> base


def norm_ticker(t: str) -> str:
    m = _FRACTIONAL.match(t)
    return m.group(1) if m else t


# '11' suffix means FII/FIAgro except units and ETFs; extend when a new
# ETF/unit is bought. SAPR11 is a unit (stock), the rest are ETFs.
NOT_FII_11 = {"SAPR11", "IVVB11", "HASH11", "JURO11", "LFTB11"}


def sleeve_of(t: str) -> str | None:
    """'fii', 'stock', or None (ETF/BDR — passive beta, in neither sleeve)."""
    if t.endswith("11") and t not in NOT_FII_11:
        return "fii"
    if t in NOT_FII_11 - {"SAPR11"} or re.search(r"3[1-9]$", t):
        return None
    return "stock"


def twr_index(nav: pd.Series, buys: pd.Series, sells: pd.Series,
              income: pd.Series) -> pd.Series:
    """Chain daily TWR into an index (base 100).

    r_t = (NAV_t + sells_t + income_t) / (NAV_{t-1} + buys_t) - 1
    Buys are treated as start-of-day external inflows, sells/income as
    end-of-day outflows. Days with no capital at risk are flat.
    """
    prev = nav.shift(1).fillna(0.0)
    denom = prev + buys
    gross = (nav + sells + income) / denom.where(denom > 0)
    return 100.0 * gross.fillna(1.0).cumprod()


def snap_to_grid(s: pd.Series, grid: pd.DatetimeIndex) -> pd.Series:
    """Sum series values onto grid dates, rolling off-grid dates forward."""
    pos = grid.searchsorted(s.index).clip(0, len(grid) - 1)
    return s.groupby(grid[pos]).sum().reindex(grid, fill_value=0.0)


def fetch_yahoo(symbols: list[str], start: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """One batched download; returns {symbol: frame with Close/Stock Splits}.

    Symbols Yahoo has nothing for are absent from the result.
    """
    import yfinance as yf

    out: dict[str, pd.DataFrame] = {}

    def harvest(raw: pd.DataFrame, wanted: list[str]) -> None:
        for sym in wanted:
            try:
                df = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                continue
            df = df.copy()
            df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
            if "Close" in df.columns and df["Close"].notna().any():
                out[sym] = df

    kwargs = dict(start=start.strftime("%Y-%m-%d"), auto_adjust=False,
                  actions=True, progress=False, group_by="ticker")
    harvest(yf.download(symbols, threads=True, **kwargs), symbols)
    # Batched downloads flake on individual symbols; retry the misses once,
    # serially, before declaring them truly absent from Yahoo.
    missing = [s for s in symbols if s not in out]
    if missing:
        harvest(yf.download(missing, threads=False, **kwargs), missing)
    return out


def split_factor(splits: pd.Series, grid: pd.DatetimeIndex,
                 closes: pd.Series | None = None,
                 trade_px: pd.Series | None = None,
                 confirm_dates: tuple = ()) -> tuple[pd.Series, list]:
    """Cumulative ratio of Yahoo splits AFTER each grid date.

    Yahoo closes are split-adjusted to the present; multiplying historical
    (actual) share counts by this factor puts qty on the same basis.

    Yahoo sometimes carries split entries it never applied to the price
    series (VINO11 2023-24: FII amortizações misreported as 5:1 and 10:1
    splits) — trusting those values a position 10-50x off. So each split is
    validated against our own trade prices: if trades between it and the
    previous split price like Yahoo adjusted for it, keep it; if they price
    like it never happened, drop it. No trades to judge by -> keep. A split
    within 7 days of one of our B3 qty_events (confirm_dates) is confirmed
    by B3's own records and skips the price test — the test's log-distance
    margin is noise-dominated for small bonificação ratios like 1.03.
    Returns (factor, rejected) with rejected = [(date, ratio), ...].
    """
    from math import log

    entries = sorted(splits[splits > 0].items()) if splits is not None else []
    bounds = [grid[0]] + [d for d, _ in entries]
    acc = 1.0  # product of accepted splits later than the one being judged
    accepted, rejected = [], []
    for i in reversed(range(len(entries))):
        d, ratio = entries[i]
        keep = True
        if any(abs((d - cd).days) <= 7 for cd in confirm_dates):
            pass
        elif closes is not None and trade_px is not None:
            win = trade_px[(trade_px.index >= bounds[i]) & (trade_px.index < d)]
            px = closes.reindex(win.index)
            imp = (win / px).dropna()
            if len(imp):
                m = float(imp.median())
                keep = abs(log(m / (acc * ratio))) < abs(log(m / acc))
        if keep:
            acc *= ratio
            accepted.append((d, ratio))
        else:
            rejected.append((d, ratio))
    f = pd.Series(1.0, index=grid)
    for d, ratio in accepted:
        f[f.index < d] *= ratio
    return f, rejected


def fetch_cdi(start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Compounded CDI index from BCB SGS série 12 (daily % rates)."""
    import requests
    r = requests.get(
        "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados",
        params={"formato": "json",
                "dataInicial": start.strftime("%d/%m/%Y"),
                "dataFinal": end.strftime("%d/%m/%Y")},
        timeout=60)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    if df.empty:
        raise SystemExit("BCB SGS returned no CDI data.")
    idx = pd.to_datetime(df["data"], format="%d/%m/%Y")
    rate = pd.to_numeric(df["valor"]) / 100.0
    return pd.Series((1.0 + rate).cumprod().values, index=idx)


def rf_sleeve(rf: pd.DataFrame, grid: pd.DatetimeIndex, cdi_index: pd.Series
              ) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, dict]:
    """Daily value + flows for bank/OTC renda fixa (CDB/LCI/CRA/Tesouro...).

    B3 reports carry RF cash events but no daily marks, so each product is
    valued as units of the CDI index: aplicação buys V/U(d) units, resgate/
    amortização sells them, value = units x U(d). A product that didn't pay
    exactly CDI gets its true return recognized on the redemption day
    (endpoint-correct, path approximate). Coupons (juros) are income.
    Special cases: resgate rows with Valor=0 mean the payout happened
    outside B3 custody -> close at the CDI-accrued estimate; 'frozen' means
    bank intervention -> value flatlines pending FGC. `rf` is
    renda_fixa_analysis.load_renda_fixa() output.
    """
    U = cdi_index.reindex(grid).ffill().bfill()
    value = pd.Series(0.0, index=grid)
    buys, sells, income = (pd.Series(0.0, index=grid) for _ in range(3))
    diag = {"products": int(rf["code"].nunique()), "open": 0.0, "closed": 0,
            "frozen": 0.0, "matured_est": 0.0}
    for code, g in rf.groupby("code"):
        units, udeltas, last_close, aplic_total = 0.0, [], None, 0.0
        for _, e in g.sort_values("Data").iterrows():
            cat, v = e["cat"], float(e["Valor"]) if pd.notna(e["Valor"]) else 0.0
            d = grid[min(grid.searchsorted(e["Data"]), len(grid) - 1)]
            u = float(U.loc[d])
            if cat == "aplic" and v > 0:
                units += v / u
                udeltas.append((d, v / u))
                buys[d] += v
                aplic_total += v
            elif cat in ("resgate", "amort") and v > 0:
                need = v / u
                short = need - units
                if short > 1e-9 and (aplic_total == 0 or short > units):
                    # Far more redeemed than ever applied -> the aplicação
                    # predates the report range; held since window start.
                    udeltas.append((grid[0], short))
                    units += short
                # A modest excess over the accrued estimate is the product
                # beating CDI -> that gain lands on the redemption day.
                take = min(units, need)
                udeltas.append((d, -take))
                units -= take
                sells[d] += v
                if cat == "resgate":
                    last_close = d
            elif cat == "resgate" and units > 1e-9:  # matured, payout outside B3
                est = units * u
                sells[d] += est
                udeltas.append((d, -units))
                units = 0.0
                diag["matured_est"] += est
            elif cat == "juros" and v > 0:
                income[d] += v
            elif cat == "frozen" and units > 1e-9:
                fv = units * u
                udeltas.append((d, -units))
                units = 0.0
                value.loc[d:] += fv
                diag["frozen"] += fv
        if last_close is not None and units > 1e-9 and aplic_total > 0:
            resid = units * float(U.loc[last_close])
            # Small residual after a redemption = product paid less than the
            # CDI estimate -> recognize the shortfall at close.
            # ponytail: >20% residual reads as a partial redemption, stays open
            if resid / aplic_total < 0.2:
                udeltas.append((last_close, -units))
                units = 0.0
        if units > 1e-9:
            diag["open"] += units * float(U.iloc[-1])
        else:
            diag["closed"] += 1
        du = pd.Series(0.0, index=grid)
        for d, x in udeltas:
            du[d] += x
        value += du.cumsum() * U
    return value, buys, sells, income, diag


def _spread_labels(vals: list[float], min_gap: float) -> list[float]:
    """Nudge label y-positions apart so line-end labels don't collide."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out = list(vals)
    prev = None
    for i in order:
        if prev is not None and out[i] - prev < min_gap:
            out[i] = prev + min_gap
        prev = out[i]
    return out


def plot(series: dict[str, tuple[pd.Series, str]], out_path: Path,
         subtitle: str,
         title: str = "Portfolio vs benchmarks — total return, base 100",
         money: bool = False) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10.8, 5.6), dpi=160)
    fig.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    protagonists = {"Portfolio", "Ações BR", "FIIs + FIAgro"}
    for name, (s, color) in series.items():
        lw = 2.4 if name in protagonists else 1.6
        ax.plot(s.index, s.values, color=color, linewidth=lw, label=name,
                solid_capstyle="round")

    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(
        mdates.AutoDateLocator()))
    if money:
        from matplotlib.ticker import FuncFormatter
        ax.yaxis.set_major_formatter(
            FuncFormatter(lambda v, _: f"{v / 1e6:,.1f}M"))

    # Direct labels at line ends (required: two palette hues are low-contrast).
    names = list(series)
    ends = [series[n][0].iloc[-1] for n in names]
    lo, hi = ax.get_ylim()
    ys = _spread_labels(ends, (hi - lo) * 0.045)
    x = series[names[0]][0].index[-1]
    for name, y in zip(names, ys):
        end = series[name][0].iloc[-1]
        lbl = f"{end / 1e6:,.2f}M" if money else f"{end:,.0f}"
        ax.annotate(f"{name}  {lbl}",
                    (x, y), xytext=(8, 0), textcoords="offset points",
                    va="center", fontsize=9,
                    color=INK if name in protagonists else INK2)
    fig.subplots_adjust(right=0.82)

    ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK2)
    ax.set_title(title + "\n",
                 loc="left", fontsize=13, color=INK, fontweight="bold")
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9, color=INK2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)


def build_portfolio_index(start_override: str | None = None,
                          sleeves: bool = False
                          ) -> tuple[dict, pd.DatetimeIndex, list[str], dict]:
    """Returns ({name: (index_series, color)}, grid, excluded, diagnostics).

    sleeves=True splits the listed portfolio into Ações BR vs FIIs+FIAgro
    (each with its own flows/income TWR) instead of the total-wealth view;
    ETFs/BDRs and bank renda fixa stay out of both sleeves.
    """
    trades = pd.read_csv(TRADES, parse_dates=["date"])
    trades = trades[~trades["market"].isin(EXCLUDED_MARKETS)].copy()
    trades["ticker"] = trades["ticker"].map(norm_ticker)

    events = pd.read_csv(CASH_EVENTS, parse_dates=["date"])
    events["ticker"] = events["ticker"].astype(str).map(norm_ticker)

    start = trades["date"].min().normalize()
    today = pd.Timestamp.today().normalize()
    grid = pd.bdate_range(start, today)

    tickers = sorted(trades["ticker"].unique())
    symbols = [f"{t}.SA" for t in tickers] + ["^BVSP", "^SP500TR", "BRL=X"]
    print(f"Fetching {len(symbols)} symbols from Yahoo ({start.date()} -> {today.date()})...")
    data = fetch_yahoo(symbols, start - pd.Timedelta(days=7))
    for bench in ("^BVSP", "^SP500TR", "BRL=X"):
        if bench not in data:
            raise SystemExit(f"Yahoo returned no data for {bench} — cannot benchmark.")

    excluded = [t for t in tickers if f"{t}.SA" not in data]
    excl_buys = float(trades.loc[trades["ticker"].isin(excluded)
                                 & (trades["side"] == "Compra"), "amount"].sum())
    if excluded:
        trades = trades[~trades["ticker"].isin(excluded)]
        events = events[~events["ticker"].isin(excluded)]

    # Daily holdings in actual (historical) share counts. qty events only for
    # tickers we actually traded (subscription receipts etc. show up in
    # cash_events under tickers that never appear in trades).
    qty_ev = events[(events["event_category"] == "qty_event")
                    & events["ticker"].isin(trades["ticker"].unique())]
    deltas = pd.concat([
        trades.pivot_table(index="date", columns="ticker",
                           values="signed_qty", aggfunc="sum"),
        qty_ev.pivot_table(index="date", columns="ticker",
                           values="qty", aggfunc="sum"),
    ]).groupby(level=0).sum()
    hold = deltas.sort_index().cumsum().reindex(grid, method="ffill").fillna(0.0)
    # Sells with no recorded prior buy (report gaps flagged by load_b3 as
    # incomplete_history, and buys settlement-dated after their sell) would
    # drive holdings negative — a phantom short whose sale proceeds would
    # print as fake return. Those shares were evidently held, so backfill
    # them from the window start: TWR needs their market value, not their
    # cost basis. hold(d) = cum(d) - min(0, min_{d'>=d} cum(d')).
    hold -= hold.iloc[::-1].cummin().iloc[::-1].clip(upper=0.0)
    hold[hold.abs() < 1e-6] = 0.0

    # Value with split-adjusted closes; put qty on the same basis via Yahoo's
    # own splits column so price x qty is consistent by construction.
    trade_px = trades.groupby(["ticker", "date"])["price"].median()
    vals: dict[str, pd.Series] = {}
    flat_days: dict[str, int] = {}
    bad_splits: dict[str, list] = {}
    for t in hold.columns:
        df = data[f"{t}.SA"]
        close = df["Close"].reindex(grid)
        held = hold[t] > 0
        n_missing = int((held & close.ffill().isna()).sum())
        if n_missing:
            flat_days[t] = n_missing
        close = close.ffill().bfill()  # bfill: held-before-history -> flat at first close
        tpx = trade_px.loc[t] if t in trade_px.index.get_level_values(0) else None
        confirm = tuple(qty_ev.loc[qty_ev["ticker"] == t, "date"])
        factor, rejected = split_factor(df.get("Stock Splits"), grid, close,
                                        tpx, confirm)
        if rejected:
            bad_splits[t] = [(d.date().isoformat(), r) for d, r in rejected]
        vals[t] = (hold[t] * factor * close).fillna(0.0)
    nav = pd.DataFrame(vals).sum(axis=1) if vals else pd.Series(0.0, index=grid)

    buys = snap_to_grid(trades.loc[trades["side"] == "Compra"]
                        .set_index("date")["amount"], grid)
    sells = snap_to_grid(trades.loc[trades["side"] == "Venda"]
                         .set_index("date")["amount"], grid)
    inc_rows = events[events["event_category"].isin(INCOME_CATEGORIES)]
    income = snap_to_grid(inc_rows.set_index("date")["amount"], grid)

    print("Fetching CDI from BCB SGS...")
    cdi = fetch_cdi(start, today).reindex(grid).ffill()

    # Bank/OTC renda fixa sleeve (CDB/LCI/CRA/Tesouro...) — total wealth,
    # not just listed securities. Irrelevant in sleeves mode.
    rf_diag: dict = {}
    rf_val = pd.Series(0.0, index=grid)
    if not sleeves:
        from scripts.renda_fixa_analysis import load_renda_fixa
        rf = load_renda_fixa()
        if not rf.empty:
            rf_val, rf_buys, rf_sells, rf_inc, rf_diag = rf_sleeve(rf, grid, cdi)
            nav += rf_val
            buys += rf_buys
            sells += rf_sells
            income += rf_inc

    # Benchmarks on the same grid, ffill across holidays, rebased below.
    ibov = data["^BVSP"]["Close"].reindex(grid).ffill()
    spx = (data["^SP500TR"]["Close"].reindex(grid).ffill()
           * data["BRL=X"]["Close"].reindex(grid).ffill())

    if sleeves:
        groups = {"Ações BR": [t for t in vals if sleeve_of(t) == "stock"],
                  "FIIs + FIAgro": [t for t in vals if sleeve_of(t) == "fii"]}
        neither = sorted(t for t in vals if sleeve_of(t) is None)
        series = {}
        for (name, cols), color in zip(groups.items(), (C_PORTFOLIO, C_SPX)):
            tt = trades[trades["ticker"].isin(cols)]
            b = snap_to_grid(tt.loc[tt["side"] == "Compra"]
                             .set_index("date")["amount"], grid)
            s = snap_to_grid(tt.loc[tt["side"] == "Venda"]
                             .set_index("date")["amount"], grid)
            inc = snap_to_grid(inc_rows[inc_rows["ticker"].isin(cols)]
                               .set_index("date")["amount"], grid)
            sub_nav = pd.DataFrame({t: vals[t] for t in cols}).sum(axis=1)
            series[name] = (twr_index(sub_nav, b, s, inc), color)
        series["CDI"] = (cdi, C_CDI)
        series["IBOV"] = (ibov, C_IBOV)
        print(f"Sleeves: Ações BR = {len(groups['Ações BR'])} tickers · "
              f"FIIs+FIAgro = {len(groups['FIIs + FIAgro'])} · "
              f"in neither (ETFs/BDRs): {neither}")
    else:
        series = {
            "Portfolio": (twr_index(nav, buys, sells, income), C_PORTFOLIO),
            "CDI": (cdi, C_CDI),
            "IBOV": (ibov, C_IBOV),
            "S&P 500 TR (BRL)": (spx, C_SPX),
        }
    if start_override:
        cut = pd.Timestamp(start_override)
        if cut >= grid[-1]:
            raise SystemExit(f"--start {start_override} is beyond the data range.")
        series = {k: (s[s.index >= cut], c) for k, (s, c) in series.items()}
    series = {k: (s.dropna() / s.dropna().iloc[0] * 100.0, c)
              for k, (s, c) in series.items()}

    diag = {
        # Pre-rebase internals for etf_counterfactual.py: real NAV in R$ and
        # the external cash flows that produced it.
        "_internals": {"nav": nav, "buys": buys, "sells": sells,
                       "income": income, "cdi": cdi, "grid": grid,
                       "vals": vals, "rf_value": rf_val},
        "rf": rf_diag,
        "bad_splits": bad_splits,
        "flat_days": flat_days,
        "income_total": float(inc_rows["amount"].sum()),
        "final_hold": hold.iloc[-1],
        "excluded_buy_total": excl_buys,
    }
    return series, grid, excluded, diag


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default=None,
                    help="Chart from this date, all series rebased to 100 there.")
    ap.add_argument("--sleeves", action="store_true",
                    help="Split into Ações BR vs FIIs+FIAgro instead of the "
                         "total-wealth view (ETFs/BDRs/renda fixa left out).")
    ap.add_argument("--out", default=None, help="PNG output path.")
    args = ap.parse_args()

    raw_trades = pd.read_csv(TRADES)
    series, grid, excluded, diag = build_portfolio_index(args.start, args.sleeves)

    lead = next(iter(series))
    first = series[lead][0].index[0]
    last = series[lead][0].index[-1]
    years = (last - first).days / 365.25

    rows = []
    for name, (s, _) in series.items():
        total = s.iloc[-1] - 100.0
        rows.append((name, f"{s.iloc[-1]:,.1f}", f"{total:+.1f}%",
                     f"{_annualize(total, years):+.2f}%/yr"))
    from tabulate import tabulate
    print()
    print(f"Period: {first.date()} -> {last.date()}  (~{years:.2f} years)")
    print(tabulate(rows, headers=["Series", "Index", "Total return", "CAGR"],
                   tablefmt="github"))

    stem = "portfolio-sleeves" if args.sleeves else "portfolio-index"
    out = Path(args.out) if args.out else REPORTS / f"{last.date()}-{stem}.png"
    if args.sleeves:
        subtitle = (f"{first.date()} -> {last.date()} · sleeve TWR incl. "
                    "dividends/rendimentos · ETFs, BDRs and renda fixa in "
                    "neither sleeve · CDI gross")
    else:
        subtitle = (f"{first.date()} -> {last.date()} · portfolio = daily TWR incl. "
                    "dividends + renda fixa (cost+CDI accrual) · CDI gross · "
                    "S&P 500 TR in BRL")
    plot(series, out, subtitle)
    print(f"\nChart saved -> {out}")

    if diag["rf"]:
        rd = diag["rf"]
        print(f"\nRenda fixa sleeve: {rd['products']} products · "
              f"{rd['closed']} closed · open now ~R$ {rd['open']:,.0f} "
              f"(valued at cost+CDI accrual)"
              + (f" · frozen pending FGC ~R$ {rd['frozen']:,.0f}"
                 if rd["frozen"] else "")
              + (f" · matured outside B3, closed at CDI estimate "
                 f"~R$ {rd['matured_est']:,.0f}" if rd["matured_est"] else ""))

    # Reconciliation: reconstructed final holdings vs positions.csv.
    if POSITIONS.exists():
        pos = pd.read_csv(POSITIONS).set_index("ticker")["qty"]
        fin = diag["final_hold"]
        mism = {t: (float(fin.get(t, 0)), float(pos.get(t, 0)))
                for t in set(pos.index) | set(fin[fin > 0].index)
                if t not in excluded and abs(fin.get(t, 0) - pos.get(t, 0)) > 0.5}
        if mism:
            print("\n⚠️  Holdings reconciliation mismatches (reconstructed vs "
                  "positions.csv) — usually load_b3's incomplete_history gaps;")
            print("   these shares are missing from the chart's NAV:")
            for t, (a, b) in sorted(mism.items()):
                print(f"   {t}: {a:g} vs {b:g}")

    print("\nCaveats:")
    n_fut = int((raw_trades["market"] == "Futuro").sum())
    if n_fut:
        print(f"- {n_fut} Futuro trades (WIN/WDO daytrades) and all options premiums")
        print("  are excluded — they have no daily position to price.")
    if excluded:
        print(f"- No Yahoo history (excluded from index and flows, "
              f"R$ {diag['excluded_buy_total']:,.0f} of buys): {excluded}")
    if diag["flat_days"]:
        print(f"- Held days valued flat for lack of Yahoo prices: {diag['flat_days']}")
    if diag["bad_splits"]:
        print(f"- Yahoo split entries rejected (contradicted by your actual "
              f"trade prices): {diag['bad_splits']}")
        print("  Usually Yahoo data errors (duplicate/phantom splits), but a")
        print("  real ratio here can mean your movimentação import is missing")
        print("  that corporate action — re-download that period if in doubt.")
    if diag["rf"]:
        print("- Bank/OTC renda fixa has no daily marks in B3 data: valued at")
        print("  cost accrued at 100% CDI, true return recognized at redemption.")
        print("  Open products' current value is therefore an estimate; prefixado/")
        print("  IPCA products have approximate paths (endpoints correct).")
    print("- TWR measures strategy performance (right for comparing to indices);")
    print("  it is NOT your money-weighted return — portfolio_vs_ibov.py has that.")
    print("- Portfolio return is net of source withholding (JCP); CDI and the")
    print("  benchmarks are gross of tax. Reembolsos are excluded.")
    print("- IBOV is a total-return index; S&P 500 TR is converted at spot BRL.")
    print("- This is analysis, not a recommendation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
