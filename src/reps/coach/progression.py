"""What to lift next, worked out by replaying what was lifted."""

import datetime as dt
import math
from collections import Counter
from dataclasses import dataclass, field

from reps.gym import pounds
from reps.library import Exercise

EARN_CAP = 18  # past this many reps the estimate means little, so the step is bridged instead
BRIDGE_REPS = 5  # a bridged set goes well at this many
UNLOADED_TOP = 15
CEILING_REPS = 20
LONG_LAYOFF_DAYS = 42


@dataclass(frozen=True)
class Performance:
    """One session's sets of one exercise: (load, reps or seconds) in the order done."""

    day: dt.date
    sets: tuple[tuple[float, int], ...]
    rir: int | None = None  # 4 means four or more
    reps: int = 0  # how many a set was set to be then, 0 for the coach's own range
    at: float = 0.0  # when it was begun


@dataclass(frozen=True)
class Chosen:
    """A weight a person set for an exercise themselves, and the reps it is for."""

    load: float
    at: float
    reps: int = 0

    @property
    def day(self) -> dt.date:
        return dt.datetime.fromtimestamp(self.at).date()


@dataclass
class Track:
    """Where a person is on one exercise."""

    load: float | None = None
    reps: list[int] = field(default_factory=list)
    misses: int = 0
    bridge: int = 0  # sets to do at the next load
    extra: dict[float, int] = field(default_factory=dict)
    stepped_from: float | None = None
    settling: bool = False  # at a weight they set, until it has been lifted once
    floor: int = 0  # reps that will do for now, after a bridge to a much heavier load
    ceiling: bool = False
    offered: bool = False  # a heavier load is earned, and left for them to take
    last_load: float | None = None  # where a ramp-up after time off starts from
    why: str = ""


@dataclass(frozen=True)
class Target:
    load: float
    reps: int


def exact_earn(load: float, heavier: float, low: int) -> float:
    """Reps at 1 in reserve that predict ``low`` reps at ``heavier`` with 1 in reserve (Epley)."""
    return 30 * (heavier * (1 + (low + 1) / 30) / load - 1) - 1


def earn_reps(load: float, heavier: float, low: int) -> int:
    return min(EARN_CAP, math.ceil(exact_earn(load, heavier, low) - 1e-9))


def needs_bridge(load: float, heavier: float, low: int) -> bool:
    return exact_earn(load, heavier, low) > EARN_CAP


def shift(loads: tuple[float, ...], load: float, steps: int) -> float:
    """Return the load ``steps`` settings away, staying inside what the gym can make."""
    at_or_below = [place for place, each in enumerate(loads) if each <= load]
    place = at_or_below[-1] if at_or_below else 0
    return loads[max(0, min(len(loads) - 1, place + steps))]


def heavier_than(loads: tuple[float, ...], load: float) -> float | None:
    return next((each for each in loads if each > load), None)


def _settle_first_load(track: Track, done: Performance, loads: tuple[float, ...], low: int):
    last_load = done.sets[-1][0]
    rir = 0 if done.rir is None else done.rir
    if rir >= 4:
        steps = 1
    elif rir <= 1 and not (track.settling and done.rir is None):
        steps = -1
    else:
        steps = 0
    track.load = shift(loads, last_load, steps)
    track.reps = [low]
    if track.settling:
        felt = {1: "one up, as it left plenty", -1: "one down, as it left little"}
        track.why = f"The weight you set, {felt.get(steps, 'as it felt right')}."
    else:
        track.why = "Your first working load, from the ramp-up."
    track.settling = False


def _advance_bridge(
    track: Track, done: Performance, heavier: float, rir: int, sets: int, low: int
) -> None:
    bridged = [reps for load, reps in done.sets if load == heavier]
    if len(bridged) < track.bridge or min(bridged) < BRIDGE_REPS or rir < 1:
        track.why = f"Same again: {track.bridge} set at the new load."
        return
    track.bridge += 1
    if track.bridge >= sets:
        track.stepped_from, track.load = track.load, heavier
        track.bridge = 0
        track.floor = max(BRIDGE_REPS, min(low, min(bridged)))
        track.reps = [track.floor]
        track.why = f"All sets at {pounds(heavier)} now."
    else:
        track.why = f"One more set at {pounds(heavier)}."


def _advance_loaded(
    track: Track, done: Performance, loads: tuple[float, ...], low: int, high: int, sets: int
) -> None:
    rir = 0 if done.rir is None else done.rir
    if track.load is None or track.settling:
        _settle_first_load(track, done, loads, low)
        return
    heavier = heavier_than(loads, track.load)
    if track.bridge and heavier:
        _advance_bridge(track, done, heavier, rir, sets, low)
        return
    counts = Counter(load for load, _ in done.sets)
    used = min(counts, key=lambda load: (-counts[load], load))
    if used != track.load:
        track.load, track.misses, track.stepped_from = shift(loads, used, 0), 0, None
        track.floor = 0
        heavier = heavier_than(loads, track.load)
    reps = [count for load, count in done.sets if load == used]
    track.reps = reps
    worst = min(reps)
    if worst < low and worst >= track.floor > 0:
        track.floor = worst
        track.misses = 0
        track.why = f"Building up to {low}s at {pounds(used)}."
        return
    track.floor = 0
    if worst < low:
        _miss(track, loads, low, reps)
        return
    track.misses = 0
    if heavier is None:
        track.stepped_from = None
        _at_ceiling(track, worst, high, rir)
        return
    second = heavier_than(loads, heavier)
    if rir >= 4 and second and worst + rir - 1 >= exact_earn(track.load, second, low):
        _step(track, second, low, f"You got {worst}s with 4 or more left, so two loads up.")
        return
    needed = earn_reps(track.load, heavier, low) + track.extra.get(track.load, 0)
    if worst >= needed and rir >= 1:
        if needs_bridge(track.load, heavier, low):
            track.bridge = 1
            track.why = f"{pounds(heavier)} is a big jump: first set there, the rest as before."
        else:
            why = f"You got {worst}s at {pounds(track.load)} with {rir} left."
            _step(track, heavier, low, why)
        return
    track.stepped_from = None
    track.why = f"{needed}s at {pounds(track.load)} earn the next load; last time {worst}s."


def _step(track: Track, load: float, low: int, why: str) -> None:
    track.stepped_from, track.load = track.load, load
    track.reps = [low]
    track.why = why


def _miss(track: Track, loads: tuple[float, ...], low: int, reps: list[int]) -> None:
    track.misses += 1
    if track.stepped_from is not None:
        previous = track.stepped_from
        track.extra[previous] = min(track.extra.get(previous, 0) + 1, 4)
        track.stepped_from = None
    if track.misses >= 2:
        track.load = shift(loads, track.load, -1)
        track.misses = 0
        track.reps = [low]
        track.why = f"Under {low} reps twice, so one load down."
    else:
        track.reps = [max(low, count) for count in reps]
        track.why = f"Under {low} reps last time: same again."


def _at_ceiling(track: Track, worst: int, high: int, rir: int) -> None:
    track.why = "Heaviest load there is, so more reps."
    if worst >= high and rir >= 1:
        track.ceiling = True
    if worst >= CEILING_REPS:
        track.why = "Heaviest load and 20 reps: lower for three seconds."


def _advance_unloaded(track: Track, done: Performance, top: int) -> None:
    track.reps = [count for _, count in done.sets]
    worst = min(track.reps)
    track.ceiling = track.ceiling or (worst >= top and (done.rir or 0) >= 1)
    track.why = f"Building to {top}."


def replay(
    exercise: Exercise,
    history: list[Performance],
    loads: tuple[float, ...],
    low: int,
    high: int,
    sets: int,
    today: dt.date,
    trained: tuple[dt.date, ...] | None = None,
    carried: Performance | None = None,
    chosen: Chosen | None = None,
) -> Track:
    """Return where a person is on an exercise after everything they've done (oldest first).

    ``carried`` is their latest with another number of reps, where there is none with this one.
    ``chosen`` is the weight they set, which ``history`` then follows on from.
    ``trained`` is every day they trained at all: time off means no training, not a rest
    from one exercise.
    """
    track = Track()
    previous: dt.date | None = None
    if chosen and exercise.kind == "load":
        track.load, track.reps, track.settling = chosen.load, [low], True
        track.why = f"You set {pounds(chosen.load)}."
        previous = chosen.day
    elif carried and exercise.kind == "load":
        _carry_over(track, carried, loads, low)
        previous = carried.day
    for done in [each for each in history if each.sets] + [None]:
        day = done.day if done else today
        if previous and exercise.kind == "load" and track.load is not None:
            _ease_after_layoff(track, _longest_gap(previous, day, trained), loads, low)
        if done is None:
            break
        if exercise.kind == "load":
            _advance_loaded(track, done, loads, low, high, sets)
        else:
            _advance_unloaded(track, done, 60 if exercise.kind == "time" else UNLOADED_TOP)
        previous = done.day
    if track.load is not None and track.load not in loads:
        track.load = shift(loads, track.load, 0)  # the step's limit came down
    return track


def _carry_over(track: Track, done: Performance, loads: tuple[float, ...], low: int) -> None:
    """Start at the load that should leave 2 in reserve at ``low`` reps (Epley)."""
    load, reps = done.sets[-1]
    best = load * (1 + (reps + (done.rir or 0)) / 30)
    track.load = shift(loads, best / (1 + (low + 2) / 30), 0)
    track.reps = [low]
    track.why = f"Worked out from your {reps}s at {pounds(load)}."


def _longest_gap(start: dt.date, end: dt.date, trained: tuple[dt.date, ...] | None) -> int:
    between = sorted(day for day in trained or () if start < day < end)
    days = [start, *between, end]
    return max((later - earlier).days for earlier, later in zip(days, days[1:], strict=False))


def _ease_after_layoff(track: Track, days: int, loads: tuple[float, ...], low: int) -> None:
    if days >= LONG_LAYOFF_DAYS:
        track.last_load, track.load, track.settling = track.load, None, False
        track.bridge, track.misses, track.reps = 0, 0, []
        track.why = f"{days} days off, so finding your load again."
    elif days >= 10:
        steps = 1 if days < 21 else 2
        track.load = shift(loads, track.load, -steps)
        track.bridge, track.misses, track.reps = 0, 0, [low]
        track.why = f"{days} days off, so {'one load' if steps == 1 else 'two loads'} down."


def offer(track: Track, done: Performance, loads: tuple[float, ...], low: int) -> None:
    """Leave a load that would go up where it was last, and say that it is earned."""
    last = shift(loads, done.sets[-1][0], 0)
    heavier = heavier_than(loads, last) if track.bridge else track.load
    if track.load is None or heavier is None or heavier <= last:
        return
    reps = [count for load, count in done.sets if load == last]
    track.load, track.bridge, track.offered = last, 0, True
    track.reps = [max(low, min(count, CEILING_REPS)) for count in reps]
    track.why = f"{pounds(heavier)} is earned: press + to take it."


def targets(
    exercise: Exercise,
    track: Track,
    loads: tuple[float, ...],
    low: int,
    sets: int,
    hold: bool = False,
) -> tuple[list[Target], str]:
    """Return the sets to do and the reason for them. With ``hold`` none goes up by itself."""
    if exercise.kind == "time":
        seconds = min([*track.reps, 60]) if track.reps else 25
        return [Target(0, min(60, seconds + 5))] * sets, track.why or "A first go."
    if exercise.kind == "reps":
        goal = _one_more(track.reps, sets, UNLOADED_TOP) if track.reps else [low] * sets
        return [Target(0, reps) for reps in goal], track.why or "A first go."
    if track.load is None:
        first = exercise.start
        if track.last_load is not None:
            first = min(first, shift(loads, track.last_load, -4))
        if hold:
            why = track.why or "First time: set a weight that leaves 2 or 3."
            return [Target(first, low)] * sets, why
        ramp = [shift(loads, first, step) for step in range(3)]
        why = track.why or "First time: work up, and stop at about 3 left."
        return [Target(load, low) for load in ramp], why
    reps = _one_more(track.reps, sets, CEILING_REPS) if track.reps != [low] else [low] * sets
    if track.offered:
        reps = (track.reps + [min(track.reps)] * sets)[:sets]
    goal = [Target(track.load, count) for count in reps]
    heavier = heavier_than(loads, track.load)
    if track.bridge and heavier:
        for place in range(min(track.bridge, sets)):
            goal[place] = Target(heavier, BRIDGE_REPS)
    return goal, track.why


def _one_more(reps: list[int], sets: int, top: int) -> list[int]:
    goal = [min(count, top) for count in (reps + [min(reps)] * sets)[:sets]]
    weakest = goal.index(min(goal))
    goal[weakest] = min(top, goal[weakest] + 1)
    return goal
