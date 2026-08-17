"""Performance measures, computed the boring correct way.

Two things here that are commonly got wrong.

**Sharpe is annualised by multiplying by the square root of 252**, not by 252.
Returns scale with time; volatility scales with its square root. Getting this
wrong inflates the ratio by about sixteen times, which is enough that the
result should look obviously absurd and somehow rarely does.

**Maximum drawdown runs on the equity curve**, not on returns, and is measured
peak to trough rather than start to trough. A strategy that doubles and then
halves has lost 50% from its peak, not broken even.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

TRADING_DAYS = 252

#: Below this annualised volatility a Sharpe ratio is not meaningful and the
#: division is numerically unstable. Returned as zero instead.
NEGLIGIBLE_VOLATILITY = 1e-12


@dataclass(frozen=True, slots=True)
class Performance:
    total_return: float
    annualised_return: float
    annualised_volatility: float
    sharpe: float
    max_drawdown: float
    trades: int
    turnover: float
    total_cost: float
    #: Costs as a share of starting capital.
    cost_drag: float

    def as_row(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def annualised_return(equity: list[float], periods: int = TRADING_DAYS) -> float:
    """Geometric mean return, scaled to a year."""
    if len(equity) < 2 or equity[0] <= 0:
        return 0.0
    years = (len(equity) - 1) / periods
    if years <= 0:
        return 0.0
    growth = equity[-1] / equity[0]
    if growth <= 0:
        return -1.0
    return growth ** (1.0 / years) - 1.0


def annualised_volatility(returns: list[float], periods: int = TRADING_DAYS) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(periods)


def sharpe(returns: list[float], periods: int = TRADING_DAYS) -> float:
    """Annualised Sharpe, risk-free rate assumed zero.

    Zero is a simplification worth naming: with cash paying 5%, a strategy
    earning 4% has a negative excess return and this will report it as
    positive.
    """
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    deviation = annualised_volatility(returns, periods)
    # Not `== 0`. A constant return series has a variance of about 1e-19
    # rather than exactly zero, and dividing by that produced a Sharpe of
    # 2.4e16, which is both meaningless and the exact shape of number this
    # project exists to teach people to distrust.
    if deviation < NEGLIGIBLE_VOLATILITY:
        return 0.0
    return (mean * periods) / deviation


def max_drawdown(equity: list[float]) -> float:
    """Largest peak-to-trough fall, as a positive fraction."""
    if not equity:
        return 0.0
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def evaluate(result) -> Performance:
    """Summarise a :class:`~honestbt.engine.Result`."""
    returns = result.returns
    return Performance(
        total_return=result.total_return,
        annualised_return=annualised_return(result.equity),
        annualised_volatility=annualised_volatility(returns),
        sharpe=sharpe(returns),
        max_drawdown=max_drawdown(result.equity),
        trades=len(result.trades),
        turnover=result.turnover,
        total_cost=result.total_cost,
        cost_drag=(
            result.total_cost / result.starting_capital
            if result.starting_capital
            else 0.0
        ),
    )
