import datetime as dt
import json
import time

import pytest

from reps.coach.plan import Profile, prescribe
from reps.coach.progression import Chosen, Performance
from reps.library import EQUIPMENT, EXERCISES

TODAY = dt.date(2026, 11, 24)
NOON = dt.datetime.combine(TODAY, dt.time(12)).timestamp()
DAY = 86400


def did(load, count, rir, ago, reps=0):
    at = NOON - ago * DAY
    return Performance(dt.date.fromtimestamp(at), ((load, count),) * 3, rir, reps, at)


def planned(load, *past, ago=3, reps=0, now=0, key="floor-press"):
    """Plan an exercise whose weight was set ``ago`` days back, for ``reps``."""
    profile = Profile(
        goals=("fitness",), equipment=frozenset(EQUIPMENT), body_kg=85, experience="regular",
        reps=now, weights={key: Chosen(load, NOON - ago * DAY, reps)},
    )
    return prescribe(profile, EXERCISES[key], {key: list(past)}, TODAY)[0]


def loads(item):
    return [load for load, _ in item.sets]


def test_a_set_weight_is_used_straight_away_without_working_up():
    item = planned(40)
    assert loads(item) == [40, 40, 40] and item.why == "You set 40 lb."
    assert [count for _, count in item.sets] == [8, 8, 8]


@pytest.mark.parametrize(
    ("rir", "load"), [(4, 45), (3, 40), (2, 40), (1, 35), (0, 35), (None, 40)]
)
def test_the_reps_left_then_move_it(rir, load):
    item = planned(40, did(40, 8, rir, ago=1))
    assert loads(item) == [load] * 3 and "weight you set" in item.why


def test_after_that_it_follows_the_usual_rules():
    steady = planned(40, did(40, 8, 2, ago=2), did(40, 8, 2, ago=1))
    assert loads(steady) == [40] * 3 and "earn the next load" in steady.why
    earned = planned(40, did(40, 8, 2, ago=2), did(40, 13, 2, ago=1))
    assert loads(earned) == [45] * 3


def test_what_was_done_before_no_longer_counts():
    assert loads(planned(40, did(15, 8, 2, ago=9), did(15, 8, 2, ago=6))) == [40] * 3


def test_setting_it_again_lower_works_too():
    assert loads(planned(20, did(40, 8, 2, ago=6))) == [20] * 3


def test_lifting_something_else_becomes_the_base():
    assert loads(planned(40, did(30, 8, 2, ago=1))) == [30] * 3


def test_time_off_still_lowers_it():
    assert loads(planned(40, ago=15)) == [35] * 3


def test_other_numbers_of_reps_keep_their_own_story():
    fives = did(45, 5, 2, ago=6, reps=5)
    assert loads(planned(30, fives, reps=0, now=5)) == [45] * 3
    assert loads(planned(30, fives, reps=0, now=0)) == [30] * 3


def test_another_number_of_reps_is_worked_out_from_it():
    lighter = planned(30, reps=0, now=15)
    assert loads(lighter) == [25] * 3 and "Worked out from" in lighter.why


def test_a_different_day_at_other_reps_does_not_lower_it():
    assert loads(planned(40, did(40, 5, 2, ago=1, reps=5))) == [40] * 3


# The page -------------------------------------------------------------------------


def test_the_page_lists_what_can_be_loaded(client, store):
    client.set_up("al", equipment=("dumbbells",))
    page = client.get("/weights")[1]
    assert "Floor press" in page and "Work up from 15 lb" in page and "Push-up" not in page
    assert "Press on the step" not in page  # no step
    assert "/weights" in client.get("/me")[1]


def test_without_dumbbells_there_is_nothing_to_set(client):
    client.set_up("al", equipment=("bike",))
    assert "Choose dumbbells" in client.get("/weights")[1]


def test_only_what_was_changed_is_kept(client, store):
    client.set_up("al", equipment=("dumbbells",))
    client.post("/weights", {"floor-press": "40", "was-floor-press": "0",
                             "curl": "0", "was-curl": "0"})
    assert set(store.weights("al")) == {"floor-press"}
    page = client.get("/weights")[1]
    assert '<option value="40" selected>40 lb' in page and "Leave it to the coach" in page
    before = store.weights("al")["floor-press"]
    client.post("/weights", {"floor-press": "40", "was-floor-press": "40"})
    assert store.weights("al")["floor-press"] == before
    client.post("/weights", {"floor-press": "0", "was-floor-press": "40"})
    assert store.weights("al") == {} and "Leave it to the coach" not in client.get("/weights")[1]


@pytest.mark.parametrize("given", ["41", "60", "-5", "heavy", "", "nan", "27.5"])
def test_only_the_dials_own_settings_are_taken(client, store, given):
    client.set_up("al", equipment=("dumbbells",))
    client.post("/weights", {"floor-press": given, "was-floor-press": "0", "push-up": "20"})
    assert store.weights("al") == {}


def test_the_next_workout_uses_it_and_the_session_moves_it(client, store):
    client.set_up("al", equipment=("dumbbells",))
    base = client.post("/workouts", {"name": "Mine"})[0].headers["Location"]
    client.post(f"{base}/add", {"key": "curl"})
    client.post("/weights", {"curl": "25", "was-curl": "0"})
    time.sleep(0.01)
    client.post(f"{base}/start", {})
    session = json.loads(client.get("/api/session")[1])
    (item,) = session["items"]
    assert {load for load, _ in item["sets"]} == {25}
    events = [{"cid": f"weight-{n:02}", "type": "set", "item": "curl", "n": n, "load": 25,
               "amount": 8} for n in range(3)]
    events.append({"cid": "weight-rir", "type": "rir", "item": "curl", "value": 4})
    client.api("/api/events", {"session": session["id"], "events": events})
    store.finish(session["id"])
    assert '<option value="30" selected>30 lb' in client.get("/weights")[1]


def test_weights_are_each_persons_own_and_exported(client, other, store):
    client.set_up("al", equipment=("dumbbells",))
    client.post("/weights", {"curl": "25", "was-curl": "0"})
    other.set_up("bo", equipment=("dumbbells",))
    assert store.weights("bo") == {} and "25 lb</option>" in other.get("/weights")[1]
    assert json.loads(client.get("/export.json")[1])["weights"]["curl"]["load"] == 25
    assert json.loads(other.get("/export.json")[1])["weights"] == {}


def test_weights_are_never_sent_to_claude(client, store):
    from reps import describe

    client.set_up("al", equipment=("dumbbells",))
    client.post("/weights", {"curl": "52.5", "was-curl": "0"})
    assert "52.5" not in json.dumps(describe.request_for(store.profile("al", 45), "arms"))
