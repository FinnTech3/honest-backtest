"""Daily price bars, and the two prices that are easy to confuse.

Every bar carries a close and an adjusted close, and the difference is the
first place a backtest goes wrong.

What that difference actually contains depends on the source, and it is worth
checking rather than assuming. **Yahoo's chart API already adjusts its OHLC for
splits**, Apple's four-for-one in August 2020 does not appear as a cliff in the
close series, which runs 124.81 to 129.04 straight through it. The `adjclose`
field additionally removes dividends, and that is the only thing separating the
two columns here. Apple's factor is 0.9155 at the start of a ten-year window and
1.0 at the end: ten years of dividends, no splits.

That still leaves the trap intact, just for a different reason. The adjusted
close is a price at which nothing ever traded. Apple's adjusted close for August
2016 is $24.23 and nobody bought Apple at $24.23 in 2016. Fill a backtest at
adjusted prices and it transacts at prices that never existed; compute returns
from unadjusted ones and every ex-dividend day looks like a small loss.

So: adjusted prices for returns, actual prices for what you could have
transacted at. A source that does *not* pre-adjust for splits makes the second
error violent rather than gradual, which is why :meth:`Series.adjustment_jumps`
exists, to find out which kind of data you have instead of trusting a docstring.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    #: Split- and dividend-adjusted close. Correct for returns, untradeable.
    adjusted_close: float
    volume: int

    @property
    def adjustment_factor(self) -> float:
        """adjusted / raw. 1.0 once no split or dividend follows this bar."""
        return self.adjusted_close / self.close if self.close else 1.0

    def adjusted(self, raw_price: float) -> float:
        """Put a raw price from this bar onto the adjusted scale.

        Needed whenever an execution price, an open, a high, a limit, has to
        be compared against a return series computed from adjusted closes.
        """
        return raw_price * self.adjustment_factor


class Series:
    """An ordered run of daily bars for one symbol."""

    __slots__ = ("symbol", "bars")

    def __init__(self, symbol: str, bars: list[Bar]) -> None:
        if not bars:
            raise ValueError(f"{symbol}: no bars")
        days = [bar.day for bar in bars]
        if days != sorted(days):
            raise ValueError(f"{symbol}: bars are not in date order")
        if len(set(days)) != len(days):
            raise ValueError(f"{symbol}: duplicate dates")
        self.symbol = symbol
        self.bars = bars

    @classmethod
    def from_yahoo(cls, payload: dict) -> Series:
        """Parse a Yahoo chart API response.

        Bars with a null field are dropped rather than interpolated. A null is
        usually a halted or untraded session, and inventing a price for it
        hands the backtest a trade that could not have happened.
        """
        result = payload["chart"]["result"][0]
        symbol = result["meta"]["symbol"]
        stamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        adjusted = result["indicators"]["adjclose"][0]["adjclose"]

        bars: list[Bar] = []
        for index, stamp in enumerate(stamps):
            fields = (
                quote["open"][index],
                quote["high"][index],
                quote["low"][index],
                quote["close"][index],
                adjusted[index],
                quote["volume"][index],
            )
            if any(value is None for value in fields):
                continue
            open_, high, low, close, adj, volume = fields
            bars.append(
                Bar(
                    day=datetime.fromtimestamp(stamp, timezone.utc).date(),
                    open=float(open_),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    adjusted_close=float(adj),
                    volume=int(volume),
                )
            )
        return cls(symbol, bars)

    @classmethod
    def from_file(cls, path: Path | str) -> Series:
        return cls.from_yahoo(json.loads(Path(path).read_text()))

    def adjusted_returns(self) -> list[float]:
        """Daily returns from adjusted closes. One shorter than the series."""
        out = []
        for previous, current in zip(self.bars, self.bars[1:]):
            out.append(current.adjusted_close / previous.adjusted_close - 1.0)
        return out

    def raw_returns(self) -> list[float]:
        """Daily returns from raw closes. Wrong across splits, on purpose.

        Kept so the size of the error can be measured rather than asserted.
        """
        out = []
        for previous, current in zip(self.bars, self.bars[1:]):
            out.append(current.close / previous.close - 1.0)
        return out

    def adjustment_jumps(self, threshold: float = 0.0005) -> list[tuple[date, float]]:
        """Days where the adjustment factor moves, and by how much.

        Tells you what your source has already done for you. Small moves are
        ex-dividend dates. A move of tens of percent means the feed is *not*
        pre-adjusted for splits, and any fill priced off the unadjusted column
        is about to be wrong by the split ratio.
        """
        found = []
        for previous, current in zip(self.bars, self.bars[1:]):
            before = previous.adjustment_factor
            after = current.adjustment_factor
            if before and abs(after / before - 1.0) > threshold:
                found.append((current.day, after / before - 1.0))
        return found

    def looks_split_adjusted(self, threshold: float = 0.20) -> bool:
        """True when no adjustment jump is large enough to be a split."""
        return all(abs(move) < threshold for _, move in self.adjustment_jumps())

    def __len__(self) -> int:
        return len(self.bars)

    def __getitem__(self, index: int) -> Bar:
        return self.bars[index]

    def __repr__(self) -> str:
        return (
            f"Series({self.symbol}, {len(self.bars)} bars, "
            f"{self.bars[0].day}..{self.bars[-1].day})"
        )
