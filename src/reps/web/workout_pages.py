"""The workout builder's screens, and the connections': heart rate and Spotify."""

import datetime as dt
import json

from reps.coach import pace
from reps.coach.custom import (
    CARDIO,
    MOST_BLOCKS,
    MOST_MINUTES,
    MOST_OPTIONS,
    MOST_SETS,
    SCOPES,
    is_cardio,
    name_of,
)
from reps.coach.generate import EFFORTS, FOCUSES, Order
from reps.coach.generate import LEAST_MINUTES as LEAST_ORDER
from reps.coach.generate import MOST_MINUTES as MOST_ORDER
from reps.coach.intervals import LEVELS, STYLES, is_levelled, style_name
from reps.coach.plan import Session
from reps.describe import whole_prompt
from reps.library import PATTERNS
from reps.web.icons import icon
from reps.web.layout import framed
from reps.web.ui import (
    button,
    chip,
    confirm_delete,
    edit_link,
    esc,
    head,
    icon_of,
    make_new,
    note,
    off,
    pill,
    reps_select,
    row,
    rows,
    select,
    tone_of,
    tone_of_item,
    usual_reps,
)

MOST_WORKOUTS = 30
NAME_LENGTH = 24
SPOTIFY_LINK_DAYS = 180  # Spotify ends a link after six months
MINUTES = tuple(range(5, MOST_MINUTES + 1, 5))
CATEGORIES = {"cardio": "Cardio", **PATTERNS}
CATEGORY_MARKS = {"cardio": ("wave", "steady"), "core": ("body", "lifting"),
                  "bar": ("body", "lifting")}


def block_name(block: dict) -> str:
    return " or ".join(name_of(key) for key in block["options"])


def block_amount(block: dict) -> str:
    if is_cardio(block):
        return f"{block['minutes']} min"
    if block.get("reps"):
        return f"{block['sets']} × {block['reps']}"
    return f"{block['sets']} set{'s' if block['sets'] > 1 else ''}"


def block_tone(block: dict) -> str:
    return tone_of_item(block["options"][0], STYLES[block.get("style", "steady")][0])


def block_detail(block: dict) -> str:
    parts = ["Takes turns"] if len(block["options"]) > 1 else []
    if is_cardio(block):
        style = block.get("style", "steady")
        parts += [style_name(style)] if style != "steady" else []
        first = block["options"][0]
        set_to = pace.pace_for(first, block)
        load = block.get("level", 0) if is_levelled(first) else set_to.load
        parts.append(pace.words(first, load, set_to.incline))
    return " · ".join(part for part in parts if part)


def stripes(workout: dict) -> str:
    """Return a workout's blocks as a strip of their colours."""
    return '<span class="stripes">' + "".join(
        f'<i class="{block_tone(block)}"></i>' for block in workout["blocks"]
    ) + "</span>"


def summary(workout: dict) -> str:
    named = [name_of(block["options"][0]) for block in workout["blocks"]]
    return ", ".join(named) or "No blocks yet"


def workouts_page(person: str, workouts: list[dict]) -> str:
    """Return the saved workouts, to build and edit (Me)."""
    shown = [
        row(each["name"], summary(each), href=f"/workouts/{each['id']}", mark="stack",
            tone="lifting", right=stripes(each))
        for each in workouts
    ]
    say = button("/me/describe", "Describe one", "quiet")
    return framed("me", "Workouts", (
        f'{head("Workouts", "Build once, use any day", "/me", say)}'
        f'{rows(shown, "Nothing saved yet. Name one below to build it.")}'
        f'{make_new("/workouts", "New workout", NAME_LENGTH)}'
    ), person)


def choose_tabs(here: str) -> str:
    def link(key: str, href: str, label: str) -> str:
        mark = " aria-current=page" if key == here else ""
        return f'<a href="{href}"{mark}>{label}</a>'

    tabs = link("saved", "/pick", "Saved") + link("made", "/generate", "Generated")
    tabs += link("said", "/describe", "Described")
    return f'<div class="segments wide">{tabs}</div>'


def coach_button(chosen: bool) -> str:
    if not chosen:
        return ""
    return (
        '<form method="post" action="/pick"><button name="workout" value="">'
        "Coach's plan</button></form>"
    )


def pick_page(
    person: str,
    workouts: list[dict],
    plans: dict[int, Session],
    picked: int | None,
    chosen: bool,
) -> str:
    """Return the saved workouts, to pick today's from (Today)."""
    shown = [
        _pick_row(each, plans[each["id"]], each["id"] == picked) for each in workouts
    ]
    return framed("today", "Pick for today", (
        f'{head("Pick for today", "Tap one to use it", "/", coach_button(chosen))}'
        f'{choose_tabs("saved")}<form method="post" action="/pick" class="setup">'
        f'{rows(shown, "Nothing saved yet. Build one in Me, Workouts.")}</form>'
        f'<div class="actions">{button("/workouts", "Build or edit", mark="edit")}</div>'
    ), person)


def generate_page(
    person: str, order: Order, offered: list[str], machines: list[str], mine: int, chosen: bool
) -> str:
    """Return what to ask for in a generated workout, within the person's setup."""
    minutes = range(LEAST_ORDER, MOST_ORDER + 1, 5)
    owned = [("", "Any machine")] + [(key, CARDIO[key]) for key in machines]
    fields = (
        '<fieldset class="choices"><legend>Focus</legend>'
        + "".join(_choice("focus", key, FOCUSES[key], order.focus) for key in offered)
        + "</fieldset>"
        '<fieldset class="choices"><legend>Effort</legend>'
        + "".join(_choice("effort", key, label, order.effort) for key, label in EFFORTS.items())
        + "</fieldset>"
        + select("Minutes", "minutes", [(str(each), f"{each} min") for each in minutes],
                  str(order.minutes))
        + reps_select(order.reps, usual_reps(0, mine), "for-strength")
        + '<div class="for-cardio"><p class="muted">For cardio</p>'
        + select("On", "machine", owned, order.machine)
        + select("Style", "style", [(key, style_name(key)) for key in STYLES], order.style)
        + "</div>"
    )
    return framed("today", "Generate", (
        f'{head("Pick for today", "Made to order, within your setup", "/", coach_button(chosen))}'
        f'{choose_tabs("made")}<form method="post" action="/generate" class="setup">'
        f'<section class="panel scroll grow top order">{fields}</section>'
        '<div class="actions"><button class="primary big">Generate</button></div></form>'
    ), person)


def describe_page(
    person: str, words: str, problem: str, chosen: bool, prompts: list[dict], later: bool
) -> str:
    """Return the box to describe a workout in, and the prompts kept to use again.

    Reached from Today to have one now, or from Me to keep one for ``later``.
    """
    here = "/me/describe" if later else "/describe"
    kept = [
        row(each["words"], href=f"{here}?prompt={each['id']}", mark="say", tone="lifting",
            extra=(
                f'<form method="post" action="{here}/forget" class="drop">'
                f'<button class="icon-button" name="prompt" value="{each["id"]}" '
                f'aria-label="Delete this prompt" title="Delete">{icon("bin")}</button></form>'
            ))
        for each in prompts
    ]
    saved = f"<h2>Saved prompts</h2>{rows(kept, css='prompts')}" if kept else ""
    frame = ('<div class="scroll grow stacked">', "</div>") if kept else ("", "")
    if later:
        top = head("Describe a workout", "Saved to use any day", "/workouts")
    else:
        top = head("Pick for today", "Say what you want", "/", coach_button(chosen))
        top += choose_tabs("said")
    return framed("me" if later else "today", "Describe", (
        f'{top}{frame[0]}<form method="post" action="{here}" id="describe" '
        f'class="{"said" if kept else "setup"}">'
        '<textarea class="panel grow" name="words" required maxlength="600" '
        'aria-label="Describe a workout" placeholder="30 min upper body, finish with bike sprints'
        '&#10;&#10;Or in short: rower 15, deadlift or curl 3x, rower 15">'
        f"{esc(words)}</textarea>{note(problem, True)}"
        '<p class="muted note">Goes to Claude: what you type, the exercises you can do, and your '
        "usual time, experience and goals. Nothing else. Short ones are read here.</p>"
        '<div class="actions"><button class="primary big">Build it</button></div></form>'
        f"{saved}{frame[1]}"
    ), person, ("describe.js",))


def _choice(field: str, value: str, label: str, chosen: str) -> str:
    on = " checked" if value == chosen else ""
    return (
        f'<label class="choice-pill"><input type="radio" name="{field}" value="{value}"{on}>'
        f"<span>{esc(label)}</span></label>"
    )


def _pace_fields(block: dict) -> str:
    """Return the rower's and the treadmill's settings, for the machines a block may use."""
    hard = " on the hard parts" if block.get("style", "steady") != "steady" else ""
    fields = ""
    if "rower" in block["options"]:
        watts = [("0", "My own pace"), *((str(each), f"{each} W") for each in pace.WATTS)]
        fields += select(f"Rower, average{hard}", "watts", watts, str(block.get("watts", 0)))
    if "treadmill" in block["options"]:
        speeds = [(f"{each:g}", f"{each:g} km/h") for each in pace.SPEEDS]
        slopes = [(str(each), str(each) if each else "Flat") for each in pace.INCLINES]
        fields += select(f"Speed{hard}", "speed", [("0", "My own pace"), *speeds],
                          f"{block.get('speed', 0):g}")
        fields += select("Incline", "incline", slopes, str(block.get("incline", 0)))
    return fields


def _pick_row(workout: dict, plan: Session, picked: bool) -> str:
    if not plan.items:
        return row(workout["name"], "Not ready: nothing in it fits your equipment",
                   mark="stack", css="dim")
    state = pill("Today", "ok") if picked else '<span class="do">Use today</span>'
    return row(workout["name"], f"{plan.minutes} min · {summary(workout)}", field="workout",
               value=str(workout["id"]), mark="stack", tone="lifting", right=state,
               css="chosen" if picked else "")


def saved_words(plan: Session, reps: int) -> str:
    parts = ["Saved", f"{plan.minutes} min" if plan.items else "", f"{reps} reps" if reps else ""]
    return " · ".join(part for part in parts if part)


def workout_page(
    person: str,
    workout: dict,
    plan: Session,
    offered: dict[str, list[str]],
    said: str = "",
    prompt_saved: bool = False,
) -> str:
    """Return a workout as a chart from top to bottom, to drag about or to tap."""
    base = f"/workouts/{workout['id']}"
    use = ""
    if plan.items:
        use = (
            '<form method="post" action="/pick"><button class="primary" name="workout" '
            f'value="{workout["id"]}">Use today</button></form>'
        )
    room = len(workout["blocks"]) < MOST_BLOCKS
    add = button(f"{base}/add", "Add", "", "plus") if room else off("Full")
    data = json.dumps(workout["blocks"]).replace("<", "\\u003c")
    aside = edit_link(f"{base}/settings", workout["name"])
    return framed("me", workout["name"], (
        f'{head(workout["name"], saved_words(plan, workout["reps"]), "/workouts", aside)}'
        f'{prompt_bar(workout, prompt_saved)}{note(said)}'
        f'<div class="builder"><div class="flow scroll grow" id="flow" '
        f'data-workout="{workout["id"]}" data-most="{MOST_BLOCKS}" data-options="{MOST_OPTIONS}">'
        f"{flow(workout, plan)}</div>{palette(offered)}</div>"
        f'<script type="application/json" id="blocks">{data}</script>'
        f'<div class="actions">{add}{button(f"{base}/refine", "Refine")}{use}</div>'
    ), person, ("flow.js",))


def prompt_bar(workout: dict, saved: bool) -> str:
    """Return what a workout was asked for as, and a way to keep it."""
    if not workout["talk"]:
        return ""
    words = whole_prompt(workout["talk"])
    keep = pill("Prompt saved", "ok")
    if not saved:
        keep = (
            f'<form method="post" action="/workouts/{workout["id"]}/prompt">'
            "<button>Save prompt</button></form>"
        )
    return (
        f'<div class="asked"><span title="{esc(words)}">“{esc(words)}”</span>{keep}</div>'
    )


def refine_page(
    person: str, workout: dict, words: str, problem: str, saved: bool
) -> str:
    """Return the conversation about a workout, and the box to ask for a change in."""
    base = f"/workouts/{workout['id']}"
    talk = "".join(
        f'<li class="you">{esc(each["you"])}</li>'
        + (f'<li class="claude">{esc(each["note"])}</li>' if each["note"] else "")
        for each in workout["talk"]
    ) or '<li class="claude">Built by hand. Say what to change.</li>'
    blocks = ", ".join(
        f"{block_name(block)} {block_amount(block)}" for block in workout["blocks"]
    )
    undo = ""
    if workout["before"]:
        undo = (
            f'<form method="post" action="{base}/refine">'
            '<button name="undo" value="1">Undo</button></form>'
        )
    return framed("me", "Refine", (
        f'{head("Refine", workout["name"], base, undo)}{prompt_bar(workout, saved)}'
        f'<section class="panel scroll grow talk"><ul>{talk}</ul></section>'
        f'<p class="now-is"><b>Now:</b> {esc(blocks or "nothing yet")}</p>'
        f'<form method="post" action="{base}/refine" class="ask" id="describe">'
        f'<input name="words" required maxlength="600" value="{esc(words)}" autocomplete="off" '
        'placeholder="Less rower, add a finisher" aria-label="What to change">'
        '<button class="primary">Change</button></form>'
        f'{note(problem, True)}<p class="muted note">Goes to Claude with this workout\'s blocks, '
        "and the same as when you described it.</p>"
    ), person, ("describe.js",))


def flow(workout: dict, plan: Session) -> str:
    """Return the chart: a start, each block with a way to add between, and an end."""
    base, blocks = f"/workouts/{workout['id']}", workout["blocks"]
    room = len(blocks) < MOST_BLOCKS

    def gap(place: int) -> str:
        if not room:
            return f'<span class="gap" data-gap="{place}"></span>'
        return (
            f'<a class="gap" data-gap="{place}" href="{base}/add?at={place}" '
            f'aria-label="Add a block here"><span>{icon("plus")}</span></a>'
        )

    drawn = [f'<div class="end">{icon("today")}Start</div>{gap(0)}']
    for place, block in enumerate(blocks):
        before = blocks[place - 1].get("music") if place else None
        drawn.append(_flow_block(base, place, block, before) + gap(place + 1))
    minutes = f"About {plan.minutes} min" if plan.items else "Add the first block"
    drawn.append(f'<div class="end">{icon("check")}{minutes}</div>')
    return "".join(drawn)


def _flow_block(base: str, place: int, block: dict, before: dict | None) -> str:
    here = f"{base}/block/{place}"
    music = block.get("music")
    several = len(block["options"]) > 1
    cards = '<span class="or">or</span>'.join(
        _flow_card(here, key, block, several) for key in block["options"]
    )
    words = ""
    if music and (before or {}).get("uri") != music["uri"]:
        words = f'<span class="tag">{icon("note")}{esc(music["name"])}</span>'
    css = " tagged" if music else ""
    css += " joins" if music and (before or {}).get("uri") == music["uri"] else ""
    label = f"Playlist: {music['name']}" if music else "Choose a playlist"
    return (
        f'<div class="block {block_tone(block)}{css}" data-place="{place}" '
        f'data-cardio="{int(is_cardio(block))}">'
        f'<button class="grip" type="button" data-grip="block" '
        f'aria-label="Move {esc(block_name(block))}">{icon("grip")}</button>'
        f'<div class="cards">{cards}</div>'
        f'<a class="tune" href="{here}/music" aria-label="{esc(label)}" '
        f'title="{esc(label)}">{icon("note")}</a>{words}</div>'
    )


def _flow_card(here: str, key: str, block: dict, several: bool) -> str:
    grip = ""
    if several:
        grip = (
            f'<button class="grip small" type="button" data-grip="option" data-key="{key}" '
            f'aria-label="Take {esc(name_of(key))} out">{icon("grip")}</button>'
        )
    detail = " · ".join(
        part for part in (block_amount(block), "" if several else block_detail(block)) if part
    )
    mark = "bolt" if block_tone(block) == "flatout" else icon_of(key)
    return (
        f'<div class="step" data-key="{key}">{grip}<a href="{here}">{chip(mark)}'
        f'<span class="words"><span class="name">{esc(name_of(key))}</span>'
        f'<span class="under">{esc(detail)}</span></span></a></div>'
    )


def palette(offered: dict[str, list[str]]) -> str:
    """Return what can be dragged in, shown beside the chart where there is room."""
    groups = "".join(
        f"<h2>{esc(CATEGORIES[category])}</h2>"
        + "".join(
            f'<button class="piece {tone_of(key)}" type="button" data-grip="new" '
            f'data-key="{key}" data-cardio="{int(key in CARDIO)}">{icon("grip")}'
            f"{esc(name_of(key))}</button>"
            for key in keys
        )
        for category, keys in offered.items() if keys
    )
    label = 'aria-label="Drag into the workout"'
    return f'<aside class="palette panel scroll" {label}>{groups}</aside>'


def block_music_page(
    person: str, workout: dict, place: int, found: list[dict], problem: bool
) -> str:
    """Return the playlists, to put one on a block, from it on, or on the whole workout."""
    base = f"/workouts/{workout['id']}"
    here = f"{base}/block/{place}/music"
    block = workout["blocks"][place]
    music = block.get("music")
    scopes = "".join(
        _choice("scope", key, label, "one") for key, label in SCOPES.items()
    )
    shown = [
        row(each["name"], field="add", value=each["uri"], mark="note", tone="cooldown",
            right=pill("On", "ok") if each == music else '<span class="do">Use</span>',
            css="chosen" if each == music else "")
        for each in found
    ]
    clear = '<button name="clear" value="1">Take off</button>' if music else ""
    words = "Spotify didn't open that. Copy the playlist's own link." if problem else ""
    empty = "No playlists: link Spotify in Me, or paste a link."
    return framed("me", "Playlist", (
        f'{head("Playlist", block_name(block), base)}'
        f'<form method="post" action="{here}" class="setup" novalidate>'
        f'<fieldset class="choices"><legend>Plays on</legend>{scopes}</fieldset>'
        f'{rows(shown, empty)}{note(words, True)}'
        '<div class="make"><input name="link" placeholder="Or paste a Spotify link" '
        'aria-label="Spotify link" autocomplete="off" autocapitalize="none">'
        f'<button name="paste" value="1">Add link</button>{clear}</div></form>'
    ), person)


def settings_page(
    person: str, workout: dict, mixes: list[dict], mine: int, sure: bool
) -> str:
    base = f"/workouts/{workout['id']}"
    choices = [("", "Usual"), ("off", "None")]
    choices += [(str(each["id"]), each["name"]) for each in mixes]
    music = "".join(
        f'<option value="{value}"{" selected" if value == workout["mix"] else ""}>{esc(label)}'
        "</option>"
        for value, label in choices
    )
    remove = confirm_delete(f"{base}/delete", f"{base}/settings?sure=1", sure)
    return framed("me", "Name", (
        f'{head("Settings", workout["name"], base, remove)}'
        f'<form method="post" action="{base}/settings" class="setup">'
        f'<section class="panel scroll grow top"><label class="pick">Name<input class="wide" '
        f'name="name" maxlength="{NAME_LENGTH}" value="{esc(workout["name"])}" required></label>'
        f'{reps_select(workout["reps"], usual_reps(0, mine))}'
        f'<label class="pick">Music<select class="wide" name="mix">{music}</select></label>'
        '</section><div class="actions"><button class="primary big">Save</button></div></form>'
    ), person)


def _tail(to: int | None, at: int | None) -> str:
    """Return the address's word on where a new exercise goes: into a block, or at a place."""
    if to is not None:
        return f"?to={to}"
    return "" if at is None else f"?at={at}"


def categories_page(
    person: str, workout: dict, categories: list[str], to: int | None, at: int | None
) -> str:
    base = f"/workouts/{workout['id']}"
    tail = _tail(to, at)
    cells = "".join(_category(f"{base}/add/{key}{tail}", key) for key in categories)
    cells = cells or '<p class="muted">Choose your equipment in Me first.</p>'
    back = base if to is None else f"{base}/block/{to}"
    title = "Add a block" if to is None else "Or instead"
    return framed("me", "Add", (
        f'{head(title, workout["name"], back)}'
        f'<div class="scroll grow"><div class="grid">{cells}</div></div>'
    ), person)


def _category(href: str, key: str) -> str:
    mark, tone = CATEGORY_MARKS.get(key, ("lift", "lifting"))
    return (
        f'<a class="card" href="{esc(href)}">{chip(mark, tone)}<b>{esc(CATEGORIES[key])}</b></a>'
    )


def choices_page(
    person: str,
    workout: dict,
    category: str,
    keys: list[str],
    to: int | None,
    at: int | None,
) -> str:
    base = f"/workouts/{workout['id']}"
    shown = [
        row(name_of(key), field="key", value=key, mark=icon_of(key), tone=tone_of(key),
            right='<span class="do">Add</span>')
        for key in keys
    ]
    hidden = "" if to is None else f'<input type="hidden" name="to" value="{to}">'
    if to is None and at is not None:
        hidden = f'<input type="hidden" name="at" value="{at}">'
    tail = _tail(to, at)
    return framed("me", CATEGORIES[category], (
        f'{head(CATEGORIES[category], workout["name"], f"{base}/add{tail}")}'
        f'<form method="post" action="{base}/add" class="setup">{hidden}{rows(shown)}</form>'
    ), person)


def block_page(person: str, workout: dict, place: int, mine: int) -> str:
    base = f"/workouts/{workout['id']}"
    block = workout["blocks"][place]
    if is_cardio(block):
        field, unit, chosen = "minutes", "min", block["minutes"]
        values = sorted({*MINUTES, chosen})  # a time of their own stays on offer
    else:
        field, unit, chosen = "sets", "sets", block["sets"]
        values = tuple(range(1, MOST_SETS + 1))
        wanted = reps_select(block.get("reps", 0), usual_reps(workout["reps"], mine))
    choices = "".join(
        f'<option value="{value}"{" selected" if value == chosen else ""}>{value} {unit}</option>'
        for value in values
    )
    several = len(block["options"]) > 1
    options = [
        row(name_of(key), field="drop", value=key, mark=icon_of(key), tone=tone_of(key),
            right='<span class="do">Take out</span>') if several
        else row(name_of(key), mark=icon_of(key), tone=tone_of(key))
        for key in block["options"]
    ]
    more = len(block["options"]) < MOST_OPTIONS
    other = button(f"{base}/add?to={place}", "Or…", "", "plus") if more else off("Or…")
    up = '<button name="move" value="up">Up</button>' if place else off("Up")
    last = place == len(workout["blocks"]) - 1
    down = off("Down") if last else '<button name="move" value="down">Down</button>'
    words = "Each time, the one you did longer ago." if several else "Add another to alternate."
    label = "Sets" if unit == "sets" else "Minutes"
    tagged = (block.get("music") or {}).get("name", "None of its own")
    playlist = row("Playlist", tagged, href=f"{base}/block/{place}/music", mark="note",
                   tone="cooldown")
    more = "" if is_cardio(block) else wanted
    if is_cardio(block):
        styles = [(key, style_name(key)) for key in STYLES]
        more = select("Style", "style", styles, block.get("style", "steady"))
        if any(is_levelled(key) for key in block["options"]):
            levels = [("0", "Same as last time")]
            levels += [(str(number), name) for number, name in enumerate(LEVELS, 1)]
            more += select("Level", "level", levels, str(block.get("level", 0)))
        more += _pace_fields(block)
    return framed("me", "Block", (
        f'{head(f"Block {place + 1}", workout["name"], base)}'
        f'<form method="post" action="{base}/block/{place}" class="setup">'
        f'<div class="scroll grow stacked"><div class="panel fields"><label class="pick">{label}'
        f'<select class="wide" name="{field}">{choices}</select></label>{more}</div>'
        f'{rows(options)}'
        f'{rows([playlist])}</div>{note(words)}'
        f'<div class="actions">{other}{up}{down}'
        '<button class="danger" name="remove" value="1">Remove</button></div>'
        '<div class="actions"><button class="primary big" name="save" value="1">Save</button>'
        "</div></form>"
    ), person)


# Connections -----------------------------------------------------------------


def heart_page(person: str, name: str, nearby: list[tuple[str, str]], scanning: bool) -> str:
    shown = [
        row(label, "Nearby", field="source", value=f"{address} {label}", mark="heart",
            tone="flatout", right='<span class="do">Use this</span>')
        for label, address in nearby
    ]
    forget = (
        '<form method="post" action="/heart">'
        '<button name="forget" value="1">Forget</button></form>'
        if name else ""
    )
    again = off("Looking…") if scanning else button("/heart?scan=1", "Look nearby", "primary")
    return framed("me", "Heart rate", (
        f'{head("Heart rate", name or "Not set", "/me", forget)}'
        f'<form method="post" action="/heart" class="setup">'
        f'{rows(shown, "Looking…" if scanning else "Nothing found nearby.")}</form>'
        '<p class="callout">With AirPods: tap Look nearby, switch to HeartCast and keep it in '
        "front for 20 seconds, then come back. An iPhone hides an app that isn't in front. "
        "Afterwards, just open HeartCast for 20 seconds before or during a workout.</p>"
        f'<div class="actions">{again}</div>'
    ), person)


def spotify_page(
    person: str, ready: bool, linked: dt.date | None, speaker: str, problem: str
) -> str:
    """Return the Spotify screen: link, unlink, and what reps does with it."""
    if linked:
        again = linked + dt.timedelta(days=SPOTIFY_LINK_DAYS)
        state = f"Linked. Spotify asks you to link again by {again:%-d %b %Y}."
        state += f" Music starts on {speaker or 'whatever is playing'}."
        action = (
            '<form method="post" action="/spotify/unlink">'
            '<button class="danger">Disconnect</button></form>'
            + button("/spotify/devices", "Play on…", "primary", "speaker")
        )
    elif ready:
        state, action = "Not linked.", button("/spotify/link", "Link Spotify", "primary")
    else:
        state, action = "Not set up on homehub yet.", off("Link Spotify")
    message = f'<p class="error" role="alert">{esc(problem)}</p>' if problem else ""
    return framed("me", "Spotify", (
        f'{head("Spotify", "Music for your sessions", "/me")}'
        f'<section class="panel scroll grow prose">{message}<p><b>{esc(state)}</b></p>'
        "<p>Shows the song playing while you have reps open, with its controls. If you choose a "
        "music mix, starts its playlists as your session moves on, and reads your playlists' "
        "names to build one.</p><p>It keeps a key from Spotify and your mixes on homehub, and "
        "nothing about what you play. Artwork loads on your phone from Spotify.</p>"
        "<p>Disconnect deletes the key. You can also remove reps in your Spotify account, "
        "under Manage apps.</p></section>"
        f'<div class="actions">{action}</div>'
    ), person)


def devices_page(person: str, speaker: str, devices: list[dict], problem: str) -> str:
    """Return the Spotify Connect devices, to choose where a session's music starts."""
    names = list(dict.fromkeys(each["name"] for each in devices))
    playing = {each["name"] for each in devices if each["active"]}
    here = set(names)
    if speaker and speaker not in names:
        names.append(speaker)

    def device(name: str) -> str:
        chosen = name == speaker
        under = "Playing now" if name in playing else ""
        if name not in here:
            under = "Not showing now"
        state = pill("Chosen", "ok") if chosen else '<span class="do">Use this</span>'
        return row(name, under, field="device", value=name, mark="speaker", tone="cooldown",
                   right=state, css="chosen" if chosen else "")

    state = "" if speaker else pill("Chosen", "ok")
    shown = [row("Whatever is playing", "Follow Spotify", field="device", value="",
                 mark="note", tone="cooldown", right=state, css="" if speaker else "chosen")]
    shown += [device(name) for name in names]
    words = problem or "To play on this phone, open Spotify on it, then look again."
    again = '<a class="button" href="spotify:">Open Spotify</a>'
    again += button("/spotify/devices", "Look again")
    if problem:
        again = button("/spotify/link", "Link again", "primary")
    return framed("me", "Play on", (
        f'{head("Play on", "Spotify Connect", "/me")}'
        f'<form method="post" action="/spotify/devices" class="setup">{rows(shown)}</form>'
        f'{note(words, bool(problem))}<div class="actions">{again}</div>'
    ), person)
