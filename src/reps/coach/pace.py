"""How hard a machine is set: the rower's watts, the treadmill's speed and incline.

A person asks for these; 0 is their own pace. Whatever is asked is kept to what is reasonable.
"""

import math
from typing import NamedTuple

from reps.coach.intervals import STYLES, level_name

WATTS_STEP, MOST_WATTS = 10, 400
SPEED_STEP, MOST_SPEED = 0.5, 16.0  # km/h
MOST_INCLINE = 12  # the treadmill's levels, each about a percent (some models a little less)
KM_PER_MILE = 1.609344  # for a speed typed in miles

WATTS = tuple(range(20, MOST_WATTS + 1, WATTS_STEP))
SPEEDS = tuple(half / 2 for half in range(4, int(MOST_SPEED * 2) + 1))
INCLINES = tuple(range(MOST_INCLINE + 1))

# The most that is reasonable, by experience: watts, and METs (the energy of a pace, where sitting
# still is 1). Hard parts with rests between may go further, and a long steady go less far.
TOP_WATTS = {"new": 150, "returning": 200, "regular": 250}
TOP_METS = {"new": 10, "returning": 12, "regular": 14}
HARD_PARTS_WATTS, HARD_PARTS_METS = 100, 1
LONG_MINUTES, LONG_METS = 20, 1
STROLL = 5.0  # km/h: what an incline with no speed is judged at
WALK_UP_TO, RUN_FROM = 6.0, 8.0  # km/h


class Pace(NamedTuple):
    """A machine's setting: watts, speed or the bike's level, and an incline."""

    load: float = 0
    incline: int = 0


def machine_of(key: str) -> str:
    return key.split("#")[0].removeprefix("finisher-")


def mets(speed: float, incline: float) -> float:
    """Return the energy of a treadmill pace (the ACSM equations, blended from walk to run)."""
    metres = speed * 1000 / 60  # a minute
    grade = incline / 100
    walking = 0.1 * metres + 1.8 * metres * grade
    running = 0.2 * metres + 0.9 * metres * grade
    share = min(1.0, max(0.0, (speed - WALK_UP_TO) / (RUN_FROM - WALK_UP_TO)))
    return (walking * (1 - share) + running * share) / 3.5 + 1


def top_watts(experience: str, hard_parts: bool) -> int:
    return TOP_WATTS[experience] + (HARD_PARTS_WATTS if hard_parts else 0)


def top_mets(experience: str, minutes: float, hard_parts: bool) -> int:
    if hard_parts:
        return TOP_METS[experience] + HARD_PARTS_METS
    return TOP_METS[experience] - (LONG_METS if minutes > LONG_MINUTES else 0)


def steepest(speed: float, top: float) -> int:
    """Return the most incline a speed may have, or -1 if it is too fast even flat."""
    return max((each for each in INCLINES if mets(speed or STROLL, each) <= top), default=-1)


def ease(block: dict, experience: str) -> tuple[dict, str]:
    """Return a cardio block at a reasonable pace, and what was changed in words."""
    eased, said = dict(block), []
    hard_parts = STYLES[block.get("style", "steady")][0] > 0
    if "rower" in block["options"]:
        top = top_watts(experience, hard_parts)
        if block.get("watts", 0) > top:
            eased["watts"] = top
            said.append(f"{block['watts']} W is a lot for {_length(block)}: eased to {top} W.")
    if "treadmill" in block["options"]:
        top = top_mets(experience, block["minutes"], hard_parts)
        speed, incline = block.get("speed", 0), block.get("incline", 0)
        while speed and steepest(speed, top) < 0:
            speed -= SPEED_STEP
        incline = min(incline, steepest(speed, top))
        if (speed, incline) != (block.get("speed", 0), block.get("incline", 0)):
            eased |= {"speed": speed, "incline": incline}
            asked = treadmill_words(block.get("speed", 0), block.get("incline", 0))
            said.append(f"{asked} is too much: eased to {treadmill_words(speed, incline)}.")
    return eased, " ".join(said)


def _length(block: dict) -> str:
    return "those rounds" if block.get("style", "steady") != "steady" else (
        f"{block['minutes']} minutes"
    )


def pace_for(key: str, block: dict) -> Pace:
    """Return what a block asks of the machine it is done on."""
    if machine_of(key) == "rower":
        return Pace(block.get("watts", 0))
    if machine_of(key) == "treadmill":
        return Pace(block.get("speed", 0), block.get("incline", 0))
    return Pace()


def as_fields(key: str, load: float, incline: int) -> dict:
    """Return a machine's setting as a block's fields: the way back from ``pace_for``."""
    if machine_of(key) == "rower":
        return {"watts": int(load)}
    if machine_of(key) == "treadmill":
        return {"speed": load, "incline": incline}
    return {"level": int(load)}


def words(key: str, load: float, incline: float = 0) -> str:
    """Return a machine's setting in the few words a list has room for."""
    if machine_of(key) == "rower":
        return f"{load:g} W" if load else ""
    if machine_of(key) == "treadmill":
        parts = [f"{load:g} km/h" if load else "", f"incline {incline:g}" if incline else ""]
        return ", ".join(part for part in parts if part)
    return level_name(load)


def treadmill_words(speed: float, incline: float) -> str:
    slope = f"incline {incline:g}" if incline else ""
    return ", ".join(part for part in (f"{speed:g} km/h" if speed else "", slope) if part)


def _snap(value: object, step: float, least: float, most: float) -> float:
    """Return a number a machine can be set to, or 0 for none or nonsense."""
    if not isinstance(value, int | float) or not 0 < value < math.inf:
        return 0
    return max(least, min(most, round(value / step) * step))


def clean(given: dict) -> dict:
    """Return a block's pace fields, made sense of."""
    return {
        "watts": int(_snap(given.get("watts"), WATTS_STEP, WATTS[0], MOST_WATTS)),
        "speed": _snap(given.get("speed"), SPEED_STEP, SPEEDS[0], MOST_SPEED),
        "incline": int(_snap(given.get("incline"), 1, 1, MOST_INCLINE)),
    }


def steps(key: str, pace: Pace, experience: str, minutes: float, hard_parts: bool) -> dict | None:
    """Return what the session screen may step through, or None where no pace was asked for."""
    if not pace.load and not pace.incline:
        return None
    if machine_of(key) == "rower":
        top = top_watts(experience, hard_parts)
        return {"unit": "watts", "loads": [each for each in WATTS if each <= top]}
    if machine_of(key) == "treadmill":
        top = top_mets(experience, minutes, hard_parts)
        tops = {each: steepest(each, top) for each in (0, *SPEEDS)}
        able = [speed for speed, most in tops.items() if most >= 0]
        able = able if not pace.load else [speed for speed in able if speed]
        return {"unit": "speed", "loads": able, "steepest": [tops[speed] for speed in able]}
    return None


def possible(key: str, load: float, incline: float) -> bool:
    """Return whether a logged setting is one the machine has."""
    if machine_of(key) == "rower":
        return load in (0, *WATTS) and incline == 0
    if machine_of(key) == "treadmill":
        return load in (0, *SPEEDS) and incline in INCLINES
    return bool(level_name(load)) and incline == 0 if load else incline == 0
