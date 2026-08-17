"""What it costs to actually do the trade.

A backtest that ignores these is not a backtest of a strategy, it is a
backtest of a price series. Four charges, in rough order of how often they
are left out:

**Commission**, the broker's fee. The only one most people remember, and
usually the smallest.

**Spread**, you buy at the ask and sell at the bid, so you pay half the
spread on entry and half on exit. Backtests priced off the close pay neither
and quietly assume you always traded at the midpoint.

**Slippage**, the price moved between deciding and arriving. Independent of
size; this is latency and luck, not impact.

**Market impact**, your own order moves the price against you, and it grows
with the square root of how much of the day's volume you are. A strategy that
looks fine on 100 shares can be unrunnable at size, and this is the term that
kills it.

Everything here is in basis points of notional. One basis point is 0.01%.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CostModel:
    """Per-trade charges, all in basis points of traded notional."""

    commission_bps: float = 0.0
    #: Half the quoted spread, what you give up crossing it once.
    half_spread_bps: float = 0.0
    slippage_bps: float = 0.0
    #: Impact in bps at 100% participation. Scales with sqrt(participation),
    #: the standard square-root law. 10% of a day's volume costs about a third
    #: of this, not a tenth.
    impact_bps_at_full_adv: float = 0.0

    def impact_bps(self, participation: float) -> float:
        """Impact for an order that is ``participation`` of daily volume."""
        if participation <= 0 or self.impact_bps_at_full_adv == 0:
            return 0.0
        return self.impact_bps_at_full_adv * math.sqrt(min(participation, 1.0))

    def total_bps(self, participation: float = 0.0) -> float:
        return (
            self.commission_bps
            + self.half_spread_bps
            + self.slippage_bps
            + self.impact_bps(participation)
        )

    def charge(self, notional: float, participation: float = 0.0) -> float:
        """Cash cost of trading ``notional``. Always positive."""
        return abs(notional) * self.total_bps(participation) / 10_000.0

    # ---- named presets --------------------------------------------------

    @classmethod
    def free(cls) -> CostModel:
        """What most published backtests assume without saying so."""
        return cls()

    @classmethod
    def retail(cls) -> CostModel:
        """A retail account in a liquid large-cap.

        Zero commission is realistic now; the spread is not, and payment for
        order flow does not make it zero, it makes it someone else's revenue.
        """
        return cls(
            commission_bps=0.0,
            half_spread_bps=1.0,
            slippage_bps=1.0,
            impact_bps_at_full_adv=300.0,
        )

    @classmethod
    def institutional(cls) -> CostModel:
        """Cheaper per share, but trading size where impact dominates."""
        return cls(
            commission_bps=0.5,
            half_spread_bps=0.75,
            slippage_bps=0.5,
            impact_bps_at_full_adv=250.0,
        )
