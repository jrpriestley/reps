import datetime as dt
import itertools
import json

import pytest

from reps import __version__
from reps.coach.custom import build
from reps.coach.generate import (
    EFFORTS,
    FOCUSES,
    PATTERNS,
    Order,
    as_blocks,
    focuses,
    generate,
    machines,
)
from reps.coach.intervals import LEVELS, STYLES, level_name, rounds, style_name
from reps.coach.plan import Profile, core_session, total_seconds
from reps.coach.progression import Performance
from reps.coach.stages import stage_of
from reps.library import EQUIPMENT, EXERCISES

TODAY = dt.date(2026, 11, 24)
EVERYTHING = Profile(equipment=frozenset(EQUIPMENT), body_kg=75)
EVERY_SET_OF_EQUIPMENT = [
    frozenset(owned)
    for size in range(len(EQUIPMENT) + 1)
    for owned in itertools.combinations(EQUIPMENT, size)
]


def check(profile, order, session):
    assert session.items, order
    keys = [item.key for item in session.items]
    assert len(keys) == len(set(keys))
    for item in session.items:
        assert item.why and item.sets, item
        exercise = EXERCISES.get(item.key)
        if exercise:
            assert exercise.needs <= profile.equipment
            assert exercise.key not in profile.avoid and exercise.pattern not in profile.avoid
        else:
            plain = item.key.split("#")[0].removeprefix("finisher-")
            assert plain in profile.equipment or plain in ("circuit", "flow"), item.key
            assert plain not in profile.avoid
            if item.work:
                over = item.sets[0][1] % (item.work + item.off)
                assert over == 0 or over >= item.work  # never stops part way through an on
            assert item.sets[0][1] % 60 == 0
        if item.kind == "load":
            allowed = profile.gym.loads(exercise, profile.body_kg)
            assert all(load in allowed for load, _ in item.sets)


def test_it_stays_inside_the_setup_for_every_set_of_equipment():
    for owned in EVERY_SET_OF_EQUIPMENT:
        for body in (None, 85):
            profile = Profile(equipment=owned, body_kg=body)
            assert {"cardio", "mobility"} <= set(focuses(profile))
            for focus in FOCUSES:
                order = Order(focus=focus, minutes=45, style="60-60")
                check(profile, order, generate(profile, order, {}, TODAY))


@pytest.mark.parametrize("focus", FOCUSES)
def test_it_fits_the_minutes_asked_for(focus):
    for minutes in range(15, 65, 5):
        for effort in EFFORTS:
            for style in STYLES:
                order = Order(focus=focus, minutes=minutes, effort=effort, style=style)
                session = generate(EVERYTHING, order, {}, TODAY)
                check(EVERYTHING, order, session)
                spent = total_seconds(list(session.items), 0, False)
                assert spent <= minutes * 60 + 120, (order, spent / 60)


def test_what_is_avoided_or_hurts_never_appears():
    avoid = frozenset({"vpush", "floor-press", "bike", "finisher-rower"})
    profile = Profile(equipment=frozenset(EQUIPMENT), body_kg=75, avoid=avoid)
    assert machines(profile) == ["treadmill"]
    for focus, turn in itertools.product(FOCUSES, range(4)):
        order = Order(focus=focus, turn=turn, machine="bike")
        session = generate(profile, order, {}, TODAY)
        check(profile, order, session)
        assert not any("bike" in item.key for item in session.items)


def test_a_focus_the_setup_cannot_cover_is_not_offered():
    nothing = Profile(avoid=frozenset({"core"}))
    assert "core" not in focuses(nothing)
    session = generate(nothing, Order(focus="core"), {}, TODAY)
    assert session.title == "Mobility"


@pytest.mark.parametrize("focus", ["upper", "lower", "core"])
def test_a_focus_holds_only_its_patterns(focus):
    for turn in range(4):
        session = generate(EVERYTHING, Order(focus=focus, turn=turn), {}, TODAY)
        patterns = {EXERCISES[item.key].pattern for item in session.items if item.key in EXERCISES}
        assert patterns and patterns <= set(PATTERNS[focus])


def test_experience_and_effort_set_the_sets_and_goals_the_reps():
    def sets(experience, effort):
        profile = Profile(equipment=frozenset({"dumbbells"}), experience=experience)
        history = {"bent-row": [Performance(TODAY - dt.timedelta(days=3), ((20, 8),) * 3, 2)]}
        order = Order(focus="upper", minutes=60, effort=effort)
        items = generate(profile, order, history, TODAY).items
        return max(len(item.sets) for item in items if item.key in EXERCISES and item.last)

    assert sets("new", "normal") == 2
    assert sets("regular", "normal") == 3
    assert sets("regular", "hard") == 4
    for goals, low in ((("muscle",), 8), (("strength",), 6)):
        profile = Profile(goals=goals, equipment=frozenset({"dumbbells"}))
        items = generate(profile, Order(focus="upper"), {}, TODAY).items
        assert items[0].sets[0][1] == low


def test_another_is_different_and_the_same_order_the_same():
    first = generate(EVERYTHING, Order(focus="upper"), {}, TODAY)
    assert first == generate(EVERYTHING, Order(focus="upper"), {}, TODAY)
    assert first != generate(EVERYTHING, Order(focus="upper", turn=1), {}, TODAY)


def test_hiit_has_an_easy_start_and_end():
    order = Order(focus="cardio", minutes=30, machine="bike", style="60-60")
    warm, hard, cool = generate(EVERYTHING, order, {}, TODAY).items
    assert (hard.work, hard.off) == (60, 60) and not warm.work and not cool.work
    assert level_name(warm.sets[0][0]) == "Easy" and level_name(hard.sets[0][0]) == "Hard"
    assert [item.sets[0][1] // 60 for item in (warm, hard, cool)] == [5, 15, 5]
    assert rounds(hard.sets[0][1], 60, 60) == 8  # the last one stops at its rest


def test_hiit_is_capped_and_the_rest_is_easy():
    order = Order(focus="cardio", minutes=60, machine="rower", style="60-60")
    warm, hard, cool = generate(EVERYTHING, order, {}, TODAY).items
    assert hard.sets[0][1] == 20 * 60
    assert warm.sets[0][1] >= 10 * 60 and cool.sets[0][1] >= 10 * 60


@pytest.mark.parametrize(
    "given",
    [{}, {"focus": "x", "minutes": "x", "effort": [], "machine": "car", "style": "9-9",
          "turn": "-4"}, {"minutes": "9999"}, {"minutes": None}],
)
def test_odd_orders_are_made_sense_of(given):
    order = Order.read(given)
    assert order.focus == "full" and 15 <= order.minutes <= 60 and order.turn == 0


# Intervals and levels --------------------------------------------------------


def test_styles_read_plainly():
    assert style_name("60-60") == "1:00 on, 1:00 off" and style_name("steady") == "Steady"


def test_the_bike_repeats_its_last_level_and_other_machines_have_none():
    bike = {"options": ["bike"], "minutes": 10}
    item = build(EVERYTHING, "x", [bike], True, {}, TODAY).items[0]
    assert level_name(item.sets[0][0]) == "Moderate"
    history = {"bike": [Performance(TODAY, ((2, 600),))]}
    item = build(EVERYTHING, "x", [bike], True, history, TODAY).items[0]
    assert level_name(item.sets[0][0]) == "Light" and item.last == "Light"
    rower = {"options": ["rower"], "minutes": 10, "level": 4}
    assert build(EVERYTHING, "x", [rower], True, {}, TODAY).items[0].sets[0][0] == 0


def test_a_block_can_be_intervals_and_counts_as_flat_out():
    block = {"options": ["rower"], "minutes": 10, "style": "60-60", "level": 0}
    item = build(EVERYTHING, "x", [block], True, {}, TODAY).items[0]
    assert (item.work, item.off, item.sets[0][1]) == (60, 60, 600)
    plain = {"key": "rower", "kind": "cardio", "sets": [[0, 600]]}
    assert stage_of([plain], {}, {}) == "steady"
    assert stage_of([plain | {"work": 60, "off": 60}], {}, {}) == "flatout"


def test_the_coachs_finisher_is_real_intervals():
    finisher = core_session(EVERYTHING, 0, 48, {}, TODAY).items[-1]
    assert (finisher.work, finisher.off) == (20, 40)


def test_a_generated_workout_becomes_blocks_that_build_the_same():
    order = Order(focus="cardio", minutes=30, machine="bike", style="60-60")
    session = generate(EVERYTHING, order, {}, TODAY)
    again = build(EVERYTHING, "x", as_blocks(session, 8), True, {}, TODAY)
    assert [(item.work, item.sets) for item in again.items] == [
        (item.work, item.sets) for item in session.items
    ]


# Through the pages -----------------------------------------------------------


def ask(client, **more):
    return client.post("/generate", {"focus": "upper", "minutes": "30", "effort": "normal"} | more)


def test_todays_workout_is_the_coachs_saved_or_generated(client):
    client.set_up()
    assert "Coach&#x27;s plan" in client.get("/")[1] or "Coach's plan" in client.get("/")[1]
    ask(client)
    today = client.get("/")[1]
    assert "Upper body" in today and "Generated for today" in today
    assert "Upper body" in client.get("/week")[1]
    number = client.post("/workouts", {"name": "Mine"})[0].headers["Location"].split("/")[-1]
    client.post(f"/workouts/{number}/add", {"key": "bike"})
    client.post("/pick", {"workout": number})
    assert "Saved workout" in client.get("/")[1]
    ask(client)
    assert "Generated for today" in client.get("/")[1]
    client.post("/pick", {"workout": ""})
    assert "Core 1 of 3" in client.get("/")[1]


def test_it_is_started_logged_and_done_with(client):
    client.set_up()
    ask(client)
    client.post("/generate/start", {"counts": "1"})
    session = json.loads(client.get("/api/session")[1])
    assert session["title"] == "Upper body"
    item = session["items"][0]
    events = [{"cid": "made-set-1", "type": "set", "item": item["key"], "n": 0,
               "load": item["sets"][0][0], "amount": 8}, {"cid": "made-end-1", "type": "finish"}]
    assert client.api("/api/events", {"session": session["id"], "events": events})[0] == 200
    assert "Core 2 of 3" in client.get("/")[1]


def test_another_and_save(client, store):
    client.set_up()
    ask(client)
    first = client.get("/")[1]
    client.post("/generate/another")
    assert client.get("/")[1] != first
    where = client.post("/generate/save")[0].headers["Location"]
    assert "Upper body" in client.get(where)[1]
    assert len(store.workouts("al")) == 1 and store.workouts("al")[0]["blocks"]


def test_the_form_offers_only_the_setup(client):
    client.set_up(equipment=("dumbbells", "bike"))
    page = client.get("/generate")[1]
    assert 'value="bike"' in page and 'value="rower"' not in page
    client.post("/setup/hurts", {"avoid": ["core"]})
    assert 'value="core"' not in client.get("/generate")[1]


@pytest.mark.parametrize("path", ["start", "another", "save"])
def test_with_nothing_generated_it_asks_first(client, path):
    client.set_up()
    assert client.post(f"/generate/{path}")[0].headers["Location"] == "/generate"


def test_a_level_is_logged_for_the_bike_and_shown(client):
    client.set_up()
    ask(client, focus="cardio", machine="bike", style="60-60")
    client.post("/generate/start", {})
    session = json.loads(client.get("/api/session")[1])
    hard = session["items"][1]
    assert hard["levels"] == list(LEVELS) and hard["tone"] == "flatout"
    made = {"cid": "level-set-1", "type": "set", "item": hard["key"], "n": 0, "load": 5,
            "amount": 600}
    assert client.api("/api/events", {"session": session["id"], "events": [made]})[0] == 200
    worse = made | {"cid": "level-set-2", "load": 7}
    assert client.api("/api/events", {"session": session["id"], "events": [worse]})[0] == 400
    end = {"cid": "level-end-1", "type": "finish"}
    client.api("/api/events", {"session": session["id"], "events": [end]})
    assert "All out" in client.get(f"/history/{session['id']}")[1]


def test_a_blocks_style_and_level_are_kept(client, store):
    client.set_up()
    base = client.post("/workouts", {"name": "Intervals"})[0].headers["Location"]
    client.post(f"{base}/add", {"key": "bike"})
    client.post(f"{base}/block/0", {"minutes": "20", "style": "30-30", "level": "4", "save": "1"})
    assert "0:30 on, 0:30 off · Hard" in client.get(base)[1]
    client.post(f"{base}/block/0", {"style": "9-9", "level": "99"})
    block = store.workouts("al")[0]["blocks"][0]
    assert (block["style"], block["level"]) == ("30-30", 4)


def test_the_version_shows(client):
    assert f"reps {__version__}" in client.get("/")[1]
    client.set_up()
    assert f"reps {__version__}" in client.get("/me")[1]
