"""Signals to run through the engine.

Each is a plain function of the series and an index, and none of them may read
past that index. That restriction is the whole point, a signal that peeks is
the bias the engine exists to expose, so the rule lives here in the callables
rather than being enforced somewhere it can be forgotten.

The default strategy is short-horizon mean reversion, chosen because it is the
textbook example of something that looks superb until it is charged for. It
trades most days, and turnover is what costs feed on.
"""

from __future__ import annotations

from .bars import Series


def mean_reversion(lookback: int = 1) -> "object":
    """Buy after a fall, sell after a rise.

    Real in the sense that short-horizon reversal genuinely exists in equity
    prices. The question the backtest answers is whether it survives being
    traded, and the answer is usually no.
    """
    if lookback < 1:
        raise ValueError("lookback must be at least 1")

    def signal(series: Series, index: int) -> float:
        if index < lookback:
            return 0.0
        past = series[index - lookback].adjusted_close
        now = series[index].adjusted_close
        if past == 0:
            return 0.0
        return 1.0 if now < past else -1.0

    signal.__name__ = f"mean_reversion_{lookback}"
    return signal


def momentum(window: int = 50) -> "object":
    """Hold long while price sits above its moving average, else flat."""
    if window < 2:
        raise ValueError("window must be at least 2")

    def signal(series: Series, index: int) -> float:
        if index < window:
            return 0.0
        window_bars = series.bars[index - window + 1 : index + 1]
        average = sum(b.adjusted_close for b in window_bars) / window
        return 1.0 if series[index].adjusted_close > average else 0.0

    signal.__name__ = f"momentum_{window}"
    return signal


def buy_and_hold(series: Series, index: int) -> float:
    """The benchmark every strategy has to beat and most quietly do not."""
    return 1.0


def peeking_mean_reversion(lookback: int = 1) -> "object":
    """Uses tomorrow's price. Cheating, deliberately, for calibration.

    Nobody writes this on purpose. It is what an off-by-one in a shifted
    column amounts to, and it is here so the resulting equity curve can be
    seen next to the honest one. If a backtest looks like this, the bug is
    this.
    """
    if lookback < 1:
        raise ValueError("lookback must be at least 1")

    def signal(series: Series, index: int) -> float:
        if index + 1 >= len(series):
            return 0.0
        now = series[index].adjusted_close
        tomorrow = series[index + 1].adjusted_close
        return 1.0 if tomorrow > now else -1.0

    signal.__name__ = f"peeking_{lookback}"
    return signal


REGISTRY = {
    "mean-reversion": mean_reversion,
    "momentum": momentum,
}
