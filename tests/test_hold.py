"""Weights that go up only when the person says."""

import datetime as dt

import pytest

from reps.coach.plan import Profile, prescribe
from reps.coach.progression import Performance
from reps.library import EQUIPMENT, EXERCISES

TODAY = dt.date(2026, 11, 25)


def did(load, count, rir, ago):
    return Performance(TODAY - dt.timedelta(days=ago), ((load, count),) * 3, rir)


def planned(*past, hold=True):
    profile = Profile(goals=("fitness",), equipment=frozenset(EQUIPMENT), body_kg=85,
                      experience="regular", hold=hold)
    return prescribe(profile, EXERCISES["floor-press"], {"floor-press": list(past)}, TODAY)[0]


def loads(item):
    return [load for load, _ in item.sets]


def test_the_first_time_stays_at_one_weight():
    assert loads(planned()) == [15, 15, 15]
    assert loads(planned(hold=False)) == [15, 17.5, 20]


@pytest.mark.parametrize("rir", [2, 3, 4])
def test_a_first_weight_is_kept_however_easy(rir):
    item = planned(did(30, 8, rir, 2))
    assert loads(item) == [30] * 3
    assert ("35 lb is earned" in item.why) == (rir == 4)


def test_an_earned_weight_is_offered_and_waits():
    past = [did(30, 8, 2, 8), did(30, 15, 2, 5), did(30, 15, 2, 2)]
    for upto in (2, 3):
        item = planned(*past[:upto])
        assert loads(item) == [30] * 3 and item.why == "35 lb is earned: press + to take it."
        assert [count for _, count in item.sets] == [15] * 3  # and no more reps are asked for
    assert loads(planned(*past[:2], hold=False)) == [35] * 3


def test_taking_it_makes_it_the_base():
    item = planned(did(30, 8, 2, 8), did(30, 15, 2, 5), did(35, 9, 2, 2))
    assert loads(item) == [35] * 3 and "earned:" not in item.why


def test_a_bridge_is_offered_too():
    past = [did(5, 8, 2, 8), did(5, 18, 2, 5)]
    assert loads(planned(*past)) == [5] * 3 and "7.5 lb is earned" in planned(*past).why


def test_it_still_comes_down():
    assert loads(planned(did(30, 8, 0, 2))) == [25] * 3
    assert loads(planned(did(30, 8, 2, 20), did(30, 8, 2, 17))) == [25] * 3


def test_the_setting_is_kept(client, store):
    client.set_up()
    assert "Weights go up" in client.get("/setup/goals")[1]
    client.post("/setup/goals", {"goals": ["strength"], "reps": "0", "hold": "1"})
    assert store.profile("al", 45).hold is True and store.profile("bo", 45).hold is False
    client.post("/setup/goals", {"goals": ["strength"], "reps": "0", "hold": "0"})
    assert store.profile("al", 45).hold is False
