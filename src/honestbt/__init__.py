"""A backtester that charges for what a real trade would have cost."""

from .bars import Bar, Series
from .costs import CostModel
from .engine import Execution, Result, Trade, run
from .metrics import Performance, evaluate, max_drawdown, sharpe
from .strategies import (
    buy_and_hold,
    mean_reversion,
    momentum,
    peeking_mean_reversion,
)

__version__ = "0.1.0"

__all__ = [
    "Bar",
    "CostModel",
    "Execution",
    "Performance",
    "Result",
    "Series",
    "Trade",
    "buy_and_hold",
    "evaluate",
    "max_drawdown",
    "mean_reversion",
    "momentum",
    "peeking_mean_reversion",
    "run",
    "sharpe",
]
