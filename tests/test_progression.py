import datetime as dt

import pytest

from reps.coach.progression import (
    Performance,
    earn_reps,
    heavier_than,
    needs_bridge,
    replay,
    targets,
)
from reps.gym import DIAL_LOADS, Gym
from reps.library import EXERCISES

PRESS = EXERCISES["floor-press"]
DAY = dt.date(2026, 11, 2)


def history(*sessions):
    """Sessions a week apart: (sets, reps in reserve)."""
    return [
        Performance(DAY + dt.timedelta(days=7 * place), tuple(sets), rir)
        for place, (sets, rir) in enumerate(sessions)
    ]


def track_of(sessions, low=8, high=12, after=7):
    past = history(*sessions)
    today = past[-1].day + dt.timedelta(days=after)
    return replay(PRESS, past, DIAL_LOADS, low, high, 3, today)


def settled(load):
    """A ramp-up that settles on ``load``."""
    return [(load, 8)], 3


@pytest.mark.parametrize(
    ("load", "reps"),
    [(5, 18), (7.5, 18), (10, 18), (12.5, 16), (15, 15), (17.5, 14), (20, 13), (22.5, 13),
     (25, 16), (30, 15), (35, 14), (40, 13), (45, 13), (50, 10)],
)
def test_reps_that_earn_each_load_match_the_spec(load, reps):
    assert earn_reps(load, heavier_than(DIAL_LOADS, load), 8) == reps


def test_only_the_lightest_two_steps_are_bridged():
    bridged = [
        load
        for load in DIAL_LOADS[:-1]
        if needs_bridge(load, heavier_than(DIAL_LOADS, load), 8)
    ]
    assert bridged == [5, 7.5]


@pytest.mark.parametrize(("rir", "load"), [(4, 25), (3, 22.5), (2, 22.5), (1, 20), (None, 20)])
def test_the_ramp_up_settles_a_first_load(rir, load):
    assert track_of([([(20, 8), (22.5, 8)], rir)]).load == load


def test_enough_reps_with_one_left_goes_up_one_load():
    track = track_of([settled(30), ([(30, 15)] * 3, 1)])
    assert (track.load, track.reps) == (35, [8])


def test_enough_reps_to_failure_stays():
    assert track_of([settled(30), ([(30, 15)] * 3, 0)]).load == 30


def test_effort_not_given_never_raises_a_load():
    assert track_of([settled(30), ([(30, 18)] * 3, None)]).load == 30


def test_four_left_can_go_two_loads_up():
    assert track_of([settled(20), ([(20, 18)] * 3, 4)]).load == 25


def test_not_earned_adds_a_rep_to_the_weakest_set():
    track = track_of([settled(30), ([(30, 10), (30, 9), (30, 8)], 2)])
    goal, _ = targets(PRESS, track, DIAL_LOADS, 8, 3)
    assert [target.reps for target in goal] == [10, 9, 9]
    assert {target.load for target in goal} == {30}


def test_one_miss_repeats_and_two_drop_a_load():
    miss = ([(30, 8), (30, 6), (30, 5)], 0)
    assert track_of([settled(30), miss]).load == 30
    assert track_of([settled(30), miss, miss]).load == 25


def test_missing_after_a_step_asks_for_more_next_time():
    track = track_of([settled(30), ([(30, 15)] * 3, 1), ([(35, 6)] * 3, 0)])
    assert track.extra == {30: 1}


def test_a_changed_load_becomes_the_new_base():
    assert track_of([settled(30), ([(25, 10)] * 3, 2)]).load == 25


def test_a_bridge_grows_a_set_at_a_time_then_takes_the_load():
    earned = ([(5, 18)] * 3, 1)
    first = ([(7.5, 6), (5, 18), (5, 18)], 1)
    second = ([(7.5, 6), (7.5, 6), (5, 18)], 1)
    third = ([(7.5, 7)] * 3, 1)
    assert track_of([settled(5), earned]).bridge == 1
    assert track_of([settled(5), earned, first]).bridge == 2
    assert track_of([settled(5), earned, first, second]).bridge == 0
    assert track_of([settled(5), earned, first, second]).load == 7.5
    assert track_of([settled(5), earned, first, second, third]).load == 7.5


@pytest.mark.parametrize(("days", "load"), [(9, 30), (10, 25), (20, 25), (21, 22.5), (41, 22.5)])
def test_time_off_lowers_the_load(days, load):
    assert track_of([settled(30), ([(30, 10)] * 3, 2)], after=days).load == load


def test_a_long_time_off_starts_again_with_a_ramp_up():
    track = track_of([settled(30), ([(30, 10)] * 3, 2)], after=42)
    goal, why = targets(PRESS, track, DIAL_LOADS, 8, 3)
    assert track.load is None
    assert [target.load for target in goal] == [15, 17.5, 20]
    assert "42 days off" in why


def test_a_light_lifter_restarts_below_their_old_load():
    track = track_of([settled(10), ([(10, 10)] * 3, 2)], after=60)
    goal, why = targets(PRESS, track, DIAL_LOADS, 8, 3)
    assert [target.load for target in goal] == [5, 7.5, 10]
    assert "60 days off" in why


def test_the_heaviest_load_becomes_more_reps():
    track = track_of([settled(52.5), ([(52.5, 12)] * 3, 2)])
    assert track.ceiling and track.load == 52.5
    goal, _ = targets(PRESS, track, DIAL_LOADS, 8, 3)
    assert max(target.reps for target in goal) == 13


def test_the_step_limit_caps_loads_by_body_weight():
    press = EXERCISES["step-press"]
    assert Gym().loads(press, None) == ()
    assert max(Gym().loads(press, 80)) == 30  # 240 - 176 lb leaves 64 lb for two
    assert max(Gym(300, True).loads(press, 80)) == 52.5
    assert Gym().loads(press, 120) == ()


def test_resting_one_exercise_while_training_is_not_time_off():
    past = history(settled(30), ([(30, 10)] * 3, 2))
    today = past[-1].day + dt.timedelta(days=35)
    trained = tuple(past[-1].day + dt.timedelta(days=3 * each) for each in range(12))
    track = replay(PRESS, past, DIAL_LOADS, 8, 12, 3, today, trained)
    assert track.load == 30


def test_the_dial_has_bowflexs_own_settings_and_nothing_between():
    small = tuple(half / 2 for half in range(10, 51, 5))
    assert DIAL_LOADS == (*small, 30, 35, 40, 45, 50, 52.5) and len(DIAL_LOADS) == 15


def test_reasons_are_in_pounds_as_the_dial_says():
    why = track_of([settled(20), ([(22.5, 8)] * 3, 2)]).why
    assert "lb" in why and "kg" not in why and "dial" not in why
