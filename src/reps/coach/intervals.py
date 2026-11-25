"""Work and rest for cardio, and the spin bike's rough levels."""

# (seconds on, seconds off); the first is steady.
STYLES = {
    "steady": (0, 0),
    "20-40": (20, 40),
    "30-30": (30, 30),
    "40-20": (40, 20),
    "30-90": (30, 90),
    "60-60": (60, 60),
    "60-120": (60, 120),
    "120-60": (120, 60),
}

# The bike has no exact settings, so a level is how it feels.
LEVELS = ("Easy", "Light", "Moderate", "Hard", "All out")
LEVELLED = {"bike"}
STEADY_LEVEL, HIIT_LEVEL = 3, 4


def clock(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def style_name(style: str) -> str:
    on, off = STYLES[style]
    return f"{clock(on)} on, {clock(off)} off" if on else "Steady"


def rounds(seconds: int, on: int, off: int) -> int:
    """Return how many rounds a time holds, counting a last one that stops at its rest."""
    return max(1, -(-seconds // (on + off)))


def whole(seconds: int, on: int, off: int) -> int:
    """Return the time to run for: all of it, unless that would stop part way through an on."""
    over = seconds % (on + off)
    cut_short = 0 < over < on
    return max(on + off, seconds - over) if cut_short else seconds


def level_name(level: float) -> str:
    return LEVELS[int(level) - 1] if 1 <= level <= len(LEVELS) else ""


def is_levelled(key: str) -> bool:
    return key.split("#")[0].removeprefix("finisher-") in LEVELLED


def last_level(history: dict, key: str) -> int:
    """Return the level last used on a machine, or 0."""
    plain = key.split("#")[0].removeprefix("finisher-")
    past = [done for each in (plain, f"finisher-{plain}") for done in history.get(each, [])]
    levels = [int(load) for done in sorted(past, key=lambda each: each.day)
              for load, _ in done.sets if load]
    return levels[-1] if levels else 0
