import datetime as dt
import json

import pytest

from reps.coach.custom import MOST_BLOCKS, build
from reps.coach.plan import Profile
from reps.coach.progression import Performance
from reps.library import EQUIPMENT

TODAY = dt.date(2026, 11, 24)
EVERYTHING = Profile(equipment=frozenset(EQUIPMENT), body_kg=75)
ROWER = {"options": ["rower"], "minutes": 15}
WEIGHTS = {"options": ["deadlift", "curl"], "sets": 4}
SANDWICH = [ROWER, WEIGHTS, {"options": ["rower"], "minutes": 10}]


def did(key, days_ago, sets=((20, 8),) * 3):
    return {key: [Performance(TODAY - dt.timedelta(days=days_ago), tuple(sets), 2)]}


def test_rower_weights_rower():
    session = build(EVERYTHING, "Sandwich", SANDWICH, True, {}, TODAY)
    assert [item.key for item in session.items] == ["rower", "deadlift", "rower#2"]
    assert [item.sets[0][1] for item in session.items if item.kind == "cardio"] == [900, 600]
    assert session.kind == "core" and session.title == "Sandwich"
    assert build(EVERYTHING, "Sandwich", SANDWICH, False, {}, TODAY).kind == "extra"


def test_an_or_block_takes_the_one_done_longer_ago():
    recent = did("deadlift", 2) | did("curl", 9)
    assert build(EVERYTHING, "x", [WEIGHTS], True, recent, TODAY).items[0].key == "curl"
    recent = did("deadlift", 9) | did("curl", 2)
    assert build(EVERYTHING, "x", [WEIGHTS], True, recent, TODAY).items[0].key == "deadlift"


def test_sets_are_the_persons_and_loads_the_coachs():
    item = build(EVERYTHING, "x", [WEIGHTS], True, did("deadlift", 3), TODAY).items[0]
    item = build(EVERYTHING, "x", [WEIGHTS], True, did("curl", 1) | did("deadlift", 3), TODAY)
    assert len(item.items[0].sets) == 4
    assert item.items[0].sets[0][0] == 20


def test_what_cannot_be_done_is_left_out():
    tower_only = Profile(equipment=frozenset({"tower"}))
    assert build(tower_only, "x", SANDWICH, True, {}, TODAY).items == ()
    sore = Profile(equipment=frozenset(EQUIPMENT), avoid=frozenset({"deadlift"}))
    assert [item.key for item in build(sore, "x", [WEIGHTS], True, {}, TODAY).items] == ["curl"]


def test_the_same_exercise_is_never_twice_in_one_session():
    twice = [WEIGHTS, WEIGHTS, WEIGHTS]
    keys = [item.key for item in build(EVERYTHING, "x", twice, True, {}, TODAY).items]
    assert keys == ["deadlift", "curl"]


# Through the pages -----------------------------------------------------------


def made(client, name="Sandwich"):
    response, _ = client.post("/workouts", {"name": name})
    base = response.headers["Location"]
    for key in ("bike", "deadlift", "bike"):
        client.post(f"{base}/add", {"key": key})
    client.post(f"{base}/add", {"key": "curl", "to": "1"})
    return base


def test_a_workout_is_built_started_and_logged(client, store):
    client.set_up()
    base = made(client)
    page = client.get(base)[1]
    assert "Dumbbell deadlift or Curl" in page and "15 min" in page
    client.post(f"{base}/block/0", {"minutes": "20", "save": "1"})
    assert client.post(f"{base}/start", {"counts": "1"})[0].headers["Location"] == "/session"
    session = json.loads(client.get("/api/session")[1])
    assert [item["key"] for item in session["items"]] == ["bike", "deadlift", "bike#2"]
    events = [
        {"cid": f"custom-{n}", "type": "set", "item": item["key"], "n": 0,
         "load": item["sets"][0][0], "amount": item["sets"][0][1]}
        for n, item in enumerate(session["items"])
    ] + [{"cid": "custom-end", "type": "finish"}]
    assert client.api("/api/events", {"session": session["id"], "events": events})[0] == 200
    assert len(store.history("al")["bike"]) == 2
    assert "Core 2 of 3" in client.get("/")[1]
    assert "Sandwich" in client.get("/week")[1]
    assert client.get(f"/history/{session['id']}")[0].status == 200


def test_an_extra_workout_leaves_the_weeks_count_alone(client):
    client.set_up()
    base = made(client)
    client.post(f"{base}/start", {})
    session = json.loads(client.get("/api/session")[1])
    events = [{"cid": "extra-set", "type": "set", "item": "bike", "n": 0, "load": 0,
               "amount": 60}, {"cid": "extra-end", "type": "finish"}]
    client.api("/api/events", {"session": session["id"], "events": events})
    assert "Core 1 of 3" in client.get("/")[1]
    assert "3 core sessions to go" in client.get("/week")[1]


def test_blocks_move_change_and_go(client, store):
    client.set_up()
    base = made(client)
    number = int(base.split("/")[-1])

    def options():
        return [block["options"] for block in store.workout("al", number)["blocks"]]

    client.post(f"{base}/block/1", {"move": "up", "sets": "5"})
    assert options() == [["deadlift", "curl"], ["bike"], ["bike"]]
    assert store.workout("al", number)["blocks"][0]["sets"] == 5
    client.post(f"{base}/block/0", {"drop": "deadlift"})
    client.post(f"{base}/block/0", {"drop": "curl"})
    assert options()[0] == ["curl"]
    client.post(f"{base}/block/2", {"remove": "1"})
    assert len(options()) == 2
    client.post(f"{base}/block/1", {"minutes": "9999"})
    assert store.workout("al", number)["blocks"][1]["minutes"] == 60


def test_cardio_and_exercises_do_not_share_a_block(client, store):
    client.set_up()
    base = made(client)
    client.post(f"{base}/add", {"key": "curl", "to": "0"})
    client.post(f"{base}/add", {"key": "nonsense"})
    blocks = store.workout("al", int(base.split("/")[-1]))["blocks"]
    assert blocks[0]["options"] == ["bike"] and len(blocks) == 3


def test_a_workout_holds_only_so_many_blocks(client, store):
    client.set_up()
    base = made(client)
    for _ in range(12):
        client.post(f"{base}/add", {"key": "bike"})
    assert len(store.workout("al", int(base.split("/")[-1]))["blocks"]) == MOST_BLOCKS


def test_workouts_are_each_persons_own(client, other, store):
    client.set_up("al")
    base = made(client, "<b>Mine</b>")
    assert "&lt;b&gt;Mine" in client.get("/workouts")[1]
    other.set_up("bo")
    assert "Mine" not in other.get("/workouts")[1]
    for path in (base, f"{base}/add", f"{base}/block/0", f"{base}/settings"):
        assert other.get(path)[0].status == 404
    for path in ("start", "delete", "settings", "add", "block/0"):
        assert other.post(f"{base}/{path}", {"name": "x"})[0].status == 404
    assert store.workout("al", int(base.split("/")[-1])) is not None


def test_deleting_a_workout_keeps_its_sessions(client, store):
    client.set_up()
    base = made(client)
    client.post(f"{base}/delete")
    assert client.get(base)[0].status == 404


@pytest.mark.parametrize("path", ["/workouts/x", "/workouts/99", "/workouts/1/block/7"])
def test_unknown_workout_pages_are_missing(client, path):
    client.set_up()
    made(client)
    assert client.get(path)[0].status == 404


def finish(client, session):
    item = session["items"][0]
    events = [{"cid": f"fin-set-{session['id']}", "type": "set", "item": item["key"], "n": 0,
               "load": item["sets"][0][0], "amount": item["sets"][0][1]},
              {"cid": f"fin-end-{session['id']}", "type": "finish"}]
    client.api("/api/events", {"session": session["id"], "events": events})


def test_a_saved_workout_is_picked_for_today_and_stays_saved(client):
    client.set_up()
    number = made(client).split("/")[-1]
    assert "Core 1 of 3" in client.get("/")[1]
    assert client.post("/pick", {"workout": number})[0].headers["Location"] == "/"
    today = client.get("/")[1]
    assert "Sandwich" in today and "Core 1 of 3" not in today and "One of my three" in today
    assert "Sandwich" in client.get("/week")[1]
    assert '<span class="pill ok">Today</span>' in client.get("/pick")[1]
    client.post(f"/workouts/{number}/start", {"counts": "1"})
    finish(client, json.loads(client.get("/api/session")[1]))
    assert "Core 2 of 3" in client.get("/")[1]
    assert "Use today" in client.get("/pick")[1]
    client.post("/pick", {"workout": number})
    assert "Sandwich" in client.get("/")[1]


def test_going_back_to_the_coachs_plan(client):
    client.set_up()
    number = made(client).split("/")[-1]
    client.post("/pick", {"workout": number})
    client.post("/pick", {"workout": ""})
    assert "Core 1 of 3" in client.get("/")[1]


def test_a_pick_lasts_the_day_and_survives_a_discarded_session(client, store):
    client.set_up()
    number = made(client).split("/")[-1]
    client.post("/pick", {"workout": number})
    client.post(f"/workouts/{number}/start", {})
    client.api("/api/discard", {"session": json.loads(client.get("/api/session")[1])["id"]})
    assert "Sandwich" in client.get("/")[1]
    assert store.picked("al", dt.date.today() + dt.timedelta(days=1)) is None


def test_a_pick_that_is_deleted_or_cannot_be_done_gives_way_to_the_coach(client):
    client.set_up()
    base = made(client)
    client.post("/pick", {"workout": base.split("/")[-1]})
    client.post("/setup/equipment", {"equipment": ["tower"]})
    assert "Core 1 of 3" in client.get("/")[1]
    assert "Not ready" in client.get("/pick")[1]
    client.post(f"{base}/delete")
    assert "Core 1 of 3" in client.get("/")[1]


def test_nobody_picks_the_others_workout(client, other):
    client.set_up("al")
    number = made(client).split("/")[-1]
    other.set_up("bo")
    other.post("/pick", {"workout": number})
    assert "Core 1 of 3" in other.get("/")[1]


def test_building_is_under_me_and_picking_under_today(client):
    client.set_up()
    assert 'href="/workouts"' in client.get("/me")[1] and 'href="/music"' in client.get("/me")[1]
    assert 'href="/pick"' in client.get("/")[1]
    assert "Build one in Me" in client.get("/pick")[1]
    made(client)
    assert "Sandwich" in client.get("/pick")[1] and "New workout" not in client.get("/pick")[1]
    assert "New workout" in client.get("/workouts")[1]
