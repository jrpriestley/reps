"""Workouts typed in a few words: ``rower 15, deadlift or curl 3x, bike 10 1 on 1 off``.

All of it is understood, or none: what it can't read goes to Claude instead.
"""

import re

from reps.coach.custom import CARDIO, MOST_BLOCKS, MOST_OPTIONS, clean_block, new_block
from reps.coach.intervals import LEVELS, STYLES
from reps.coach.pace import KM_PER_MILE
from reps.coach.plan import LEAST_REPS, MOST_REPS
from reps.library import EXERCISES

BETWEEN = re.compile(r"\s*(?:,|;|\n|->|→|\bthen\b)\s*", re.I)
OR = re.compile(r"\s+or\s+|\s*/\s*(?=[a-z])", re.I)
NOISE = {"of", "the", "for", "on", "at", "a", "with", "do", "some", "and"}

ALIASES = {
    "row": "rower", "rowing": "rower", "rowing machine": "rower", "bike": "bike",
    "cycling": "bike", "cycle": "bike", "spin": "bike", "run": "treadmill",
    "running": "treadmill", "walk": "treadmill", "circuit": "circuit", "stretch": "flow",
    "stretching": "flow", "mobility": "flow", "squat": "goblet-squat", "squats": "goblet-squat",
    "bench": "floor-press", "bench press": "floor-press", "press": "shoulder-press",
    "overhead press": "shoulder-press", "rdl": "romanian-deadlift", "lunge": "split-squat",
    "lunges": "split-squat", "biceps": "curl", "bicep curl": "curl", "biceps curl": "curl",
    "triceps": "triceps-press", "rows": "bent-row", "dumbbell row": "bent-row",
}


def _names() -> dict[str, str]:
    known = dict(ALIASES)
    for key, name in [*CARDIO.items(), *((key, each.name) for key, each in EXERCISES.items())]:
        for words in (key.replace("-", " "), name.lower().replace("-", " ")):
            known[words] = key
            known[words + "s"] = key
    return known


NAMES = _names()
LONGEST_FIRST = sorted(NAMES, key=len, reverse=True)

MINUTES = re.compile(r"\b(\d{1,2})\s*(?:min(?:ute)?s?|m)?\b")
SETS = re.compile(r"\b(\d)\s*(?:x|×|sets?)\s*(?:(?:of\s*)?(\d{1,2})(?:\s*reps?)?)?\b")
PLAIN_NUMBER = re.compile(r"\b(\d)\b")
ON_OFF = re.compile(
    r"\b(\d{1,3})\s*(?:min(?:ute)?s?|m|s|sec(?:ond)?s?)?\s*(?:on|:|/)\s*"
    r"(\d{1,3})\s*(?:min(?:ute)?s?|m|s|sec(?:ond)?s?)?(?:\s*off)?\b"
)
HIIT = re.compile(r"\b(?:hiit|intervals?)\b")
WATTS = re.compile(r"\b(\d{2,3})\s*(?:w|watts?)\b(?:\s*(?:avg|average))?")
SPEED = re.compile(r"\b(\d{1,2}(?:\.\d+)?)\s*(kmh|kph|mph)\b")
# However it is said, the number is the one on the treadmill.
INCLINE = re.compile(
    r"\b(\d{1,2})\s*(?:%|°|deg(?:rees?)?\b)(?:\s*incline\b)?|\bincline\s*(?:of\s*)?(\d{1,2})\b"
    r"(?:\s*(?:%|°|deg(?:rees?)?\b))?"
)
LEVEL = re.compile("|".join(rf"\b{re.escape(name.lower())}\b" for name in reversed(LEVELS)))


def parse(words: str) -> list[dict] | None:
    """Return a workout's blocks, or None if any part isn't plain."""
    plain = words.strip().lower().replace("km/h", "kmh")  # its stroke is not an "or"
    parts = [part for part in BETWEEN.split(plain) if part]
    if not parts or len(parts) > MOST_BLOCKS:
        return None
    blocks = [_block(part) for part in parts]
    return blocks if all(blocks) else None


def _take(pattern: re.Pattern, text: str) -> tuple[re.Match | None, str]:
    found = pattern.search(text)
    if not found:
        return None, text
    return found, f"{text[:found.start()]} {text[found.end():]}"


def _exercise(text: str) -> tuple[str, str] | None:
    plain = text.replace("-", " ")
    for name in LONGEST_FIRST:
        found = re.search(rf"\b{re.escape(name)}\b", plain)
        if found:
            return NAMES[name], f"{plain[:found.start()]} {plain[found.end():]}"
    return None


def _block(text: str) -> dict | None:
    choices = OR.split(text)
    if len(choices) > MOST_OPTIONS:
        return None
    options, left = [], ""
    for choice in choices:
        found = _exercise(choice)
        if found is None:
            return None
        options.append(found[0])
        left += f" {found[1]}"
    if len({key in CARDIO for key in options}) > 1:
        return None
    block = new_block(options[0]) | {"options": options}
    left = _cardio(block, left) if options[0] in CARDIO else _strength(block, left)
    if left is None or set(re.findall(r"\w+", left)) - NOISE:
        return None
    return clean_block(block)


def _cardio(block: dict, text: str) -> str | None:
    found, text = _take(ON_OFF, text)
    if found:
        on, off = (_seconds(found[0], int(found[place])) for place in (1, 2))
        style = next((key for key, times in STYLES.items() if times == (on, off)), None)
        if style is None:
            return None
        block["style"] = style
    hiit, text = _take(HIIT, text)
    if hiit and not found:
        block["style"] = "60-60"
    level, text = _take(LEVEL, text)
    if level:
        block["level"] = [name.lower() for name in LEVELS].index(level[0]) + 1
    text = _pace(block, text)
    if text is None:
        return None
    minutes, text = _take(MINUTES, text)
    if minutes:
        block["minutes"] = int(minutes[1])
    return text


def _pace(block: dict, text: str) -> str | None:
    """Read watts, a speed and an incline; None if the machine doesn't have it."""
    watts, text = _take(WATTS, text)
    speed, text = _take(SPEED, text)
    incline, text = _take(INCLINE, text)
    if watts and block["options"] != ["rower"]:
        return None
    if (speed or incline) and block["options"] != ["treadmill"]:
        return None
    if watts:
        block["watts"] = int(watts[1])
    if speed:
        block["speed"] = float(speed[1]) * (KM_PER_MILE if speed[2] == "mph" else 1)
    if incline:
        block["incline"] = int(incline[1] or incline[2])
    return text


def _seconds(words: str, number: int) -> int:
    """Return an interval's length: ``1 on`` is a minute, ``30 on`` is seconds."""
    said_minutes = bool(re.search(r"\d\s*m", words)) and number <= 3
    return number * 60 if said_minutes or number <= 3 else number


def _strength(block: dict, text: str) -> str | None:
    found, text = _take(SETS, text)
    if not found:
        found, text = _take(PLAIN_NUMBER, text)
    if found:
        block["sets"] = int(found[1])
    if found and found.re is SETS and found[2]:
        block["reps"] = int(found[2])
        if not LEAST_REPS <= block["reps"] <= MOST_REPS:
            return None
    return text
