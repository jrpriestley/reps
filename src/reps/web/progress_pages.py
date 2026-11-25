"""Progress: the total lifted each week, and a line for each exercise."""

import datetime as dt

from reps.coach.progress import Line, Point
from reps.gym import pounds
from reps.web import chart
from reps.web.chart import Mark
from reps.web.layout import framed
from reps.web.ui import head, icon_of, row, rows, tone_of

SPARKS = 12  # the latest days a row's small line shows


def _amount(line: Line, point: Point) -> str:
    if line.unit == "lb":
        return pounds(point.value)
    return f"{point.value:g} {line.unit}" if line.unit == "reps" else f"{point.value:g}s"


def _words(line: Line, point: Point) -> str:
    reps = f" × {point.reps}" if point.reps else ""
    return f"{_amount(line, point)}{reps} · {point.day:%a %-d %b}"


def _since(line: Line) -> str:
    first, last = line.points[0], line.points[-1]
    if len(line.points) == 1:
        return f"First on {first.day:%-d %b}"
    if first.value == last.value:
        return f"Same since {first.day:%-d %b}"
    return f"{_amount(line, first)} on {first.day:%-d %b}"


def _row(line: Line) -> str:
    key = line.exercise.key
    small = chart.spark([point.value for point in line.points[-SPARKS:]])
    return row(
        line.exercise.name, _since(line), href=f"/progress/{key}", mark=icon_of(key),
        tone=tone_of(key),
        right=f'{small}<span class="big-amount">{_amount(line, line.points[-1])}</span>',
    )


def _weeks(weeks: list[tuple[dt.date, float]]) -> str:
    this = weeks[-1][0]
    marks = [
        Mark(total, f"{total:,.0f} lb · " + (
            "this week" if monday == this else f"week of {monday:%-d %b}"))
        for monday, total in weeks
    ]
    drawn = chart.columns("Lifted each week", marks, f"{weeks[0][0]:%-d %b}", "This week")
    return f'<section class="panel lifting">{drawn}</section>'


def progress_page(
    person: str, views: str, weeks: list[tuple[dt.date, float]], lines: list[Line]
) -> str:
    title = head("History", aside=views)
    if not lines:
        empty = rows([], "Nothing yet. What you lift will show here.")
        return framed("history", "Progress", title + empty, person)
    listed = "".join(_row(line) for line in lines)
    return framed("history", "Progress", (
        f'{title}<div class="scroll grow progress">'
        f'{_weeks(weeks) if any(total for _, total in weeks) else ""}'
        f'<section class="panel"><ul class="rows">{listed}</ul></section></div>'
    ), person, ("chart.js",))


def exercise_page(person: str, line: Line) -> str:
    first, last = line.points[0].day, line.points[-1].day
    span = max(1, (last - first).days)
    marks = [
        Mark(point.value, _words(line, point), (point.day - first).days / span)
        for point in line.points
    ]
    title = {"lb": "Heaviest that day, each dumbbell", "reps": "Most reps that day",
             "s": "Longest that day"}[line.unit]
    if line.unit == "lb" and line.exercise.dumbbells == 1:
        title = "Heaviest that day"
    drawn = chart.line(title, marks, f"{first:%-d %b}", f"{last:%-d %b}")
    days = [
        row(f"{point.day:%a %-d %b}", f"{point.reps} reps at that" if point.reps else "",
            right=f'<span class="big-amount">{_amount(line, point)}</span>')
        for point in reversed(line.points)
    ]
    return framed("history", line.exercise.name, (
        f'{head(line.exercise.name, "Progress", "/history?view=progress")}'
        f'<div class="two-up"><section class="panel lifting">{drawn}</section>'
        f'{rows(days)}</div>'
    ), person, ("chart.js",))
