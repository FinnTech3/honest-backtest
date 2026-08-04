"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .bars import Series
from .costs import CostModel
from .engine import Execution, run
from .metrics import evaluate
from .strategies import (
    buy_and_hold,
    mean_reversion,
    momentum,
    peeking_mean_reversion,
)

DATA = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
SYMBOLS = ("AAPL", "MSFT", "KO", "SPY")


def load(symbol: str) -> Series:
    path = DATA / f"{symbol}_10y.json"
    if not path.exists():
        raise SystemExit(f"no data for {symbol} at {path}")
    return Series.from_file(path)


def _signal(name: str, lookback: int):
    if name == "mean-reversion":
        return mean_reversion(lookback)
    if name == "momentum":
        return momentum(lookback if lookback > 1 else 50)
    raise SystemExit(f"unknown strategy {name!r}")


#: Each step adds one charge on top of the last. Order runs cheapest first, so
#: the row where the strategy dies names the charge that killed it.
LADDER = [
    ("no costs at all", CostModel()),
    ("+ commission", CostModel(commission_bps=0.5)),
    ("+ half spread", CostModel(commission_bps=0.5, half_spread_bps=1.0)),
    (
        "+ slippage",
        CostModel(commission_bps=0.5, half_spread_bps=1.0, slippage_bps=1.0),
    ),
    (
        "+ market impact",
        CostModel(
            commission_bps=0.5,
            half_spread_bps=1.0,
            slippage_bps=1.0,
            impact_bps_at_full_adv=300.0,
        ),
    ),
]


def cmd_waterfall(args) -> int:
    series = load(args.symbol)
    signal = _signal(args.strategy, args.lookback)

    print(
        f"{args.strategy} on {series.symbol}, {len(series)} sessions "
        f"{series[0].day} to {series[-1].day}\n"
    )
    print(f"{'assumption':<26}{'ann. return':>13}{'sharpe':>9}"
          f"{'max DD':>9}{'cost drag':>11}")
    print("-" * 68)

    impossible = evaluate(
        run(series, signal, execution=Execution.SAME_CLOSE, costs=CostModel())
    )
    print(f"{'fill at decision close':<26}{impossible.annualised_return:>12.1%}"
          f"{impossible.sharpe:>9.2f}{impossible.max_drawdown:>9.1%}"
          f"{impossible.cost_drag:>10.1%}")
    print(f"{'  (not achievable)':<26}")

    for label, model in LADDER:
        performance = evaluate(
            run(series, signal, execution=Execution.NEXT_OPEN, costs=model)
        )
        print(f"{label:<26}{performance.annualised_return:>12.1%}"
              f"{performance.sharpe:>9.2f}{performance.max_drawdown:>9.1%}"
              f"{performance.cost_drag:>10.1%}")

    held = evaluate(run(series, buy_and_hold, execution=Execution.NEXT_OPEN))
    print("-" * 68)
    print(f"{'buy and hold':<26}{held.annualised_return:>12.1%}"
          f"{held.sharpe:>9.2f}{held.max_drawdown:>9.1%}{held.cost_drag:>10.1%}")

    final = evaluate(
        run(series, signal, execution=Execution.NEXT_OPEN, costs=LADDER[-1][1])
    )
    print(
        f"\nTurnover {final.turnover:,.0f}x starting capital over the period. "
        f"Costs took\n{final.cost_drag:.0%} of it. A strategy is only as good "
        f"as the price it can be traded at."
    )
    return 0


def cmd_lookahead(args) -> int:
    """Show what the two timing errors actually do to a result."""
    series = load(args.symbol)
    honest = mean_reversion(args.lookback)
    cheating = peeking_mean_reversion(args.lookback)

    print(f"mean reversion on {series.symbol}, no costs, so only timing differs\n")
    print(f"{'':<40}{'ann. return':>13}{'sharpe':>9}")
    print("-" * 62)

    rows = [
        ("decide on close, fill at that close", honest, Execution.SAME_CLOSE),
        ("decide on close, fill at next open", honest, Execution.NEXT_OPEN),
        ("read tomorrow's price (a real bug)", cheating, Execution.NEXT_OPEN),
    ]
    for label, signal, execution in rows:
        performance = evaluate(
            run(series, signal, execution=execution, costs=CostModel())
        )
        print(f"{label:<40}{performance.annualised_return:>12.1%}"
              f"{performance.sharpe:>9.2f}")

    print(
        "\nThe last row is what an off-by-one in a shifted column buys you, and\n"
        "it is the shape to recognise: a Sharpe no strategy earns. The first two\n"
        "rows differ only in when the fill lands, and the gap between them is\n"
        "the overnight move — which for this strategy runs the other way, so\n"
        "the impossible version is not the flattering one. Look-ahead does not\n"
        "reliably inflate a result. It distorts it, and the direction depends\n"
        "on what the strategy is trading."
    )
    return 0


def cmd_universe(args) -> int:
    """Run the honest configuration across several symbols."""
    signal = _signal(args.strategy, args.lookback)
    model = LADDER[-1][1]
    print(f"{args.strategy}, next-open fills, all costs applied\n")
    print(f"{'symbol':<10}{'ann. return':>13}{'sharpe':>9}{'trades':>9}"
          f"{'cost drag':>11}")
    print("-" * 52)
    for symbol in SYMBOLS:
        series = load(symbol)
        performance = evaluate(
            run(series, signal, execution=Execution.NEXT_OPEN, costs=model)
        )
        print(f"{symbol:<10}{performance.annualised_return:>12.1%}"
              f"{performance.sharpe:>9.2f}{performance.trades:>9}"
              f"{performance.cost_drag:>10.1%}")
    print(
        "\nOne symbol is an anecdote. Four is barely better, and every one of\n"
        "them survived ten years, which is its own selection."
    )
    return 0


#: Windows swept by `sensitivity`. Wide enough that a strategy working at
#: only one of them is visible as such.
WINDOWS = (10, 20, 30, 40, 50, 60, 75, 100, 150, 200)


def cmd_sensitivity(args) -> int:
    """Run the strategy across a range of parameters, not just one.

    A single backtest at one setting says almost nothing, because the setting
    was chosen after seeing the data. Sweeping shows whether the result is a
    property of the strategy or of the number.
    """
    series = load(args.symbol)
    model = LADDER[-1][1]
    benchmark = evaluate(
        run(series, buy_and_hold, execution=Execution.NEXT_OPEN)
    ).annualised_return

    print(f"momentum on {series.symbol}, all costs applied, "
          f"{len(series)} sessions\n")
    print(f"{'window':>7}{'ann. return':>13}{'sharpe':>9}{'trades':>8}"
          f"{'vs buy & hold':>15}")
    print("-" * 52)

    results = []
    for window in WINDOWS:
        performance = evaluate(
            run(series, momentum(window), execution=Execution.NEXT_OPEN,
                costs=model)
        )
        results.append((window, performance))
        print(f"{window:>7}{performance.annualised_return:>12.1%}"
              f"{performance.sharpe:>9.2f}{performance.trades:>8}"
              f"{performance.annualised_return - benchmark:>+14.1%}")

    print("-" * 52)
    print(f"{'buy and hold':>7}{benchmark:>12.1%}")

    beat = [w for w, p in results if p.annualised_return > benchmark]
    best_window, best = max(results, key=lambda r: r[1].annualised_return)

    print(
        f"\nWindows tried: {len(WINDOWS)}. Windows that beat buy and hold: "
        f"{len(beat)}."
    )
    print(
        f"\nThe best of them is {best_window} days at "
        f"{best.annualised_return:.1%} and a Sharpe of {best.sharpe:.2f}, "
        f"which\nwould read well quoted on its own. It is still "
        f"{benchmark - best.annualised_return:.1%} a year behind\ndoing "
        f"nothing, and it is the winner of {len(WINDOWS)} attempts — picking "
        f"the best\nparameter after the fact is its own bias, and a "
        f"sweep is how you see it."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="honestbt",
        description="Backtest a strategy and charge it for what trading costs.",
    )
    parser.add_argument("--symbol", default="AAPL", choices=SYMBOLS)
    parser.add_argument("--strategy", default="mean-reversion",
                        choices=("mean-reversion", "momentum"))
    parser.add_argument("--lookback", type=int, default=1)
    sub = parser.add_subparsers(dest="command", required=True)

    waterfall = sub.add_parser(
        "waterfall", help="add one honest assumption at a time"
    )
    waterfall.set_defaults(func=cmd_waterfall)

    lookahead = sub.add_parser(
        "lookahead", help="what the timing errors do on their own"
    )
    lookahead.set_defaults(func=cmd_lookahead)

    universe = sub.add_parser("universe", help="the honest run across symbols")
    universe.set_defaults(func=cmd_universe)

    sensitivity = sub.add_parser(
        "sensitivity", help="sweep the parameter instead of trusting one value"
    )
    sensitivity.set_defaults(func=cmd_sensitivity)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
