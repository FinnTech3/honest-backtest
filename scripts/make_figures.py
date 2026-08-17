"""Generates the figures in docs/figures from the committed price data.

Numbers are computed here rather than typed in, so a chart cannot drift away
from the backtest behind it. Run after changing anything that affects results:

    python3 scripts/make_figures.py

Emits a light and a dark variant of each chart. GitHub picks between them with
<picture>; a website can use either.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from honestbt.bars import Series  # noqa: E402
from honestbt.costs import CostModel  # noqa: E402
from honestbt.engine import Execution, run  # noqa: E402
from honestbt.metrics import evaluate  # noqa: E402
from honestbt.strategies import buy_and_hold, mean_reversion  # noqa: E402

OUT = ROOT / "docs" / "figures"
DATA = ROOT / "src" / "honestbt" / "data"
SYMBOL = "AAPL"

FULL_COSTS = CostModel(
    commission_bps=0.5,
    half_spread_bps=1.0,
    slippage_bps=1.0,
    impact_bps_at_full_adv=300.0,
)

LADDER = [
    ("no costs", CostModel()),
    ("+ commission", CostModel(commission_bps=0.5)),
    ("+ spread", CostModel(commission_bps=0.5, half_spread_bps=1.0)),
    ("+ slippage",
     CostModel(commission_bps=0.5, half_spread_bps=1.0, slippage_bps=1.0)),
    ("+ impact", FULL_COSTS),
]


@dataclass(frozen=True)
class Theme:
    name: str
    surface: str
    text_primary: str
    text_secondary: str
    muted: str
    gridline: str
    baseline: str
    series: tuple[str, str, str]
    negative: str
    positive: str


LIGHT = Theme("light", "#fcfcfb", "#0b0b0b", "#52514e", "#898781",
              "#e1e0d9", "#c3c2b7", ("#2a78d6", "#eb6834", "#1baf7a"),
              "#e34948", "#2a78d6")
DARK = Theme("dark", "#1a1a19", "#ffffff", "#c3c2b7", "#898781",
             "#2c2c2a", "#383835", ("#3987e5", "#d95926", "#199e70"),
             "#e66767", "#3987e5")

FONT = "system-ui,-apple-system,'Segoe UI',sans-serif"


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def equity_chart(curves, theme: Theme) -> str:
    """Three equity curves on a log scale.

    Log, because buy and hold ends about twelve times its start while the
    strategy hovers near one. On a linear axis the two strategy lines would be
    flat against the bottom and the comparison between them, which is the
    point, would be unreadable.
    """
    width, height = 780, 430
    left, right, top, bottom = 56, 168, 74, 54
    plot_w, plot_h = width - left - right, height - top - bottom

    every = [v for _, values, _ in curves for v in values]
    lo, hi = max(min(every), 0.05), max(every)
    log_lo, log_hi = math.log10(lo * 0.85), math.log10(hi * 1.15)

    def y(value: float) -> float:
        clamped = max(value, lo * 0.85)
        frac = (math.log10(clamped) - log_lo) / (log_hi - log_lo)
        return top + plot_h - frac * plot_h

    def x(index: int, total: int) -> float:
        return left + (index / max(total - 1, 1)) * plot_w

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" '
        f'font-family="{FONT}" role="img" '
        f'aria-label="Growth of one dollar: mean reversion before costs, after '
        f'costs, and buy and hold">',
        f'<rect width="{width}" height="{height}" fill="{theme.surface}"/>',
        f'<text x="{left}" y="30" font-size="15" font-weight="600" '
        f'fill="{theme.text_primary}">Growth of $1, {esc(SYMBOL)}, ten '
        f'years</text>',
        f'<text x="{left}" y="50" font-size="12" fill="{theme.text_secondary}">'
        f'Log scale. Daily mean reversion, before and after real trading '
        f'costs</text>',
    ]

    for tick in (0.5, 1, 2, 5, 10, 20):
        if not (lo * 0.85 <= tick <= hi * 1.15):
            continue
        ty = y(tick)
        parts.append(
            f'<line x1="{left}" y1="{ty:.1f}" x2="{left + plot_w}" '
            f'y2="{ty:.1f}" stroke="{theme.gridline}" stroke-width="1"/>'
        )
        label = f"{tick:g}x"
        parts.append(
            f'<text x="{left - 10}" y="{ty + 4:.1f}" font-size="11" '
            f'fill="{theme.muted}" text-anchor="end">{label}</text>'
        )

    # Break-even, so a curve below it is visibly losing money.
    parts.append(
        f'<line x1="{left}" y1="{y(1.0):.1f}" x2="{left + plot_w}" '
        f'y2="{y(1.0):.1f}" stroke="{theme.baseline}" stroke-width="1.5" '
        f'stroke-dasharray="4 3"/>'
    )

    for (label, values, colour_index), _ in zip(curves, curves):
        colour = theme.series[colour_index]
        step = max(1, len(values) // 420)
        points = [
            f"{x(i, len(values)):.1f},{y(v):.1f}"
            for i, v in enumerate(values)
            if i % step == 0 or i == len(values) - 1
        ]
        parts.append(
            f'<polyline fill="none" stroke="{colour}" stroke-width="2" '
            f'stroke-linejoin="round" points="{" ".join(points)}"/>'
        )
        # Direct label at the right end. Required, not decorative: one series
        # colour sits below 3:1 on the light surface, so identity cannot rest
        # on hue alone.
        end_y = y(values[-1])
        parts.append(
            f'<circle cx="{left + plot_w:.1f}" cy="{end_y:.1f}" r="4" '
            f'fill="{colour}" stroke="{theme.surface}" stroke-width="1.5"/>'
        )
        parts.append(
            f'<text x="{left + plot_w + 12:.1f}" y="{end_y - 3:.1f}" '
            f'font-size="12" font-weight="600" fill="{theme.text_primary}">'
            f'{values[-1]:,.1f}x</text>'
        )
        parts.append(
            f'<text x="{left + plot_w + 12:.1f}" y="{end_y + 12:.1f}" '
            f'font-size="11" fill="{theme.text_secondary}">{esc(label)}</text>'
        )

    parts.append(
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" '
        f'y2="{top + plot_h}" stroke="{theme.baseline}" stroke-width="1"/>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def ladder_chart(rows, theme: Theme) -> str:
    """Annualised return as each charge is switched on."""
    width, row_h, top, bottom = 700, 40, 74, 46
    label_w, right_pad = 132, 96
    height = top + len(rows) * row_h + bottom
    plot_w = width - label_w - right_pad

    lo = min(min(v for _, v in rows), 0.0) - 0.02
    hi = max(max(v for _, v in rows), 0.0) + 0.03
    span = hi - lo

    def x(value: float) -> float:
        return label_w + (value - lo) / span * plot_w

    zero = x(0.0)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" '
        f'font-family="{FONT}" role="img" '
        f'aria-label="Annualised return as each trading cost is applied">',
        f'<rect width="{width}" height="{height}" fill="{theme.surface}"/>',
        f'<text x="24" y="30" font-size="15" font-weight="600" '
        f'fill="{theme.text_primary}">What each cost takes</text>',
        f'<text x="24" y="50" font-size="12" fill="{theme.text_secondary}">'
        f'Annualised return, daily mean reversion on {esc(SYMBOL)}, charges '
        f'added one at a time</text>',
    ]

    bar_h = 20
    for index, (label, value) in enumerate(rows):
        centre = top + index * row_h + row_h / 2 - bar_h / 2
        colour = theme.negative if value < 0 else theme.positive
        bx = min(zero, x(value))
        bw = max(abs(x(value) - zero), 2.0)
        parts.append(
            f'<text x="{label_w - 14}" y="{centre + bar_h / 2 + 4:.1f}" '
            f'font-size="12.5" fill="{theme.text_primary}" text-anchor="end">'
            f'{esc(label)}</text>'
        )
        parts.append(
            f'<rect x="{bx:.1f}" y="{centre:.1f}" width="{bw:.1f}" '
            f'height="{bar_h}" rx="4" fill="{colour}"/>'
        )
        patch_x = zero - 4 if value < 0 else zero
        parts.append(
            f'<rect x="{patch_x:.1f}" y="{centre:.1f}" width="4" '
            f'height="{bar_h}" fill="{colour}"/>'
        )
        label_x = bx - 8 if value < 0 else bx + bw + 8
        anchor = "end" if value < 0 else "start"
        parts.append(
            f'<text x="{label_x:.1f}" y="{centre + bar_h / 2 + 4:.1f}" '
            f'font-size="12" fill="{theme.text_primary}" '
            f'text-anchor="{anchor}">{value * 100:+.1f}%</text>'
        )

    parts.append(
        f'<line x1="{zero:.1f}" y1="{top - 8}" x2="{zero:.1f}" '
        f'y2="{top + len(rows) * row_h}" stroke="{theme.baseline}" '
        f'stroke-width="2"/>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    series = Series.from_file(DATA / f"{SYMBOL}_10y.json")
    signal = mean_reversion(1)

    def normalised(result):
        base = result.starting_capital
        return [value / base for value in result.equity]

    free = run(series, signal, execution=Execution.NEXT_OPEN, costs=CostModel())
    charged = run(series, signal, execution=Execution.NEXT_OPEN, costs=FULL_COSTS)
    held = run(series, buy_and_hold, execution=Execution.NEXT_OPEN)

    curves = [
        ("buy and hold", normalised(held), 2),
        ("before costs", normalised(free), 0),
        ("after costs", normalised(charged), 1),
    ]

    rows = [
        (
            label,
            evaluate(
                run(series, signal, execution=Execution.NEXT_OPEN, costs=model)
            ).annualised_return,
        )
        for label, model in LADDER
    ]

    OUT.mkdir(parents=True, exist_ok=True)
    for theme in (LIGHT, DARK):
        (OUT / f"equity-{theme.name}.svg").write_text(equity_chart(curves, theme))
        (OUT / f"costs-{theme.name}.svg").write_text(ladder_chart(rows, theme))

    print(f"wrote 4 figures to {OUT.relative_to(ROOT)}")
    for label, values, _ in curves:
        print(f"  {label:<14} ends at {values[-1]:.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
