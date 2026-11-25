import datetime as dt
import json

import pytest

from reps.coach import custom
from reps.coach.custom import clean_blocks, tag
from reps.coach.plan import Profile
from reps.library import EQUIPMENT
from reps.web import workout_pages

ROWER = {"options": ["rower"], "minutes": 15, "style": "steady", "level": 0}
ARMS = {"options": ["curl", "triceps-press"], "sets": 3}
SONGS = {"uri": "spotify:playlist:abc123", "name": "Songs"}
OTHERS = {"uri": "spotify:playlist:def456", "name": "Others"}


def made(client):
    client.set_up(equipment=("dumbbells", "rower", "bike"))
    base = client.post("/workouts", {"name": "Flow"})[0].headers["Location"]
    return base, base.split("/")[-1]


def arrange(client, number, blocks):
    return client.api(f"/api/workouts/{number}", {"blocks": blocks})


def test_blocks_are_kept_as_dragged(client, store):
    base, number = made(client)
    status, answer = arrange(client, number, [ARMS, ROWER | {"music": SONGS}])
    assert status == 200 and answer["blocks"] == [ARMS, ROWER | {"music": SONGS}]
    assert store.workout("al", int(number))["blocks"] == answer["blocks"]
    assert 'data-place="1"' in answer["flow"] and "Songs" in answer["flow"]
    assert "min" in answer["saved"]
    assert "Air rower" in client.get(base)[1]


@pytest.mark.parametrize(
    "blocks",
    [None, {}, [{}], [{"options": []}], [{"options": ["nonsense"]}], [{"options": "curl"}],
     [{"options": ["curl", "rower"]}], ["curl"], [ARMS] * (custom.MOST_BLOCKS + 1)],
)
def test_blocks_that_make_no_sense_are_refused(client, store, blocks):
    _base, number = made(client)
    arrange(client, number, [ARMS])
    assert arrange(client, number, blocks)[0] == 400
    assert store.workout("al", int(number))["blocks"] == [ARMS]


def test_odd_settings_are_made_sense_of():
    odd = {"options": ["bike", "bike"], "minutes": 999, "style": "x", "level": 99, "sets": 4,
           "music": {"uri": "javascript:alert(1)", "name": "x"}, "extra": 1}
    assert clean_blocks([odd]) == [
        {"options": ["bike"], "minutes": 60, "style": "steady", "level": 5}
    ]
    assert clean_blocks([{"options": ["curl"], "sets": "9", "minutes": 3}]) == [
        {"options": ["curl"], "sets": 3}
    ]


def test_a_block_takes_three_choices_at_most():
    many = {"options": ["curl", "triceps-press", "lateral-raise", "bent-row"], "sets": 2}
    assert len(clean_blocks([many])[0]["options"]) == custom.MOST_OPTIONS


def test_nobody_arranges_the_others_workout(client, other, store):
    _base, number = made(client)
    arrange(client, number, [ARMS])
    other.set_up("bo")
    assert arrange(other, number, [ROWER])[0] == 404
    assert store.workout("al", int(number))["blocks"] == [ARMS]
    body = json.dumps({"blocks": [ROWER]})
    headers = {"Origin": "https://evil.example"}
    assert client.send("POST", f"/api/workouts/{number}", body, headers)[0].status == 403


def test_names_are_never_markup(client):
    base, number = made(client)
    bad = {"uri": "spotify:playlist:abc", "name": "</script><script>alert(1)</script>"}
    _status, answer = arrange(client, number, [ROWER | {"music": bad}])
    assert "<script>alert" not in answer["flow"]
    assert "<script>alert" not in client.get(base)[1]


def test_a_block_is_added_between_two(client, store):
    base, number = made(client)
    arrange(client, number, [ROWER, ARMS])
    page = client.get(f"{base}/add/cardio?at=1")[1]
    assert 'name="at" value="1"' in page
    client.post(f"{base}/add", {"key": "bike", "at": "1"})
    kept = [each["options"][0] for each in store.workout("al", int(number))["blocks"]]
    assert kept == ["rower", "bike", "curl"]


# Playlists on blocks ----------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "tagged"),
    [("one", [False, True, False]), ("on", [False, True, True]), ("all", [True, True, True])],
)
def test_a_playlist_goes_on_a_block_from_it_on_or_throughout(scope, tagged):
    blocks = [dict(ROWER), dict(ARMS), dict(ROWER)]
    tag(blocks, 1, SONGS, scope)
    assert ["music" in block for block in blocks] == tagged
    tag(blocks, 1, None, "all")
    assert not any("music" in block for block in blocks)


@pytest.fixture
def linked(server, monkeypatch):
    app = server.RequestHandlerClass.app
    app.spotify.client_id = "client"
    app.store.save_spotify_token("al", "token")
    app.played = []
    monkeypatch.setattr(app.spotify, "playlists", lambda _who: [SONGS, OTHERS])
    monkeypatch.setattr(app.spotify, "song", lambda _who: {"playing": True})
    monkeypatch.setattr(
        app.spotify, "play", lambda _who, uri, _device="": app.played.append(uri[-6:])
    )
    return app


def test_playlists_are_tagged_through_the_page(client, linked, store):
    base, number = made(client)
    arrange(client, number, [ROWER, ARMS, ROWER])
    assert "Others" in client.get(f"{base}/block/1/music")[1]
    tapped = {"add": SONGS["uri"], "scope": "on", "link": ""}  # as a browser sends it
    client.post(f"{base}/block/1/music", tapped)
    client.post(f"{base}/block/2/music", {"add": OTHERS["uri"], "scope": "one"})

    def names():
        blocks = store.workout("al", int(number))["blocks"]
        return [block.get("music", {}).get("name") for block in blocks]

    assert names() == [None, "Songs", "Others"]
    where = client.post(f"{base}/block/0/music", {"add": "spotify:playlist:stranger"})[0]
    assert "problem" in where.headers["Location"] and names()[0] is None
    client.post(f"{base}/block/1/music", {"clear": "1", "scope": "all"})
    assert names() == [None, None, None]


def test_a_band_joins_blocks_with_the_same_playlist():
    workout = {"id": 1, "blocks": [ROWER | {"music": SONGS}, ARMS | {"music": SONGS},
                                   ROWER | {"music": OTHERS}]}
    drawn = workout_pages.flow(workout, custom.build(
        Profile(equipment=frozenset(EQUIPMENT)), "x", workout["blocks"], True, {},
        dt.date(2026, 11, 24)))
    assert drawn.count(" joins") == 1 and drawn.count("class=\"tag\"") == 2


def test_a_blocks_playlist_plays_over_the_mix_and_the_mix_comes_back(client, linked, store):
    _base, number = made(client)
    arrange(client, number, [ROWER, ARMS | {"music": SONGS}, ROWER])
    mix = store.add_mix("al", "Gym")
    store.save_mix("al", store.mix("al", mix) | {"stages": {"all": [OTHERS]}})
    store.use_mix("al", mix)
    client.post(f"/workouts/{number}/start", {"counts": "1"})
    session = json.loads(client.get("/api/session")[1])

    def log(item, sets):
        events = [{"cid": f"music-{item['key']}-{n}", "type": "set", "item": item["key"], "n": n,
                   "load": item["sets"][n][0], "amount": 8} for n in range(sets)]
        if item["kind"] != "cardio":
            events.append({"cid": f"music-rir-{item['key']}", "type": "rir",
                           "item": item["key"], "value": 2})
        assert client.api("/api/events", {"session": session["id"], "events": events})[0] == 200

    client.get("/api/music")
    log(session["items"][0], 1)
    client.get("/api/music")
    client.get("/api/music")
    log(session["items"][1], len(session["items"][1]["sets"]))
    client.get("/api/music")
    assert linked.played == ["def456", "abc123", "def456"]


def test_a_tagged_workout_plays_with_no_mix_at_all(client, linked):
    _base, number = made(client)
    arrange(client, number, [ROWER | {"music": SONGS}, ARMS])
    client.post(f"/workouts/{number}/start", {})
    client.get("/api/music")
    assert linked.played == ["abc123"]
