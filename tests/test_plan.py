import datetime as dt
import itertools

import pytest

from reps.coach.plan import (
    CORE_SESSIONS,
    GOALS,
    WEEKLY,
    Profile,
    candidates,
    next_session,
    swap,
    total_seconds,
    uncovered,
)
from reps.coach.progression import Performance
from reps.gym import DIAL_LOADS
from reps.library import EQUIPMENT, EXERCISES

MONDAY = dt.date(2026, 11, 23)
EVERYTHING = frozenset(EQUIPMENT)
EVERY_SET_OF_EQUIPMENT = [
    frozenset(owned)
    for size in range(len(EQUIPMENT) + 1)
    for owned in itertools.combinations(EQUIPMENT, size)
]
EVERY_PAIR_OF_GOALS = [(goal,) for goal in GOALS] + list(itertools.permutations(GOALS, 2))


def check(profile, session):
    assert session.items
    for item in session.items:
        assert item.why, item
        assert item.sets, item
        exercise = EXERCISES.get(item.key)
        if exercise:
            assert exercise.needs <= profile.equipment
            assert exercise.key not in profile.avoid and exercise.pattern not in profile.avoid
        if item.kind == "load":
            allowed = profile.gym.loads(exercise, profile.body_kg)
            assert all(load in allowed for load, _ in item.sets)
    keys = [item.key for item in session.items]
    assert len(keys) == len(set(keys))
    strength = [item for item in session.items if item.kind != "cardio"]
    blocks = sum(item.sets[0][1] for item in session.items if item.kind == "cardio")
    if session.kind == "core":
        assert total_seconds(strength, 0, False) + blocks <= profile.minutes * 60 + 60


@pytest.mark.parametrize("minutes", [30, 45, 60])
def test_every_set_of_equipment_gives_valid_sessions(minutes):
    for owned in EVERY_SET_OF_EQUIPMENT:
        for body in (None, 60, 85, 115):
            profile = Profile(equipment=owned, minutes=minutes, body_kg=body)
            for index in range(6):
                check(profile, next_session(profile, index, 48, {}, MONDAY))


@pytest.mark.parametrize("goals", EVERY_PAIR_OF_GOALS)
def test_every_pair_of_goals_gives_valid_sessions(goals):
    for minutes in range(30, 65, 5):
        profile = Profile(goals=goals, equipment=EVERYTHING, minutes=minutes, body_kg=75)
        for index in range(6):
            check(profile, next_session(profile, index, 48, {}, MONDAY))


@pytest.mark.parametrize("minutes", [30, 45, 60])
def test_three_sessions_cover_squat_hinge_push_and_pull(minutes):
    for owned in EVERY_SET_OF_EQUIPMENT:
        profile = Profile(equipment=owned, minutes=minutes, body_kg=75)
        trained = {
            EXERCISES[item.key].pattern
            for index in range(CORE_SESSIONS)
            for item in next_session(profile, index, 48, {}, MONDAY).items
            if item.key in EXERCISES
        }
        assert {"squat", "hinge", "hpush", "pull", "core"} <= trained, owned


def test_the_same_inputs_give_the_same_session():
    profile = Profile(equipment=EVERYTHING, body_kg=75)
    first = next_session(profile, 1, 48, {}, MONDAY)
    assert first == next_session(profile, 1, 48, {}, MONDAY)


def test_avoided_patterns_and_exercises_never_appear():
    avoid = frozenset({"vpush", "goblet-squat"})
    profile = Profile(equipment=EVERYTHING, avoid=avoid, minutes=60, body_kg=75)
    for index in range(CORE_SESSIONS):
        check(profile, next_session(profile, index, 48, {}, MONDAY))


def test_the_pull_up_bar_appears_at_most_once_a_week():
    profile = Profile(equipment=EVERYTHING, minutes=60, body_kg=75)
    keys = [
        item.key
        for index in range(CORE_SESSIONS)
        for item in next_session(profile, index, 48, {}, MONDAY).items
    ]
    assert keys.count("pull-up") <= 1


def test_loaded_work_stays_off_the_step_without_a_body_weight():
    profile = Profile(equipment=EVERYTHING, body_kg=None)
    for pattern in WEEKLY:
        assert not any(exercise.on_step for exercise in candidates(profile, pattern))


def test_bonus_sessions_follow_the_three():
    profile = Profile(equipment=EVERYTHING)
    assert [next_session(profile, done, 48, {}, MONDAY).kind for done in range(5)] == [
        "core", "core", "core", "bonus", "bonus"]


def test_everything_is_covered_with_dumbbells_and_tower():
    assert uncovered(Profile(equipment=frozenset({"dumbbells", "tower"}))) == []


def test_swap_keeps_the_pattern():
    profile = Profile(equipment=EVERYTHING, body_kg=75)
    other = swap(profile, "goblet-squat", {"goblet-squat"}, {}, MONDAY)
    assert EXERCISES[other.key].pattern == "squat"


# Simulated lifters -----------------------------------------------------------


class Lifter:
    """Lifts by Epley from a hidden one-rep max, and reports effort honestly."""

    def __init__(self, strength, gain, effort=1, skips=()):
        self.strength, self.gain, self.effort, self.skips = strength, gain, effort, skips

    def perform(self, item, week):
        if item.kind == "cardio":
            return None
        if item.kind != "load":
            done = tuple((0, count) for _, count in item.sets)
            return Performance(self.today, done, 1, item.reps)
        best = self.strength[EXERCISES[item.key].pattern] * (1 + self.gain * week)
        sets, left = [], 4
        for load, goal in item.sets:
            most = max(0, int(30 * (best / load - 1)))
            count = min(goal, max(1, most - self.effort))
            sets.append((load, count))
            left = min(4, max(0, most - count))
        return Performance(self.today, tuple(sets), left, item.reps)


START = {pattern: 35 for pattern in ("squat", "hinge", "lunge", "hpush", "vpush", "pull", "arms")}
LIFTERS = {
    "steady": Lifter(START, 0.01),
    "stalled": Lifter(START, 0),
    "weak": Lifter({key: 12 for key in START}, 0.01),
    "strong": Lifter({key: 110 for key in START}, 0.01),
    "fading": Lifter(START, -0.005),
    "away": Lifter(START, 0.005, skips=set(range(10, 13)) | set(range(30, 38))),
}


@pytest.mark.parametrize("name", LIFTERS)
@pytest.mark.parametrize("owned", [EVERYTHING, frozenset({"dumbbells"}), frozenset({"tower"})])
@pytest.mark.parametrize("reps", [0, 5, 8, 12, 15])
def test_a_year_of_training_keeps_the_rules(name, owned, reps):
    lifter, history = LIFTERS[name], {}
    profile = Profile(equipment=owned, body_kg=75, reps=reps)
    heaviest = {}
    for week in range(52):
        if week in lifter.skips:
            continue
        for index in range(CORE_SESSIONS):
            lifter.today = MONDAY + dt.timedelta(days=7 * week + 2 * index)
            session = next_session(profile, index, week, history, lifter.today)
            check(profile, session)
            for item in session.items:
                done = lifter.perform(item, week)
                if not done:
                    continue
                if item.kind == "load" and item.key in heaviest and item.last:
                    place = DIAL_LOADS.index(max(load for load, _ in item.sets))
                    assert place - heaviest[item.key] <= 2, item
                if item.kind == "load":
                    heaviest[item.key] = DIAL_LOADS.index(max(load for load, _ in done.sets))
                history.setdefault(item.key, []).append(done)


@pytest.mark.parametrize("reps", [0, 5, 6, 8, 10, 12, 15])
def test_a_steady_lifter_gets_stronger_over_a_year(reps):
    lifter, history = LIFTERS["steady"], {}
    profile = Profile(equipment=frozenset({"dumbbells"}), reps=reps)
    for week in range(52):
        for index in range(CORE_SESSIONS):
            lifter.today = MONDAY + dt.timedelta(days=7 * week + 2 * index)
            for item in next_session(profile, index, week, history, lifter.today).items:
                done = lifter.perform(item, week)
                if done:
                    history.setdefault(item.key, []).append(done)
    for key in ("goblet-squat", "floor-press", "romanian-deadlift"):
        loads = [max(load for load, _ in done.sets) for done in history[key]]
        assert loads[-1] > loads[3], (key, loads)
