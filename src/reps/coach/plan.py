"""Which exercises a session holds, fitted to the person's equipment and minutes."""

import datetime as dt
from dataclasses import asdict, dataclass, field, replace

from reps.coach.intervals import (
    HIIT_LEVEL,
    LEVELS,
    STEADY_LEVEL,
    STYLES,
    clock,
    is_levelled,
    last_level,
    level_name,
    whole,
)
from reps.coach.pace import Pace
from reps.coach.progression import Chosen, Performance, Track, offer, replay, targets
from reps.gym import Gym
from reps.library import EXERCISES, MACHINES, PATTERNS, Exercise

CORE_SESSIONS = 3
BLOCK_WEEKS = 5  # how long the main lifts stay the same
WARM_UP_SECONDS = 180
SET_SECONDS = 45
SIDE_SECONDS = 30
CHANGE_SECONDS = 40
STEP = 5  # minutes: times are in fives, to be easy to plan around
LEAST_FINISHER = STEP
LEAST_MINUTES, MOST_MINUTES = 30, 60

# Most important first: short sessions keep the front.
TEMPLATES = (
    ("squat", "hpush", "pull", "core", "hinge", "vpush", "arms", "arms"),
    ("hinge", "vpush", "pull", "lunge", "core", "bar", "hpush", "arms"),
    ("lunge", "hpush", "pull", "hinge", "core", "squat", "vpush", "arms"),
)
LEAST_REPS, MOST_REPS = 5, 15
REPS = (5, 6, 8, 10, 12, 15)  # on offer; any between may be typed
REPS_ROOM = 4
MAIN = {"squat", "hinge", "lunge", "hpush", "vpush", "pull"}
WEEKLY = ("squat", "hinge", "lunge", "hpush", "vpush", "pull", "core")

GOALS = {
    "strength": "Strength",
    "fitness": "Fitness",
    "muscle": "Muscle",
    "mobility": "Mobility",
}

FLOW = "Hips, hamstrings, chest and upper back: 30 seconds each side, easy breathing."


@dataclass(frozen=True)
class Profile:
    goals: tuple[str, ...] = ("strength", "fitness")
    equipment: frozenset[str] = frozenset()
    minutes: int = 45
    experience: str = "returning"
    avoid: frozenset[str] = frozenset()  # exercise or pattern keys, and anything that hurts
    body_kg: float | None = None
    gym: Gym = field(default_factory=Gym)
    reps: int = 0  # how many a set starts at, 0 for the coach's range
    hold: bool = False  # weights go up only when they say
    weights: dict[str, Chosen] = field(default_factory=dict)  # those they set themselves


@dataclass(frozen=True)
class Item:
    key: str
    name: str
    kind: str  # load, reps, time, or cardio (one timed block)
    sets: tuple[tuple[float, int], ...]  # (load, reps or seconds)
    rest: int = 60
    per_side: bool = False
    dumbbells: int = 0
    why: str = ""
    cue: str = ""
    last: str = ""
    work: int = 0  # cardio in intervals: seconds on, and off
    off: int = 0
    incline: int = 0  # the treadmill's; its speed is the load
    reps: int = 0  # what a set was set to be, 0 for the coach's range
    music: dict | None = None  # a playlist of its own, over the mix's

    def seconds(self) -> int:
        if self.kind == "cardio":
            return self.sets[0][1] + CHANGE_SECONDS
        working = SET_SECONDS + (SIDE_SECONDS if self.per_side else 0)
        return len(self.sets) * (working + self.rest) + CHANGE_SECONDS


@dataclass(frozen=True)
class Session:
    kind: str  # core or bonus
    title: str
    minutes: int
    items: tuple[Item, ...]

    def as_dict(self) -> dict:
        return asdict(self)


History = dict[str, list[Performance]]


def rep_range(profile: Profile, pattern: str) -> tuple[int, int]:
    if profile.reps:
        return profile.reps, profile.reps + REPS_ROOM
    heavy = "strength" in profile.goals and pattern in MAIN
    return (6, 10) if heavy else (8, 12)


def working_sets(profile: Profile) -> int:
    return 2 if profile.experience == "new" else 3


def available(profile: Profile, exercise: Exercise) -> bool:
    if not exercise.needs <= profile.equipment:
        return False
    if exercise.key in profile.avoid or exercise.pattern in profile.avoid:
        return False
    return exercise.kind != "load" or bool(profile.gym.loads(exercise, profile.body_kg))


def candidates(profile: Profile, pattern: str) -> list[Exercise]:
    """Return the best-ranked exercises this person can do for a pattern."""
    able = [
        exercise
        for exercise in EXERCISES.values()
        if exercise.pattern == pattern and available(profile, exercise)
    ]
    best = min((exercise.rank for exercise in able), default=0)
    return [exercise for exercise in able if exercise.rank == best]


def uncovered(profile: Profile) -> list[str]:
    return [PATTERNS[pattern] for pattern in WEEKLY if not candidates(profile, pattern)]


def _range(profile: Profile, exercise: Exercise) -> tuple[int, int]:
    if exercise.kind == "reps":
        return profile.reps or 8, 15
    return rep_range(profile, exercise.pattern)


def follow(
    profile: Profile, exercise: Exercise, history: History, today: dt.date, sets: int
) -> tuple[Track, str]:
    """Return where a person is on an exercise, and what they did last."""
    low, high = _range(profile, exercise)
    past = history.get(exercise.key, [])
    chosen = profile.weights.get(exercise.key) if exercise.kind == "load" else None
    if chosen:  # it starts that number of reps afresh, and the others keep their own
        past = [done for done in past if done.reps != chosen.reps or done.at > chosen.at]
        its_low, _ = _range(replace(profile, reps=chosen.reps), exercise)
        as_done = Performance(chosen.day, ((chosen.load, its_low),), 2, chosen.reps, chosen.at)
        past = sorted([as_done, *past], key=lambda done: (done.day, done.at))
    # Fives and twelves are lifted at different loads, so each number of reps is its own story.
    alike = [done for done in past if done.reps == profile.reps or exercise.kind == "time"]
    carried = None if alike else next((done for done in reversed(past) if done.sets), None)
    if chosen and chosen.reps == profile.reps:
        alike = [done for done in alike if done != as_done]
    else:
        chosen = None
    track = replay(
        exercise, alike, profile.gym.loads(exercise, profile.body_kg), low, high, sets, today,
        training_days(history), carried, chosen,
    )
    done = [each for each in alike if each.sets]
    if profile.hold and exercise.kind == "load" and done:
        offer(track, done[-1], profile.gym.loads(exercise, profile.body_kg), low)
    return track, last_time(alike or ([carried] if carried else []))


def prescribe(
    profile: Profile,
    exercise: Exercise,
    history: History,
    today: dt.date,
    sets: int | None = None,
) -> tuple[Item, bool]:
    """Return what to do for an exercise, and whether a harder version is due."""
    loads = profile.gym.loads(exercise, profile.body_kg)
    low, _ = _range(profile, exercise)
    sets = sets or working_sets(profile)
    track, last = follow(profile, exercise, history, today, sets)
    goal, why = targets(exercise, track, loads, low, sets, profile.hold)
    main = exercise.pattern in MAIN
    rest = 90 if main and profile.minutes >= 45 and "strength" in profile.goals else 60
    item = Item(
        key=exercise.key,
        name=exercise.name,
        kind=exercise.kind,
        sets=tuple((target.load, target.reps) for target in goal),
        rest=rest,
        per_side=exercise.per_side,
        dumbbells=exercise.dumbbells if exercise.kind == "load" else 0,
        why=why,
        cue=exercise.cue,
        last=last,
        reps=0 if exercise.kind == "time" else profile.reps,
    )
    return item, track.ceiling


def training_days(history: History) -> tuple[dt.date, ...]:
    return tuple(sorted({done.day for past in history.values() for done in past}))


def last_time(past: list[Performance]) -> str:
    done = [each for each in past if each.sets]
    return " ".join(str(count) for _, count in done[-1].sets) if done else ""


def with_harder(
    profile: Profile, exercise: Exercise, history: History, today: dt.date
) -> Item:
    """Prescribe an exercise, or its harder version once this one is outgrown."""
    item, outgrown = prescribe(profile, exercise, history, today)
    harder = EXERCISES.get(exercise.harder)
    if outgrown and harder and available(profile, harder):
        return with_harder(profile, harder, history, today)
    return item


def core_items(
    profile: Profile, index: int, block: int, history: History, today: dt.date
) -> list[Item]:
    chosen: list[Item] = []
    for pattern in TEMPLATES[index % CORE_SESSIONS]:
        fresh = [
            exercise
            for exercise in candidates(profile, pattern)
            if exercise.key not in {item.key for item in chosen}
        ]
        if not fresh:
            continue
        exercise = fresh[block % len(fresh)]
        item = with_harder(profile, exercise, history, today)
        if item.key not in {each.key for each in chosen}:
            chosen.append(item)
    return chosen


def in_fives(seconds: float) -> int:
    """Return a time in minutes, to the nearest five."""
    return max(STEP, STEP * round(seconds / 60 / STEP))


def total_seconds(items: list[Item], finisher_minutes: int, flow: bool) -> int:
    parts = WARM_UP_SECONDS + sum(item.seconds() for item in items)
    return parts + finisher_minutes * 60 + CHANGE_SECONDS + (5 * 60 if flow else 0)


def fit(profile: Profile, items: list[Item]) -> tuple[list[Item], int]:
    """Cut a session to its minutes: finisher first, then accessories, sets, main lifts."""
    flow = "mobility" in profile.goals
    finisher = STEP if profile.minutes < 45 else 2 * STEP
    least = LEAST_FINISHER
    if "fitness" in profile.goals and profile.minutes >= 60:
        least = 2 * STEP
    room = profile.minutes * 60

    def too_long() -> bool:
        return total_seconds(items, finisher, flow) > room

    items = list(items)
    while too_long() and finisher > least:
        finisher -= STEP
    while too_long() and len(items) > 4:
        items.pop()
    for ramps_too in (False, True):
        for place in reversed(range(len(items))):
            cuttable = len(items[place].sets) > 2 and (ramps_too or not _is_ramp(items[place]))
            if too_long() and cuttable:
                items[place] = replace(items[place], sets=items[place].sets[:2])
    while too_long() and len(items) > 1:
        items.pop()
    return items, finisher


def _is_ramp(item: Item) -> bool:
    climbs = len({load for load, _ in item.sets}) > 1
    return item.kind == "load" and item.last == "" and climbs


def machine(profile: Profile, turn: int) -> tuple[str, str]:
    owned = [
        key
        for key in MACHINES
        if key in profile.equipment and not {key, f"finisher-{key}"} & profile.avoid
    ]
    if not owned:
        return "circuit", "Bodyweight circuit"
    key = owned[turn % len(owned)]
    return key, MACHINES[key]


NO_PACE = Pace()
EFFORT_LEVELS = {"easy": -1, "normal": 0, "hard": 1}


def cardio(
    key: str,
    name: str,
    minutes: int,
    why: str,
    cue: str,
    work: int = 0,
    off: int = 0,
    *,
    level: int = 0,
    pace: Pace = NO_PACE,
    history: History | None = None,
    effort: str = "normal",
) -> Item:
    """Return a timed block. On a machine with levels, steady work repeats the last one used."""
    seconds = minutes * 60
    if work:
        seconds = whole(seconds, work, off)
        cue = cue or f"{clock(work)} hard, {clock(off)} easy."
    last = ""
    if is_levelled(key):
        usual = HIIT_LEVEL if work else last_level(history or {}, key) or STEADY_LEVEL
        level = level or max(1, min(len(LEVELS), usual + EFFORT_LEVELS[effort]))
        last = level_name(last_level(history or {}, key))
        pace = Pace(level)
    return Item(key, name, "cardio", ((pace.load, seconds),), rest=0, why=why, cue=cue, last=last,
                work=work, off=off, incline=pace.incline)


def finisher_item(
    profile: Profile, turn: int, minutes: int, history: History | None = None
) -> Item:
    key, name = machine(profile, turn)
    title, why = f"Finisher: {name.lower()}", "Ends the session."
    if key == "circuit":
        cue = "30 seconds each, round and round: squats, push-ups, lunges, plank."
        return cardio(f"finisher-{key}", title, minutes, why, cue)
    return cardio(f"finisher-{key}", title, minutes, why, "", *STYLES["20-40"], history=history)


def core_session(
    profile: Profile, index: int, week: int, history: History, today: dt.date
) -> Session:
    block = week // BLOCK_WEEKS
    items, minutes = fit(profile, core_items(profile, index, block, history, today))
    items.append(finisher_item(profile, index + week, minutes, history))
    if "mobility" in profile.goals:
        items.append(cardio("flow", "Mobility flow", 5, "Mobility is one of your goals.", FLOW))
    title = f"Core {index + 1} of {CORE_SESSIONS}"
    return Session("core", title, profile.minutes, tuple(items))


def bonus_session(profile: Profile, index: int, week: int, history: History) -> Session:
    turn = index - CORE_SESSIONS
    kinds = ["cardio", "circuit", "mobility"]
    if profile.goals[0] == "mobility":
        kinds = ["mobility", "cardio", "circuit"]
    kind = kinds[turn % len(kinds)]
    working = profile.minutes - 5
    if kind == "mobility":
        item = cardio("flow", "Mobility flow", min(working, 30), "An easy day.", FLOW)
    elif kind == "circuit":
        item = finisher_item(profile, turn + week, min(working, 25), history)
        item = replace(item, name=item.name.replace("Finisher: ", "").capitalize())
        item = replace(item, why="Intervals, on top of your three sessions.")
    else:
        key, name = machine(profile, turn + week)
        cue = "Steady: you could talk in sentences." if key != "circuit" else "Brisk, not hard."
        why = "Steady cardio, on top of your three sessions."
        item = cardio(key, name, working, why, cue, history=history)
    return Session("bonus", "Bonus", profile.minutes, (item,))


def next_session(
    profile: Profile, done_this_week: int, week: int, history: History, today: dt.date
) -> Session:
    """Return the session after ``done_this_week`` others, in the ISO week number ``week``."""
    if done_this_week < CORE_SESSIONS:
        return core_session(profile, done_this_week, week, history, today)
    return bonus_session(profile, done_this_week, week, history)


def swap(
    profile: Profile, key: str, used: set[str], history: History, today: dt.date
) -> Item | None:
    """Return another exercise for the same pattern, or None if there isn't one."""
    pattern = EXERCISES[key].pattern
    others = [
        exercise
        for exercise in sorted(EXERCISES.values(), key=lambda each: each.rank)
        if exercise.pattern == pattern
        and exercise.key not in used
        and available(profile, exercise)
    ]
    return prescribe(profile, others[0], history, today)[0] if others else None
