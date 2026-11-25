"""Which stage a session is at, for the music."""

STAGES = {
    "all": "Whole session",
    "warmup": "Warm-up",
    "lifting": "Lifting",
    "flatout": "Flat out",
    "steady": "Steady cardio",
    "cooldown": "Cool-down",
}


def is_done(item: dict, sets: int, state: dict) -> bool:
    if state.get("skipped") or state.get("hurt"):
        return True
    asked = item["kind"] != "cardio" and state.get("rir") is None
    return sets >= len(item["sets"]) and not asked


def current_item(
    items: list[dict], sets: dict[str, int], states: dict[str, dict]
) -> dict | None:
    return next(
        (each for each in items
         if not is_done(each, sets.get(each["key"], 0), states.get(each["key"], {}))),
        None,
    )


def stage_of(items: list[dict], sets: dict[str, int], states: dict[str, dict]) -> str:
    """Return the stage: easy until the first set, then by the exercise, easy once all done."""
    now = current_item(items, sets, states)
    if now is None:
        return "cooldown"
    if now["kind"] != "cardio":
        return "lifting" if any(sets.values()) else "warmup"
    if now.get("work"):
        return "flatout"
    key = now["key"].split("#")[0]
    if key == "flow":
        return "cooldown"
    return "flatout" if key == "circuit" or key.startswith("finisher-") else "steady"


def any_playlist(stages: dict[str, list[dict]], turn: int) -> dict | None:
    """Return a mix's playlist outside a session: the whole session's, else its first stage's."""
    first = next((stage for stage in STAGES if stages.get(stage)), "all")
    return playlist_for(stages, first, turn)


def playlist_for(stages: dict[str, list[dict]], stage: str, turn: int) -> dict | None:
    """Return a stage's playlist (its own, else the whole session's), taking turns."""
    choices = stages.get(stage) or stages.get("all") or []
    return choices[turn % len(choices)] if choices else None
