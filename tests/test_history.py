import datetime as dt
import json
import re

import pytest

from reps.coach.custom import CARDIO
from reps.guides import GUIDES, guide_for
from reps.library import EXERCISES


def add(store, person, day, title="Core 1 of 3", kind="core"):
    start = dt.datetime.combine(day, dt.time(7, 0)).timestamp()
    return store.run(
        "INSERT INTO session (person, started, ended, kind, title, plan, coach)"
        " VALUES (?, ?, ?, ?, ?, ?, 1)",
        person, start, start + 1800, kind, title, json.dumps({"items": []}),
    )


DAY = dt.date(2026, 3, 10)


def test_a_workout_day_opens_what_was_done(client, store):
    client.set_up()
    number = add(store, "al", DAY)
    page = client.get("/history?month=2026-03")[1]
    assert "March 2026" in page and "1 done" in page
    assert f'href="/history/{number}"' in page and "10 March: Core 1 of 3" in page
    assert "/history?month=2026-03" in client.get(f"/history/{number}")[1]


def test_two_on_a_day_open_a_list_and_an_extra_looks_different(client, store):
    client.set_up()
    add(store, "al", DAY)
    add(store, "al", DAY, "Rower", "extra")
    add(store, "al", DAY + dt.timedelta(days=1), "Rower", "extra")
    page = client.get("/history?month=2026-03")[1]
    assert "/history?day=2026-03-10" in page and "cal-day done extra" in page
    day = client.get("/history?day=2026-03-10")[1]
    assert "Core 1 of 3" in day and "Rower" in day and "Tuesday 10 March" in day


def test_the_chosen_view_is_remembered(client, store):
    client.set_up()
    add(store, "al", DAY)
    assert "cal-day" in client.get("/history")[1]
    assert "cal-day" not in client.get("/history?view=list")[1]
    again = client.get("/history")[1]
    assert "Core 1 of 3" in again and "cal-day" not in again
    client.get("/history?view=calendar")
    assert "cal-day" in client.get("/history")[1]


@pytest.mark.parametrize("query", ["month=x", "month=2026-13", "day=never", "view=x", "page=-1"])
def test_odd_addresses_still_show_history(client, query):
    client.set_up()
    assert client.get(f"/history?{query}")[0].status == 200


def test_months_stop_at_the_first_session_and_this_one(client, store):
    client.set_up()
    add(store, "al", DAY)
    first = client.get("/history?month=2026-03")[1]
    assert "Later month" in first and "Earlier month" not in first
    this = client.get("/history")[1]
    assert "Earlier month" in this and "Later month" not in this


def test_the_calendar_is_each_persons_own(client, other, store):
    client.set_up("al")
    add(store, "al", DAY, "Secret")
    other.set_up("bo")
    for address in ("/history?month=2026-03", "/history?day=2026-03-10", "/history?view=list"):
        assert "Secret" not in other.get(address)[1]


# Guides ----------------------------------------------------------------------


def test_everything_that_can_be_prescribed_has_a_guide():
    for key in [*EXERCISES, *CARDIO, "finisher-bike", "finisher-circuit", "rower#2"]:
        guide = guide_for(key)
        assert guide and len(guide.steps) == 4 and guide.watch, key
    assert set(GUIDES) == {*EXERCISES, *CARDIO}


def test_guides_fit_a_phone_and_link_only_to_ace():
    for key, guide in GUIDES.items():
        assert all(len(step) <= 80 for step in guide.steps) and len(guide.watch) <= 90, key
        about = guide.as_dict()
        assert about["link"].startswith("https://www.acefitness.org/"), key
        assert len(about["source"]) <= 40, key


def test_a_guide_opens_from_today_and_comes_with_the_session(client):
    client.set_up()
    links = re.findall(r'href="(/how/[^"]+)"', client.get("/")[1])
    assert links
    for link in links:
        assert "Watch for" in client.get(link)[1]
    page = client.get("/how/front-squat")[1]
    assert "22/front-squat/" in page and "noopener" in page
    assert client.get("/how/nonsense")[0].status == 404
    client.post("/start", {})
    items = json.loads(client.get("/api/session")[1])["items"]
    assert all(item["how"]["steps"] for item in items)
