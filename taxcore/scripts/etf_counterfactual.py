"""Counterfactual: your actual deposits/withdrawals replayed into ETF mixes.

Takes the real portfolio's external cash flows (every buy, sell, dividend
and renda-fixa event, on their true dates) from portfolio_index and
simulates pouring the same money into simple daily-rebalanced ETF mixes
instead. Money-weighted by construction: same cash, same dates, different
vehicle. BOVA11/IVVB11 closes already embed their TER and reinvest
distributions; the CDI leg is the raw CDI index (a Selic ETF proxy).

Ignored on purpose: trading costs, spreads, and taxes on either side —
so this overstates the ETF route a little (15% IR on every rebalance/sale,
no R$20k isenção for ETFs) and understates the real portfolio's tax perks.

Usage:
    python -m scripts.etf_counterfactual
    python -m scripts.etf_counterfactual --mix "BOVA11=0.6,CDI=0.4"
    python -m scripts.etf_counterfactual --start 2023-09-01
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from scripts._common import annualize as _annualize, parse_weights
from scripts.portfolio_index import (
    C_CDI, C_IBOV, C_PORTFOLIO, C_SPX, REPORTS,
    build_portfolio_index, fetch_yahoo, plot,
)

DEFAULT_MIX = "BOVA11=0.4,IVVB11=0.3,CDI=0.3"


def mix_unit_price(weights: dict[str, float], legs: dict[str, pd.Series],
                   grid: pd.DatetimeIndex, rebalance: str = "D") -> pd.Series:
    """Unit price of a rebalanced mix (base 1.0 at grid start).

    rebalance="D": rebalance to target weights every day. Otherwise a pandas
    period alias ("Q", "M", "A"): weights drift within each period and reset
    to target at each period boundary (e.g. "Q" = quarter-end rebalance).
    """
    if abs(sum(weights.values()) - 1.0) > 1e-6:
        raise SystemExit(f"Mix weights must sum to 1: {weights}")
    if rebalance == "D":
        r = sum(w * legs[k].pct_change().fillna(0.0) for k, w in weights.items())
        return (1.0 + r).cumprod()
    unit = pd.Series(index=grid, dtype=float)
    total = 1.0
    for _, sub in grid.to_series().groupby(grid.to_period(rebalance)):
        idx = sub.index
        rel = sum(w * (legs[k].loc[idx] / legs[k].loc[idx[0]])
                  for k, w in weights.items())
        unit.loc[idx] = total * rel  # continuous: new period's day-1 rel == 1
        total = float(unit.loc[idx[-1]])
    return unit


def simulate(flows: pd.Series, unit: pd.Series,
             initial_value: float = 0.0) -> pd.Series:
    """Value path from replaying external flows into `unit` at daily prices."""
    units = (flows / unit).cumsum() + initial_value / unit.iloc[0]
    return units * unit


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mix", default=DEFAULT_MIX,
                    help=f"Custom mix, e.g. 'BOVA11=0.6,CDI=0.4' "
                         f"(default: {DEFAULT_MIX})")
    ap.add_argument("--rebalance", default="D", choices=["D", "M", "Q", "A"],
                    help="Rebalance cadence: D=daily (default), M=monthly, "
                         "Q=quarter-end, A=annual.")
    ap.add_argument("--start", default=None,
                    help="Simulate from this date, seeded with the real "
                         "portfolio's value on that day.")
    ap.add_argument("--frozen", default=None, metavar="DATE",
                    help="Also simulate 'kept buying what I held on DATE': "
                         "the real portfolio frozen at that day's weights "
                         "(RF book as a CDI leg), fed the same flows. "
                         "Implies --start DATE.")
    ap.add_argument("--frozen-add", default=None, metavar="MIX",
                    help="Blend extra legs into the frozen basket, e.g. "
                         "'IVVB11=0.3' = 70%% old basket + 30%% IVVB11. "
                         "Adds a second frozen scenario for comparison.")
    ap.add_argument("--out", default=None, help="PNG output path.")
    args = ap.parse_args()

    _, _, _, diag = build_portfolio_index()
    it = diag["_internals"]
    grid, nav, cdi = it["grid"], it["nav"], it["cdi"]
    flows = it["buys"] - it["sells"] - it["income"]

    tickers = sorted({k for k in parse_weights(args.mix) if k != "CDI"}
                     | {"BOVA11", "IVVB11"})
    data = fetch_yahoo([f"{t}.SA" for t in tickers], grid[0])
    legs: dict[str, pd.Series] = {"CDI": cdi}
    for t in tickers:
        if f"{t}.SA" not in data:
            raise SystemExit(f"Yahoo has no data for {t} — can't simulate.")
        close = data[f"{t}.SA"]["Close"].reindex(grid).ffill()
        if close.isna().any():
            raise SystemExit(f"{t} has no prices at the start of the period "
                             f"({grid[0].date()}) — pick another leg.")
        legs[t] = close

    mixes = {
        f"ETF mix ({args.mix})": parse_weights(args.mix),
        "100% CDI": {"CDI": 1.0},
        "100% IVVB11": {"IVVB11": 1.0},
    }

    if args.frozen:
        d0 = grid[grid <= pd.Timestamp(args.frozen)][-1]
        held = {t: float(v.loc[d0]) for t, v in it["vals"].items()
                if float(v.loc[d0]) > 100.0}
        rf0 = float(it["rf_value"].loc[d0])
        # Total-return legs (Adj Close = dividends reinvested, Yahoo's records)
        fdata = fetch_yahoo([f"{t}.SA" for t in held], grid[0])
        dropped = {}
        for t, w in held.items():
            df = fdata.get(f"{t}.SA")
            adj = (df["Adj Close"].reindex(grid).ffill()
                   if df is not None and "Adj Close" in df.columns else None)
            if adj is None or pd.isna(adj.loc[d0]):
                dropped[t] = w  # no usable prices at d0; weight redistributed
            else:
                legs[t] = adj
        kept = {t: w for t, w in held.items() if t in legs}
        total = sum(kept.values()) + rf0
        weights = {t: w / total for t, w in kept.items()}
        weights["CDI"] = weights.get("CDI", 0.0) + rf0 / total
        name = f"Frozen {d0:%b-%Y} basket"
        mixes = {name: weights, **mixes}
        del mixes["100% IVVB11"]  # keep the chart at 4 series
        if args.frozen_add:
            extra = parse_weights(args.frozen_add)
            missing = [t for t in extra if t not in legs]
            if missing:
                raise SystemExit(f"No price legs for {missing} — only "
                                 f"CDI/BOVA11/IVVB11 and held tickers work.")
            scale = 1.0 - sum(extra.values())
            blended = {t: w * scale for t, w in weights.items()}
            for t, w in extra.items():
                blended[t] = blended.get(t, 0.0) + w
            mixes = {f"{name} + {args.frozen_add}": blended, name: weights,
                     f"ETF mix ({args.mix})": mixes[f"ETF mix ({args.mix})"]}
        args.start = str(d0.date())
        top = sorted(kept.items(), key=lambda kv: -kv[1])[:10]
        print(f"\nFrozen basket as of {d0.date()} — listed R$ {sum(kept.values()):,.0f} "
              f"+ RF R$ {rf0:,.0f} (CDI leg, {rf0 / total:.0%}). Top weights:")
        for t, w in top:
            print(f"   {t:<8} {w / total:6.1%}")
        if dropped:
            print(f"   (no prices at {d0.date()}, weight redistributed: "
                  f"{sorted(dropped)} = R$ {sum(dropped.values()):,.0f})")

    initial = 0.0
    if args.start:
        cut = pd.Timestamp(args.start)
        initial = float(nav[nav.index >= cut].iloc[0])
        nav = nav[nav.index >= cut]
        flows = flows[flows.index >= cut]
        grid = grid[grid >= cut]
        legs = {k: s[s.index >= cut] for k, s in legs.items()}

    series = {"Actual portfolio": (nav, C_PORTFOLIO)}
    for (name, w), color in zip(mixes.items(), (C_CDI, C_IBOV, C_SPX)):
        series[name] = (simulate(flows, mix_unit_price(w, legs, grid,
                                 args.rebalance), initial), color)

    net_invested = initial + float(flows.sum())
    years = (grid[-1] - grid[0]).days / 365.25
    rows = []
    for name, (v, _) in series.items():
        end = float(v.iloc[-1])
        rows.append((name, f"R$ {end:,.0f}", f"R$ {end - net_invested:+,.0f}"))
    from tabulate import tabulate
    print()
    print(f"Period: {grid[0].date()} -> {grid[-1].date()}  (~{years:.2f} years)")
    print(f"Seed at start: R$ {initial:,.0f} · net external flows since: "
          f"R$ {float(flows.sum()):,.0f} · total capital: R$ {net_invested:,.0f}")
    print(tabulate(rows, headers=["Scenario (same deposits, same dates)",
                                  "End value", "Profit"], tablefmt="github"))
    print()
    for name, w in mixes.items():
        u = mix_unit_price(w, legs, grid, args.rebalance)
        tot = (float(u.iloc[-1]) / float(u.iloc[0]) - 1.0) * 100.0
        print(f"  {name}: vehicle return {tot:+.1f}% "
              f"({_annualize(tot, years):+.2f}%/yr)")

    out = Path(args.out) if args.out else (
        REPORTS / f"{grid[-1].date()}-etf-counterfactual.png")
    subtitle = (f"{grid[0].date()} -> {grid[-1].date()} · your real cash "
                "flows replayed into ETF mixes · gross of taxes/costs on "
                "all sides")
    plot(series, out, subtitle,
         title="Actual portfolio vs ETF counterfactuals — value in R$",
         money=True)
    print(f"\nChart saved -> {out}")
    print("\nCaveats:")
    print("- Same external cash flows, so this is a money-weighted comparison;")
    print("  differences are vehicle + strategy, not deposit timing.")
    print("- Both sides gross of tax. In reality the ETF route pays 15% IR on")
    print("  sales with no R$20k isenção; the real portfolio's dividends/FII")
    print("  rendimentos were tax-free. Hindsight bias: this window is known.")
    print("- CDI leg is the raw index (no ETF fee); BOVA11/IVVB11 closes embed")
    print("  their real TERs. Daily rebalancing, no trading costs.")
    print("- Analysis, not a recommendation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
