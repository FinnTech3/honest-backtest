"""Tests for the backtester.

Real market data comes from recorded API responses in tests/fixtures, so the
suite is offline and deterministic. Timing and accounting are tested against
hand-built series where the right answer can be worked out on paper.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from honestbt.bars import Bar, Series
from honestbt.costs import CostModel
from honestbt.engine import Execution, run
from honestbt.metrics import (
    TRADING_DAYS,
    annualised_return,
    evaluate,
    max_drawdown,
    sharpe,
)
from honestbt.strategies import (
    buy_and_hold,
    mean_reversion,
    momentum,
    peeking_mean_reversion,
)

from importlib import resources

#: The same files the CLI ships, so the tests exercise what users get.
FIXTURES = resources.files("honestbt") / "data"


@pytest.fixture(scope="session")
def aapl() -> Series:
    return Series.from_file(FIXTURES / "AAPL_10y.json")


def make_series(prices, opens=None, volume=1_000_000, symbol="TEST") -> Series:
    """A series with no dividend adjustment, so raw and adjusted agree."""
    start = date(2020, 1, 1)
    bars = []
    for index, close in enumerate(prices):
        open_ = close if opens is None else opens[index]
        bars.append(
            Bar(
                day=start + timedelta(days=index),
                open=open_,
                high=max(open_, close),
                low=min(open_, close),
                close=close,
                adjusted_close=close,
                volume=volume,
            )
        )
    return Series(symbol, bars)


# ---- bars ---------------------------------------------------------------


def test_parses_real_data(aapl):
    assert len(aapl) > 2_000
    assert aapl.symbol == "AAPL"
    assert all(b.close > 0 for b in aapl.bars)


def test_bars_are_ordered_and_unique(aapl):
    days = [b.day for b in aapl.bars]
    assert days == sorted(days)
    assert len(set(days)) == len(days)


def test_out_of_order_bars_rejected():
    bars = make_series([1.0, 2.0]).bars
    with pytest.raises(ValueError, match="date order"):
        Series("X", list(reversed(bars)))


def test_empty_series_rejected():
    with pytest.raises(ValueError, match="no bars"):
        Series("X", [])


def test_yahoo_feed_is_already_split_adjusted(aapl):
    """Checked rather than assumed, it decides how fills must be priced.

    Apple split four for one in August 2020. The close series runs straight
    through it, so the only thing separating close from adjusted close in this
    feed is dividends.
    """
    assert aapl.looks_split_adjusted()
    jumps = aapl.adjustment_jumps()
    assert jumps, "ten years of a dividend payer should show adjustments"
    assert all(abs(move) < 0.05 for _, move in jumps), "a split would be huge"


def test_adjustment_factor_reaches_one_at_the_end(aapl):
    assert aapl[-1].adjustment_factor == pytest.approx(1.0, abs=1e-9)


def test_adjusted_puts_a_raw_price_on_the_adjusted_scale():
    bar = Bar(date(2020, 1, 1), 100.0, 101.0, 99.0, 100.0, 50.0, 1)
    assert bar.adjustment_factor == pytest.approx(0.5)
    assert bar.adjusted(80.0) == pytest.approx(40.0)


# ---- costs --------------------------------------------------------------


def test_impact_follows_the_square_root_of_participation():
    model = CostModel(impact_bps_at_full_adv=100.0)
    assert model.impact_bps(1.0) == pytest.approx(100.0)
    assert model.impact_bps(0.25) == pytest.approx(50.0)
    assert model.impact_bps(0.01) == pytest.approx(10.0)


def test_impact_is_not_linear_in_size():
    """Ten percent of volume costs a third of full, not a tenth."""
    model = CostModel(impact_bps_at_full_adv=300.0)
    assert model.impact_bps(0.10) > 300.0 * 0.10 * 2


def test_charges_are_always_positive():
    model = CostModel.retail()
    assert model.charge(-50_000, 0.1) > 0
    assert model.charge(50_000, 0.1) > 0


def test_free_model_charges_nothing():
    assert CostModel.free().charge(1_000_000, 0.5) == 0.0


# ---- engine timing ------------------------------------------------------


def test_execution_mode_changes_the_result():
    """Regression: fills used to be recorded without affecting profit.

    The first engine applied close-to-close returns and merely stored the fill
    price, so both execution modes produced byte-identical equity curves and
    the look-ahead comparison measured nothing at all. Any version where these
    two agree is broken.
    """
    prices = [100.0, 102.0, 99.0, 103.0, 101.0, 104.0, 100.0, 105.0]
    opens = [100.0, 101.0, 101.5, 100.0, 103.5, 101.5, 103.0, 101.0]
    series = make_series(prices, opens)
    signal = mean_reversion(1)

    same = run(series, signal, execution=Execution.SAME_CLOSE)
    later = run(series, signal, execution=Execution.NEXT_OPEN)
    assert same.equity != later.equity


def test_overnight_gap_accrues_to_the_position_held_into_it():
    """Buy at the open and the prior night's move is not yours."""
    # Flat, then a big overnight gap up, then nothing.
    series = make_series(
        prices=[100.0, 120.0, 120.0],
        opens=[100.0, 120.0, 120.0],
    )

    def go_long(_series, index):
        return 1.0 if index >= 0 else 0.0

    result = run(series, go_long, execution=Execution.NEXT_OPEN)
    # The decision is made on day 0's close; the fill is day 1's open, which
    # is already past the gap, so none of the 20% is earned.
    assert result.final_equity == pytest.approx(result.starting_capital)


def test_same_close_execution_captures_the_gap():
    series = make_series(
        prices=[100.0, 120.0, 120.0],
        opens=[100.0, 120.0, 120.0],
    )

    def go_long(_series, index):
        return 1.0

    result = run(series, go_long, execution=Execution.SAME_CLOSE)
    assert result.final_equity == pytest.approx(result.starting_capital * 1.2)


def test_buy_and_hold_tracks_the_underlying(aapl):
    result = run(aapl, buy_and_hold, execution=Execution.NEXT_OPEN)
    underlying = aapl[-1].adjusted_close / aapl[0].adjusted_close - 1.0
    # Not exact: the position is taken at day one's open, so the first
    # overnight gap is missed. It should be close.
    assert result.total_return == pytest.approx(underlying, rel=0.02)


def test_buy_and_hold_trades_once(aapl):
    result = run(aapl, buy_and_hold, execution=Execution.NEXT_OPEN)
    assert len(result.trades) == 1


def test_no_signal_means_no_trades_and_no_change(aapl):
    result = run(aapl, lambda s, i: 0.0, execution=Execution.NEXT_OPEN)
    assert result.trades == []
    assert result.final_equity == pytest.approx(result.starting_capital)


def test_weights_are_clamped():
    series = make_series([100.0, 101.0, 102.0])
    result = run(series, lambda s, i: 99.0, execution=Execution.NEXT_OPEN)
    assert all(-1.0 <= t.weight_to <= 1.0 for t in result.trades)


def test_warmup_suppresses_early_trades(aapl):
    result = run(aapl, mean_reversion(1), warmup=500)
    assert result.trades
    assert result.trades[0].day >= aapl[500].day


# ---- costs bite ---------------------------------------------------------


def test_costs_reduce_the_result(aapl):
    signal = mean_reversion(1)
    free = evaluate(run(aapl, signal, costs=CostModel.free()))
    charged = evaluate(run(aapl, signal, costs=CostModel.retail()))
    assert charged.annualised_return < free.annualised_return
    assert charged.total_cost > 0


def test_a_high_turnover_strategy_is_destroyed_by_costs(aapl):
    """The headline result, pinned.

    Daily mean reversion is profitable before costs and not after. If this
    ever passes with a positive charged return, either the cost model has been
    weakened or the engine has stopped charging.
    """
    signal = mean_reversion(1)
    free = evaluate(run(aapl, signal, costs=CostModel.free()))
    charged = evaluate(
        run(
            aapl,
            signal,
            costs=CostModel(
                commission_bps=0.5,
                half_spread_bps=1.0,
                slippage_bps=1.0,
                impact_bps_at_full_adv=300.0,
            ),
        )
    )
    assert free.annualised_return > 0
    assert charged.annualised_return < 0
    assert charged.cost_drag > 1.0, "costs should exceed starting capital"


def test_turnover_is_reported(aapl):
    result = run(aapl, mean_reversion(1))
    assert result.turnover > 100


# ---- look-ahead ---------------------------------------------------------


def test_peeking_produces_an_implausible_sharpe(aapl):
    """Calibration for what a real look-ahead bug looks like."""
    cheat = evaluate(run(aapl, peeking_mean_reversion(1), costs=CostModel.free()))
    honest = evaluate(run(aapl, mean_reversion(1), costs=CostModel.free()))
    assert cheat.sharpe > 5.0
    assert cheat.sharpe > honest.sharpe * 10


def test_honest_signal_cannot_see_the_future(aapl):
    """The signal must return the same value however much data follows it."""
    signal = mean_reversion(1)
    index = 1_000
    truncated = Series(aapl.symbol, aapl.bars[: index + 1])
    assert signal(aapl, index) == signal(truncated, index)


def test_momentum_signal_cannot_see_the_future(aapl):
    signal = momentum(50)
    index = 1_000
    truncated = Series(aapl.symbol, aapl.bars[: index + 1])
    assert signal(aapl, index) == signal(truncated, index)


# ---- metrics ------------------------------------------------------------


def test_sharpe_annualises_by_the_square_root_of_time():
    """Multiplying by 252 rather than its root inflates this about 16x."""
    noisy = [0.001 if i % 2 else -0.0005 for i in range(TRADING_DAYS)]
    mean = sum(noisy) / len(noisy)
    variance = sum((r - mean) ** 2 for r in noisy) / (len(noisy) - 1)
    manual = (mean * TRADING_DAYS) / (math.sqrt(variance) * math.sqrt(TRADING_DAYS))
    assert sharpe(noisy) == pytest.approx(manual)


def test_constant_returns_give_a_sharpe_of_zero():
    """Regression: a flat series produced a Sharpe of 2.4e16.

    Its variance is about 1e-19 rather than exactly zero, so an `== 0` guard
    missed it and the division ran anyway. An absurd Sharpe is the shape this
    whole project teaches you to distrust, so emitting one is a bug.
    """
    assert sharpe([0.001] * TRADING_DAYS) == 0.0
    assert sharpe([0.0] * TRADING_DAYS) == 0.0


def test_max_drawdown_is_peak_to_trough():
    """Doubling then halving is a 50% drawdown, not break-even."""
    assert max_drawdown([100, 200, 100]) == pytest.approx(0.5)
    assert max_drawdown([100, 110, 121]) == 0.0
    assert max_drawdown([]) == 0.0


def test_annualised_return_is_geometric():
    # Exactly one year of trading days, doubling.
    equity = [100.0] * TRADING_DAYS + [200.0]
    assert annualised_return(equity) == pytest.approx(1.0, rel=1e-6)


def test_annualised_return_handles_a_wipeout():
    assert annualised_return([100.0, 0.0]) == -1.0


def test_evaluate_returns_every_field(aapl):
    performance = evaluate(run(aapl, mean_reversion(1), costs=CostModel.retail()))
    row = performance.as_row()
    for field in ("sharpe", "max_drawdown", "turnover", "cost_drag"):
        assert field in row


# ---- strategy guards ----------------------------------------------------


def test_invalid_lookbacks_rejected():
    with pytest.raises(ValueError):
        mean_reversion(0)
    with pytest.raises(ValueError):
        momentum(1)


# ---- parameter sensitivity ----------------------------------------------


def test_no_momentum_window_beats_buy_and_hold(aapl):
    """The result that makes the single-parameter version untrustworthy.

    One backtest at one setting says almost nothing, because the setting was
    chosen after seeing the data. Sweeping shows whether the result belongs to
    the strategy or to the number, and here no window wins, so the 50-day
    figure quoted elsewhere is not a lucky pick, it is representative.
    """
    from honestbt.cli import LADDER, WINDOWS

    model = LADDER[-1][1]
    benchmark = evaluate(run(aapl, buy_and_hold)).annualised_return

    returns = {
        window: evaluate(
            run(aapl, momentum(window), costs=model)
        ).annualised_return
        for window in WINDOWS
    }
    assert len(returns) >= 8, "sweep too narrow to say anything"
    assert all(r < benchmark for r in returns.values()), (
        "a window beat buy and hold, the README's conclusion needs revising"
    )


def test_the_best_window_still_loses(aapl):
    """Reporting the winner of a sweep is itself a bias, so name the cost."""
    from honestbt.cli import LADDER, WINDOWS

    model = LADDER[-1][1]
    benchmark = evaluate(run(aapl, buy_and_hold)).annualised_return
    best = max(
        evaluate(run(aapl, momentum(w), costs=model)).annualised_return
        for w in WINDOWS
    )
    assert best < benchmark


def test_impact_sizing_does_not_use_volume_from_after_the_fill(aapl):
    """Regression: a look-ahead leak inside the cost model.

    Average daily volume was taken through the bar being traded on. A fill at
    that bar's open happens before the day's volume exists, so the impact
    charge was sized with a number from after the trade. Small in effect and
    exactly the class of error this project is about, which is why it is
    pinned rather than quietly corrected.

    Constructed so the fill day's volume is wildly unlike the days before it:
    if it were still being averaged in, the charge would move.
    """
    from honestbt.engine import _participation

    quiet = make_series([100.0] * 25, volume=1_000)
    loud_bars = list(quiet.bars)
    loud_bars[10] = Bar(
        day=loud_bars[10].day, open=100.0, high=100.0, low=100.0,
        close=100.0, adjusted_close=100.0, volume=10_000_000,
    )
    loud = Series("TEST", loud_bars)

    # Filled at bar 10's open: bar 10's volume has not happened yet.
    at_open = _participation(loud, 9, notional=10_000, price=100.0, window=20)
    # Filled at bar 10's close: it has.
    at_close = _participation(loud, 10, notional=10_000, price=100.0, window=20)

    assert at_open > at_close, (
        "the huge volume on the fill day must not shrink an open fill's "
        "participation, that would be information from after the trade"
    )
    reference = _participation(quiet, 9, notional=10_000, price=100.0, window=20)
    assert at_open == pytest.approx(reference)


def test_participation_before_any_history_is_zero():
    series = make_series([100.0, 101.0])
    from honestbt.engine import _participation

    assert _participation(series, -1, 10_000, 100.0, 20) == 0.0
