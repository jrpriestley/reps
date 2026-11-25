import json
import time

import pytest

HOUR = 3600


@pytest.fixture
def session(client, store):
    """A session begun an hour ago."""
    client.set_up()
    client.post("/start", {})
    number = json.loads(client.get("/api/session")[1])["id"]
    store.run("UPDATE session SET started = started - ? WHERE id = ?", HOUR, number)
    return number


def tell(client, session, kind, at, cid):
    event = {"cid": f"pause-{cid:04}", "type": kind, "at": at}
    return client.api("/api/events", {"session": session, "events": [event]})[0]


def paused(store, session):
    return store.session("al", session)["paused"]


def test_paused_time_is_left_out(client, store, session):
    now = time.time()
    assert tell(client, session, "pause", now - 1800, 1) == 200
    assert json.loads(client.get("/api/session")[1])["paused"] is True
    tell(client, session, "resume", now - 600, 2)
    assert json.loads(client.get("/api/session")[1])["paused"] is False
    assert paused(store, session) == pytest.approx(1200)
    first = json.loads(client.get("/api/session")[1])["items"][0]
    event = {"cid": "pause-set1", "type": "set", "item": first["key"], "n": 0, "load": 0,
             "amount": 8}
    client.api("/api/events", {"session": session, "events": [event]})
    client.api("/api/events", {"session": session, "events": [
        {"cid": "pause-end1", "type": "finish"}]})
    page = client.get(f"/history/{session}")[1]
    assert ">40<" in page


def test_the_same_event_twice_counts_once(client, store, session):
    now = time.time()
    for _ in range(2):
        tell(client, session, "pause", now - 600, 1)
        tell(client, session, "resume", now - 300, 2)
    assert paused(store, session) == pytest.approx(300)


def test_pausing_twice_keeps_the_first(client, store, session):
    now = time.time()
    tell(client, session, "pause", now - 600, 1)
    tell(client, session, "pause", now - 400, 2)
    tell(client, session, "resume", now - 300, 3)
    tell(client, session, "resume", now - 100, 4)
    assert paused(store, session) == pytest.approx(300)


def test_a_wrong_clock_never_makes_time(client, store, session):
    now = time.time()
    tell(client, session, "pause", now - 5 * HOUR, 1)
    tell(client, session, "resume", now + 5 * HOUR, 2)
    assert paused(store, session) == pytest.approx(HOUR, abs=5)
    tell(client, session, "pause", now, 3)
    tell(client, session, "resume", now - 900, 4)
    assert paused(store, session) == pytest.approx(HOUR, abs=5)


def test_finishing_while_paused_leaves_the_pause_out(client, store, session):
    tell(client, session, "pause", time.time() - 600, 1)
    store.finish(session)
    found = store.session("al", session)
    assert found["paused"] == pytest.approx(600, abs=5) and found["paused_at"] is None


def test_closing_a_forgotten_session_ignores_a_later_pause(client, store, session):
    tell(client, session, "pause", time.time() - 60, 1)
    store.finish(session, time.time() - 600)
    assert paused(store, session) == 0


@pytest.mark.parametrize("at", ["nan", "inf", "soon", None])
def test_nonsense_times_are_refused(client, store, session, at):
    assert tell(client, session, "pause", at, 1) == 400
    assert store.session("al", session)["paused_at"] is None


def test_only_your_own_session_pauses(client, other, store, session):
    other.set_up("bo")
    assert tell(other, session, "pause", time.time(), 1) == 404
    assert store.session("al", session)["paused_at"] is None
