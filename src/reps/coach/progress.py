"""What has been lifted over time: pure functions over a person's history."""

import datetime as dt
from dataclasses import dataclass

from reps.coach.progression import Performance
from reps.library import EXERCISES, Exercise

WEEKS = 12
UNITS = {"load": "lb", "reps": "reps", "time": "s"}


@dataclass(frozen=True)
class Point:
    """The best of one day: the heaviest weight, or the most reps or seconds without one."""

    day: dt.date
    value: float
    reps: int = 0  # at that weight


@dataclass(frozen=True)
class Line:
    exercise: Exercise
    points: tuple[Point, ...]

    @property
    def unit(self) -> str:
        return UNITS[self.exercise.kind]


def _best(exercise: Exercise, done: Performance) -> Point | None:
    if exercise.kind != "load":
        return Point(done.day, max(amount for _, amount in done.sets))
    lifted = [(load, reps) for load, reps in done.sets if load and reps]
    return Point(done.day, *max(lifted)) if lifted else None


def lines(history: dict[str, list[Performance]]) -> list[Line]:
    """Return a line for each exercise done, the latest done first."""
    found = []
    for key, work in history.items():
        exercise = EXERCISES.get(key)
        if exercise is None:  # cardio
            continue
        days: dict[dt.date, Point] = {}
        for done in work:
            point = _best(exercise, done) if done.sets else None
            if point and (point.day not in days
                          or (point.value, point.reps) > _rank(days[point.day])):
                days[point.day] = point
        if days:
            found.append(Line(exercise, tuple(days[day] for day in sorted(days))))
    return sorted(found, key=lambda line: (-line.points[-1].day.toordinal(), line.exercise.name))


def _rank(point: Point) -> tuple[float, int]:
    return point.value, point.reps


def lifted(exercise: Exercise, done: Performance) -> float:
    """Return the pounds moved: every dumbbell, every rep, and both sides of a one-sided one."""
    if exercise.kind != "load":
        return 0
    held = exercise.dumbbells * (2 if exercise.per_side else 1)
    return sum(load * reps for load, reps in done.sets) * held


def by_week(
    history: dict[str, list[Performance]], today: dt.date, weeks: int = WEEKS
) -> list[tuple[dt.date, float]]:
    """Return the total lifted in each week to this one, by the Monday it began."""
    this = today - dt.timedelta(days=today.weekday())
    totals = {this - dt.timedelta(weeks=ago): 0.0 for ago in reversed(range(weeks))}
    for key, work in history.items():
        if exercise := EXERCISES.get(key):
            for done in work:
                monday = done.day - dt.timedelta(days=done.day.weekday())
                if monday in totals:
                    totals[monday] += lifted(exercise, done)
    return list(totals.items())
