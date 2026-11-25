"""What has been lifted over time, and its charts."""

import datetime as dt
import re

import pytest

from reps.coach.progress import by_week, lifted, lines
from reps.coach.progression import Performance
from reps.library import EXERCISES
from reps.web import chart

TODAY = dt.date(2026, 11, 25)  # a Wednesday


def done(ago, *sets):
    return Performance(TODAY - dt.timedelta(days=ago), sets, 2)


def test_a_line_takes_each_days_best():
    history = {"floor-press": [done(5, (20, 10), (25, 8), (25, 9)), done(0, (30, 8), (25, 12))]}
    line = lines(history)[0]
    assert [(point.value, point.reps) for point in line.points] == [(25, 9), (30, 8)]
    assert line.unit == "lb"


def test_two_sessions_in_a_day_are_one_point():
    history = {"curl": [done(1, (20, 10)), done(1, (25, 6)), done(1, (25, 5))]}
    assert [(point.value, point.reps) for point in lines(history)[0].points] == [(25, 6)]


def test_without_a_weight_the_most_reps_or_seconds_count():
    history = {"push-up": [done(1, (0, 12), (0, 15))], "plank": [done(0, (0, 40), (0, 35))]}
    plank, press = lines(history)
    assert (plank.points[0].value, plank.unit) == (40, "s")
    assert (press.points[0].value, press.unit) == (15, "reps")


def test_cardio_and_what_was_never_lifted_have_no_line():
    assert lines({"rower": [done(0, (100, 600))], "curl": [done(0, (0, 0))]}) == []


@pytest.mark.parametrize("key, total", [
    ("floor-press", 2 * 20 * 10),  # two dumbbells
    ("goblet-squat", 20 * 10),  # one
    ("one-arm-row", 2 * 20 * 10),  # one, on both sides
    ("split-squat", 4 * 20 * 10),  # two, on both sides
    ("push-up", 0),
])
def test_every_dumbbell_rep_and_side_is_counted(key, total):
    assert lifted(EXERCISES[key], done(0, (20, 10))) == total


def test_weeks_begin_on_monday_and_empty_ones_are_kept():
    history = {"goblet-squat": [done(2, (20, 10)), done(3, (30, 10)), done(400, (50, 10))]}
    weeks = by_week(history, TODAY)
    assert len(weeks) == 12 and weeks[-1] == (dt.date(2026, 11, 23), 200)
    assert weeks[-2] == (dt.date(2026, 11, 16), 300) and weeks[0][1] == 0


@pytest.mark.parametrize("low, high", [(0, 1), (0, 12345), (25, 27.5), (30, 30), (0, 0),
                                       (5, 52.5), (0.1, 0.3)])
def test_ticks_are_few_and_reach_past_both_ends(low, high):
    marks = chart.ticks(low, high)
    assert marks[0] <= low and marks[-1] >= high and 2 <= len(marks) <= 6
    assert marks[-1] > marks[0]


@pytest.mark.parametrize("values", [[25], [25, 25], [25, 30, 27.5], list(range(60))])
def test_a_line_stays_inside_its_plot(values):
    marks = [chart.Mark(value, f"{value} lb", n / max(1, len(values) - 1))
             for n, value in enumerate(values)]
    drawn = chart.line("Heaviest", marks, "1 Nov", "25 Nov")
    for x, y in re.findall(r'cx="([\d.]+)" cy="([\d.]+)"', drawn):
        assert chart.LEFT <= float(x) <= chart.WIDTH and chart.TOP <= float(y) <= chart.BASE
    assert "style=" not in drawn and chart.spark(values)


def test_the_pages(client, store):
    client.set_up()
    assert "Nothing yet" in client.get("/history?view=progress")[1]
    assert client.get("/progress/curl")[0].status == 404
    run = store.run
    run("INSERT INTO session (person, started, ended, title, kind, plan, coach)"
        " VALUES ('al', 1790000000, 1790002000, 'Push', 'strength', '{\"items\": []}', 1)")
    number = store.finished("al")[0]["id"]
    run("INSERT INTO set_log (cid, session, item, n, load, amount, at)"
        " VALUES ('one', ?, 'curl', 0, 25, 10, 1790000100)", number)
    page = client.get("/history?view=progress")[1]
    assert "Curl" in page and "25 lb" in page and "/progress/curl" in page
    assert "25 lb × 10" in client.get("/progress/curl")[1]
    assert "Progress" in client.get("/history")[1]  # the view is remembered


def test_one_persons_progress_is_not_anothers(client, other, store):
    client.set_up("al")
    other.set_up("bo")
    store.run("INSERT INTO session (person, started, ended, title, kind, plan, coach)"
              " VALUES ('al', 1790000000, 1790002000, 'Push', 'strength', '{\"items\": []}', 1)")
    number = store.finished("al")[0]["id"]
    store.run("INSERT INTO set_log (cid, session, item, n, load, amount, at)"
              " VALUES ('one', ?, 'curl', 0, 25, 10, 1790000100)", number)
    assert client.get("/progress/curl")[0].status == 200
    assert other.get("/progress/curl")[0].status == 404
