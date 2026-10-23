"""
Generic backtest simulation engine.
"""

import math

import pandas as pd

from backtests.core import tax as taxmod


def _apply_returns(
    positions: dict, ret_row: pd.Series, max_ret: float = 5.0, min_ret: float = -0.90
) -> None:
    """Apply returns to all open positions, clipped against data artifacts.

    max_ret guards artifacts (unadjusted reverse splits as +N00%); real periods
    below +500% pass uncapped, and glitch filtering belongs upstream. min_ret
    guards the same downward — but -100% is not an artifact, it is a write-off,
    and clipping it hands a tenth of a wiped-out position back forever.
    """
    for t, pos in positions.items():
        if t not in ret_row.index:
            r = 0.0
        else:
            try:
                r = float(ret_row.loc[t])
                if math.isnan(r):
                    r = 0.0
            except (ValueError, TypeError):
                r = 0.0

        r = -1.0 if r <= -1.0 else min(max(r, min_ret), max_ret)

        # If it is a short position (negative current value), apply a forced Stop Loss.
        # If the stock goes up more than 50% in a single period, the broker forcefully
        # closes the short position. We simulate this by hard-capping the short loss.
        if pos["current_value"] < 0 and r > 0.50:
            r = 0.50

        pos["current_value"] *= 1.0 + r


def compute_trades(
    positions: dict,
    target_weights: pd.Series,
    nav: float,
) -> tuple[dict[str, float], dict[str, float]]:
    """Pure rebalance diff: BRL to sell and buy per ticker to hit target weights.

    Args:
        positions: current ledger, {ticker: {"cost_basis", "current_value"}}.
        target_weights: target weight per ticker for this rebalance row.
        nav: portfolio value to rebalance to (may include a pending buy-in).

    Returns:
        (sells, buys) in BRL.
          sells[t] = cash to raise by selling a long OR covering a short — the
                     amount that must go through the tax engine.
          buys[t]  = cash to deploy into a long, OR (negative) capital to open /
                     extend a short (never taxed: opening a position realizes no
                     gain).

    Routing follows the *current* sign of the holding, not whether it is held:
      • diff < 0 while flat or short opens / deepens a short: a negative *buy*,
        never taxed. (As a sell it read as a full cover and dropped the short.)
      • diff > 0 while short is a *sell*: covering realizes taxable P&L.
      • crossing zero is close-then-open: the whole position through sells,
        the new side through buys.
    Pure: no mutation of `positions`. The order is not irrelevant, though: the
    caller sums over these dicts and opens positions in their order, and float
    addition is not associative. A set here made a run's last bits depend on the
    process's string hash seed — held book first, then new targets, in order.
    """
    sells: dict[str, float] = {}  # {ticker: cash_to_raise}
    buys: dict[str, float] = {}   # {ticker: cash_to_deploy}

    all_tickers = dict.fromkeys([*positions, *target_weights[target_weights != 0].index])
    for t in all_tickers:
        current_val = positions.get(t, {}).get("current_value", 0.0)
        target_w = target_weights.get(t, 0.0)
        if pd.isna(target_w):
            target_w = 0.0

        target_val = nav * target_w
        diff = target_val - current_val

        if diff < -0.01:            # less exposure: sell a long, or open/deepen a short
            if current_val > 0:
                sells[t] = current_val if target_val < 0 else abs(diff)
                if target_val < 0:
                    buys[t] = target_val    # flip: close the long, open the short
            else:
                buys[t] = diff              # negative capital, never taxed
        elif diff > 0.01:           # more exposure: buy a long, or cover a short
            if current_val < 0:
                sells[t] = abs(current_val) if target_val > 0 else diff
                if target_val > 0:
                    buys[t] = target_val    # flip: cover the short, open the long
            else:
                buys[t] = diff

    return sells, buys


def _execute_rebalance(
    positions: dict,
    target_weights: pd.Series,
    portfolio_nav: float,
    slippage: float,
    ledger: "taxmod.Ledger",
    when,
) -> tuple[float, float, float]:
    """
    Execute a strict rebalance to match target weights.
    Returns (tax_paid, cash_drag, turnover)

    `tax_paid` is the bill for the apuração period that just closed, not for
    this row's sells — the period is the unit, and it cannot be settled until it
    is over. See backtests.core.tax.
    """
    # 1. Identify current weights and determine buys/sells (pure diff)
    sells, buys = compute_trades(positions, target_weights, portfolio_nav)
    ledger.realise_write_offs(positions)

    # Turnover is the gross BRL traded: every sell is positive, every buy value is
    # positive for a long and negative for an opened short — abs() folds both.
    # (Same total as the original in-loop accumulation; a pure sum, order-invariant.)
    turnover_cash = sum(sells.values()) + sum(abs(v) for v in buys.values())

    # 2. Book this row and collect whatever the closed period owes.
    tax_paid = ledger.accrue(when, sells, positions)

    # We must pay tax out of the cash we raised. If there isn't enough cash,
    # we simulate an automatic drag on the portfolio NAV.
    # We also deduct slippage from all trades.
    cash_drag = tax_paid + turnover_cash * slippage

    # 3. Update the Ledger
    for t, sell_amount in sells.items():
        pos = positions[t]

        # Determine the fraction sold based on whether it is a long or short position
        if pos["current_value"] > 0:
            fraction_sold = min(1.0, sell_amount / pos["current_value"])
        elif pos["current_value"] < 0:
            # We are covering a short. "sell_amount" is positive here representing the cash required to cover.
            fraction_sold = min(1.0, sell_amount / abs(pos["current_value"]))
        else:
            fraction_sold = 1.0

        if fraction_sold >= 0.999:
            del positions[t]
        else:
            if pos["current_value"] > 0:
                pos["current_value"] -= sell_amount
            else:
                # Covering a short moves current value closer to 0
                pos["current_value"] += sell_amount

            pos["cost_basis"] -= pos["cost_basis"] * fraction_sold

    # Deploy buys (after reducing them proportionately by cash_drag if we are fully invested)
    # If the portfolio is not 100% invested, this cash drag is just a reduction in NAV.
    # To keep it mathematically simple: we scale all buys down slightly so we don't over-leverage to pay taxes.
    total_buy_cash = sum(v for v in buys.values() if v > 0)
    buy_adjustment_factor = 1.0
    residual_drag = 0.0
    if cash_drag > 0:
        if cash_drag < total_buy_cash:
            buy_adjustment_factor = (total_buy_cash - cash_drag) / total_buy_cash
        else:
            buy_adjustment_factor = 0.0  # Taxes wiped out all buy liquidity
            residual_drag = cash_drag - total_buy_cash

    for t, buy_amount in buys.items():
        # Only long buys spend cash and absorb the drag; shrinking a short with
        # them handed the drag back to NAV (a smaller short is worth more).
        actual_deploy = buy_amount * buy_adjustment_factor if buy_amount > 0 else buy_amount
        if t not in positions:
            positions[t] = {"cost_basis": actual_deploy, "current_value": actual_deploy}
        else:
            positions[t]["cost_basis"] += actual_deploy
            positions[t]["current_value"] += actual_deploy

    # Any drag not absorbed by scaling down buys must still be paid — deduct it
    # from NAV via a proportional reduction across the final positions
    # (previously the excess silently vanished).
    if residual_drag > 0:
        nav_after = sum(p["current_value"] for p in positions.values())
        if nav_after > 0:
            scale = max(0.0, 1.0 - residual_drag / nav_after)
            for p in positions.values():
                p["current_value"] *= scale

    turnover_pct = turnover_cash / max(portfolio_nav, 1.0)

    return tax_paid, cash_drag, turnover_pct


def run_simulation(
    returns_matrix: pd.DataFrame,
    target_weights: pd.DataFrame,
    initial_capital: float = 100_000,
    tax_rate: float = 0.15,
    slippage: float = 0.0,
    name: str = "Strategy",
    monthly_sales_exemption: "float | None" = None,
    tax_period: str = taxmod.MONTHLY,
    loss_carry: str = taxmod.FORWARD,
    contribution: "float | pd.Series" = 0.0,
    asset_classes: "str | None" = None,
) -> dict:
    """
    Run a generic, institutional-grade portfolio simulation.

    Args:
        returns_matrix: DataFrame of asset returns (rows=dates, cols=tickers)
        target_weights: DataFrame of target weights (rows=dates, cols=tickers).
                       Rows must sum to 1.0 (or >1.0 if using negative leverage).
        initial_capital: Starting BRL
        tax_rate: Capital gains tax rate applied to net realized gains
        monthly_sales_exemption: gross sales below which a **calendar month** is
                       isento. None means the class has no isenção at all, which
                       is not the same as zero — see backtests.core.tax.Rule.
        tax_period: "mensal" or "anual". Exterior (Lei 14.754/2023) nets the
                       whole year and settles once, in the ajuste.
        loss_carry: "forward", "period" or "none". Ganho de capital (cripto)
                       compensates nothing; exterior nets inside the year and
                       carries nothing across it.
        asset_classes: "B3" apurates per asset class (tax.market_ledger); None, one line.
        slippage: Transaction cost applied per trade (e.g., 0.001 for 0.1%)
        contribution: Periodic buy-in in BRL (aporte). Two forms:
                     • scalar — a flat amount **per calendar month**; deposits
                       accrue by calendar month and land on the next live
                       rebalance row (1x for ME, 3x for QE, first weekly row of
                       each month for W-FRI).
                     • pandas Series indexed by date — real dated cash flows
                       (the what-if replay). At each live rebalance the engine
                       deploys every flow that has arrived and not yet been
                       deployed (cumulative-minus-deployed), so pre-inception
                       and hold-row flows land on the next live row — same
                       "deploy at the rebalance" approximation as the scalar
                       path. Pair with initial_capital=0 for a pure flow replay.
                     Deposited into NAV right before the rebalance, so the
                     existing target-weight logic spreads it over the book —
                     the name farthest below target absorbs the most new cash.
                     Negative = withdrawal.

    Returns:
        Dictionary containing pre-tax and after-tax equity curves, tax ledgers, etc.
    """
    has_flows = (
        bool((contribution > 0).any()) if isinstance(contribution, pd.Series)
        else contribution > 0
    )
    if initial_capital <= 0 and not has_flows:
        raise ValueError(
            "No money in the portfolio: set initial_capital > 0, contribution > 0, "
            "or both (initial_capital=0 with a contribution is a pure-DCA run)."
        )

    pt_pos, at_pos = {}, {}  # pre-tax and after-tax positions

    ledger = taxmod.market_ledger(tax_rate, monthly_sales_exemption, tax_period,
                                  loss_carry, asset_classes)
    pt_ledger = taxmod.free_ledger()

    pretax_values, aftertax_values = [], []
    tax_paid_list, loss_cf_list, turnover_list, dates = [], [], [], []
    contrib_list = []

    initialized = False
    last_deploy = None      # date of the last rebalance that received a deposit
    deployed = 0.0          # BRL of dated flows already deployed (Series path only)

    # Contract guard: NAV is defined as the sum of position values and this engine
    # does NOT hold uninvested cash. So any live rebalance row whose target weights
    # sum to < 1 has that gap silently dropped from NAV — a fake drawdown that
    # compounds every rebalance. Warn loudly rather than fail silently; strategies
    # must park the residual in CDI_ASSET. (Rows that sum to 0 are held; rows > 1
    # are the separate leverage case, not flagged here.)
    _row_sums = target_weights.fillna(0.0).sum(axis=1)
    _under = (_row_sums > 1e-9) & (_row_sums < 1.0 - 1e-4)
    if bool(_under.any()):
        _off = _under[_under].index
        print(f"\n⚠️  {name}: {len(_off)} live rebalance row(s) with target weights "
              f"summing < 1 (first {_off[0].date()}, sum={_row_sums.loc[_off[0]]:.3f}); "
              f"run_simulation drops that uninvested weight from NAV (fake drawdown). "
              f"Park the residual in CDI_ASSET.")

    for i in range(len(returns_matrix)):
        date = returns_matrix.index[i]

        # 1. Look up the Target Weights for THIS period
        # Target weights should have been calculated using data from i-1 (no lookahead)
        t_weights = target_weights.iloc[i].fillna(0.0)

        # Before inception, an all-zero row means the strategy hasn't started yet
        if t_weights.abs().sum() == 0 and not initialized:
            continue

        ret_row = returns_matrix.iloc[i]

        if not initialized:
            for t, w in t_weights.items():
                if w != 0:
                    # Charge slippage on the initial deployment (turnover = 1.0)
                    alloc = initial_capital * w * (1.0 - slippage)
                    pt_pos[t] = {"cost_basis": alloc, "current_value": alloc}
                    at_pos[t] = {"cost_basis": alloc, "current_value": alloc}

            initialized = True
            last_deploy = date

            nav0 = sum(p["current_value"] for p in pt_pos.values())
            pretax_values.append(nav0)
            aftertax_values.append(nav0)
            tax_paid_list.append(0.0)
            loss_cf_list.append(0.0)
            turnover_list.append(1.0)
            contrib_list.append(0.0)
            dates.append(date)
            continue

        # ── Step 1: Apply Returns ────
        _apply_returns(pt_pos, ret_row)
        _apply_returns(at_pos, ret_row)

        pt_nav = sum(p["current_value"] for p in pt_pos.values())
        at_nav = sum(p["current_value"] for p in at_pos.values())

        # After inception, an all-zero target row means "no valid targets this
        # period" — hold current positions. Rebalancing to zero would liquidate
        # everything, and since sale proceeds aren't tracked as cash
        # (NAV = sum of position values), NAV would collapse to 0 and fake a
        # margin call on the next step.
        live = bool(t_weights.abs().sum() != 0)

        # ── Step 1b: Periodic buy-in (aporte) ────
        # Whole calendar months elapsed since the last deposit — exact for ME/QE
        # and lumpy-but-realistic for weekly (lands on the first row of a month).
        # On a hold row there is no rebalance to ride, so nothing is deposited and
        # last_deploy stays put: the months accrue and land on the next live row.
        # ponytail: the cash is deployed at the rebalance, not the day it arrives;
        # a quarterly book gets 3 months in one go instead of sitting in cash.
        # A book that has already gone negative is bankrupt — check BEFORE the
        # deposit so a buy-in can't resurrect it (a wiped-out short book would
        # otherwise reappear at a fake NAV). Exactly 0.0 is the never-funded
        # case (initial_capital=0, pure DCA), which the post-deposit check below
        # still catches if no cash arrives.
        if pt_nav < 0 or at_nav < 0:
            print(f"\n💀 MARGIN CALL! Portfolio went bankrupt on {date.date()}")
            break

        cash = 0.0
        if contribution is not None and live:
            if isinstance(contribution, pd.Series):
                # Dated flows: deploy everything that has arrived (index <= date)
                # and not yet been deployed. Cumulative-minus-deployed captures
                # pre-inception and hold-row flows on the next live rebalance —
                # no window edge to lose a flow on.
                cash = float(contribution[contribution.index <= date].sum()) - deployed
            else:
                months = (date.year - last_deploy.year) * 12 + date.month - last_deploy.month
                cash = contribution * months
        if cash:
            # Handed to _execute_rebalance as extra NAV: every position's
            # (target_val - current_value) gap widens by w_i * cash, so the
            # underweight names take the biggest share of the new money.
            pt_nav += cash
            at_nav += cash
            last_deploy = date
            deployed += cash

        if pt_nav <= 0 or at_nav <= 0:
            print(f"\n💀 MARGIN CALL! Portfolio went bankrupt on {date.date()}")
            break

        if not live:
            pretax_values.append(pt_nav)
            aftertax_values.append(at_nav)
            tax_paid_list.append(0.0)
            loss_cf_list.append(ledger.carry)
            turnover_list.append(0.0)
            contrib_list.append(0.0)
            dates.append(date)
            continue

        # ── Step 2: Strict Rebalance (Pre-Tax Ledger: Zero Tax) ──
        # The pre-tax ledger runs the same rebalance with a zero-rate ledger,
        # so it still drops its write-offs. Its drag and turnover are the
        # after-tax book's to report.
        _execute_rebalance(pt_pos, t_weights, pt_nav, slippage=slippage,
                           ledger=pt_ledger, when=date)

        # ── Step 3: Strict Rebalance (After-Tax Ledger: Real Tax) ──
        tax_paid, _, turnover = _execute_rebalance(
            at_pos, t_weights, at_nav, slippage=slippage,
            ledger=ledger, when=date,
        )

        pt_final_nav = sum(p["current_value"] for p in pt_pos.values())
        at_final_nav = sum(p["current_value"] for p in at_pos.values())

        # ── Step 4: Record state ───────────────────────────────────────
        pretax_values.append(pt_final_nav)
        aftertax_values.append(at_final_nav)
        tax_paid_list.append(tax_paid)
        loss_cf_list.append(ledger.carry)
        turnover_list.append(turnover)
        contrib_list.append(cash)
        dates.append(date)

    # The last period never had a following rebalance to be charged on, so it
    # is settled here. Folded into tax_paid as well as NAV: the old deferred
    # path deducted it from the curve and forgot the total, so `total_tax_paid`
    # in metrics.json came up short by exactly the final month.
    final_tax = ledger.close()
    if final_tax and aftertax_values:
        aftertax_values[-1] -= final_tax
        tax_paid_list[-1] += final_tax
        loss_cf_list[-1] = ledger.carry

    idx = pd.DatetimeIndex(dates)
    contribs = pd.Series(contrib_list, index=idx, name="Contribution (BRL)")
    return {
        "pretax_values": pd.Series(pretax_values, index=idx, name=f"{name} (Pre-Tax)"),
        "aftertax_values": pd.Series(
            aftertax_values, index=idx, name=f"{name} (After-Tax)"
        ),
        "tax_paid": pd.Series(tax_paid_list, index=idx, name="Tax Paid (BRL)"),
        "loss_carryforward": pd.Series(
            loss_cf_list, index=idx, name="Loss Carryforward (BRL)"
        ),
        "turnover": pd.Series(turnover_list, index=idx, name="Turnover"),
        # Cash added per rebalance, and the running cost basis of the whole book.
        # NAV includes deposits, so returns must be computed with
        # metrics.value_to_ret(values, contributions=...) — a raw pct_change reads
        # every buy-in as a gain.
        "contributions": contribs,
        "invested": (initial_capital + contribs.cumsum()).rename("Invested (BRL)"),
    }
