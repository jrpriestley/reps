"""Workouts a person builds: their blocks in order, with the coach's loads."""

import datetime as dt
import re
from dataclasses import replace

from reps.coach import pace
from reps.coach.intervals import LEVELS, STYLES
from reps.coach.plan import (
    FLOW,
    LEAST_REPS,
    MOST_REPS,
    History,
    Item,
    Profile,
    Session,
    available,
    cardio,
    in_fives,
    prescribe,
    total_seconds,
)
from reps.library import EXERCISES, MACHINES

CARDIO = {**MACHINES, "circuit": "Bodyweight circuit", "flow": "Mobility flow"}
CUES = {
    "circuit": "30 seconds each, round and round: squats, push-ups, lunges, plank.",
    "flow": FLOW,
}
MOST_BLOCKS = 10
MOST_OPTIONS = 3
MOST_SETS = 6
MOST_MINUTES = 60


def base_key(key: str) -> str:
    """Return what an item is: the second rower block of a workout is ``rower#2``."""
    return key.split("#")[0]


def is_cardio(block: dict) -> bool:
    return block["options"][0] in CARDIO


def name_of(key: str) -> str:
    return CARDIO.get(key) or EXERCISES[key].name


def can_do(profile: Profile, key: str) -> bool:
    if key in EXERCISES:
        return available(profile, EXERCISES[key])
    if key in profile.avoid:
        return False
    return key not in MACHINES or key in profile.equipment


def choices(profile: Profile, category: str) -> list[str]:
    """Return what a person could put in a block, for a pattern or ``cardio``."""
    keys = CARDIO if category == "cardio" else [
        exercise.key for exercise in EXERCISES.values() if exercise.pattern == category
    ]
    return [key for key in keys if can_do(profile, key)]


def new_block(key: str) -> dict:
    if key in CARDIO:
        return {"options": [key], "minutes": 15, "style": "steady", "level": 0}
    return {"options": [key], "sets": 3}


def least_recent(keys: list[str], history: History) -> str:
    def last_done(key: str) -> dt.date:
        past = history.get(key)
        return past[-1].day if past else dt.date.min

    return min(keys, key=last_done)


def build(
    profile: Profile, name: str, blocks: list[dict], counts: bool, history: History, today: dt.date
) -> Session:
    """Return a workout ready to do. A block nothing can be done for is left out."""
    items: list[Item] = []
    used: dict[str, int] = {}
    for block in blocks:
        able = [
            key
            for key in block["options"]
            if can_do(profile, key) and (key in CARDIO or key not in used)
        ]
        if not able:
            continue
        key = least_recent(able, history)
        used[key] = used.get(key, 0) + 1
        why = "Your workout." if len(able) == 1 else "The one you did longer ago."
        if key in CARDIO:
            item_key = key if used[key] == 1 else f"{key}#{used[key]}"
            work, off = STYLES[block.get("style", "steady")]
            items.append(cardio(
                item_key, CARDIO[key], block["minutes"], why, CUES.get(key, ""), work, off,
                level=block.get("level", 0), history=history,
                pace=pace.pace_for(key, pace.ease(block, profile.experience)[0]),
            ))
        else:
            wanted = replace(profile, reps=block.get("reps") or profile.reps)
            item, _ = prescribe(wanted, EXERCISES[key], history, today, block["sets"])
            items.append(item)
        items[-1] = replace(items[-1], music=block.get("music"))
    minutes = in_fives(total_seconds(items, 0, False)) if items else 0
    return Session("core" if counts else "extra", name, minutes, tuple(items))


# What a page may send back ----------------------------------------------------

PLAYLIST = re.compile(r"spotify:playlist:\w{1,40}")
MOST_NAME = 100


def clean_music(given: object) -> dict | None:
    if not isinstance(given, dict):
        return None
    uri, name = str(given.get("uri", "")), str(given.get("name", ""))[:MOST_NAME]
    return {"uri": uri, "name": name or "Playlist"} if PLAYLIST.fullmatch(uri) else None


def clamp_reps(reps: int) -> int:
    """Return reps a set can have; 0 stays 0, for the usual."""
    return max(LEAST_REPS, min(MOST_REPS, reps)) if reps > 0 else 0


def _between(value: object, least: int, most: int, usual: int) -> int:
    return max(least, min(most, value)) if isinstance(value, int) else usual


def clean_block(given: object) -> dict:
    """Return a block made sense of, or raise ValueError if it can't be."""
    if not isinstance(given, dict) or not isinstance(given.get("options"), list):
        raise ValueError("block")
    options = list(dict.fromkeys(str(key) for key in given["options"]))[:MOST_OPTIONS]
    if not options or any(key not in CARDIO and key not in EXERCISES for key in options):
        raise ValueError("options")
    if len({key in CARDIO for key in options}) > 1:
        raise ValueError("cardio and exercises don't share a block")
    block = new_block(options[0]) | {"options": options}
    if is_cardio(block):
        block["minutes"] = _between(given.get("minutes"), 1, MOST_MINUTES, block["minutes"])
        style = given.get("style")
        block["style"] = style if isinstance(style, str) and style in STYLES else "steady"
        block["level"] = _between(given.get("level"), 0, len(LEVELS), 0)
        block |= {field: each for field, each in pace.clean(given).items() if each}
    else:
        block["sets"] = _between(given.get("sets"), 1, MOST_SETS, block["sets"])
        reps = given.get("reps")
        if isinstance(reps, int) and reps > 0:
            block["reps"] = clamp_reps(reps)
    music = clean_music(given.get("music"))
    return block | ({"music": music} if music else {})


def clean_blocks(given: object) -> list[dict]:
    if not isinstance(given, list) or len(given) > MOST_BLOCKS:
        raise ValueError("blocks")
    return [clean_block(each) for each in given]


SCOPES = {"one": "This block", "on": "From here on", "all": "Whole workout"}


def tag(blocks: list[dict], place: int, music: dict | None, scope: str) -> None:
    """Put a playlist on a block, on it and those after, or on all of them; None takes it off."""
    first = 0 if scope == "all" else place
    last = place + 1 if scope == "one" else len(blocks)
    for block in blocks[first:last]:
        if music:
            block["music"] = music
        else:
            block.pop("music", None)
