"""The music mix builder's screens, in the workout builder's manner."""

from reps.coach.stages import STAGES
from reps.web.layout import framed
from reps.web.ui import (
    TONE_ICONS,
    button,
    confirm_delete,
    edit_link,
    esc,
    head,
    make_new,
    note,
    off,
    pill,
    row,
    rows,
)
from reps.web.workout_pages import NAME_LENGTH

MOST_MIXES = 30
MOST_CHOICES = 3


def describe(mix: dict) -> str:
    """Return a mix in a line: its playlist, or how many stages have their own."""
    filled = [stage for stage in STAGES if mix["stages"].get(stage)]
    if not filled:
        return "Empty: add a playlist"
    first = mix["stages"][filled[0]][0]["name"]
    own = [STAGES[stage] for stage in filled if stage != "all"]
    return first if not own else f"{first} · {len(own)} stage{'s' if len(own) > 1 else ''}"


def stage_stripes(mix: dict) -> str:
    return '<span class="stripes">' + "".join(
        f'<i class="{stage}"></i>' for stage in STAGES
        if stage != "all" and mix["stages"].get(stage)
    ) + "</span>"


def mixes_page(
    person: str, mixes: list[dict], usual: int | None, linked: bool, problem: str = ""
) -> str:
    """Return the saved mixes: a tap plays one and makes it the usual, the pencil opens it."""
    shown = [_saved_row(each, each["id"] == usual) for each in mixes]
    turn_off = ""
    if usual is not None:
        turn_off = '<form method="post" action="/music/usual"><button name="mix" value="">'
        turn_off += "Music off</button></form>"
    empty = "Nothing saved yet. Name one below to build it."
    if not linked:
        shown, empty = [], "Link Spotify first, in Me."
    return framed("me", "Music", (
        f'{head("Music mixes", "Tap to play", "/me", turn_off)}{note(problem, True)}'
        f'<form method="post" action="/music/usual" class="setup">{rows(shown, empty)}</form>'
        f'{make_new("/music", "New mix", NAME_LENGTH)}'
    ), person)


def _saved_row(mix: dict, usual: bool) -> str:
    edit = edit_link(f"/music/{mix['id']}", mix["name"])
    right = stage_stripes(mix) + (pill("On", "ok") if usual else "")
    if not any(mix["stages"].values()):
        return row(mix["name"], describe(mix), mark="note", extra=edit, css="dim")
    return row(mix["name"], describe(mix), field="mix", value=str(mix["id"]), mark="note",
               tone="cooldown", right=right, extra=edit, css="chosen" if usual else "")


def mix_page(person: str, mix: dict) -> str:
    base = f"/music/{mix['id']}"
    stages = mix["stages"]

    def detail(stage: str) -> str:
        chosen = stages.get(stage)
        if chosen:
            return ", ".join(each["name"] for each in chosen)
        if stage == "all":
            return "Choose a playlist"
        return "Same as whole session" if stages.get("all") else "No change"

    shown = [
        row(label, detail(stage), href=f"{base}/{stage}", mark=TONE_ICONS[stage], tone=stage,
            css="" if stages.get(stage) else "dim")
        for stage, label in STAGES.items()
    ]
    return framed("me", mix["name"], (
        f'{head(mix["name"], "Saved", "/music", edit_link(f"{base}/settings", mix["name"]))}'
        f'{rows(shown)}{note("One playlist: fill Whole session. Add stages to change it.")}'
    ), person)


def stage_page(person: str, mix: dict, stage: str, problem: bool = False) -> str:
    base = f"/music/{mix['id']}/{stage}"
    chosen = mix["stages"].get(stage, [])
    shown = [
        row(each["name"], field="drop", value=each["uri"], mark=TONE_ICONS[stage], tone=stage,
            right='<span class="do">Take out</span>')
        for each in chosen
    ]
    add = button(f"{base}/add", "Choose a playlist", "primary", "plus")
    paste = (
        f'<form method="post" action="{base}" class="make"><input name="link" '
        'placeholder="Or paste a Spotify link" required aria-label="Spotify link" '
        'autocomplete="off" autocapitalize="none"><button>Add link</button></form>'
    )
    if len(chosen) >= MOST_CHOICES:
        add, paste = off("Full: three playlists"), ""
    words = "Several take turns, one session each." if len(chosen) > 1 else "Add up to three."
    if problem:
        words = "Spotify didn't open that. Copy the playlist's own link."
    return framed("me", STAGES[stage], (
        f'{head(STAGES[stage], mix["name"], f"/music/{mix["id"]}")}'
        f'<form method="post" action="{base}" class="setup">'
        f'{rows(shown, "No playlist for this stage.")}</form>'
        f'{note(words, problem)}{paste}<div class="actions">{add}</div>'
    ), person)


def playlists_page(person: str, mix: dict, stage: str, found: list[dict]) -> str:
    base = f"/music/{mix['id']}/{stage}"
    shown = [
        row(each["name"], field="add", value=each["uri"], mark="note", tone=stage,
            right='<span class="do">Add</span>')
        for each in found
    ]
    return framed("me", "Playlists", (
        f'{head("Your playlists", f"{mix["name"]} · {STAGES[stage]}", base)}'
        f'<form method="post" action="{base}" class="setup">'
        f'{rows(shown, "No playlists came back from Spotify.")}</form>'
    ), person)


def mix_settings_page(person: str, mix: dict, sure: bool) -> str:
    base = f"/music/{mix['id']}"
    remove = confirm_delete(f"{base}/delete", f"{base}/settings?sure=1", sure)
    return framed("me", "Name", (
        f'{head("Name", mix["name"], base, remove)}'
        f'<form method="post" action="{base}/settings" class="setup">'
        f'<section class="panel scroll grow top"><label class="pick">Name<input class="wide" '
        f'name="name" maxlength="{NAME_LENGTH}" value="{esc(mix["name"])}" required></label>'
        '</section><div class="actions"><button class="primary big">Save</button></div></form>'
    ), person)
