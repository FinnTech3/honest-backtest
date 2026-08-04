"""The backtest loop, with the timing made explicit.

The most damaging bug in backtesting is not a cost left out. It is deciding to
trade using a price you then trade *at*. If a signal reads today's close and the
fill is booked at today's close, the backtest used information from the end of
the session to transact at it. Nothing errors; the equity curve simply comes out
too good.

:class:`Execution` makes that choice visible:

- ``SAME_CLOSE`` — decide on today's close, fill at today's close. Not
  achievable. Kept so the size of the lie can be measured.
- ``NEXT_OPEN`` — decide on today's close, fill at tomorrow's open. Achievable.

For the distinction to mean anything, each day is split in two. A day earns an
overnight return from the previous close to today's open, and an intraday return
from today's open to today's close. A fill at the open therefore lands between
them: the position going into the open earns the gap, and the position after it
earns the rest of the day.

Getting this wrong is subtle, because a version that applies close-to-close
returns and simply records a fill price produces *identical* results for both
execution modes. The fill price never touches the profit, and the look-ahead
demonstration silently measures nothing.

Returns run on adjusted prices; fills are priced from the actual open and close
and converted onto the adjusted scale, because you transact at real prices and
measure with adjusted ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Callable

from .bars import Series
from .costs import CostModel

#: Given the series and an index ``t``, return a target weight in [-1, 1].
#: The callable must not read past ``t``.
Signal = Callable[[Series, int], float]


class Execution(Enum):
    #: Fill at the same close the decision was made on. Not achievable.
    SAME_CLOSE = "same_close"
    #: Fill at the next session's open. Achievable.
    NEXT_OPEN = "next_open"


@dataclass(frozen=True, slots=True)
class Trade:
    day: date
    price: float
    weight_from: float
    weight_to: float
    notional: float
    cost: float
    participation: float


@dataclass
class Result:
    symbol: str
    equity: list[float] = field(default_factory=list)
    days: list[date] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    total_cost: float = 0.0
    starting_capital: float = 0.0

    @property
    def final_equity(self) -> float:
        return self.equity[-1] if self.equity else self.starting_capital

    @property
    def total_return(self) -> float:
        if not self.equity or self.starting_capital == 0:
            return 0.0
        return self.final_equity / self.starting_capital - 1.0

    @property
    def returns(self) -> list[float]:
        return [
            after / before - 1.0
            for before, after in zip(self.equity, self.equity[1:])
            if before != 0
        ]

    @property
    def turnover(self) -> float:
        """Total notional traded, as a multiple of starting capital."""
        if self.starting_capital == 0:
            return 0.0
        return sum(abs(t.notional) for t in self.trades) / self.starting_capital


def run(
    series: Series,
    signal: Signal,
    *,
    execution: Execution = Execution.NEXT_OPEN,
    costs: CostModel | None = None,
    capital: float = 100_000.0,
    warmup: int = 0,
    adv_window: int = 20,
) -> Result:
    """Walk the series once, applying ``signal`` and charging ``costs``."""
    model = costs or CostModel.free()
    result = Result(symbol=series.symbol, starting_capital=capital)

    state = _State(equity=capital, weight=0.0)
    pending: float | None = None

    # A next-open fill needs a following bar to exist.
    last_decision = (
        len(series) - 1 if execution is Execution.SAME_CLOSE else len(series) - 2
    )

    for index in range(len(series)):
        bar = series[index]

        if index > 0:
            previous = series[index - 1]
            open_adjusted = bar.adjusted(bar.open)

            # Overnight gap, earned by whatever position was already held.
            if previous.adjusted_close:
                state.equity *= 1.0 + state.weight * (
                    open_adjusted / previous.adjusted_close - 1.0
                )

            # A scheduled fill lands here, at the open, between the two legs.
            if pending is not None:
                _execute(
                    result, model, state, series, index,
                    target=pending, price=bar.open, day=bar.day,
                    adv_window=adv_window,
                )
                pending = None

            # The rest of the session, earned by the position after the fill.
            if open_adjusted:
                state.equity *= 1.0 + state.weight * (
                    bar.adjusted_close / open_adjusted - 1.0
                )

        if warmup <= index <= last_decision:
            target = _clamp(signal(series, index))
            if target != state.weight:
                if execution is Execution.SAME_CLOSE:
                    _execute(
                        result, model, state, series, index,
                        target=target, price=bar.close, day=bar.day,
                        adv_window=adv_window,
                    )
                else:
                    pending = target

        result.equity.append(state.equity)
        result.days.append(bar.day)

    return result


@dataclass
class _State:
    equity: float
    weight: float


def _execute(
    result: Result,
    model: CostModel,
    state: _State,
    series: Series,
    index: int,
    *,
    target: float,
    price: float,
    day: date,
    adv_window: int,
) -> None:
    notional = abs(target - state.weight) * state.equity
    participation = _participation(series, index, notional, price, adv_window)
    cost = model.charge(notional, participation)
    state.equity -= cost
    result.total_cost += cost
    result.trades.append(
        Trade(
            day=day,
            price=price,
            weight_from=state.weight,
            weight_to=target,
            notional=notional,
            cost=cost,
            participation=participation,
        )
    )
    state.weight = target


def _participation(
    series: Series, index: int, notional: float, price: float, window: int
) -> float:
    """Order size as a fraction of recent average daily volume."""
    if price <= 0 or window <= 0:
        return 0.0
    start = max(0, index - window + 1)
    volumes = [b.volume for b in series.bars[start : index + 1]]
    average = sum(volumes) / len(volumes) if volumes else 0
    if average <= 0:
        return 0.0
    return (notional / price) / average


def _clamp(weight: float) -> float:
    return max(-1.0, min(1.0, float(weight)))
