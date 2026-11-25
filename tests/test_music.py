import json

import pytest

from reps import conductor as module
from reps.coach.stages import playlist_for, stage_of
from reps.conductor import OPEN_SPOTIFY, REFUSED, Conductor
from reps.spotify import FailedError, NoDeviceError, RefusedError, Spotify

EASY, FAST, OTHER = ({"uri": f"spotify:playlist:{name}", "name": name}
                     for name in ("easy", "fast", "other"))
MIX = {"all": [EASY], "flatout": [FAST]}


def item(key, kind="load", sets=2):
    return {"key": key, "kind": kind, "sets": [[10, 8]] * sets}


SESSION = [item("squat"), item("rower", "cardio", 1), item("finisher-bike", "cardio", 1),
           item("flow", "cardio", 1)]


@pytest.mark.parametrize(
    ("sets", "states", "stage"),
    [({}, {}, "warmup"),
     ({"squat": 1}, {}, "lifting"),
     ({"squat": 2}, {}, "lifting"),  # effort not answered yet
     ({"squat": 2}, {"squat": {"rir": 2}}, "steady"),
     ({}, {"squat": {"skipped": 1}}, "steady"),
     ({"squat": 2, "rower": 1}, {"squat": {"rir": 2}}, "flatout"),
     ({"squat": 2, "rower": 1, "finisher-bike": 1}, {"squat": {"rir": 2}}, "cooldown"),
     ({"squat": 2, "rower": 1, "finisher-bike": 1, "flow": 1}, {"squat": {"rir": 0}},
      "cooldown")],
)
def test_the_stage_follows_the_session(sets, states, stage):
    assert stage_of(SESSION, sets, states) == stage


def test_a_second_rower_block_and_a_circuit():
    assert stage_of([item("rower#2", "cardio", 1)], {}, {}) == "steady"
    assert stage_of([item("circuit", "cardio", 1)], {}, {}) == "flatout"


def test_a_stage_takes_its_own_playlist_else_the_whole_sessions_in_turn():
    assert playlist_for(MIX, "flatout", 0) == FAST
    assert playlist_for(MIX, "lifting", 0) == EASY
    assert playlist_for({"flatout": [FAST]}, "lifting", 0) is None
    turns = [playlist_for({"all": [EASY, OTHER]}, "steady", turn) for turn in range(3)]
    assert turns == [EASY, OTHER, EASY]


class FakeSpotify:
    def __init__(self):
        self.played, self.trouble, self.active = [], None, True

    def play(self, _person, uri, device=""):
        if self.trouble:
            raise self.trouble
        if not device and not self.active:
            raise NoDeviceError
        self.played.append(uri.split(":")[-1] + (f"@{device}" if device else ""))


@pytest.fixture
def scene(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    spotify = FakeSpotify()
    leader = Conductor(spotify)

    def follow(stage, playing=True, session=1, mix=MIX, device=""):
        wanted = playlist_for(mix, stage, session)
        return leader.follow("al", session, stage, wanted, playing, device)

    return follow, spotify, now


def test_it_starts_and_changes_with_the_stage_only(scene):
    follow, spotify, _now = scene
    for stage in ("warmup", "warmup", "lifting", "lifting", "flatout", "flatout", "cooldown"):
        follow(stage)
    assert spotify.played == ["easy", "fast", "easy"]  # lifting shares warm-up's playlist


def test_a_single_playlist_starts_once(scene):
    follow, spotify, _now = scene
    for stage in ("warmup", "lifting", "flatout", "cooldown"):
        follow(stage, mix={"all": [EASY]})
    assert spotify.played == ["easy"]


def test_music_they_stopped_stays_stopped_until_they_resume(scene):
    follow, spotify, _now = scene
    follow("lifting")
    follow("flatout", playing=False)
    assert spotify.played == ["easy"]
    follow("flatout", playing=True)
    assert spotify.played == ["easy", "fast"]


def test_with_nothing_open_it_says_so_and_tries_again(scene):
    follow, spotify, now = scene
    spotify.trouble = NoDeviceError()
    assert follow("warmup", playing=False) == OPEN_SPOTIFY
    spotify.trouble = None
    assert follow("warmup", playing=False) == OPEN_SPOTIFY  # not asked again yet
    now[0] += module.DEVICE_RETRY_SECONDS
    assert follow("warmup", playing=False) == "" and spotify.played == ["easy"]


def test_music_starts_on_the_chosen_device_then_stays_where_they_have_it(scene):
    follow, spotify, _now = scene
    follow("warmup", playing=False, device="Gym")
    follow("flatout", device="Gym")
    spotify.active = False
    follow("cooldown", device="Gym")
    assert spotify.played == ["easy@Gym", "fast", "easy@Gym"]


def test_a_chosen_device_that_is_missing_is_named_and_nothing_plays_elsewhere(scene):
    follow, spotify, _now = scene
    spotify.trouble = NoDeviceError()
    assert follow("warmup", device="Gym") == "Spotify can't see Gym"
    assert spotify.played == []


def test_a_failure_is_quiet_and_tried_again(scene):
    follow, spotify, now = scene
    spotify.trouble = FailedError()
    assert follow("warmup") == ""
    spotify.trouble = None
    now[0] += module.DEVICE_RETRY_SECONDS
    follow("warmup")
    assert spotify.played == ["easy"]


def test_a_refusal_is_said_and_not_repeated(scene):
    follow, spotify, _now = scene
    spotify.trouble = RefusedError()
    assert follow("warmup") == REFUSED
    spotify.trouble = None
    follow("warmup")
    assert spotify.played == []


def test_a_new_session_starts_afresh(scene):
    follow, spotify, _now = scene
    follow("warmup")
    follow("warmup", playing=False, session=2)
    assert spotify.played == ["easy", "easy"]


# Spotify's side --------------------------------------------------------------


def service(answers, sent):
    def send(request):
        sent.append((request.get_method(), request.full_url, request.data))
        if "api/token" in request.full_url:
            return 200, b'{"access_token": "a", "expires_in": 3600}', {}
        return answers.pop(0)

    return Spotify("client", "https://reps/x", lambda _who: "r", lambda _who, _token: None, send)


@pytest.mark.parametrize(
    ("status", "error"),
    [(204, None), (404, NoDeviceError), (403, RefusedError), (429, FailedError),
     (500, FailedError)],
)
def test_starting_a_playlist(status, error):
    sent = []
    spotify = service([(status, b"", {})], sent)
    if error:
        with pytest.raises(error):
            spotify.play("al", EASY["uri"])
    else:
        spotify.play("al", EASY["uri"])
    method, url, data = sent[-1]
    assert (method, url) == ("PUT", "https://api.spotify.com/v1/me/player/play")
    assert json.loads(data) == {"context_uri": EASY["uri"]}


def test_playlists_come_in_pages_and_odd_ones_are_left_out():
    first = {"items": [{**EASY}, None, {"uri": "spotify:album:1", "name": "x"}], "next": "more"}
    last = {"items": [{**FAST, "name": ""}], "next": None}
    sent = []
    spotify = service([(200, json.dumps(each).encode(), {}) for each in (first, last)], sent)
    assert spotify.playlists("al") == [EASY, {**FAST, "name": "Untitled"}]
    spotify.playlists("al")
    assert len(sent) == 3  # a token and two pages, then from memory


def test_devices_leave_out_those_it_may_not_control():
    body = {"devices": [
        {"id": "1", "name": "Gym", "is_active": True, "is_restricted": False},
        {"id": "2", "name": "Car", "is_restricted": True},
        {"id": None, "name": "Ghost", "is_restricted": False},
    ]}
    spotify = service([(200, json.dumps(body).encode(), {})], [])
    assert spotify.devices("al") == [{"id": "1", "name": "Gym", "active": True}]


def test_playing_on_a_device_finds_its_id_by_name():
    body = json.dumps({"devices": [{"id": "a b", "name": "Gym"}]}).encode()
    sent = []
    service([(200, body, {}), (204, b"", {})], sent).play("al", EASY["uri"], "Gym")
    assert sent[-1][1] == "https://api.spotify.com/v1/me/player/play?device_id=a+b"
    with pytest.raises(NoDeviceError):
        service([(200, body, {})], []).play("al", EASY["uri"], "Kitchen")


def test_a_link_made_before_the_permission_is_refused():
    with pytest.raises(RefusedError):
        service([(403, b"{}", {})], []).devices("al")


LINK = "https://open.spotify.com/playlist/37i9dQZF1FwPcjAujYI8WH?si=abc"
NAMED = "spotify:playlist:37i9dQZF1FwPcjAujYI8WH"


@pytest.mark.parametrize(
    "link", [LINK, NAMED, f"  {LINK}", LINK.replace(".com/", ".com/intl-fr/")]
)
def test_a_pasted_link_opens_a_playlist_the_list_leaves_out(link):
    sent = []
    body = json.dumps({"name": "Most Played", "uri": NAMED}).encode()
    spotify = service([(200, body, {})], sent)
    assert spotify.playlist("al", link) == {"uri": NAMED, "name": "Most Played"}
    assert sent[-1][1].startswith("https://api.spotify.com/v1/playlists/37i9dQZF1FwPcjAujYI8WH?")


@pytest.mark.parametrize(
    "link",
    ["", "hello", "https://evil.example/playlist/37i9dQZF1FwPcjAujYI8WH",
     "https://open.spotify.com/track/37i9dQZF1FwPcjAujYI8WH",
     "https://open.spotify.com/playlist/../me", "spotify:playlist:short"],
)
def test_other_links_are_never_asked_about(link):
    sent = []
    assert service([], sent).playlist("al", link) is None and sent == []


@pytest.mark.parametrize("status", [403, 404, 429, 500])
def test_a_playlist_spotify_will_not_open(status):
    assert service([(status, b"{}", {})], []).playlist("al", LINK) is None


# Through the pages -----------------------------------------------------------


@pytest.fixture
def app(server, monkeypatch):
    app = server.RequestHandlerClass.app
    app.spotify.client_id = "client"
    app.store.save_spotify_token("al", "token")
    app.store.save_spotify_token("bo", "token")
    many = [EASY, FAST, OTHER] + [{"uri": f"spotify:playlist:{n}", "name": f"List {n}"}
                                  for n in range(10)]
    app.played = []
    monkeypatch.setattr(app.spotify, "playlists", lambda _who: many)
    monkeypatch.setattr(app.spotify, "song", lambda _who: None)
    monkeypatch.setattr(
        app.spotify, "play", lambda who, uri, device="": app.played.append((who, uri))
    )
    return app


def built(client, name="Gym"):
    base = client.post("/music", {"name": name})[0].headers["Location"]
    client.post(f"{base}/all", {"add": EASY["uri"]})
    client.post(f"{base}/flatout", {"add": FAST["uri"]})
    return base


def test_a_mix_is_built_saved_and_used(client, app, store):
    client.set_up()
    base = built(client)
    page = client.get(base)[1]
    assert "easy" in page and "fast" in page and "Same as" in page
    assert "List 9" in client.get(f"{base}/all/add")[1]
    client.post("/music/usual", {"mix": base.split("/")[-1]})
    assert '<span class="pill ok">On</span>' in client.get("/music")[1]
    client.post("/start", {})
    client.get("/api/music")
    assert app.played == [("al", EASY["uri"])] * 2  # on choosing it, and as the session began


def test_music_is_off_until_chosen_and_can_be_turned_off(client, app):
    client.set_up()
    base = built(client)
    client.post("/start", {})
    client.get("/api/music")
    assert app.played == []
    client.post("/music/usual", {"mix": base.split("/")[-1]})
    client.post("/music/usual", {"mix": ""})
    assert "Music off" not in client.get("/music")[1]


def test_a_workout_can_have_its_own_mix_or_none(client, app, store):
    client.set_up()
    number = int(built(client).split("/")[-1])
    workout = store.add_workout("al", "Row")
    client.post(f"/workouts/{workout}/settings", {"name": "Row", "mix": str(number)})
    assert store.mix_for("al", store.workout("al", workout)) == number
    client.post("/music/usual", {"mix": str(number)})
    client.post(f"/workouts/{workout}/settings", {"name": "Row", "mix": "off"})
    assert store.mix_for("al", store.workout("al", workout)) is None
    client.post(f"/workouts/{workout}/settings", {"name": "Row", "mix": "999"})
    assert store.mix_for("al", store.workout("al", workout)) == number
    client.post(f"/music/{number}/delete")
    assert store.mix_for("al") is None


def test_only_the_persons_own_playlists_go_in_three_to_a_stage(client, app, store):
    client.set_up()
    base = built(client)
    client.post(f"{base}/all", {"add": "spotify:playlist:stranger"})
    for n in range(5):
        client.post(f"{base}/all", {"add": f"spotify:playlist:{n}"})
    stages = store.mix("al", int(base.split("/")[-1]))["stages"]
    assert [each["name"] for each in stages["all"]] == ["easy", "List 0", "List 1"]
    client.post(f"{base}/all", {"drop": EASY["uri"]})
    assert len(store.mix("al", int(base.split("/")[-1]))["stages"]["all"]) == 2
    assert client.get(f"{base}/nonsense")[0].status == 404


def test_mixes_are_each_persons_own(client, other, app, store):
    client.set_up("al")
    base = built(client)
    other.set_up("bo")
    assert other.get(base)[0].status == 404
    assert other.post(f"{base}/all", {"add": FAST["uri"]})[0].status == 404
    assert other.post(f"{base}/delete")[0].status == 404
    other.post("/music/usual", {"mix": base.split("/")[-1]})
    assert store.person("bo")["mix"] is None


def test_a_link_is_pasted_into_a_stage(client, app, store, monkeypatch):
    client.set_up()
    base = built(client)
    found = {"uri": NAMED, "name": "Most Played"}
    known = {LINK: found}
    monkeypatch.setattr(app.spotify, "playlist", lambda _who, link: known.get(link))
    where = client.post(f"{base}/flatout", {"link": LINK})[0].headers["Location"]
    assert where == f"{base}/flatout"
    assert "Most Played" in client.get(f"{base}/flatout")[1]
    where = client.post(f"{base}/flatout", {"link": "nonsense"})[0].headers["Location"]
    assert "open that" in client.get(where)[1]
    stages = store.mix("al", int(base.split("/")[-1]))["stages"]
    assert [each["name"] for each in stages["flatout"]] == ["fast", "Most Played"]


def test_a_device_is_chosen_from_those_showing(client, app, store, monkeypatch):
    client.set_up()
    gym = {"id": "1", "name": "Gym", "active": False}
    monkeypatch.setattr(app.spotify, "devices", lambda _who: [gym])
    assert "Use this" in client.get("/spotify/devices")[1]
    client.post("/spotify/devices", {"device": "Stranger"})
    assert store.person("al")["speaker"] == ""
    client.post("/spotify/devices", {"device": "Gym"})
    assert store.person("al")["speaker"] == "Gym"
    assert "Music starts on Gym" in client.get("/spotify")[1]
    monkeypatch.setattr(app.spotify, "devices", lambda _who: [])
    assert "Not showing now" in client.get("/spotify/devices")[1]
    client.post("/spotify/devices", {"device": ""})
    assert store.person("al")["speaker"] == ""


def test_an_old_link_is_asked_to_link_again(client, app, monkeypatch):
    client.set_up()

    def refuse(_who):
        raise RefusedError

    monkeypatch.setattr(app.spotify, "devices", refuse)
    assert "Link again" in client.get("/spotify/devices")[1]


# The widget -------------------------------------------------------------------


@pytest.fixture
def player(app, monkeypatch):
    """A pretend player: what is on, and the commands it was given."""
    app.song, app.commands, app.settled = None, [], []
    monkeypatch.setattr(app.spotify, "song", lambda _who: app.song)
    monkeypatch.setattr(app.spotify, "move", lambda _who, name: app.commands.append(f"to {name}"))
    monkeypatch.setattr(app.spotify, "settle", lambda _who, _before: app.settled.append(_before))
    monkeypatch.setattr(app.spotify, "following", lambda _who, _song: None)
    monkeypatch.setattr(app.spotify, "seek", lambda _who, by: app.commands.append(f"seek {by}"))
    for name in ("pause", "resume", "skip", "previous"):
        monkeypatch.setattr(app.spotify, name, lambda _who, name=name: app.commands.append(name))
    return app


def press(client, action, **more):
    return client.api("/api/music", {"action": action, **more})


def song(playing):
    return {"title": "Song", "by": "One", "link": "https://open.spotify.com/track/1", "art": "",
            "playing": playing}


def test_every_screen_has_the_widget(client, player):
    client.set_up()
    for path in ("/", "/week", "/history", "/me", "/workouts", "/music", "/heart", "/setup/goals"):
        page = client.get(path)[1]
        assert "data-music" in page and "/music.js" in page, path
    client.post("/start", {})
    assert "/music.js" in client.get("/session")[1]


def test_only_a_changed_song_is_waited_for(client, player):
    client.set_up()
    player.song = song(True)
    for action in ("toggle", "back", "forward"):
        press(client, action)
    assert player.settled == []
    press(client, "next")
    press(client, "previous")
    assert player.settled == [player.song, player.song]


def test_back_and_forward(client, player):
    client.set_up()
    player.song = song(True)
    press(client, "back")
    press(client, "forward")
    press(client, "previous")
    assert player.commands == ["seek -15", "seek 15", "previous"] and player.played == []


def test_play_pause_and_next(client, player):
    client.set_up()
    player.song = song(True)
    press(client, "toggle")
    player.song = song(False)
    press(client, "toggle")
    press(client, "next")
    assert player.commands == ["pause", "resume", "skip"] and player.played == []


def test_play_with_nothing_on_starts_the_mix(client, player):
    client.set_up()
    number = int(built(client).split("/")[-1])
    press(client, "mix", mix=number)
    press(client, "toggle")
    assert player.played == [("al", EASY["uri"])] * 2 and player.commands == []


def test_play_in_a_session_starts_its_stage_again(client, player):
    client.set_up()
    press(client, "mix", mix=int(built(client).split("/")[-1]))
    client.post("/start", {})
    client.get("/api/music")
    press(client, "toggle")
    assert player.played == [("al", EASY["uri"])] * 3  # chosen, session begun, pressed


def test_choosing_a_mix_mid_session_changes_the_music(client, player):
    client.set_up()
    client.post("/start", {})
    assert press(client, "mix", mix=int(built(client).split("/")[-1]))[1]["mix"]["name"] == "Gym"
    assert player.played == [("al", EASY["uri"])]
    assert press(client, "mix", mix=None)[1]["mix"] is None


def test_a_mix_that_is_not_theirs_is_ignored(client, other, player):
    client.set_up("al")
    number = int(built(client).split("/")[-1])
    other.set_up("bo")
    assert press(other, "mix", mix=number)[1]["mix"] is None


@pytest.mark.parametrize(
    ("trouble", "note"),
    [(NoDeviceError, OPEN_SPOTIFY), (RefusedError, "Spotify won't do that just now"),
     (FailedError, "Spotify didn't answer")],
)
def test_the_widget_says_what_went_wrong(client, player, monkeypatch, trouble, note):
    client.set_up()

    def fail(_who):
        raise trouble

    monkeypatch.setattr(player.spotify, "skip", fail)
    assert press(client, "next")[1]["note"] == note


@pytest.mark.parametrize("sent", [{"action": "dance"}, {}, {"action": "mix", "mix": "x"}])
def test_odd_controls_are_refused(client, player, sent):
    client.set_up()
    assert client.api("/api/music", sent)[0] == 400


def test_the_widget_needs_sign_in_and_its_own_site(client, player):
    assert client.api("/api/music", {"action": "next"})[0] == 401
    client.set_up()
    body = json.dumps({"action": "next"})
    response, _ = client.send("POST", "/api/music", body, {"Origin": "https://evil.example"})
    assert response.status == 403 and player.commands == []


def test_choosing_a_mix_starts_it_on_the_chosen_device(client, player, store, monkeypatch):
    client.set_up()
    started = []
    monkeypatch.setattr(
        player.spotify, "play", lambda _who, uri, device="": started.append((uri, device))
    )
    store.save_person("al", speaker="Office")
    number = int(built(client).split("/")[-1])
    press(client, "mix", mix=number)
    client.post("/music/usual", {"mix": str(number)})
    assert started == [(EASY["uri"], "Office")] * 2
    press(client, "mix", mix=None)
    assert len(started) == 2


def test_a_mix_that_cannot_start_says_why(client, player, store, monkeypatch):
    client.set_up()

    def missing(*_):
        raise NoDeviceError

    monkeypatch.setattr(player.spotify, "play", missing)
    store.save_person("al", speaker="Office")
    number = built(client).split("/")[-1]
    where = client.post("/music/usual", {"mix": number})[0].headers["Location"]
    assert "t see Office" in client.get(where)[1]


def test_choosing_a_device_moves_what_is_playing(client, player, store, monkeypatch):
    client.set_up()
    office = {"id": "1", "name": "Office", "active": False}
    monkeypatch.setattr(player.spotify, "devices", lambda _who: [office])
    client.post("/spotify/devices", {"device": "Office"})
    assert player.commands == []
    player.song = song(True)
    client.post("/spotify/devices", {"device": "Office"})
    assert player.commands == ["to Office"] and store.person("al")["speaker"] == "Office"


def test_moving_finds_the_device_and_leaves_an_active_one_alone():
    def listing(active):
        return json.dumps({"devices": [{"id": "7", "name": "Gym", "is_active": active}]}).encode()

    sent = []
    service([(200, listing(False), {}), (204, b"", {})], sent).move("al", "Gym")
    assert sent[-1][:2] == ("PUT", "https://api.spotify.com/v1/me/player")
    assert json.loads(sent[-1][2]) == {"device_ids": ["7"]}
    sent = []
    service([(200, listing(True), {})], sent).move("al", "Gym")
    assert len(sent) == 2
