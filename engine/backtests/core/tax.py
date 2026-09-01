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

from backtests.core import universe

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
    """One market's apuração.

    `exemption` is `None` when the class has none, and that is not the same as
    zero: a class with no isenção and a class whose isenção happens to be zero
    behave differently on a period with no sales at all, and only one of them is
    real. `apuracao.Regra` makes the same distinction for the same reason, and
    collapsing it to `exemption > 0` is what the old code did.
    """

    rate: float
    exemption: float | None
    period: str = MONTHLY
    carry: str = FORWARD


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
    """

    def __init__(self, rule: Rule):
        self.rule = rule
        self._key = _KEY[rule.period]
        self._open = None
        self._sales = self._gain = self._loss = 0.0
        self._carry = 0.0

    @property
    def carry(self) -> float:
        return self._carry

    def realise_write_offs(self, positions: dict) -> None:
        """Drop wiped-out positions and book the loss in the open period.

        Into the period, not straight into the carry. A write-off is a loss
        realised on a date, so it nets against gains in the same period even for
        a class that carries nothing forward — which is what `monthly_apuracao`
        does, and what the old code (which added it to the carry regardless)
        gave crypto for free.
        """
        self._loss += universe.realise_write_offs(positions)

    def accrue(self, when, sales: dict, positions: dict) -> float:
        """Book one rebalance. Returns the tax now due for the previous period."""
        key = self._key(when)
        due = 0.0
        if self._open is not None and key != self._open:
            due = self._settle()
        self._open = key

        gross, gain, loss = realised(sales, positions)
        self._sales += gross
        self._gain += gain
        self._loss += loss
        return due

    def close(self) -> float:
        """Settle whatever period is still open. The end of the run."""
        return self._settle() if self._open is not None else 0.0

    def _settle(self) -> float:
        """The apuração itself, transcribed from `monthly_apuracao`."""
        pnl = self._gain - self._loss
        rule = self.rule
        isento = rule.exemption is not None and self._sales <= rule.exemption

        tax = 0.0
        if isento:
            # An isento gain is free and does not consume the carry. An isento
            # *loss* still feeds it — the old code returned early here and
            # dropped it, which quietly overstated the tax on the next gain.
            if pnl < 0 and rule.carry != NONE:
                self._carry += -pnl
        elif pnl > 0:
            compensacao = min(self._carry, pnl) if rule.carry != NONE else 0.0
            self._carry -= compensacao
            tax = rule.rate * (pnl - compensacao)
        elif pnl < 0 and rule.carry != NONE:
            self._carry += -pnl

        if rule.carry == PERIOD:
            # Lei 14.754: the year nets and closes. Nothing crosses into the next.
            self._carry = 0.0

        self._open = None
        self._sales = self._gain = self._loss = 0.0
        return tax


def free_ledger() -> Ledger:
    """The pre-tax ledger: same mechanics, no tax.

    A real Ledger rather than a branch, so the pre-tax book still drops its
    write-offs and the two ledgers stay the same shape.
    """
    return Ledger(Rule(rate=0.0, exemption=None, period=MONTHLY, carry=NONE))
