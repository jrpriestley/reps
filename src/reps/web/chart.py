"""Charts drawn as SVG: columns and a line, each with a point to touch for its words.

Only presentation attributes are used: the page's policy allows no inline style.
"""

import math
from dataclasses import dataclass

from reps.web.ui import esc

WIDTH, HEIGHT = 340, 150
LEFT, RIGHT, TOP, BOTTOM = 40, 12, 16, 20
PLOT_WIDTH, PLOT_HEIGHT = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM
BASE = TOP + PLOT_HEIGHT
COLUMN = 18
FEW = 8


@dataclass(frozen=True)
class Mark:
    """A value, the words shown when it is touched, and its place along the axis (0 to 1)."""

    value: float
    words: str
    at: float = 0


def ticks(low: float, high: float, most: int = 4) -> list[float]:
    """Return a few round numbers that reach from below ``low`` to above ``high``."""
    if high <= low:
        low, high = max(0, low - 5), high + 5
    rough = (high - low) / (most - 1)
    size = 10 ** math.floor(math.log10(rough))
    step = next(size * each for each in (1, 2, 2.5, 5, 10) if size * each >= rough)
    first = math.floor(low / step)
    last = math.ceil(high / step)
    return [step * each for each in range(first, last + 1)]


def short(number: float, thousands: bool) -> str:
    return f"{number / 1000:g}K" if thousands and number else f"{number:,g}"


def _axis(marks: list[float]) -> str:
    low, high = marks[0], marks[-1]
    drawn = ""
    for mark in marks:
        y = BASE - PLOT_HEIGHT * (mark - low) / (high - low)
        drawn += (
            f'<line class="rule" x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>'
            f'<text x="{LEFT - 6}" y="{y + 3.5:.1f}" text-anchor="end">'
            f"{short(mark, high >= 10000)}</text>"
        )
    return drawn


def _ends(first: str, last: str) -> str:
    y = HEIGHT - 4
    return (
        f'<text x="{LEFT}" y="{y}">{esc(first)}</text>'
        f'<text x="{WIDTH - RIGHT}" y="{y}" text-anchor="end">{esc(last)}</text>'
    )


def _framed(title: str, first: str, shown: str, inside: str) -> str:
    """Return the chart under its words, which touching a mark changes."""
    return (
        f'<figure class="chart" data-chart><figcaption><b>{esc(title)}</b>'
        f'<span data-words aria-live="polite">{esc(shown)}</span></figcaption>'
        f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" role="group" aria-label="{esc(title)}. '
        f'{esc(first)}">{inside}</svg></figure>'
    )


def _touch(x: float, width: float, mark: Mark, inside: str) -> str:
    """Return a mark with a target the whole height of the plot, wider than what is drawn."""
    left = min(max(x - width / 2, 0), WIDTH - width)
    return (
        f'<g class="mark" tabindex="0" role="img" data-mark="{esc(mark.words)}" '
        f'aria-label="{esc(mark.words)}"><rect class="reach" x="{left:.1f}" y="0" '
        f'width="{width:.1f}" height="{HEIGHT}"/>{inside}</g>'
    )


def columns(title: str, marks: list[Mark], first: str, last: str) -> str:
    """Return a column for each mark, from nothing up."""
    scale = ticks(0, max((mark.value for mark in marks), default=0) or 1)
    slot = PLOT_WIDTH / len(marks)
    drawn = _axis(scale)
    for n, mark in enumerate(marks):
        x = LEFT + slot * (n + 0.5)
        tall = PLOT_HEIGHT * mark.value / scale[-1]
        column = ""
        if mark.value:
            # Rounded at its end, and square where it stands.
            curve = min(4, tall)
            left, right, top = x - COLUMN / 2, x + COLUMN / 2, BASE - tall
            column = (
                f'<path class="fill" d="M{left:.1f} {BASE}V{top + curve:.1f}'
                f"q0 {-curve:.1f} 4 {-curve:.1f}H{right - 4:.1f}"
                f'q4 0 4 {curve:.1f}V{BASE}z"/>'
            )
        drawn += _touch(x, slot, mark, column)
    return _framed(title, f"{len(marks)} weeks", marks[-1].words, drawn + _ends(first, last))


def line(title: str, marks: list[Mark], first: str, last: str) -> str:
    """Return a line through the marks, each at its own place in time."""
    values = [mark.value for mark in marks]
    scale = ticks(min(values), max(values))
    low, high = scale[0], scale[-1]
    inner = PLOT_WIDTH - 12
    places = [
        (LEFT + 6 + inner * mark.at, BASE - PLOT_HEIGHT * (mark.value - low) / (high - low))
        for mark in marks
    ]
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in places)
    # Dots would run together, so beyond a few only the last shows, and the one touched.
    quiet = " quiet" if len(marks) > FEW else ""
    drawn = f'{_axis(scale)}<polyline class="stroke" points="{path}"/>'
    for n, (mark, (x, y)) in enumerate(zip(marks, places, strict=True)):
        gaps = [abs(x - places[m][0]) for m in (n - 1, n + 1) if 0 <= m < len(places)]
        drawn += _touch(
            x, min([*gaps, 44]) or 2, mark,
            f'<line class="cross" x1="{x:.1f}" x2="{x:.1f}" y1="{TOP}" y2="{BASE}"/>'
            f'<circle class="dot{quiet if n < len(marks) - 1 else ""}" '
            f'cx="{x:.1f}" cy="{y:.1f}" r="4"/>',
        )
    ends = _ends(first, last) if len(marks) > 1 else _ends(first, "")
    return _framed(title, f"{len(marks)} days", marks[-1].words, drawn + ends)


def spark(values: list[float]) -> str:
    """Return a small line for a row, whose words say the same."""
    wide, tall, edge = 72, 28, 5
    low, high = min(values), max(values)
    across = (wide - 2 * edge) / max(1, len(values) - 1)
    places = [
        (edge + across * n if len(values) > 1 else wide - edge,
         tall / 2 if high == low
         else tall - edge - (tall - 2 * edge) * (value - low) / (high - low))
        for n, value in enumerate(values)
    ]
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in places)
    x, y = places[-1]
    return (
        f'<svg class="spark" viewBox="0 0 {wide} {tall}" aria-hidden="true">'
        f'<polyline class="stroke" points="{path}"/>'
        f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="4"/></svg>'
    )
