"""The pieces every screen is made of: headings, sections and rows."""

import html

from reps.coach.plan import REPS
from reps.library import EXERCISES, MACHINES
from reps.web.icons import icon

CARDIO_TONES = {**{key: "steady" for key in MACHINES}, "circuit": "flatout", "flow": "cooldown"}
TONE_ICONS = {"lifting": "lift", "steady": "wave", "flatout": "bolt", "cooldown": "leaf",
              "warmup": "sun", "all": "note"}


def esc(value: object) -> str:
    return html.escape(str(value))


def tone_of(key: str) -> str:
    """Return the colour an exercise or a session's item goes by: its stage."""
    plain = key.split("#")[0]
    if plain.startswith("finisher-"):
        return "flatout"
    return CARDIO_TONES.get(plain, "lifting")


def tone_of_item(key: str, work: int) -> str:
    """Return an item's colour: intervals are flat out on any machine."""
    return "flatout" if work else tone_of(key)


def icon_of(key: str) -> str:
    plain = key.split("#")[0]
    if plain in EXERCISES and EXERCISES[plain].kind != "load":
        return "body"
    return TONE_ICONS[tone_of(key)]


def chip(name: str, tone: str = "") -> str:
    return f'<span class="chip {tone}">{icon(name)}</span>'


def head(title: str, sub: str = "", back: str = "", aside: str = "") -> str:
    """Return a screen's heading, with the way back on its left."""
    way = ""
    if back:
        way = f'<a class="back" href="{esc(back)}" aria-label="Back">{icon("back")}</a>'
    under = f'<p class="sub">{esc(sub)}</p>' if sub else ""
    return (
        f'<div class="head">{way}<div class="titles"><h1>{esc(title)}</h1>{under}</div>'
        f"{aside}</div>"
    )


def rows(items: list[str], empty: str = "", css: str = "") -> str:
    """Return rows in a panel that scrolls inside when there are many."""
    if not items and empty:
        return f'<section class="panel scroll grow empty {css}"><p>{esc(empty)}</p></section>'
    listed = f'<ul class="rows">{"".join(items)}</ul>'
    return f'<section class="panel scroll grow {css}">{listed}</section>'


def section(title: str, items: list[str]) -> str:
    return (
        f'<section class="group"><h2>{esc(title)}</h2>'
        f'<ul class="rows panel">{"".join(items)}</ul></section>'
    )


def row(
    name: str,
    sub: str = "",
    *,
    href: str = "",
    field: str = "",
    value: str = "",
    mark: str = "",
    tone: str = "",
    right: str = "",
    extra: str = "",
    css: str = "",
) -> str:
    """Return a row: a link with ``href``, a button with ``field``, else plain words.

    ``right`` and ``extra`` are markup; ``extra`` sits outside the link or button.
    """
    under = f'<span class="under">{esc(sub)}</span>' if sub else ""
    inside = (
        (chip(mark, tone) if mark else "")
        + f'<span class="words"><span class="name">{esc(name)}</span>{under}</span>'
        + right
    )
    if href:
        inside += "" if extra else icon("go")
        main = f'<a class="row" href="{esc(href)}">{inside}</a>'
    elif field:
        main = (
            f'<button class="row" name="{field}" value="{esc(value)}">{inside}</button>'
        )
    else:
        main = f'<div class="row">{inside}</div>'
    return f'<li class="{css}">{main}{extra}</li>'


def edit_link(href: str, name: str) -> str:
    return (
        f'<a class="icon-button" href="{esc(href)}" aria-label="Edit {esc(name)}" '
        f'title="Edit">{icon("edit")}</a>'
    )


def button(href: str, label: str, css: str = "", mark: str = "") -> str:
    drawn = icon(mark) if mark else ""
    return f'<a class="button {css}" href="{esc(href)}">{drawn}{esc(label)}</a>'


def off(label: str) -> str:
    return f'<span class="button off" aria-disabled="true">{esc(label)}</span>'


def pill(label: str, css: str = "") -> str:
    return f'<span class="pill {css}">{esc(label)}</span>'


def note(words: str, bad: bool = False) -> str:
    if not words:
        return ""
    css, role = ("error", " role=alert") if bad else ("muted", "")
    return f'<p class="note {css}"{role}>{esc(words)}</p>'


def make_new(action: str, label: str, length: int) -> str:
    return (
        f'<form method="post" action="{action}" class="make"><input name="name" '
        f'maxlength="{length}" placeholder="{label}" required aria-label="{label}">'
        f'<button class="primary">{icon("plus")}Build</button></form>'
    )


def confirm_delete(action: str, ask: str, sure: bool) -> str:
    if sure:
        return (
            f'<form method="post" action="{action}">'
            f'<button class="danger">{icon("bin")}Delete for good</button></form>'
        )
    return button(ask, "Delete", "quiet", "bin")


def check(kind: str, name: str, value: str, label: str, on: bool, more: str = "") -> str:
    return (
        f'<label class="check"><input type="{kind}" name="{name}" value="{esc(value)}"'
        f'{" checked" if on else ""}{more}><span>{esc(label)}</span></label>'
    )


def select(label: str, field: str, options: list[tuple[str, str]], chosen: str) -> str:
    listed = "".join(
        f'<option value="{value}"{" selected" if value == chosen else ""}>{esc(words)}</option>'
        for value, words in options
    )
    field_html = f'<select class="wide" name="{field}">{listed}</select>'
    return f'<label class="pick">{label}{field_html}</label>'


def usual_reps(workout: int, mine: int) -> str:
    """Return the reps a set has when left alone, and whose choice that is."""
    if workout:
        return f"{workout} (this workout's)"
    return f"{mine} (my usual)" if mine else "Coach's choice"


def reps_select(chosen: int, usual: str, css: str = "") -> str:
    """Return a choice of how many reps a set is, with ``usual`` for what applies anyway."""
    offered = sorted({*REPS, chosen} - {0})
    options = [("0", usual), *((str(each), f"{each} reps") for each in offered)]
    field = select("Reps per set", "reps", options, str(chosen))
    return field.replace('class="pick"', f'class="pick {css}"') if css else field
