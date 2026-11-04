"""
Config-driven strategies
========================
Two generic ``StrategyBase`` subclasses that turn "portfolio with rebalancing"
strategies into data. Instead of a bespoke Python class per strategy, a YAML
spec (auto-registered by ``strategy_registry.discover()``) drives one of:

  * ``RankAndHold``  — rank a factor over a liquid universe, take the top
    N / top-pct, weight them, rebalance. Covers the ~16 ranked-equity
    strategies whose only real difference was one signal line.
  * ``FixedWeight``  — hold a fixed set of sleeves at constant weights,
    rebalanced on the grid. Covers the ETF/CDI blend strategies.

The heavy lifting (rebalancing, tax, slippage) stays in ``run_simulation``;
these classes only build ``target_weights``.

Signal convention: every named signal returns a wide (date x ticker) DataFrame
where **higher = better**, so selection is always ``nlargest``. A low-P/E
strategy is expressed as ``earnings_yield`` (higher yield = cheaper), etc.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtests.core import markets
# resolve_factor and _signal_defs are re-exported: scripts and tests import them
# from here, where they lived before spec_signal.
from backtests.core.spec_signal import (  # noqa: F401
    _build_signal, _signal_defs, _weight_frame, only_in_months, resolve_factor,
    sector_filter,
)
from backtests.core.strategy_base import (
    market_param, cap_and_renormalize,
    StrategyBase, ParameterSpec, keep_most_liquid_per_root,
    COMMON_RUN_SPECS, COMMON_START_DATE, COMMON_END_DATE,
)


def _read_flag(shared: dict, name: str, ic: dict, index, i: int) -> bool:
    """One regime input at rebalance row i: the flag's value at `index[i - offset]`,
    thresholded by `gt` when given. A date the flag does not cover reads False."""
    flag = shared[ic["flag"]]
    if isinstance(flag, pd.DataFrame):
        # `DataFrame.get(date)` looks up a column and returns None, so a per-ticker
        # frame (above_ma200) read False on every date and the book parked in CDI
        # for the whole run with no error. validate_spec refuses it at save; this
        # catches a spec stored before that, or one never shelled through it.
        raise ValueError(
            f"regime input {name!r}: flag {ic['flag']!r} is a per-ticker frame, not a "
            "market series — a regime reads one value per date (see regimes.REGIME_FLAGS)")
    j = i - int(ic.get("offset", 1))
    raw = flag.get(index[j]) if 0 <= j < len(index) else None
    if raw is None or pd.isna(raw):
        return False
    if "gt" in ic:
        return bool(float(raw) > ic["gt"])
    return bool(raw)


def _eval_regime(regime: dict, shared: dict, i: int, index=None):
    """Evaluate the regime rules at rebalance row i. Each input is a boolean read
    of a shared_data flag at the date `index[i - offset]` (optionally `> gt`);
    `index` is the rebalance grid, ``shared["ret"].index`` by default. Returns the
    first matching rule dict (carries `top_pct` or `park`), or None (=> cash).

    By date, not position: the flags live on the benchmark's / CDI's own index,
    which can start later than the price grid (SPY via AlphaVantage ~1999-11, a
    failed first CDI chunk), and `flag.iloc[i-offset]` then read a future flag.
    A date the flag does not cover reads False, like a NaN.
    """
    if index is None:
        index = shared["ret"].index
    vals = {nm: _read_flag(shared, nm, ic, index, i)
            for nm, ic in regime.get("inputs", {}).items()}
    for rule in regime.get("rules", []):
        if rule.get("default"):
            return rule
        # `min_true: N` = an N-of-M vote over all inputs (research_multifactor's
        # "2 of 3" gate), instead of naming an exact combination.
        if "min_true" in rule:
            if sum(vals.values()) >= int(rule["min_true"]):
                return rule
            continue
        conds = {k: v for k, v in rule.items() if k not in ("top_pct", "park", "default")}
        if all(vals.get(k) == v for k, v in conds.items()):
            return rule
    return None


class _SpecStrategy(StrategyBase):
    """A strategy whose name, description and market come from its spec."""

    def __init__(self, spec: dict) -> None:
        self.spec = spec
        self.market = markets.get(spec.get("market")).key
        # A spec (or a strategy sleeve of one) may need CVM fundamentals; the
        # spec declares it so the service loads them (can't query the registry
        # here — we're being constructed *inside* registry discovery).
        self.needs_fundamentals = bool(spec.get("needs_fundamentals", False))

    @property
    def name(self) -> str:
        return self.spec["name"]

    @property
    def description(self) -> str:
        return self.spec.get("description", self.spec["name"])


# ── Ranked-equity engine ──────────────────────────────────────────────────────

class RankAndHold(_SpecStrategy):
    """Generic rank -> top-N -> weight -> rebalance strategy, driven by a spec."""

    def get_parameter_specs(self) -> list[ParameterSpec]:
        uni, _spec = self.spec.get("universe", {}), markets.get(self.market)
        sel = self.spec.get("selection", {})
        specs = [
            *COMMON_RUN_SPECS,
            market_param(self.market),
            ParameterSpec("min_adtv", "Min ADTV (BRL)", "float",
                          float(uni.get("min_adtv", _spec.default_min_adtv)),
                          min_value=0.0, step=100_000.0),
            ParameterSpec("min_price", "Min Price (BRL)", "float",
                          float(uni.get("min_price", _spec.default_min_price)),
                          min_value=0.0, max_value=10.0, step=0.5),
            ParameterSpec("rebalance_freq", "Rebalance Frequency", "choice",
                          self.spec.get("rebalance", "ME"), choices=["ME", "QE", "W-FRI"]),
        ]
        # Expose lookback for whichever the signal uses (single or composite —
        # one shared lookback drives both composite legs, like multifactor.py).
        lb = self.spec.get("signal", {}).get("lookback")
        if lb is None and self.spec.get("composite"):
            lb = self.spec["composite"][0].get("lookback")
        if lb is not None:
            specs.append(ParameterSpec("lookback", "Lookback Periods", "int", int(lb),
                                       min_value=1, max_value=36, step=1))
        # Suppress the top_pct slider when a regime overlay always overrides it
        # (every rule sets its own top_pct or parks) — it would be a dead knob.
        regime = self.spec.get("regime")
        rules = (regime or {}).get("rules", [])
        top_pct_dead = bool(regime and rules and all(
            ("top_pct" in r or r.get("park") or r.get("default")) for r in rules))
        if "top_pct" in sel and not top_pct_dead:
            specs.append(ParameterSpec("top_pct", "Top Percentile", "float",
                                       float(sel["top_pct"]),
                                       min_value=0.01, max_value=0.50, step=0.01))
        if "top_n" in sel:
            specs.append(ParameterSpec("top_n", "Number of Stocks", "int",
                                       int(sel["top_n"]), min_value=1, max_value=100, step=1))
        return specs

    def generate_signals(self, shared_data: dict, params: dict):
        spec = self.spec
        uni = spec.get("universe", {})
        sel = spec.get("selection", {})

        ret = shared_data["ret"]
        adtv = shared_data["adtv"]
        raw_close = shared_data["raw_close"]

        min_adtv = float(params.get("min_adtv", uni.get("min_adtv", 1_000_000)))
        min_price = float(params.get("min_price", uni.get("min_price", 1.0)))
        min_names = int(sel.get("min_names", 5))
        min_hold = int(sel.get("min_hold", min_names))   # floor on the held count
        top_pct = params.get("top_pct", sel.get("top_pct"))
        top_n = params.get("top_n", sel.get("top_n"))
        sticky = bool(sel.get("sticky", False))
        dedup_roots = bool(sel.get("dedup_roots", False))
        vrange = spec.get("signal", {}).get("range")
        smallcap = bool(uni.get("smallcap_below_median", False))
        weighting = spec.get("weighting", "equal")
        wframe = _weight_frame(spec, params, shared_data)
        sector_ok = sector_filter(uni.get("sectors"), shared_data, ret.index)

        signal, max_lb = _build_signal(spec, params, shared_data)
        start = int(spec.get("warmup", max_lb + int(spec.get("warmup_pad", 1))))
        start = max(start, 1)

        tw = pd.DataFrame(0.0, index=ret.index, columns=ret.columns)
        r = ret.copy()

        # Optional macro-regime overlay: gate the equity book on a regime flag,
        # parking in CDI (and holding IBOV as an available asset) when "off".
        regime = spec.get("regime")
        cash = None
        if regime:
            cash = "CDI_ASSET"
            r[cash] = shared_data["cdi_monthly"]
            if regime.get("add_ibov", True):
                r["IBOV"] = shared_data["ibov_ret"]
            tw[cash] = 0.0
            park_few = bool(regime.get("park_when_too_few", True))
            cash_loc = tw.columns.get_loc(cash)

        prev_sel: list = []
        for i in range(start, len(ret)):
            eff_top_pct = top_pct
            if regime:
                action = _eval_regime(regime, shared_data, i, ret.index)
                if action is None or action.get("park"):
                    tw.iloc[i, cash_loc] = 1.0
                    continue
                eff_top_pct = action.get("top_pct", top_pct)

            adtv_r = adtv.iloc[i - 1]
            raw_r = raw_close.iloc[i - 1]
            # A fundamentals factor covers more tickers than the traded universe
            # (f_pe_ratio_dyn spans every share class); align to the universe so
            # the liquidity mask applies and the extras drop out.
            sig_row = signal.iloc[i - 1].reindex(adtv_r.index)

            mask = (adtv_r >= min_adtv) & (raw_r >= min_price) & sector_ok.iloc[i - 1]
            valid = sig_row[mask].dropna()
            valid = valid[np.isfinite(valid)]
            if vrange is not None:
                valid = valid[(valid >= vrange[0]) & (valid <= vrange[1])]
            if smallcap:
                liq_adtv = adtv_r[valid.index]
                valid = valid[liq_adtv <= liq_adtv.median()]
            if dedup_roots:
                # With the market: B3's four-character root would merge GOOG into
                # GOOGL and BTCUSDT into BTCUSDC. No-ops where there are no roots.
                valid = keep_most_liquid_per_root(valid, adtv_r, self.market)
            if len(valid) < min_names:
                # `sticky`: carry the previous book through a thin month rather
                # than going flat (volume_breakout's prev_sel fallback).
                if not (sticky and prev_sel):
                    if regime and park_few:
                        tw.iloc[i, cash_loc] = 1.0
                    continue
                selected = prev_sel
            else:
                n = int(top_n) if top_n else max(min_hold, int(len(valid) * eff_top_pct))
                selected = valid.nlargest(n).index.tolist()
            prev_sel = selected
            w = self._weights(selected, adtv_r, wframe, i, weighting, spec.get("max_weight"))
            for t, wt in w.items():
                if t in tw.columns:
                    tw.iloc[i, tw.columns.get_loc(t)] = wt

        return r, only_in_months(tw, spec.get("rebalance_months"))

    @classmethod
    def _weights(cls, selected, adtv_r, wframe, i, scheme, max_weight=None) -> dict:
        """The book's weights; ``max_weight`` caps each name and spreads the
        excess over the rest (equal weight when the cap cannot be met)."""
        w = cls._scheme_weights(selected, adtv_r, wframe, i, scheme)
        if not max_weight:
            return w
        return cap_and_renormalize(pd.Series(w, dtype=float), float(max_weight)).to_dict()

    @staticmethod
    def _scheme_weights(selected, adtv_r, wframe, i, scheme) -> dict:
        # "market_cap" is a legacy alias for "adtv" and weights by liquidity, as
        # it always did — this engine has no share counts, so it never saw a cap.
        # The word is not free: sp500_b3/top_mcap weight by a real point-in-time
        # market cap under it, so a spec author reading either could reasonably
        # expect the other's behaviour. Kept accepted because user specs live in
        # a tenant database this repo cannot grep and rewrite.
        if scheme in ("adtv", "market_cap"):
            size = adtv_r[selected].clip(lower=0)
            tot = size.sum()
            if tot > 0:
                return (size / tot).to_dict()
        # "expr": proportional to weight_expr's value, read at the signal's row.
        # A held name it scores NaN or below zero gets nothing; if none score
        # above zero, the book falls back to equal weight rather than holding cash.
        if scheme == "expr" and wframe is not None:
            size = (wframe.iloc[i - 1].reindex(selected).replace([np.inf, -np.inf], np.nan)
                    .clip(lower=0).fillna(0.0))
            tot = size.sum()
            if tot > 0 and np.isfinite(tot):
                return (size / tot).to_dict()
        # equal (default) and fallback
        w = 1.0 / len(selected)
        return {t: w for t in selected}


# ── Fixed-weight blend engine ─────────────────────────────────────────────────

class FixedWeight(_SpecStrategy):
    """Constant-weight sleeves rebalanced on the grid. Sleeves are B3 tickers
    (read off the plane), the special asset ``CDI``, or another registered
    ``strategy`` (its whole book becomes one sleeve). User portfolios compile to
    the same three forms (platform strategy_specs), so they run through here too."""

    def get_parameter_specs(self) -> list[ParameterSpec]:
        return [
            COMMON_START_DATE, COMMON_END_DATE,
            ParameterSpec("rebalance_freq", "Rebalance Frequency", "choice",
                          self.spec.get("rebalance", "QE"), choices=["ME", "QE", "W-FRI"]),
        ]

    def generate_signals(self, shared_data: dict, params: dict):
        spec = self.spec
        park = bool(spec.get("park_in_cdi_until_live", True))
        # per_sleeve: park each dead ETF's weight in CDI individually (divo style).
        # all_or_nothing: hold nothing until every ETF is live, else 100% CDI
        # (baseline_thirds style).
        park_mode = spec.get("park_mode", "per_sleeve")
        r = shared_data["ret"].copy()
        r["CDI_ASSET"] = shared_data["cdi_monthly"]
        tw = pd.DataFrame(0.0, index=r.index, columns=r.columns)

        etf_cols = []
        for sleeve in spec["sleeves"]:
            weight = float(sleeve["weight"])
            if sleeve.get("asset") == "CDI":
                tw["CDI_ASSET"] += weight
            elif "strategy" in sleeve:
                _nest_sleeve(sleeve["strategy"], weight, shared_data, params, r, tw)
            else:
                etf_cols.append(_ticker_sleeve(sleeve["ticker"], weight, shared_data, r, tw,
                                               park and park_mode == "per_sleeve"))

        if park and park_mode == "all_or_nothing" and etf_cols:
            live = (r[etf_cols] != 0).all(axis=1).cummax()   # baseline_thirds.py:70-71
            tw.loc[~live, etf_cols + ["CDI_ASSET"]] = 0.0
            tw.loc[~live, "CDI_ASSET"] = 1.0

        # Park any residual on live rows into CDI so weights always sum to 1.
        # A nested `strategy` sleeve emits all-zero weights during its own warmup
        # (fundamentals/EWMA), leaving its share unallocated; run_simulation does
        # NOT hold uninvested cash (NAV = sum of positions, simulation.py:347-350),
        # so that share would be vaporized every rebalance — compounding a fake
        # drawdown (a 50/50 blend bled 100k -> 26k through the sleeve warmup).
        live_rows = tw.abs().sum(axis=1) > 0
        resid = (1.0 - tw.sum(axis=1)).clip(lower=0.0)
        tw.loc[live_rows, "CDI_ASSET"] = tw.loc[live_rows, "CDI_ASSET"] + resid[live_rows]

        return r, only_in_months(tw, spec.get("rebalance_months"))


def _nest_sleeve(name: str, weight: float, shared_data: dict, params: dict, r, tw) -> None:
    """Run a registered strategy, scale its book by `weight`, fold it into the blend
    in place. Its stocks and CDI_ASSET already share the blend's return matrix; a
    column that does not (a nested portfolio's ETF) takes the nested run's returns."""
    from backtests.core.strategy_registry import get_registry
    sub = get_registry().get(name)
    r_sub, tw_sub = sub.generate_signals(shared_data, {**sub.get_default_parameters(), **params})
    if getattr(sub, "spec", {}).get("rebalance_months"):
        # Its all-zero months mean "hold", not "nothing": carry its last book
        # forward, or the blend would park this sleeve's share in CDI on them.
        live = tw_sub.abs().sum(axis=1) > 0
        tw_sub = tw_sub[live].reindex(tw_sub.index).ffill().fillna(0.0)
    # For warmup.late_books: a sleeve still warming up hides behind CDI.
    shared_data.setdefault("_sleeve_books", {})[name] = tw_sub
    for col in tw_sub.columns:
        if col not in tw.columns:
            tw[col] = 0.0
        if col not in r.columns:
            r[col] = r_sub[col].reindex(r.index).fillna(0.0) if col in r_sub else 0.0
        tw[col] = tw[col] + weight * tw_sub[col].reindex(tw.index).fillna(0.0)


def _ticker_sleeve(ticker: str, weight: float, shared_data: dict, r, tw, park: bool) -> str:
    """One listed instrument as a sleeve, folded into ``r``/``tw`` in place; returns
    its column. With ``park``, the weight sits in CDI on rows before its first price."""
    from backtests.core.data import download_benchmark
    col = ticker.split(".")[0]
    px_daily = download_benchmark(ticker, str(r.index[0].date()), str(r.index[-1].date()))
    # Daily returns for metrics.strategy_daily_values to mark this sleeve
    # intra-period; the rebalance-cadence `r[col]` can't reveal its drawdown.
    shared_data.setdefault("_daily_asset_ret", {})[col] = px_daily.pct_change()
    px = px_daily.reindex(r.index, method="ffill")
    r[col] = px.pct_change().fillna(0.0)
    # Added, not assigned: a nested strategy sleeve may already hold this name,
    # and assigning would drop that share of the book without a word.
    tw[col] = (tw[col] if col in tw.columns else 0.0) + weight
    if park:
        dead = px.isna()
        tw.loc[dead, col] -= weight
        tw.loc[dead, "CDI_ASSET"] += weight
    return col
