import json

import pytest

from reps.coach import pace, shorthand
from reps.coach.custom import build, clean_block
from reps.coach.generate import Order, as_blocks, generate
from reps.coach.pace import ease, mets, steepest, top_mets
from reps.coach.plan import Profile
from reps.describe import reasonable
from reps.library import EQUIPMENT

EVERYTHING = Profile(equipment=frozenset(EQUIPMENT), body_kg=85)


def belt(minutes=15, style="steady", **asked):
    return clean_block({"options": ["treadmill"], "minutes": minutes, "style": style} | asked)


def rower(minutes=15, style="steady", **asked):
    return clean_block({"options": ["rower"], "minutes": minutes, "style": style} | asked)


def test_the_energy_of_a_pace_matches_the_published_equations():
    assert mets(4.8, 0) == pytest.approx(3.3, abs=0.1)
    assert mets(12, 0) == pytest.approx(12.4, abs=0.1)
    assert mets(12.9, 12) == pytest.approx(19.9, abs=0.1)


@pytest.mark.parametrize("experience", ["new", "returning", "regular"])
@pytest.mark.parametrize("style", ["steady", "60-60"])
def test_eight_miles_an_hour_at_twelve_percent_is_never_allowed(experience, style):
    eased, said = ease(belt(speed=12.9, incline=12, style=style), experience)
    assert eased["incline"] <= 4 and "too much" in said
    assert mets(eased["speed"], eased["incline"]) <= top_mets(experience, 15, style != "steady")


def test_running_up_the_steepest_is_never_allowed():
    for experience in ("new", "returning", "regular"):
        eased, _ = ease(belt(speed=10, incline=12, style="60-60"), experience)
        assert eased["incline"] < 12


@pytest.mark.parametrize("experience", ["new", "returning", "regular"])
def test_the_incline_comes_down_before_the_speed_and_steeper_is_always_slower(experience):
    top = top_mets(experience, 30, False)
    most = [steepest(speed, top) for speed in pace.SPEEDS]
    assert most == sorted(most, reverse=True) and most[0] == 12
    eased, _ = ease(belt(30, speed=9, incline=12), experience)
    if steepest(9, top) >= 0:
        assert eased["speed"] == 9


def test_a_sensible_pace_is_left_alone():
    for block in (belt(speed=5, incline=4), belt(speed=10), belt(incline=3), belt(),
                  rower(watts=100), rower()):
        assert ease(block, "returning") == (block, "")


def test_a_lot_of_watts_is_eased_but_rounds_may_have_more():
    eased, said = ease(rower(30, watts=400), "new")
    assert eased["watts"] == 150 and said == "400 W is a lot for 30 minutes: eased to 150 W."
    assert ease(rower(style="30-30", watts=240), "new") == (rower(style="30-30", watts=240), "")


@pytest.mark.parametrize(("given", "kept"), [
    ({"watts": 104}, {"watts": 100}), ({"speed": 8.2}, {"speed": 8.0}),
    ({"incline": 40}, {"incline": 12}), ({"watts": "lots"}, {}), ({"speed": -1}, {}),
    ({"speed": float("nan")}, {}), ({"speed": float("inf")}, {}), ({"watts": True}, {"watts": 20}),
])
def test_paces_are_made_sense_of(given, kept):
    block = clean_block({"options": ["rower", "treadmill"]} | given)
    assert {key: block[key] for key in ("watts", "speed", "incline") if key in block} == kept


def test_a_block_asks_only_its_own_machine():
    block = clean_block({"options": ["treadmill"], "watts": 100, "speed": 6, "incline": 2})
    item = build(EVERYTHING, "x", [block], True, {}, None).items[0]
    assert (item.sets[0][0], item.incline) == (6, 2)
    block = clean_block({"options": ["bike"], "watts": 100, "speed": 6, "level": 2})
    item = build(EVERYTHING, "x", [block], True, {}, None).items[0]
    assert (item.sets[0][0], item.incline) == (2, 0)


def test_what_is_built_is_eased_even_if_saved_before():
    block = {"options": ["treadmill"], "minutes": 15, "style": "steady", "level": 0,
             "speed": 13.0, "incline": 12}
    item = build(EVERYTHING, "x", [block], True, {}, None).items[0]
    assert item.incline < 12


@pytest.mark.parametrize(("words", "asked"), [
    ("rower 15 100w", {"watts": 100}), ("rower 15 at 100 watts avg", {"watts": 100}),
    ("treadmill 20 9 km/h", {"speed": 9.0}), ("treadmill 20 5 mph", {"speed": 8.0}),
    ("treadmill 20 5kmh 4 deg", {"speed": 5.0, "incline": 4}),
    ("walk 20 incline 6", {"incline": 6}), ("walk 20 3% incline", {"incline": 3}),
])
def test_paces_are_read_in_short(words, asked):
    (block,) = shorthand.parse(words)
    assert block["minutes"] in (15, 20)
    assert {key: block[key] for key in ("watts", "speed", "incline") if key in block} == asked


@pytest.mark.parametrize("words", ["rower 15 9kmh", "bike 10 100w", "treadmill 10 100 watts"])
def test_a_pace_a_machine_lacks_is_not_plain(words):
    assert shorthand.parse(words) is None


def test_an_or_still_reads_beside_a_speed():
    (block,) = shorthand.parse("rower/bike 15")
    assert block["options"] == ["rower", "bike"]


def test_generated_cardio_keeps_its_levels_when_saved():
    plan = generate(EVERYTHING, Order(focus="cardio", machine="bike"), {}, None)
    assert as_blocks(plan, 8)[0]["level"] > 0
    plan = generate(EVERYTHING, Order(focus="cardio", machine="treadmill"), {}, None)
    assert as_blocks(plan, 8)[0]["speed"] == 0


def test_strength_blocks_have_no_pace():
    block = clean_block({"options": ["curl"], "sets": 3, "watts": 100})
    assert "watts" not in block and reasonable([block], EVERYTHING) == ([block], "")


def test_words_are_metric():
    assert pace.words("treadmill#2", 8, 3) == "8 km/h, incline 3"
    assert pace.words("treadmill", 0, 3) == "incline 3"
    assert pace.treadmill_words(8, 3) == "8 km/h, incline 3"
    assert pace.words("rower", 100) == "100 W" and pace.words("rower", 0) == ""
    assert pace.words("bike", 4) == "Hard"


# On the page ----------------------------------------------------------------------


def with_block(client, store, key):
    client.set_up("al", equipment=("rower", "treadmill", "dumbbells"))
    base = client.post("/workouts", {"name": "Mine"})[0].headers["Location"]
    client.post(f"{base}/add", {"key": key})
    return base


def test_a_pace_is_set_on_a_block_and_eased_in_words(client, store):
    base = with_block(client, store, "treadmill")
    page = client.get(f"{base}/block/0")[1]
    assert "Speed" in page and "Incline" in page and "Rower" not in page
    response, _ = client.post(f"{base}/block/0", {"speed": "13", "incline": "12", "save": "1"})
    assert "eased" in response.headers["Location"]
    assert store.workouts("al")[0]["blocks"][0]["incline"] < 12
    client.post(f"{base}/block/0", {"speed": "6", "incline": "2", "save": "1"})
    assert "6 km/h, incline 2" in client.get(base)[1]
    client.post(f"{base}/block/0", {"speed": "nan", "incline": "x", "save": "1"})
    block = store.workouts("al")[0]["blocks"][0]
    assert (block["speed"], block["incline"]) == (0, 0)


def begun(client, store, base):
    number = base.rsplit("/", 1)[1]
    client.post("/pick", {"workout": number})
    client.post(f"{base}/start", {})
    return store.active("al")["id"]


def test_the_session_steps_through_reasonable_paces_and_logs_them(client, store):
    base = with_block(client, store, "treadmill")
    client.post(f"{base}/block/0", {"speed": "6", "incline": "2", "save": "1"})
    session = begun(client, store, base)
    (item,) = json.loads(client.get("/api/session")[1])["items"]
    steps = item["pace"]
    assert steps["unit"] == "speed" and 0 not in steps["loads"]
    assert len(steps["loads"]) == len(steps["steepest"])
    event = {"cid": "pace-0001", "type": "set", "item": "treadmill", "n": 0, "load": 6.5,
             "incline": 1, "amount": 900}
    assert client.api("/api/events", {"session": session, "events": [event]})[0] == 200
    store.finish(session)
    assert "6.5 km/h, incline 1" in client.get(f"/history/{session}")[1]


@pytest.mark.parametrize(("load", "incline"), [(6.3, 0), (40, 0), (6, 13), (6, 1.5)])
def test_an_impossible_pace_is_not_logged(client, store, load, incline):
    base = with_block(client, store, "treadmill")
    session = begun(client, store, base)
    event = {"cid": "pace-0001", "type": "set", "item": "treadmill", "n": 0, "load": load,
             "incline": incline, "amount": 900}
    assert client.api("/api/events", {"session": session, "events": [event]})[0] == 400


def test_describing_from_me_is_for_later(client, store):
    client.set_up("al", equipment=("rower",))
    assert "/me/describe" in client.get("/workouts")[1]
    page = client.get("/me/describe")[1]
    assert "Pick for today" not in page and "Describe a workout" in page
    assert "Pick for today" in client.get("/describe")[1]
    response, page = client.post("/me/describe", {"words": ""})
    assert response.status == 400 and "Pick for today" not in page
    client.post("/me/describe", {"words": "rower 15 100w"})
    assert store.workouts("al")[0]["blocks"][0]["watts"] == 100
