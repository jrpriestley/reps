"""Asking the describe service for a workout's blocks."""

import json
import re
import socket
from pathlib import Path

from reps.coach import custom, pace
from reps.coach.intervals import STYLES, style_name
from reps.coach.plan import GOALS, STEP, Profile, working_sets
from reps.library import PATTERNS

ANSWER_SECONDS = 150  # it gives up itself sooner, and may have tried twice
MOST_WORDS = 600
SENT_OF_A_BLOCK = (
    "options", "sets", "reps", "minutes", "style", "level", "watts", "speed", "incline",
)
TYPED_SETS = re.compile(r"\b\d+\s*(?:x|×|sets?\b)", re.I)


class NotSetUpError(Exception):
    """There is no describe service running."""


def request_for(profile: Profile, words: str, workout: dict | None = None) -> dict:
    """Return what is sent: the words, what this person can do, and their preferences.

    Their weight, what hurts, heart rate, history and name never are.
    """
    groups = {"cardio": "cardio", **PATTERNS}
    exercises = [
        {"key": key, "name": custom.name_of(key), "group": group.lower()}
        for category, group in groups.items()
        for key in custom.choices(profile, category)
    ]
    styles = [{"key": key, "name": style_name(key)} for key in STYLES]
    changing = {}
    if workout:
        changing = {
            "blocks": [
                {key: block[key] for key in SENT_OF_A_BLOCK if key in block}
                for block in workout["blocks"]
            ],
            "said": [each["you"] for each in workout["talk"]],
        }
    return changing | {
        "words": words[:MOST_WORDS],
        "exercises": exercises,
        "styles": styles,
        "most_blocks": custom.MOST_BLOCKS,
        "usual": {
            "minutes": profile.minutes,
            "experience": profile.experience,
            "goals": [GOALS[goal] for goal in profile.goals],
        },
    }


def ask(path: Path, request: dict) -> dict:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as line:
            line.settimeout(ANSWER_SECONDS)
            line.connect(str(path))
            line.sendall(json.dumps(request).encode() + b"\n")
            with line.makefile("rb") as answer:
                return json.loads(answer.readline() or b"{}")
    except TimeoutError:
        return {"error": "Claude took too long. Try again."}
    except OSError as problem:
        raise NotSetUpError from problem
    except ValueError:
        return {"error": "That came back garbled. Try again."}


def blocks_from(answer: dict, profile: Profile) -> list[dict]:
    """Return an answer's blocks, checked as if a person had sent them."""
    blocks = []
    for given in answer.get("blocks", [])[:custom.MOST_BLOCKS]:
        able = [key for key in given.get("options", []) if key in _known(profile)]
        if not able:
            continue
        # One kind to a block, whatever came back.
        able = [key for key in able if (key in custom.CARDIO) == (able[0] in custom.CARDIO)]
        blocks.append(custom.clean_block(given | {"options": able}))
    return blocks


def keep_music(blocks: list[dict], before: list[dict]) -> list[dict]:
    """Put playlists back on the blocks that are still there: they are never sent."""
    left = [dict(block) for block in before if block.get("music")]
    kept = []
    for block in blocks:
        same = next((each for each in left if each["options"] == block["options"]), None)
        if same:
            left.remove(same)
            block = block | {"music": same["music"]}
        kept.append(block)
    return kept


def whole_prompt(talk: list[dict]) -> str:
    """Return all that was asked for, as one prompt to use again."""
    first, *changes = [each["you"] for each in talk]
    return ". Then: ".join([first, *changes])[:MOST_WORDS]


def ease(blocks: list[dict], words: str, profile: Profile) -> list[dict]:
    """Keep a new lifter to the coach's sets, unless they typed how many they want."""
    if profile.experience != "new" or TYPED_SETS.search(words):
        return blocks
    most = working_sets(profile)
    return [
        block | {"sets": min(block["sets"], most)} if "sets" in block else block
        for block in blocks
    ]


def reasonable(blocks: list[dict], profile: Profile) -> tuple[list[dict], str]:
    """Keep paces to what is sensible for them, saying what was eased."""
    eased = [
        pace.ease(block, profile.experience) if custom.is_cardio(block) else (block, "")
        for block in blocks
    ]
    return [block for block, _ in eased], " ".join(said for _, said in eased if said)


def in_fives(blocks: list[dict], words: str) -> list[dict]:
    """Put cardio in fives of minutes, but for a time they typed."""
    typed = {int(number) for number in re.findall(r"\d+", words)}
    return [
        block | {"minutes": max(STEP, STEP * round(block["minutes"] / STEP))}
        if "minutes" in block and block["minutes"] not in typed else block
        for block in blocks
    ]


def _known(profile: Profile) -> set[str]:
    return {each["key"] for each in request_for(profile, "")["exercises"]}
