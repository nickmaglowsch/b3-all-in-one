"""What the holder owes, and when — the apuração the simulator runs on.

Split out of `simulation` because a tax rate is not the whole of a tax rule.
Three things differ between the markets this bench trades, and only the first
was modelled:

  ações      15%, isento up to R$20k of gross sales **in a calendar month**,
             losses carried forward without limit
  cripto     ganho de capital: same first band, R$35k isenção, and **no loss
             compensation at all** — a crypto loss is simply lost, which is the
             single most surprising rule in the whole table
  exterior   Lei 14.754/2023: 15%, no isenção and no monthly DARF. Gains and
             losses net across **the whole calendar year** and settle once, in
             the ajuste anual, and the excess loss does not become a carry

`taxcore/scripts/apuracao.py` is the authority; this is a deliberate copy,
because the engine and taxcore run in separate venvs and cannot import each
other. `test_markets.py` is the seam that catches a divergence, and it now pins
`carry` and `periodo` as well as the rate and the isenção — those two being
exactly the fields whose divergence the old comments in `markets.py` admitted to.

## The period is the unit, not the rebalance row

This is the change with teeth. The isenção is a threshold on a **month's** gross
sales, and the simulator used to apply it to whatever one rebalance row happened
to sell: on a quarterly cadence a quarter of sales was measured against a
monthly ceiling, and on a weekly one a book could sell R$19k every Friday and
never pay a cent. So a period accrues, and settles when it closes — which also
means the bill lands on the first rebalance of the following period rather than
inside the row that caused it. That is what a DARF does: month M is due at the
end of M+1, and the ajuste anual is paid the following year.

A consequence worth stating plainly: **every stored B3 backtest's after-tax
curve moves.** It has to. Both the timing and the isenção were wrong before.
"""

from dataclasses import dataclass

from backtests.core import asset_classes, universe

# The three carry policies, which are not two booleans: `carry=True` in taxcore
# means different things under a monthly rule and an annual one. For ações the
# bucket survives indefinitely; for exterior `annual_apuracao` nets inside one
# year and explicitly drops what is left over. Naming that here keeps the
# distinction out of a comment.
FORWARD = "forward"     # ações: the carry outlives the period
PERIOD = "period"       # exterior: nets within the year, nothing crosses
NONE = "none"           # cripto: ganho de capital compensates nothing

MONTHLY = "mensal"
ANNUAL = "anual"

_KEY = {MONTHLY: lambda d: (d.year, d.month), ANNUAL: lambda d: (d.year,)}


@dataclass(frozen=True)
class Rule:
    """One apuração line: a market's, or one asset class's within a market.

    `exemption` is `None` when the class has none, and that is not the same as
    zero: a class with no isenção and a class whose isenção happens to be zero
    behave differently on a period with no sales at all, and only one of them is
    real. `apuracao.Regra` makes the same distinction for the same reason, and
    collapsing it to `exemption > 0` is what the old code did.

    `pool` is the loss bucket the line shares with other lines — taxcore's
    `Regra.grupo`. None is a bucket of its own.
    """

    rate: float
    exemption: float | None
    period: str = MONTHLY
    carry: str = FORWARD
    pool: str | None = None


# ── B3: one apuração, three lines ─────────────────────────────────────────────
# taxcore `apuracao.REGRAS` apurates a B3 month on separate lines, and the bench
# used to put every sale on the ações one — so an ETF blend selling under R$20k a
# month paid nothing, when by law an ETF sale has no isenção at all. The lines,
# as taxcore has them (test_asset_classes runs taxcore's monthly_apuracao against
# this ledger on the same months):
#
#   AÇÃO     15%, isento when the month's *ações* sales are <= R$20k  } operações
#   BDR_ETF  15%, no isenção                                          } comuns: one
#   FII      20%, no isenção, a loss pool of its own                    loss pool
#
# Renda fixa — the engine's own CDI_ASSET and an ETF de renda fixa (LFTB11) —
# stays on the ações line, which is where the bench has always booked it. taxcore
# leaves it out of the apuração (it is taxed at source); modelling that is its
# own change, and keeping it here means a book of only ações and CDI scores
# exactly as it did before the lines existed. The `IBOV` column a regime overlay
# holds (an index, unknown to taxcore) falls on the ações line the same way, and
# a *11 on none of taxcore's lists is a FII, as taxcore presumes it.
FII_RATE = 0.20
_B3_LINE = {"AÇÃO": "AÇÃO", "ETF_RF": "AÇÃO", "ETF_RV": "BDR_ETF", "BDR": "BDR_ETF",
            "FII": "FII"}


def b3_line(ticker: str) -> str:
    """The apuração line a B3 column's sales land on."""
    return _B3_LINE[asset_classes.b3_class(ticker)]


def b3_rules(rate: float, exemption: float | None, period: str = MONTHLY,
             carry: str = FORWARD) -> dict:
    """B3's lines. `rate` — the market's, a what-if knob — sets the two 15% lines;
    the FII line keeps its own 20%, which is the law rather than a choice."""
    return {
        "AÇÃO": Rule(rate, exemption, period, carry, pool="COMUNS"),
        "BDR_ETF": Rule(rate, None, period, carry, pool="COMUNS"),
        "FII": Rule(FII_RATE, None, period, carry),
    }


def market_ledger(tax_rate: float, monthly_sales_exemption: float | None,
                  tax_period: str, loss_carry: str,
                  asset_classes: str | None = None) -> "Ledger":
    """The after-tax ledger run_simulation keeps: per asset class on a market that
    splits its apuração by class (`asset_classes="B3"`), one line otherwise."""
    if asset_classes == "B3":
        return Ledger(b3_rules(tax_rate, monthly_sales_exemption, tax_period, loss_carry),
                      classify=b3_line)
    if asset_classes is not None:
        raise ValueError(f"no asset-class scheme {asset_classes!r}")
    return Ledger(Rule(rate=tax_rate, exemption=monthly_sales_exemption,
                       period=tax_period, carry=loss_carry))


def realised(sales: dict, positions: dict, *,
             ignore_short_losses: bool = True) -> tuple:
    """(gross sales, gross gain, gross loss) for one rebalance's sells.

    Pure: reads the ledger, never writes it. Lifted unchanged from the old
    `_compute_tax` — the arithmetic per position was never the questionable
    part, only what was done with the totals afterwards.
    """
    gross_sales = gross_gain = gross_loss = 0.0
    for ticker, amount_sold in sales.items():
        pos = positions.get(ticker)
        if pos is None or amount_sold <= 0 or pos["current_value"] == 0:
            continue
        gross_sales += amount_sold

        fraction_sold = min(1.0, amount_sold / abs(pos["current_value"]))
        cost_of_sold_portion = pos["cost_basis"] * fraction_sold
        if pos["current_value"] > 0:
            pnl = amount_sold - cost_of_sold_portion
        else:
            # Covering a short: the basis is negative (the cash received).
            pnl = abs(cost_of_sold_portion) - amount_sold

        # A margin/short loss is not deductible against capital gains.
        if ignore_short_losses and cost_of_sold_portion < 0 and pnl < 0:
            continue
        if pnl > 0:
            gross_gain += pnl
        else:
            gross_loss += abs(pnl)
    return gross_sales, gross_gain, gross_loss


class Ledger:
    """One market's apuração across a simulation.

    Accrues each rebalance into the open period and settles when the period
    closes. `accrue` returns the bill for the period that just ended, which is
    zero on every row but the first of a new period.

    Every booking rolls the period first (`_roll`), so a write-off on the first
    row of a month lands in that month — it used to land in the one before,
    because `accrue` was the only thing that rolled and it runs after the
    write-offs. A roll outside `accrue` (a write-off on a hold row) settles the
    closed period into `_due`, and the next payment collects it.

    One `Rule` is one line for every sale (US, crypto, the pre-tax book). A dict
    of rules is a market apurated per asset class, with `classify` naming the
    line each column's sales land on — B3, through `market_ledger`. All lines
    settle together, on one period.
    """

    def __init__(self, rule: "Rule | dict", classify=None):
        self.rules = rule if isinstance(rule, dict) else {"": rule}
        periods = {r.period for r in self.rules.values()}
        if len(periods) != 1:
            raise ValueError(f"one ledger settles on one period, got {sorted(periods)}")
        self._key = _KEY[periods.pop()]
        self._classify = classify or (lambda _ticker: "")
        self._pool = {name: r.pool or name for name, r in self.rules.items()}
        self._carry = dict.fromkeys(self._pool.values(), 0.0)
        self._open = None
        self._due = 0.0         # settled, not yet paid
        self._reset()

    def _reset(self) -> None:
        # Per line: [gross sales, gross gain, gross loss] of the open period.
        self._totals = {name: [0.0, 0.0, 0.0] for name in self.rules}

    @property
    def carry(self) -> float:
        """The prejuízo a compensar, across every loss pool."""
        return sum(self._carry.values())

    def _roll(self, when) -> None:
        """Open the period `when` falls in, settling the one it leaves."""
        key = self._key(when)
        if self._open is not None and key != self._open:
            self._due += self._settle()
        self._open = key

    def realise_write_offs(self, positions: dict, when) -> None:
        """Drop wiped-out positions and book the loss in `when`'s period.

        Into the period, not straight into the carry. A write-off is a loss
        realised on a date, so it nets against gains in the same period even for
        a class that carries nothing forward — which is what `monthly_apuracao`
        does, and what the old code (which added it to the carry regardless)
        gave crypto for free. On the line of the class that was written off.
        With nothing to write off the period is left alone: a hold row only
        touches the ledger when a position died on it.
        """
        # Grouped by line, and booked as each group's one sum: on a single-line
        # ledger that is exactly the old L + (a + b), not (L + a) + b — float
        # addition is not associative, and single-line markets promise the same
        # bytes as before the lines existed.
        dead: dict = {}
        for t in [t for t, p in positions.items() if p["current_value"] == 0]:
            dead.setdefault(self._classify(t), {})[t] = positions.pop(t)
        if dead:
            self._roll(when)
        for name, group in dead.items():
            self._totals[name][2] += universe.realise_write_offs(group)

    def accrue(self, when, sales: dict, positions: dict) -> float:
        """Book one rebalance. Returns the tax now due for the previous period."""
        self._roll(when)
        due, self._due = self._due, 0.0

        by_line: dict = {}
        for t, amount in sales.items():
            by_line.setdefault(self._classify(t), {})[t] = amount
        for name, part in by_line.items():
            totals = self._totals[name]
            for i, v in enumerate(realised(part, positions)):
                totals[i] += v
        return due

    def close(self) -> float:
        """Settle whatever period is still open. The end of the run."""
        due, self._due = self._due, 0.0
        return due + self._settle() if self._open is not None else due

    def _line(self, name: str) -> tuple:
        """(pnl, isento gain) of one line in the closing period — taxcore `_linha`.
        The isenção tests the line's own gross sales, so ETF sales never count
        towards the ações R$20k and never enjoy it."""
        sales, gain, loss = self._totals[name]
        pnl = gain - loss
        exemption = self.rules[name].exemption
        isento = exemption is not None and sales <= exemption
        return pnl, (max(0.0, pnl) if isento else 0.0)

    def _settle(self) -> float:
        """The apuração itself, transcribed from taxcore `monthly_apuracao`.

        Losses first, across each whole pool, so an ETF loss shelters an ação
        gain of the same month (operações comuns). An isento gain is free and
        does not consume the carry; an isento *loss* still feeds it — the old
        code returned early there and dropped it, which quietly overstated the
        tax on the next gain. A class that carries nothing (cripto) neither feeds
        nor spends a pool.
        """
        lines = {name: self._line(name) for name in self.rules}
        for name, (pnl, isento_gain) in lines.items():
            if self.rules[name].carry != NONE:
                self._carry[self._pool[name]] += max(0.0, isento_gain - pnl)
        tax = 0.0
        for name, (pnl, isento_gain) in lines.items():
            tax += self._tax_line(name, max(0.0, pnl - isento_gain))
        for name, rule in self.rules.items():
            if rule.carry == PERIOD:
                # Lei 14.754: the year nets and closes. Nothing crosses into the next.
                self._carry[self._pool[name]] = 0.0
        self._open = None
        self._reset()
        return tax

    def _tax_line(self, name: str, gain: float) -> float:
        rule, pool = self.rules[name], self._pool[name]
        compensacao = min(self._carry[pool], gain) if rule.carry != NONE else 0.0
        self._carry[pool] -= compensacao
        return rule.rate * (gain - compensacao)


def free_ledger() -> Ledger:
    """The pre-tax ledger: same mechanics, no tax.

    A real Ledger rather than a branch, so the pre-tax book still drops its
    write-offs and the two ledgers stay the same shape.
    """
    return Ledger(Rule(rate=0.0, exemption=None, period=MONTHLY, carry=NONE))
