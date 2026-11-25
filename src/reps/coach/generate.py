"""A workout made to order: a focus, minutes and effort, inside the person's setup.

It chooses as the coach does, so equipment, what to avoid, experience, goals, the step's limit
and the loads are all the person's own.
"""

import datetime as dt
from dataclasses import asdict, dataclass, replace

from reps.coach import pace
from reps.coach.custom import CARDIO, can_do, clamp_reps
from reps.coach.intervals import STYLES
from reps.coach.plan import (
    FLOW,
    LEAST_FINISHER,
    STEP,
    History,
    Item,
    Profile,
    Session,
    available,
    cardio,
    finisher_item,
    prescribe,
    total_seconds,
    working_sets,
)
from reps.library import EXERCISES, MACHINES, Exercise

FOCUSES = {
    "full": "Full body",
    "upper": "Upper body",
    "lower": "Lower body",
    "core": "Core",
    "cardio": "Cardio",
    "mobility": "Mobility",
}
EFFORTS = {"easy": "Easy", "normal": "Normal", "hard": "Hard"}
LEAST_MINUTES, MOST_MINUTES = 15, 60
MOST_SETS = 4
MOST_HIIT = 20  # minutes of intervals, rests included
LEAST_HIIT = 20  # minutes that leave room for an easy start and end
FLOW_MINUTES = 5

# Most important first: short sessions keep the front.
PATTERNS = {
    "full": ("squat", "hpush", "pull", "hinge", "core", "vpush", "lunge", "arms"),
    "upper": ("hpush", "pull", "vpush", "arms", "pull", "hpush", "bar", "arms"),
    "lower": ("squat", "hinge", "lunge", "core", "hinge", "squat", "lunge"),
    "core": ("core", "core", "core", "core"),
}
TONES = {"cardio": "steady", "mobility": "cooldown"}


@dataclass(frozen=True)
class Order:
    """What was asked for. ``turn`` goes up each time they ask for another."""

    focus: str = "full"
    minutes: int = 45
    effort: str = "normal"
    machine: str = ""  # '' for any
    style: str = "steady"
    turn: int = 0
    reps: int = 0  # a set, 0 for the person's usual

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def read(cls, given: dict) -> "Order":
        """Return an order from stored or posted fields, with sense made of odd ones."""
        usual = cls()
        minutes = _whole(given.get("minutes"), usual.minutes)
        machine = str(given.get("machine") or "")
        return cls(
            focus=_one_of(given.get("focus"), FOCUSES, usual.focus),
            minutes=max(LEAST_MINUTES, min(MOST_MINUTES, round(minutes / 5) * 5)),
            effort=_one_of(given.get("effort"), EFFORTS, usual.effort),
            machine=machine if machine in MACHINES else "",
            style=_one_of(given.get("style"), STYLES, usual.style),
            turn=max(0, _whole(given.get("turn"), 0)),
            reps=clamp_reps(_whole(given.get("reps"), 0)),
        )


def _one_of(value: object, known: dict, usual: str) -> str:
    return value if isinstance(value, str) and value in known else usual


def _whole(value: object, usual: int) -> int:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return usual


def machines(profile: Profile) -> list[str]:
    return [
        key for key in MACHINES
        if can_do(profile, key) and f"finisher-{key}" not in profile.avoid
    ]


def focuses(profile: Profile) -> list[str]:
    """Return the focuses this person's setup can cover."""
    return [
        focus for focus in FOCUSES
        if focus not in PATTERNS
        or any(_able(profile, pattern) for pattern in PATTERNS[focus])
    ]


def _able(profile: Profile, pattern: str) -> list[Exercise]:
    return [
        exercise for exercise in EXERCISES.values()
        if exercise.pattern == pattern and available(profile, exercise)
    ]


def _choose(
    profile: Profile, pattern: str, taken: set[str], history: History, turn: int
) -> Exercise | None:
    """Return the best-ranked exercise not done lately, moving on one for each ``turn``."""
    free = [each for each in _able(profile, pattern) if each.key not in taken]
    if not free:
        return None
    best = min(each.rank for each in free)
    ranked = [each for each in free if each.rank == best]

    def last_done(exercise: Exercise) -> dt.date:
        past = history.get(exercise.key)
        return past[-1].day if past else dt.date.min

    ranked.sort(key=last_done)
    return ranked[turn % len(ranked)]


def generate(profile: Profile, order: Order, history: History, today: dt.date) -> Session:
    if order.focus not in focuses(profile):
        order = replace(order, focus="mobility")
    profile = replace(profile, minutes=order.minutes, reps=order.reps or profile.reps)
    if order.focus == "mobility":
        why = "Mobility, as you asked."
        items = [cardio("flow", "Mobility flow", order.minutes - STEP, why, FLOW)]
    elif order.focus == "cardio":
        items = _cardio_items(profile, order, history)
    else:
        items = _strength_items(profile, order, history, today)
    return Session("core", FOCUSES[order.focus], order.minutes, tuple(items))


def _cardio_items(profile: Profile, order: Order, history: History) -> list[Item]:
    owned = machines(profile)
    chosen = order.machine if order.machine in owned else ""
    if not chosen:
        chosen = owned[order.turn % len(owned)] if owned else "circuit"
    on, off = STYLES[order.style]
    minutes = order.minutes - STEP  # setting up, and changes of pace
    why = "Cardio, as you asked."
    if not on or order.minutes < LEAST_HIIT or chosen == "circuit":
        item = cardio(chosen, CARDIO[chosen], minutes, why, "", on, off, history=history,
                      effort=order.effort)
        return [item]
    # Intervals sit between an easy start and an easy end, and stop at what is sensible.
    hard_minutes = min(MOST_HIIT, minutes - 2 * STEP)
    cool_minutes = (minutes - hard_minutes) // 2 // STEP * STEP
    warm_minutes = minutes - hard_minutes - cool_minutes
    name = CARDIO[chosen]
    warm = cardio(chosen, f"{name}: warm up", warm_minutes, "Easy, before the intervals.", "",
                  level=1, history=history)
    hard = cardio(f"{chosen}#2", f"{name}: intervals", hard_minutes, why, "", on, off,
                  history=history, effort=order.effort)
    cool = cardio(f"{chosen}#3", f"{name}: cool down", cool_minutes, "Easy, to finish.", "",
                  level=1, history=history)
    return [warm, hard, cool]


def _strength_items(
    profile: Profile, order: Order, history: History, today: dt.date
) -> list[Item]:
    sets = working_sets(profile) + {"easy": -1, "normal": 0, "hard": 1}[order.effort]
    sets = max(1, min(MOST_SETS, sets))
    items: list[Item] = []
    for pattern in PATTERNS[order.focus]:
        taken = {item.key for item in items}
        exercise = _choose(profile, pattern, taken, history, order.turn)
        if exercise:
            item, _ = prescribe(profile, exercise, history, today, sets)
            items.append(replace(item, sets=item.sets[:max(sets, 2)]))
    finisher = LEAST_FINISHER if "fitness" in profile.goals and order.minutes >= 30 else 0
    flow = "mobility" in profile.goals and order.minutes >= 30
    _fit(items, order.minutes * 60, finisher, flow)
    if finisher:
        items.append(finisher_item(profile, order.turn, finisher, history))
    if flow:
        why = "Mobility is one of your goals."
        items.append(cardio("flow", "Mobility flow", FLOW_MINUTES, why, FLOW))
    return items


def _fit(items: list[Item], room: int, finisher: int, flow: bool) -> None:
    """Cut a list to its time: the last exercises first, then sets."""
    while len(items) > 3 and total_seconds(items, finisher, flow) > room:
        items.pop()
    for place in reversed(range(len(items))):
        while len(items[place].sets) > 1 and total_seconds(items, finisher, flow) > room:
            items[place] = replace(items[place], sets=items[place].sets[:-1])
    while len(items) > 1 and total_seconds(items, finisher, flow) > room:
        items.pop()


def as_blocks(session: Session, most: int) -> list[dict]:
    """Return a generated workout as the blocks of a saved one."""
    blocks = []
    for item in session.items:
        key = item.key.split("#")[0].removeprefix("finisher-")
        if key in CARDIO:
            blocks.append({"options": [key], "minutes": max(1, round(item.sets[0][1] / 60)),
                           "style": f"{item.work}-{item.off}" if item.work else "steady",
                           **pace.as_fields(key, item.sets[0][0], item.incline)})
        else:
            blocks.append({"options": [key], "sets": len(item.sets)})
    return blocks[:most]
