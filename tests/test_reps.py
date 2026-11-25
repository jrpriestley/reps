import datetime as dt
import json

import pytest

from reps.coach import shorthand
from reps.coach.custom import build, clean_block
from reps.coach.generate import Order, generate
from reps.coach.plan import Profile, prescribe
from reps.coach.progression import Performance, replay
from reps.gym import DIAL_LOADS
from reps.library import EQUIPMENT, EXERCISES

TODAY = dt.date(2026, 11, 24)
PRESS = EXERCISES["floor-press"]
EVERYTHING = Profile(equipment=frozenset(EQUIPMENT), body_kg=85)


def did(load, count, rir=2, reps=0, ago=3):
    return Performance(TODAY - dt.timedelta(days=ago), ((load, count),) * 3, rir, reps)


def asked(reps, *past, key="floor-press"):
    profile = Profile(equipment=frozenset(EQUIPMENT), body_kg=85, reps=reps)
    return prescribe(profile, EXERCISES[key], {key: list(past)}, TODAY)[0]


@pytest.mark.parametrize("reps", [5, 8, 12, 15])
def test_a_first_go_is_at_the_reps_asked_for(reps):
    item = asked(reps)
    assert {count for _, count in item.sets} == {reps} and item.reps == reps


def test_fewer_reps_are_heavier_and_more_are_lighter():
    past = did(35, 12)
    fives, twelves = asked(5, past), asked(12, past)
    assert fives.sets[0][0] > 35 >= twelves.sets[0][0]
    assert {count for _, count in fives.sets} == {5}
    assert "Worked out from your 12s" in fives.why and fives.last


def test_each_number_of_reps_keeps_its_own_place():
    fives, twelves = did(35, 5, reps=5, ago=2), did(20, 12, reps=12, ago=4)
    assert {load for load, _ in asked(5, twelves, fives).sets} == {35}
    assert {load for load, _ in asked(12, twelves, fives).sets} == {20}


def test_lifting_fives_is_not_missing_twelves():
    past = [did(20, 12, reps=12, ago=15), did(35, 5, reps=5, ago=10), did(35, 5, reps=5, ago=5)]
    assert asked(12, *past).sets[0][0] == 20


def test_time_off_counts_for_a_load_carried_over():
    fresh, rusty = asked(5, did(25, 10)), asked(5, did(25, 10, ago=15))
    assert rusty.sets[0][0] < fresh.sets[0][0]


def test_holds_and_unloaded_work_keep_to_their_own():
    assert asked(5, key="plank").reps == 0
    assert {count for _, count in asked(5, key="push-up").sets} == {5}


def test_after_a_bridge_the_reps_are_built_back_not_missed():
    past = [did(5, 18, reps=15, ago=60)] + [
        Performance(TODAY - dt.timedelta(days=ago), sets, 2, 15) for ago, sets in (
            (56, ((7.5, 6), (5, 18), (5, 18))), (52, ((7.5, 6), (7.5, 6), (5, 18))),
            (48, ((7.5, 6),) * 3), (44, ((7.5, 8),) * 3), (40, ((7.5, 9),) * 3))
    ]
    past.insert(0, Performance(TODAY - dt.timedelta(days=64), ((5, 15),) * 3, 2, 15))
    days = tuple(TODAY - dt.timedelta(days=ago) for ago in range(1, 64, 3))
    track = replay(PRESS, past, DIAL_LOADS, 15, 19, 3, TODAY, days)
    assert track.load == 7.5 and track.misses == 0


def test_block_over_workout_over_person():
    blocks = [{"options": ["curl"], "sets": 3, "reps": 6}, {"options": ["bent-row"], "sets": 3}]
    profile = Profile(equipment=frozenset(EQUIPMENT), reps=12)
    curl, row = build(profile, "x", blocks, True, {}, TODAY).items
    assert (curl.reps, row.reps) == (6, 12)


def test_a_generated_workout_can_have_its_own():
    order = Order.read({"focus": "upper", "reps": "15"})
    items = generate(EVERYTHING, order, {}, TODAY).items
    assert {item.reps for item in items if item.kind == "load"} == {15}
    assert Order.read({"reps": "lots"}).reps == 0 and Order.read({"reps": 99}).reps == 15


@pytest.mark.parametrize(("words", "reps"), [
    ("curl 3x12", 12), ("curl 3 x 8 reps", 8), ("curl 3 sets of 10", 10), ("curl 3x", 0)])
def test_reps_are_read_in_short(words, reps):
    (block,) = shorthand.parse(words)
    assert block["sets"] == 3 and block.get("reps", 0) == reps


def test_reps_out_of_range_are_not_plain():
    assert shorthand.parse("curl 3x50") is None
    assert clean_block({"options": ["curl"], "reps": 50})["reps"] == 15
    assert "reps" not in clean_block({"options": ["rower"], "reps": 10})


# On the page ----------------------------------------------------------------------


def test_a_person_sets_their_usual(client, store):
    client.set_up("al")
    assert store.person("al")["reps"] == 0
    assert "Reps per set" in client.get("/setup/goals")[1]
    client.post("/setup/goals", {"goals": ["strength"], "reps": "12"})
    assert store.profile("al", 45).reps == 12 and store.profile("bo", 45).reps == 0
    client.post("/setup/goals", {"goals": ["strength"], "reps": "nonsense"})
    assert store.profile("al", 45).reps == 0


def made(client):
    client.set_up("al")
    base = client.post("/workouts", {"name": "Mine"})[0].headers["Location"]
    client.post(f"{base}/add", {"key": "curl"})
    return base


def test_a_workout_and_a_block_set_their_own(client, store):
    base = made(client)
    client.post(f"{base}/settings", {"name": "Mine", "mix": "", "reps": "10"})
    assert "10 reps" in client.get(base)[1]
    client.post(f"{base}/block/0", {"sets": "3", "reps": "8", "save": "1"})
    assert "3 × 8" in client.get(base)[1]
    client.post(f"{base}/block/0", {"sets": "3", "reps": "0", "save": "1"})
    assert store.workouts("al")[0]["blocks"][0]["reps"] == 0
    client.post("/pick", {"workout": base.rsplit("/", 1)[1]})
    client.post(f"{base}/start", {})
    session = json.loads(client.get("/api/session")[1])
    assert {count for _, count in session["items"][0]["sets"]} == {10}
    swapped = client.api("/api/swap", {"session": session["id"], "item": "curl"})[1]
    assert swapped["items"][0]["reps"] == 10


def test_left_alone_says_what_it_will_be(client):
    base = made(client)
    here = f"{base}/block/0"
    assert "Coach&#x27;s choice" in client.get(here)[1]
    client.post("/setup/goals", {"goals": ["strength"], "reps": "12"})
    assert "12 (my usual)" in client.get(here)[1]
    assert "12 (my usual)" in client.get(f"{base}/settings")[1]
    assert "12 (my usual)" in client.get("/generate")[1]
    client.post(f"{base}/settings", {"name": "Mine", "mix": "", "reps": "10"})
    assert "10 (this workout&#x27;s)" in client.get(here)[1]


def test_history_remembers_what_was_asked(client, store):
    base = made(client)
    client.post(f"{base}/block/0", {"sets": "3", "reps": "8", "save": "1"})
    client.post(f"{base}/start", {})
    session = json.loads(client.get("/api/session")[1])["id"]
    event = {"cid": "reps-0001", "type": "set", "item": "curl", "n": 0, "load": 15, "amount": 8}
    client.api("/api/events", {"session": session, "events": [event]})
    store.finish(session)
    (done,) = store.history("al")["curl"]
    assert done.reps == 8
