import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "heart"))

import reps_heart  # noqa: E402

from reps.web import heart as heart_module  # noqa: E402

KEY = {"Authorization": "Bearer feed-key"}
MONITOR = {"name": "Relay", "address": "AA:BB:CC:DD:EE:FF"}


@pytest.fixture
def app(server):
    app = server.RequestHandlerClass.app
    app.heart_key = "feed-key"
    return app


def feed(client, path, data):
    response, text = client.send("POST", path, json.dumps(data), KEY)
    return response.status, json.loads(text)


def choose(client, name="Relay"):
    client.get("/heart?scan=2")
    feed(client, "/feed/nearby", {"devices": [MONITOR | {"name": name}]})
    client.post("/heart", {"source": f"{MONITOR['address']} {name}"})


def test_the_feed_needs_its_key(client, app):
    for key in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer "}):
        assert client.send("GET", "/feed/wanted", headers=key)[0].status == 401
    app.heart_key = ""
    empty = {"Authorization": "Bearer "}
    assert client.send("GET", "/feed/wanted", headers=empty)[0].status == 401


def test_a_source_is_chosen_from_what_is_nearby(client, app, store):
    client.set_up()
    client.post("/heart", {"source": "11:22:33:44:55:66 Stranger"})
    assert store.person("al")["heart_name"] == ""
    choose(client)
    assert store.person("al")["heart_name"] == "Relay"
    client.post("/heart", {"forget": "1"})
    assert store.person("al")["heart_name"] == ""


def test_a_source_is_always_watched_for_and_says_who_is_here(client, app, monkeypatch):
    client.set_up()
    assert feed(client, "/feed/wanted", {})[1]["watch"] == []
    choose(client)

    def here():
        (watched,) = feed(client, "/feed/wanted", {})[1]["watch"]
        assert watched["name"] == "Relay" and watched["address"] == MONITOR["address"]
        return watched["here"]

    later = time.time() + heart_module.PRESENT_SECONDS + 1
    client.post("/start", {})
    session = json.loads(client.get("/api/session")[1])["id"]
    assert here() is True
    client.api("/api/discard", {"session": session})
    assert here() is False
    client.get("/api/heart")  # what any open screen asks
    assert here() is True
    monkeypatch.setattr(heart_module.time, "time", lambda: later)
    assert here() is False


def test_looking_goes_on_while_the_relay_app_is_in_front(client, app, monkeypatch):
    client.set_up()
    page = client.get("/heart?scan=1")
    assert page[0].headers["Refresh"].endswith("url=/heart")
    assert feed(client, "/feed/wanted", {})[1]["scan"] is True
    start = time.time()
    monkeypatch.setattr(heart_module.time, "time", lambda: start + 100)
    assert feed(client, "/feed/wanted", {})[1]["scan"] is True
    feed(client, "/feed/nearby", {"devices": [MONITOR]})
    monkeypatch.setattr(heart_module.time, "time", lambda: start + 200)
    assert feed(client, "/feed/wanted", {})[1]["scan"] is False
    response, text = client.get("/heart")
    assert "Relay" in text and "Refresh" not in response.headers


def test_every_signed_in_screen_has_a_place_for_it(client, app):
    client.set_up()
    for path in ("/", "/week", "/history", "/me", "/workouts", "/heart", "/gym"):
        page = client.get(path)[1]
        assert "data-heart" in page and "/heart.js" in page, path
    client.post("/start", {})
    assert "/heart.js" in client.get("/session")[1]


def test_nothing_is_recorded_outside_a_session(client, app, store):
    client.set_up()
    choose(client)
    feed(client, "/feed/heart", {"person": "al", "bpm": 88})
    assert json.loads(client.get("/api/heart")[1])["bpm"] == 88
    assert store.rows("SELECT * FROM heart") == []


def test_a_reading_shows_is_kept_and_goes_stale(client, app, store, monkeypatch):
    client.set_up()
    choose(client)
    client.post("/start", {})
    session = json.loads(client.get("/api/session")[1])["id"]
    assert feed(client, "/feed/heart", {"person": "al", "bpm": 128})[0] == 200
    assert json.loads(client.get("/api/heart")[1]) == {"set": True, "bpm": 128, "unsure": False}
    assert store.heart_summary(session) == (128, 128)
    later = time.time() + heart_module.STALE_SECONDS + 1
    monkeypatch.setattr(heart_module.time, "time", lambda: later)
    assert json.loads(client.get("/api/heart")[1])["bpm"] is None


def test_readings_stay_with_their_person(client, other, app):
    client.set_up("al")
    other.set_up("bo")
    feed(client, "/feed/heart", {"person": "al", "bpm": 128})
    assert json.loads(other.get("/api/heart")[1])["bpm"] is None


@pytest.mark.parametrize(
    "data",
    [{"person": "al", "bpm": 0}, {"person": "al", "bpm": 400}, {"person": "x", "bpm": 90},
     {"bpm": 90}, [], {"person": "al", "bpm": "fast"}],
)
def test_impossible_readings_are_refused(client, app, data):
    assert feed(client, "/feed/heart", data)[0] == 400


def test_the_session_page_shows_the_summary(client, app, store):
    client.set_up()
    client.post("/start", {})
    session = json.loads(client.get("/api/session")[1])
    store.run("INSERT INTO heart VALUES (?, 1, 100), (?, 2, 140)", session["id"], session["id"])
    item = session["items"][0]
    events = [{"cid": "heart-set", "type": "set", "item": item["key"], "n": 0, "load": 10,
               "amount": 8}, {"cid": "heart-end", "type": "finish"}]
    client.api("/api/events", {"session": session["id"], "events": events})
    page = client.get(f"/history/{session['id']}")[1]
    assert "♥ 120" in page and "top 140" in page
    assert '"heart": [' in client.get("/export.json")[1]


# The receiver ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "bpm"),
    [(b"\x00\x48", 72), (b"\x01\x2c\x01", 300), (b"\x16\x50", 80), (b"\x06\x50", 80),
     (b"\x04\x50", None), (b"\x00", None), (b"", None), (b"\x01\x50", None),
     (b"\x10\x55\x00\x03", 85)],
)
def test_heart_rate_measurements_are_read(data, bpm):
    assert reps_heart.beats(data) == bpm


def test_a_source_is_known_by_address_or_name():
    assert reps_heart.matches(MONITOR, "", "aa:bb:cc:dd:ee:ff")
    assert reps_heart.matches(MONITOR, "Relay", "00:00:00:00:00:00")
    assert not reps_heart.matches(MONITOR, "Other", "00:00:00:00:00:00")
    assert not reps_heart.matches({"name": "", "address": ""}, "", "00:00:00:00:00:00")


def monitor(address, name="HeartCast-iPhon"):
    return SimpleNamespace(address=address), name


def test_one_monitor_with_the_name_is_chosen():
    wanted = {"person": "bo", "name": "HeartCast-iPhon", "address": "AA:AA"}
    only = monitor("BB:BB")
    assert reps_heart.choose(wanted, [only, monitor("CC:CC", "Lamp")], []) is only[0]


def test_two_monitors_with_the_name_are_refused_unless_the_address_settles_it():
    wanted = {"person": "bo", "name": "HeartCast-iPhon", "address": "AA:AA"}
    pair = [monitor("BB:BB"), monitor("CC:CC")]
    assert reps_heart.choose(wanted, pair, []) is None
    known = monitor("aa:aa")
    assert reps_heart.choose(wanted, [*pair, known], []) is known[0]


def test_one_monitor_two_people_could_claim_is_refused():
    wanted = {"person": "bo", "name": "HeartCast-iPhon", "address": "AA:AA"}
    rival = {"person": "al", "name": "HeartCast-iPhon", "address": "DD:DD"}
    assert reps_heart.choose(wanted, [monitor("BB:BB")], [rival]) is None


def test_the_session_is_told_when_a_source_is_unsure(client, app):
    client.set_up()
    choose(client)
    client.post("/start", {})
    feed(client, "/feed/unsure", {"person": "al"})
    assert json.loads(client.get("/api/heart")[1])["unsure"] is True
    feed(client, "/feed/heart", {"person": "al", "bpm": 90})
    assert json.loads(client.get("/api/heart")[1])["unsure"] is False


class FakeReps:
    def __init__(self):
        self.wanted = {"watch": [], "scan": False}
        self.sent = []

    async def send(self, path, data=None):
        if path == "/feed/wanted":
            return self.wanted
        self.sent.append((path, data))
        return {}


class FakeLink:
    def __init__(self, device):
        self.device, self.address, self.is_connected = device, device.address, False

    async def connect(self):
        self.is_connected = True

    async def start_notify(self, _characteristic, arrived):
        self.arrived = arrived

    async def disconnect(self):
        self.is_connected = False


class FakeScanner:
    looks = 0

    @classmethod
    async def discover(cls, timeout, return_adv):
        cls.looks += 1
        strap = SimpleNamespace(address=MONITOR["address"], name="Relay")
        lamp = SimpleNamespace(address="99:99:99:99:99:99", name="Lamp")
        service = [reps_heart.HEART_SERVICE]
        return {
            "strap": (strap, SimpleNamespace(local_name="Relay", service_uuids=service)),
            "lamp": (lamp, SimpleNamespace(local_name="Lamp", service_uuids=[])),
        }


def test_the_receiver_connects_passes_readings_on_and_lets_go():
    async def scene():
        reps = FakeReps()
        receiver = reps_heart.Receiver(reps, FakeScanner, FakeLink)
        FakeScanner.looks = 0
        await receiver.round()
        assert FakeScanner.looks == 0  # nobody is training, so the radio stays quiet
        reps.wanted = {"watch": [MONITOR | {"person": "al"}], "scan": False}
        await receiver.round()
        link = receiver.links["al"]
        link.arrived(None, bytearray(b"\x00\x60"))
        await asyncio.sleep(0.01)
        assert reps.sent == [("/feed/heart", {"person": "al", "bpm": 96})]
        await receiver.round()
        assert FakeScanner.looks == 1
        link.is_connected = False  # out of range: found again next round
        await receiver.round()
        assert receiver.links["al"] is not link
        reps.wanted = {"watch": [], "scan": False}
        await receiver.round()
        assert receiver.links == {}

    asyncio.run(scene())


def test_only_heart_rate_monitors_are_reported():
    async def scene():
        reps = FakeReps()
        reps.wanted = {"watch": [], "scan": True}
        await reps_heart.Receiver(reps, FakeScanner, FakeLink).round()
        assert reps.sent == [("/feed/nearby", {"devices": [MONITOR]})]

    asyncio.run(scene())


class Phones:
    """A scanner that sees phones all sharing HeartCast's name."""

    seen = ()

    @classmethod
    async def discover(cls, timeout, return_adv):
        service = [reps_heart.HEART_SERVICE]
        return {
            address: (SimpleNamespace(address=address, name="HeartCast-iPhon"),
                      SimpleNamespace(local_name="HeartCast-iPhon", service_uuids=service))
            for address in cls.seen
        }


def wants(**here):
    return {"scan": False, "watch": [
        {"person": person, "name": "HeartCast-iPhon", "address": "", "here": is_here}
        for person, is_here in here.items()
    ]}


def scene_of(*steps):
    """Run rounds of (who is here, phones in front, ...); return the receiver after each."""
    async def play():
        Phones.seen, reps = (), FakeReps()
        receiver = reps_heart.Receiver(reps, Phones, FakeLink)
        after = []
        for wanted, seen in steps:
            reps.wanted, Phones.seen = wanted, seen
            await receiver.round()
            after.append((sorted(receiver.links), sorted(receiver.held), receiver.quiet))
        return after

    return asyncio.run(play())


def test_the_app_opened_before_reps_is_found_and_claimed_once_they_are_here():
    assert scene_of(
        (wants(al=False, bo=False), ("AA",)),  # HeartCast in front, reps not open yet
        (wants(al=False, bo=True), ()),  # then reps, so HeartCast no longer announces itself
    ) == [([], ["HeartCast-iPhon"], True), (["bo"], [], False)]


def test_with_one_person_set_up_it_is_theirs_straight_away():
    assert scene_of((wants(bo=False), ("AA",))) == [(["bo"], [], True)]


def test_two_people_here_leave_a_shared_name_alone():
    assert scene_of(
        (wants(al=False, bo=False), ("AA",)),
        (wants(al=True, bo=True), ()),
        (wants(al=True, bo=False), ()),
    )[1:] == [([], ["HeartCast-iPhon"], False), (["al"], [], False)]


def test_two_phones_in_front_with_nobody_here_are_left_alone():
    assert scene_of((wants(al=False, bo=False), ("AA", "BB"))) == [([], [], True)]


def test_the_one_here_is_found_past_one_who_is_not():
    assert scene_of((wants(al=False, bo=True), ("AA",))) == [(["bo"], [], False)]


def test_a_silent_link_is_let_go_when_they_are_not_here(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(reps_heart.time, "monotonic", lambda: now[0])

    async def play():
        Phones.seen, reps = ("AA",), FakeReps()
        receiver = reps_heart.Receiver(reps, Phones, FakeLink)
        reps.wanted = wants(bo=False)
        await receiver.round()
        link, Phones.seen = receiver.links["bo"], ()
        now[0] += reps_heart.IDLE_SECONDS - 10
        link.arrived(None, bytearray(b"\x00\x60"))
        now[0] += reps_heart.IDLE_SECONDS - 10
        await receiver.round()
        assert "bo" in receiver.links  # it spoke
        reps.wanted = wants(bo=True)
        now[0] += reps_heart.IDLE_SECONDS * 3
        await receiver.round()
        assert "bo" in receiver.links  # they are here
        reps.wanted = wants(bo=False)
        await receiver.round()
        assert receiver.links == {} and not link.is_connected

    asyncio.run(play())


def test_a_held_phones_readings_go_to_nobody():
    async def play():
        Phones.seen, reps = ("AA",), FakeReps()
        receiver = reps_heart.Receiver(reps, Phones, FakeLink)
        reps.wanted = wants(al=False, bo=False)
        await receiver.round()
        link = receiver.held["HeartCast-iPhon"]
        link.arrived(None, bytearray(b"\x00\x60"))
        await asyncio.sleep(0.01)
        assert reps.sent == []
        reps.wanted = wants(al=False, bo=True)
        await receiver.round()
        link.arrived(None, bytearray(b"\x00\x61"))
        await asyncio.sleep(0.01)
        assert reps.sent == [("/feed/heart", {"person": "bo", "bpm": 97})]

    asyncio.run(play())
