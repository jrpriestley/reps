import datetime as dt
import http.client
import json
import re
import ssl
import threading
import time

import pytest

from reps.config import Config
from reps.store import STALE_SECONDS, week_number
from reps.web.server import Server


def cid(number):
    return f"test-{number:08d}"


def started(client):
    client.post("/start", {"minutes": "45"})
    return json.loads(client.get("/api/session")[1])


def log_everything(client, session, rir=2):
    events = []
    for item in session["items"]:
        for n, (load, amount) in enumerate(item["sets"]):
            events.append({"cid": cid(len(events)), "type": "set", "item": item["key"],
                           "n": n, "load": load, "amount": amount})
        if item["kind"] != "cardio":
            events.append({"cid": cid(len(events)), "type": "rir", "item": item["key"],
                           "value": rir})
    events.append({"cid": cid(len(events)), "type": "finish"})
    return client.api("/api/events", {"session": session["id"], "events": events})


# Sign-in ---------------------------------------------------------------------


def test_every_page_asks_for_sign_in(client):
    for path in ("/", "/week", "/history", "/me", "/gym", "/export.json", "/session"):
        assert "Sign in" in client.get(path)[1]
    assert client.get("/api/session")[0].status == 401


def test_a_wrong_password_is_refused_and_five_lock_out(client):
    for _ in range(5):
        assert client.sign_in(password="wrong")[0].status == 403
    assert client.sign_in()[0].status == 429


def test_only_listed_people_sign_in(client):
    assert client.sign_in("root")[0].status == 403


def test_the_accounts_own_name_signs_the_person_in(client, store):
    assert client.sign_in("bo-web")[0].status == 303
    assert store.rows("SELECT person FROM login")[0]["person"] == "bo"


def test_the_cookie_is_safe_and_only_its_hash_is_stored(client, store):
    cookie = client.sign_in()[0].headers["Set-Cookie"]
    assert cookie.startswith("__Host-reps=")
    for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"):
        assert flag in cookie
    assert store.rows("SELECT token FROM login")[0]["token"] != client.cookies["__Host-reps"]


def test_signing_out_ends_it(client):
    client.set_up()
    token = client.cookies["__Host-reps"]
    client.post("/logout")
    client.cookies["__Host-reps"] = token
    assert "Sign in" in client.get("/")[1]


def test_other_hosts_and_sites_are_refused(client):
    assert client.get("/", Host="homehub:8090")[0].status == 421
    client.set_up()
    assert client.post("/start", {}, Origin="https://evil.example")[0].status == 403
    assert client.post("/start", {}, Origin="null")[0].status == 403


def test_pages_carry_the_content_policy(client):
    response, _ = client.get("/")
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


# Setup -----------------------------------------------------------------------


def test_a_new_person_is_walked_through_setup(client):
    client.sign_in()
    assert client.get("/")[0].headers["Location"] == "/setup/goals"
    client.set_up()
    assert "Core 1 of 3" in client.get("/")[1]


@pytest.mark.parametrize(
    ("step", "fields"),
    [("goals", {}), ("goals", {"goals": ["strength", "fitness", "muscle"]}),
     ("days", {"days": ["0", "1"]}), ("body", {"body_kg": "900"}), ("body", {"body_kg": "x"})],
)
def test_setup_refuses_what_makes_no_sense(client, step, fields):
    client.set_up()
    assert client.post(f"/setup/{step}", fields)[0].status == 400


def test_setup_is_each_persons_own(client, other):
    client.set_up("al", equipment=("dumbbells",))
    other.set_up("bo", equipment=("tower", "bike"))
    assert "1 of 7 items" in client.get("/me")[1]
    assert "2 of 7 items" in other.get("/me")[1]


def test_changing_equipment_changes_the_plan(client):
    client.set_up(equipment=("dumbbells",))
    assert "Floor press" in client.get("/")[1]
    client.post("/setup/equipment", {"equipment": ["tower"]})
    page = client.get("/")[1]
    assert "Floor press" not in page and "Push-up on the tower" in page


def test_minutes_stay_inside_what_is_allowed(client):
    client.set_up()
    assert "<b>60</b> min" in client.get("/?minutes=500")[1]
    assert "<b>30</b> min" in client.get("/?minutes=1")[1]
    assert "<b>45</b> min" in client.get("/?minutes=soon")[1]


# Sessions --------------------------------------------------------------------


def test_a_session_from_start_to_history(client):
    client.set_up()
    session = started(client)
    assert log_everything(client, session)[0] == 200
    assert "Core 2 of 3" in client.get("/")[1]
    assert "Core 1 of 3" in client.get("/history")[1]
    assert "2 left" in client.get(f"/history/{session['id']}")[1]
    assert "Done" in client.get("/week")[1]


def test_the_same_events_sent_twice_count_once(client, store):
    client.set_up()
    session = started(client)
    log_everything(client, session)
    before = len(store.logged(session["id"]))
    log_everything(client, session)
    assert len(store.logged(session["id"])) == before


def test_the_next_session_builds_on_the_last(client, store):
    client.set_up(equipment=("dumbbells",))
    session = started(client)
    log_everything(client, session, rir=2)
    history = store.history("al")
    press = next(item for item in session["items"] if item["key"] == "floor-press")
    assert history["floor-press"][0].sets == tuple(tuple(each) for each in press["sets"])
    assert history["floor-press"][0].rir == 2


def test_undo_takes_a_set_back(client, store):
    client.set_up()
    session = started(client)
    item = session["items"][0]
    made = {"cid": cid(1), "type": "set", "item": item["key"], "n": 0, "load": 10, "amount": 8}
    gone = {"cid": cid(2), "type": "unset", "item": item["key"], "target": cid(1)}
    client.api("/api/events", {"session": session["id"], "events": [made]})
    assert len(store.logged(session["id"])) == 1
    client.api("/api/events", {"session": session["id"], "events": [gone]})
    assert store.logged(session["id"]) == []


def test_what_hurts_leaves_the_plan_until_cleared(client):
    client.set_up()
    session = started(client)
    key = session["items"][0]["key"]
    events = [{"cid": cid(1), "type": "hurt", "item": key}]
    client.api("/api/events", {"session": session["id"], "events": events})
    client.api("/api/discard", {"session": session["id"]})
    assert key not in [item["key"] for item in started(client)["items"]]
    assert "hurts" in client.get("/setup/hurts")[1]
    client.post("/setup/hurts", {})
    client.api("/api/discard", {"session": session["id"] + 1})
    assert key in [item["key"] for item in started(client)["items"]]


def test_swap_changes_one_exercise(client):
    client.set_up()
    session = started(client)
    key = session["items"][0]["key"]
    status, answer = client.api("/api/swap", {"session": session["id"], "item": key})
    assert status == 200
    assert answer["items"][0]["key"] != key
    assert answer["items"][1:] == session["items"][1:]


@pytest.mark.parametrize(
    "event",
    [{"cid": "short", "type": "finish"}, {"cid": cid(1), "type": "set", "item": "nonsense"},
     {"cid": cid(1), "type": "dance", "item": None}, {"type": "finish"}],
)
def test_bad_events_are_refused(client, event):
    client.set_up()
    session = started(client)
    event = event | {"item": session["items"][0]["key"]} if "item" in event and not event[
        "item"] else event
    assert client.api("/api/events", {"session": session["id"], "events": [event]})[0] == 400


def test_a_load_the_dumbbells_cannot_make_is_refused(client):
    client.set_up()
    session = started(client)
    event = {"cid": cid(1), "type": "set", "item": session["items"][0]["key"], "n": 0,
             "load": 27, "amount": 8}
    assert client.api("/api/events", {"session": session["id"], "events": [event]})[0] == 400


def test_one_person_cannot_reach_the_others_sessions(client, other):
    client.set_up("al")
    session = started(client)
    log_everything(client, session)
    other.set_up("bo")
    number = session["id"]
    assert other.get(f"/history/{number}")[0].status == 404
    assert other.post(f"/history/{number}/delete")[0].status == 404
    assert other.api("/api/events", {"session": number, "events": []})[0] == 404
    assert other.api("/api/swap", {"session": number, "item": "x"})[0] == 404
    assert "Core 1 of 3" not in other.get("/history")[1]
    assert "floor-press" not in other.get("/export.json")[1]
    assert client.get(f"/history/{number}")[0].status == 200


def test_export_holds_no_tokens(client):
    client.set_up()
    log_everything(client, started(client))
    text = client.get("/export.json")[1]
    assert "sets" in text and client.cookies["__Host-reps"] not in text and "token" not in text


def test_a_session_left_open_is_closed(client, store):
    client.set_up()
    session = started(client)
    store.run("UPDATE session SET started = ?", time.time() - STALE_SECONDS - 60)
    assert client.get("/api/session")[0].status == 404
    assert store.session("al", session["id"]) is None


def test_a_discarded_sessions_number_is_never_reused(client):
    client.set_up()
    first = started(client)
    client.api("/api/discard", {"session": first["id"]})
    assert started(client)["id"] > first["id"]


def test_weeks_turn_over_on_monday():
    sunday = dt.date(2026, 11, 29)
    assert week_number(sunday) + 1 == week_number(sunday + dt.timedelta(days=1))
    assert week_number(sunday) == week_number(sunday - dt.timedelta(days=6))


def test_the_backup_can_be_read_back(client, store, tmp_path):
    client.set_up()
    log_everything(client, started(client))
    copy = type(store)(store.backup(tmp_path / "backups"))
    assert len(copy.finished("al")) == 1
    assert copy.history("al") == store.history("al")


def test_plain_http_on_the_https_port_is_sent_to_https(tmp_path):
    config = Config(host="reps", port=0, bind="127.0.0.1", state_dir=tmp_path, tls_dir=None)
    server = Server(config, ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for host, where in (("reps", "https://reps/"), ("reps:8090", "https://reps:8090/"),
                        ("evil.example", "https://reps/")):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", "/", headers={"Host": host})
        response = connection.getresponse()
        assert (response.status, response.headers["Location"]) == (301, where)
        connection.close()
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize(("theme", "colours"), [
    ("system", {"#ffffff", "#141e1c"}), ("light", {"#ffffff"}), ("dark", {"#141e1c"}),
])
def test_the_browsers_bars_match_the_theme(client, theme, colours):
    client.set_up()
    client.post("/theme", {"theme": theme})
    page = client.get("/")[1]
    assert set(re.findall(r'"theme-color" content="(#\w+)"', page)) == colours
