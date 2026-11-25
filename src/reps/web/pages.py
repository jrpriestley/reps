"""The server-drawn screens. Each fits its window, so lists are paged rather than scrolled."""

import calendar
import datetime as dt
import json
import sqlite3
import time

from reps import __version__
from reps.coach import pace
from reps.coach.custom import base_key
from reps.coach.intervals import clock, rounds
from reps.coach.plan import (
    CORE_SESSIONS,
    GOALS,
    LEAST_MINUTES,
    MOST_MINUTES,
    Item,
    Session,
    uncovered,
)
from reps.guides import Guide, guide_for
from reps.gym import DIAL_LOADS, KG_PER_LB, pounds
from reps.library import EQUIPMENT, EXERCISES, PATTERNS
from reps.store import Store, local_day, week_start
from reps.web.icons import icon
from reps.web.layout import THEMES, bare, framed, page
from reps.web.ui import (
    button,
    check,
    chip,
    confirm_delete,
    esc,
    head,
    icon_of,
    off,
    reps_select,
    row,
    rows,
    section,
    select,
    tone_of,
    tone_of_item,
)

COPIES_LATE = 3 * 24 * 3600  # seconds; one or two missed nights mend themselves
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
EXPERIENCE = {
    "new": "New: two sets of everything",
    "returning": "Returning: three sets, starting light",
    "regular": "Regular: three sets",
}


def amounts(sets: list[tuple[float, int]], kind: str) -> str:
    """Return sets in a few characters: 3×10, or 10 9 8 when they differ."""
    if kind == "cardio":
        return f"{round(sets[0][1] / 60)} min"
    counts = [count for _, count in sets]
    unit = "s" if kind == "time" else ""
    if len(set(counts)) == 1:
        return f"{len(counts)}×{counts[0]}{unit}"
    return " ".join(str(count) for count in counts) + unit


def loads(
    sets: list[tuple[float, int]], kind: str = "load", key: str = "", incline: float = 0
) -> str:
    used = sorted({load for load, _ in sets if load})
    if kind == "cardio":
        return pace.words(key, used[-1] if used else 0, incline)
    if not used:
        return ""
    return pounds(used[0]) if len(used) == 1 else f"{used[0]:g}–{pounds(used[-1])}"


def item_row(item: Item, why: bool) -> str:
    load = loads(item.sets, item.kind, item.key, item.incline)
    if item.work:
        times = rounds(item.sets[0][1], item.work, item.off)
        pattern = f"{times} × {clock(item.work)} on, {clock(item.off)} off"
        if load:
            pattern = f"{clock(item.work)}/{clock(item.off)} · {load}"  # all there is room for
        load = pattern
    right = f'<span class="big-amount">{amounts(item.sets, item.kind)}</span>'
    under = item.why if why else load
    linked = f"/how/{base_key(item.key)}" if guide_for(item.key) else ""
    return row(item.name, under, href=linked, right=right, css="why" if why else "",
               mark="bolt" if item.work else icon_of(item.key),
               tone=tone_of_item(item.key, item.work))


def today_page(
    person: str,
    plan: Session,
    active: bool,
    why: bool,
    pick: dict | None = None,
    done: int = 0,
    generated: bool = False,
) -> str:
    """Return today's plan: the coach's, a saved workout, or a generated one."""
    if active:
        action, control = '<a class="button primary big" href="/session">Resume</a>', ""
    elif generated:
        action, control = _start_at("/generate/start"), _counts(True)
    elif pick:
        action = _start_at(f'/workouts/{pick["id"]}/start')
        control = _counts(bool(pick["counts"]))
    else:
        action, control = _start_coached(plan), _minutes_stepper(plan)
    chosen = pick is not None or generated
    here = "/?" if chosen else f"/?minutes={plan.minutes}&"
    eyebrow = "Generated for today" if generated else "Saved workout" if pick else "Coach's plan"
    more = ""
    if generated and not active:
        more = _small_button("/generate/another", "Another")
        more += _small_button("/generate/save", "Save")
    flip = here + ("" if why else "why=1")
    dots = "".join(
        f'<i class="{"on" if number < done else ""}"></i>' for number in range(CORE_SESSIONS)
    )
    return framed("today", "Today", (
        '<div class="two-up"><section class="hero"><div class="hero-top">'
        f'<span class="eyebrow">{eyebrow}</span>'
        f'<span class="dots" role="img" aria-label="{done} of {CORE_SESSIONS} done this week">'
        f'{dots}</span></div><h1>{esc(plan.title)}</h1>'
        f'<div class="hero-facts"><span><b>{plan.minutes}</b> min</span>'
        f'<span><b>{len(plan.items)}</b> exercises</span>{control}</div>'
        f'<div class="actions">{button("/pick", "Change", "on-hero", "stack")}{action}</div>'
        f'</section>{rows([item_row(item, why) for item in plan.items])}</div>'
        f'<div class="flips"><a class="flip" href="{esc(flip)}">'
        f'{"Show loads" if why else "Why these?"}</a>{more}</div>'
    ), person)


def _start_coached(plan: Session) -> str:
    return (
        f'<form method="post" action="/start"><input type="hidden" name="minutes" '
        f'value="{plan.minutes}"><button class="primary big">Start</button></form>'
    )


def _start_at(address: str) -> str:
    return (
        f'<form method="post" action="{address}" id="start">'
        '<button class="primary big">Start</button></form>'
    )


def _counts(on: bool) -> str:
    return check("checkbox", "counts", "1", "One of my three", on, ' form="start"')


def _small_button(address: str, label: str) -> str:
    return f'<form method="post" action="{address}"><button class="flip">{label}</button></form>'


def _minutes_stepper(plan: Session) -> str:
    less, more = plan.minutes - 5, plan.minutes + 5
    return (
        '<div class="stepper">'
        f'{_minutes_link(less, "−", less >= LEAST_MINUTES)}'
        f'{_minutes_link(more, "+", more <= MOST_MINUTES)}</div>'
    )


def _minutes_link(minutes: int, label: str, allowed: bool) -> str:
    if not allowed:
        return off(label)
    href = f"/?minutes={minutes}"
    return f'<a class="button" href="{href}" aria-label="{minutes} minutes">{label}</a>'


def week_page(
    person: str, setup: dict, done: list[sqlite3.Row], today: dt.date, pick: dict | None = None
) -> str:
    monday = week_start(today)
    by_day: dict[dt.date, list[sqlite3.Row]] = {}
    for each in done:
        by_day.setdefault(local_day(each["started"]), []).append(each)
    counted = [each for each in done if each["kind"] != "extra"]
    left = max(0, CORE_SESSIONS - len(counted))
    coming = {}
    number = len(counted)
    for day in sorted(setup["days"]):
        date = monday + dt.timedelta(days=day)
        if date >= today and date not in by_day:
            number += 1
            core = number <= CORE_SESSIONS
            coming[date] = f"Core {number} of {CORE_SESSIONS}" if core else "Bonus"
    if pick:
        coming[today] = pick["name"]
    shown = [
        _week_row(monday + dt.timedelta(days=offset), name, by_day, coming, today, pick)
        for offset, name in enumerate(DAY_NAMES)
    ]
    if left:
        note = f"{left} core session{'s' if left > 1 else ''} to go. A missed day slides along."
    else:
        note = "That's a full week. Anything more is a bonus."
    minutes = round(sum(_seconds(each) for each in done) / 60)
    return framed("week", "Week", (
        f'{head("This week", f"{monday:%-d %b} to {monday + dt.timedelta(days=6):%-d %b}")}'
        f'<div class="tiles">{_tile(f"{CORE_SESSIONS - left}/{CORE_SESSIONS}", "core")}'
        f'{_tile(str(len(done)), "sessions")}{_tile(str(minutes), "minutes")}</div>'
        f'{rows(shown, css="week")}<p class="muted note">{note}</p>'
    ), person)


def _week_row(
    date: dt.date,
    name: str,
    by_day: dict[dt.date, list[sqlite3.Row]],
    coming: dict[dt.date, str],
    today: dt.date,
    pick: dict | None,
) -> str:
    css = "now" if date == today else ""
    day = f'<span class="day"><b>{name}</b>{date.day}</span>'
    if date in by_day:
        titles = [each["title"] for each in by_day[date]]
        titles += [pick["name"]] if pick and date == today else []
        words = f'<span class="name">{esc(", ".join(titles))}</span>'
        inside = f'{day}{words}<span class="pill ok">Done</span>{icon("go")}'
        where = f"/history/{by_day[date][0]['id']}"
        return f'<li class="{css}"><a class="row" href="{where}">{inside}</a></li>'
    words = f'<span class="name">{esc(coming.get(date, ""))}</span>'
    if not coming.get(date):
        words = '<span class="name muted">Rest</span>'
    return f'<li class="{css}"><div class="row">{day}{words}</div></li>'


def _tile(value: str, label: str) -> str:
    return f'<div class="tile"><b>{esc(value)}</b><span>{esc(label)}</span></div>'


def how_page(person: str, name: str, key: str, guide: Guide) -> str:
    steps = "".join(f"<li>{esc(step)}</li>" for step in guide.steps)
    about = guide.as_dict()
    return framed("today", name, (
        f'{head(name, "How to", "/")}'
        f'<section class="panel scroll grow guide {tone_of(key)}"><ol>{steps}</ol>'
        f'<p class="callout"><b>Watch for:</b> {esc(guide.watch)}</p>'
        '<p class="muted">Stop if anything hurts.</p></section>'
        f'<div class="actions"><a class="button" href="{esc(about["link"])}" target="_blank" '
        f'rel="noopener noreferrer">{icon("link")}{esc(about["source"])}</a></div>'
    ), person)


def history_views(view: str, month: str) -> str:
    def link(key: str, label: str) -> str:
        here = " aria-current=page" if key == view else ""
        href = f"/history?view={key}&amp;month={month}"
        return f'<a href="{href}"{here}>{label}</a>'

    return (
        f'<div class="segments views">{link("calendar", "Month")}{link("list", "List")}'
        f'{link("progress", "Progress")}</div>'
    )


def history_list_page(person: str, sessions: list[sqlite3.Row], month: str) -> str:
    return framed("history", "History", (
        f'{head("History", f"{len(sessions)} sessions", aside=history_views("list", month))}'
        f'{rows(_session_rows(sessions), "Nothing yet. Your sessions will show here.")}'
    ), person)


def history_day_page(person: str, sessions: list[sqlite3.Row], day: dt.date) -> str:
    return framed("history", "History", (
        f'{head(f"{day:%A %-d %B}", back=f"/history?month={day:%Y-%m}")}'
        f'{rows(_session_rows(sessions), "Nothing that day.")}'
    ), person)


def _session_rows(sessions: list[sqlite3.Row]) -> list[str]:
    return [
        row(
            each["title"],
            f'{local_day(each["started"]):%a %-d %b}',
            href=f'/history/{each["id"]}',
            mark="wave" if each["kind"] == "extra" else "lift",
            tone="steady" if each["kind"] == "extra" else "lifting",
            right=f'<span class="big-amount">{_minutes(each)} min</span>',
        )
        for each in sessions
    ]


def _minutes(session: sqlite3.Row) -> int:
    return max(1, round(_seconds(session) / 60))


def _seconds(session: sqlite3.Row) -> float:
    return session["ended"] - session["started"] - session["paused"]


def history_calendar_page(
    person: str, sessions: list[sqlite3.Row], month: dt.date, today: dt.date
) -> str:
    """Return a month with its workout days marked; a marked day opens what was done."""
    by_day: dict[dt.date, list[sqlite3.Row]] = {}
    for each in sessions:
        by_day.setdefault(local_day(each["started"]), []).append(each)
    weeks = calendar.Calendar().monthdatescalendar(month.year, month.month)
    names = "".join(f'<span class="cal-name">{name[:1]}</span>' for name in DAY_NAMES)
    cells = "".join(
        _calendar_cell(day, by_day.get(day, []), today) if day.month == month.month
        else "<span></span>"
        for week in weeks
        for day in week
    )
    before = month - dt.timedelta(days=1)
    after = (month + dt.timedelta(days=32)).replace(day=1)
    earliest = min(by_day, default=today)
    earlier = _history_link(
        f"month={before:%Y-%m}", "back", "Earlier month", before >= earliest.replace(day=1)
    )
    later = _history_link(f"month={after:%Y-%m}", "go", "Later month", after <= today)
    here = [each for each in sessions
            if f'{local_day(each["started"]):%Y-%m}' == f"{month:%Y-%m}"]
    minutes = sum(_minutes(each) for each in here)
    return framed("history", "History", (
        f'{head("History", aside=history_views("calendar", f"{month:%Y-%m}"))}'
        '<div class="two-up"><section class="panel month scroll">'
        f'<div class="month-head">{earlier}<b>{month:%B %Y}</b>{later}</div>'
        f'<div class="cal">{names}{cells}</div>'
        f'<p class="muted note">{len(here)} done · {minutes} min</p></section>'
        f'{rows(_session_rows(here), "Nothing this month.")}</div>'
    ), person)


def _calendar_cell(day: dt.date, done: list[sqlite3.Row], today: dt.date) -> str:
    css = " now" if day == today else ""
    if not done:
        return f'<span class="cal-day{css}">{day.day}</span>'
    titles = ", ".join(each["title"] for each in done)
    where = f"/history/{done[0]['id']}" if len(done) == 1 else f"/history?day={day}"
    extra = " extra" if all(each["kind"] == "extra" for each in done) else ""
    return (
        f'<a class="cal-day done{extra}{css}" href="{where}" title="{esc(titles)}" '
        f'aria-label="{day:%-d %B}: {esc(titles)}">{day.day}</a>'
    )


def _history_link(query: str, mark: str, label: str, allowed: bool) -> str:
    if not allowed:
        return '<span class="icon-button off"></span>'
    return (
        f'<a class="icon-button" href="/history?{query}" aria-label="{label}">{icon(mark)}</a>'
    )


def past_session_page(person: str, store: Store, session: sqlite3.Row, sure: bool) -> str:
    plan = json.loads(session["plan"])
    states = store.states(session["id"])
    sets: dict[str, list[tuple[float, int]]] = {}
    inclines: dict[str, float] = {}
    for each in store.logged(session["id"]):
        sets.setdefault(each["item"], []).append((each["load"], each["amount"]))
        inclines[each["item"]] = each["incline"]
    lines = []
    for item in plan["items"]:
        done = sets.get(item["key"])
        under, right = "Skipped", ""
        if done:
            effort = states.get(item["key"], {}).get("rir")
            left = f"{effort}{'+' if effort == 4 else ''} left" if effort is not None else ""
            used = loads(done, item["kind"], item["key"], inclines[item["key"]])
            under = " · ".join(part for part in (used, left) if part)
            right = f'<span class="big-amount">{amounts(done, item["kind"])}</span>'
        tone = tone_of_item(item["key"], item.get("work", 0)) if done else ""
        lines.append(row(item["name"], under, mark=icon_of(item["key"]), tone=tone, right=right))
    day, heart = local_day(session["started"]), store.heart_summary(session["id"])
    tiles = _tile(str(_minutes(session)), "minutes") + _tile(str(len(sets)), "exercises")
    if heart:
        tiles += _tile(f"♥ {heart[0]}", f"top {heart[1]}")
    remove = confirm_delete(
        f'/history/{session["id"]}/delete', f'/history/{session["id"]}?sure=1', sure
    )
    return framed("history", session["title"], (
        f'{head(session["title"], f"{day:%A %-d %B}", f"/history?month={day:%Y-%m}", remove)}'
        f'<div class="tiles">{tiles}</div>{rows(lines)}'
    ), person)


# Setup -----------------------------------------------------------------------

STEPS = ("goals", "equipment", "time", "days", "body", "hurts")
FIRST_STEPS = ("goals", "equipment", "time", "days", "body")
STEP_TITLES = {
    "goals": "Goals and reps", "equipment": "Equipment", "time": "Typical time",
    "days": "Days", "body": "Experience and weight", "hurts": "Avoid",
}


def me_page(
    person: str, setup: dict, workouts: int, mix: str, spotify: bool, missing: list[str],
    copied: float | None = None,
) -> str:
    covered = f"{len(setup['equipment'])} of {len(EQUIPMENT)} items"
    covered += f", no {', '.join(missing).lower()}" if missing else ""
    avoided = len(setup["avoid"]) + len(setup["hurts"])
    training = [
        row("Workouts", f"{workouts} saved", href="/workouts", mark="stack", tone="lifting"),
        row("Goals and reps", ", ".join(GOALS[goal] for goal in setup["goals"]),
            href="/setup/goals",
            mark="target", tone="lifting"),
        row("Weights", "What you lift, by exercise", href="/weights", mark="lift",
            tone="lifting"),
        row("Equipment", covered, href="/setup/equipment", mark="lift", tone="lifting"),
        row("Typical time", f"{setup['minutes']} min", href="/setup/time", mark="clock",
            tone="lifting"),
        row("Days", " ".join(DAY_NAMES[day] for day in sorted(setup["days"])),
            href="/setup/days", mark="week", tone="lifting"),
    ]
    body = [
        row("Experience and weight", setup["experience"].capitalize(), href="/setup/body",
            mark="scale", tone="steady"),
        row("Avoid", f"{avoided} left out" if avoided else "Nothing", href="/setup/hurts",
            mark="shield", tone="steady"),
        row("Heart rate", setup["heart_name"] or "Not set", href="/heart", mark="heart",
            tone="flatout"),
    ]
    music = [
        row("Music mixes", mix or "Off", href="/music", mark="note", tone="cooldown"),
        row("Play on", setup["speaker"] or "Whatever is playing", href="/spotify/devices",
            mark="speaker", tone="cooldown"),
        row("Spotify", "Linked" if spotify else "Not linked", href="/spotify", mark="link",
            tone="cooldown"),
    ]
    themes = "".join(
        f'<button name="theme" value="{theme}">{theme.title()}</button>' for theme in THEMES
    )
    app = [
        '<li><form method="post" action="/theme" class="segments themes">'
        f"{themes}</form></li>",
        row("Export my data", "Everything, as a file", href="/export.json", mark="save"),
        *_copies_row(copied),
        '<li><form method="post" action="/logout"><button class="row">'
        f'{chip("out")}<span class="words"><span class="name">Sign out</span>'
        f'<span class="under">{esc(person)}</span></span></button></form></li>',
    ]
    return framed("me", "Me", (
        f'<div class="scroll grow groups"><div class="columns">{section("Training", training)}'
        f'{section("Music", music)}{section("Body", body)}{section("This app", app)}'
        f'<p class="muted note version">reps {__version__}</p></div></div>'
    ), person)


def copies_late(copied: float, now: float) -> bool:
    return now - copied > COPIES_LATE


def _copies_row(copied: float | None) -> list[str]:
    """Say when the encrypted copies last reached Proton Drive, if they are set up."""
    if copied is None:
        return []
    day = local_day(copied)
    days = (dt.date.today() - day).days
    when = {0: "Today", 1: "Yesterday"}.get(days, f"{day:%a %-d %b}")
    late = copies_late(copied, time.time())
    sub = f"None since {when}" if late else f"{when}, to Proton Drive"
    return [row("Backup", sub, mark="shield", tone="flatout" if late else "steady")]


def setup_page(person: str, step: str, setup: dict, error: str = "") -> str:
    first = not setup["ready"]
    fields = {
        "goals": _goal_fields, "equipment": _equipment_fields, "time": _time_fields,
        "days": _day_fields, "body": _body_fields, "hurts": _avoid_fields,
    }[step](setup)
    back = "" if first else "/me"
    aside = ""
    if step == "equipment" and not first:
        aside = button("/gym", "Step limit", "quiet")
    count = f"Step {FIRST_STEPS.index(step) + 1} of {len(FIRST_STEPS)}" if first else ""
    message = f'<p class="error" role="alert">{esc(error)}</p>' if error else ""
    content = (
        f'{head(STEP_TITLES[step], count, back, aside)}'
        f'<form method="post" action="/setup/{step}" class="setup">'
        f'<section class="panel scroll grow">{message}{fields}</section>'
        f'<div class="actions">'
        f'<button class="primary big">{"Next" if first else "Save"}</button></div></form>'
    )
    if first:
        return bare(STEP_TITLES[step], content)
    return framed("me", STEP_TITLES[step], content, person)


def _goal_fields(setup: dict) -> str:
    return '<p class="muted">Pick one or two.</p>' + "".join(
        check("checkbox", "goals", key, label, key in setup["goals"])
        for key, label in GOALS.items()
    ) + reps_select(setup["reps"], "Coach's choice") + select(
        "Weights go up", "hold", [("0", "When earned"), ("1", "Only when I say")],
        str(setup["hold"]),
    ) + (
        '<p class="muted">Reps climb from here until the next weight is earned. A saved workout '
        "can have its own.</p>"
    )


def _equipment_fields(setup: dict) -> str:
    return "".join(
        check("checkbox", "equipment", key, label, key in setup["equipment"])
        for key, label in EQUIPMENT.items()
    )


def _time_fields(setup: dict) -> str:
    def options(chosen: int | None, usual: bool) -> str:
        first = '<option value="">Usual</option>' if usual else ""
        return first + "".join(
            f'<option value="{minutes}"{" selected" if minutes == chosen else ""}>'
            f"{minutes} min</option>"
            for minutes in range(LEAST_MINUTES, MOST_MINUTES + 1, 5)
        )

    days = "".join(
        f'<label class="pick">{name}<select name="day{day}">'
        f'{options(setup["day_minutes"].get(str(day)), True)}</select></label>'
        for day, name in enumerate(DAY_NAMES)
    )
    return (
        f'<label class="pick">Usually<select name="minutes">{options(setup["minutes"], False)}'
        f'</select></label><div class="two">{days}</div>'
    )


def _day_fields(setup: dict) -> str:
    note = '<p class="muted">Three or more. Missed days slide along.</p>'
    return note + '<div class="two">' + "".join(
        check("checkbox", "days", str(day), name, day in setup["days"])
        for day, name in enumerate(DAY_NAMES)
    ) + "</div>"


def _body_fields(setup: dict) -> str:
    weight = "" if setup["body_kg"] is None else f"{setup['body_kg']:g}"
    levels = "".join(
        check("radio", "experience", key, label, key == setup["experience"])
        for key, label in EXPERIENCE.items()
    )
    return (
        f'{levels}<label class="pick">Body weight, kg<input name="body_kg" inputmode="decimal" '
        f'value="{weight}" placeholder="optional"></label>'
        '<p class="muted">Only for the step\'s weight limit. Without it, weights stay off the '
        "step. Only you see it.</p>"
    )


def _avoid_fields(setup: dict) -> str:
    patterns = "".join(
        check("checkbox", "avoid", key, label, key in setup["avoid"])
        for key, label in PATTERNS.items()
        if key != "bar"  # switched off as equipment instead
    )
    hurting = "".join(
        check("checkbox", "hurts", key, f"{_exercise_name(key)} hurts", True)
        for key in setup["hurts"]
    )
    note = "Untick once it stops hurting."
    hurting = f'<p class="muted">{note}</p>{hurting}' if hurting else ""
    intro = '<p class="muted">Leave out of my plan</p>'
    return f'{intro}<div class="two">{patterns}</div>{hurting}'


def _exercise_name(key: str) -> str:
    if key in EXERCISES:
        return EXERCISES[key].name
    return key.replace("finisher-", "").replace("-", " ").capitalize()


def weights_page(person: str, groups: list[tuple[str, list[dict]]], reps: int) -> str:
    """Return each exercise's weight, to set. Each is {key, name, pair, loads, now, start}."""
    blocks = "".join(
        f"<h2>{esc(label)}</h2>"
        f'<div class="panel fields">{"".join(map(_weight_field, found))}</div>'
        for label, found in groups
    ) or '<p class="muted">Choose dumbbells in Equipment first.</p>'
    amount = f"{reps} reps" if reps else "your usual reps"
    return framed("me", "Weights", (
        f'{head("Weights", "Pounds on each dumbbell", "/me")}'
        '<form method="post" action="/weights" class="setup">'
        f'<div class="scroll grow stacked weights">{blocks}</div>'
        f'<p class="muted note">One that leaves 2 or 3 in you at {amount}.</p>'
        '<div class="actions"><button class="primary big">Save</button></div></form>'
    ), person)


def _weight_field(exercise: dict) -> str:
    now = exercise["now"]
    options = [("0", f"Work up from {pounds(exercise['start'])}")] if not now else []
    options += [(f"{load:g}", pounds(load)) for load in exercise["loads"]]
    if exercise["set"]:
        options.append(("0", "Leave it to the coach"))
    was = f'<input type="hidden" name="was-{exercise["key"]}" value="{now or 0:g}">'
    return was + select(exercise["name"], exercise["key"], options, f"{now or 0:g}")


def gym_page(person: str, limit_lb: float, confirmed: bool, allowed_lb: float) -> str:
    margin = "" if confirmed else " That is 80%, until the label confirms it."
    return framed("me", "Gym", (
        f'{head("Gym", "What the equipment can take", "/setup/equipment")}'
        '<form method="post" action="/gym" class="setup">'
        '<section class="panel scroll grow"><p>Dumbbells: Bowflex 552, '
        f"{DIAL_LOADS[0]:g} to {pounds(DIAL_LOADS[-1])}.</p>"
        '<label class="pick">Step limit, lb<input name="step_limit_lb" inputmode="decimal" '
        f'value="{limit_lb:g}"></label>'
        f'{check("checkbox", "step_confirmed", "1", "Read from its label", confirmed)}'
        f'<p class="muted">The coach allows {allowed_lb:g} lb '
        f"({allowed_lb * KG_PER_LB:.0f} kg) on it: person and dumbbells together.{margin}</p>"
        '</section><div class="actions"><button class="primary big">Save</button></div></form>'
    ), person)


def session_page() -> str:
    return page(
        "Session",
        '<main id="app"><p class="muted">Loading your session.</p>'
        '<noscript>The session screen needs JavaScript.</noscript></main>',
        scripts=("heart.js", "music.js", "session.js"),
        css="session",
    )


def message_page(message: str) -> str:
    return bare("reps", f'<p>{esc(message)}</p><p><a class="button" href="/">Back</a></p>')


def missing_patterns(store: Store, person: str) -> list[str]:
    return uncovered(store.profile(person, 45))
