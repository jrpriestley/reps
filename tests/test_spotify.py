import json
import urllib.parse

import pytest

from reps import spotify as module
from reps.spotify import DEFAULT_WAIT, FailedError, Spotify, song_from

TRACK = {
    "is_playing": True,
    "item": {
        "type": "track", "name": "Song", "artists": [{"name": "One"}, {"name": "Two"}],
        "external_urls": {"spotify": "https://open.spotify.com/track/1"},
        "album": {"images": [
            {"url": "https://i.scdn.co/image/big", "width": 640},
            {"url": "https://i.scdn.co/image/small", "width": 64},
        ]},
    },
}
SHOWN = {"title": "Song", "by": "One, Two", "link": "https://open.spotify.com/track/1",
         "art": "https://i.scdn.co/image/small", "playing": True}


class FakeSpotify:
    """Spotify's two addresses, answering from lists."""

    def __init__(self):
        self.token = (200, {"access_token": "a1", "refresh_token": "r1", "expires_in": 3600})
        self.playing = [(200, TRACK, {})]
        self.asked, self.forms = [], []

    def __call__(self, request):
        if request.full_url == module.TOKEN:
            self.forms.append(dict(urllib.parse.parse_qsl(request.data.decode())))
            status, body = self.token
            return status, json.dumps(body).encode(), {}
        self.asked.append(request.headers["Authorization"])
        status, body, headers = self.playing[0] if len(self.playing) == 1 else self.playing.pop(0)
        return status, b"" if body is None else json.dumps(body).encode(), headers


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    return now


@pytest.fixture
def linked(clock):
    tokens = {"al": "r0"}
    fake = FakeSpotify()

    def save(person, token):
        tokens[person] = token

    service = Spotify("client", "https://reps/spotify/callback", lambda who: tokens.get(who, ""),
                      save, fake)
    return service, fake, tokens


def test_a_song_is_shown_with_its_smallest_artwork():
    assert song_from(TRACK) == SHOWN


@pytest.mark.parametrize(
    "playing",
    [{}, {"is_playing": True, "item": None},
     {"is_playing": True, "currently_playing_type": "ad"}],
)
def test_nothing_playing_shows_nothing(playing):
    assert song_from(playing) is None


def test_a_paused_song_still_shows():
    assert song_from(TRACK | {"is_playing": False}) == SHOWN | {"playing": False}


def test_episodes_and_local_files():
    episode = {"is_playing": True, "item": {
        "type": "episode", "name": "Part 1", "show": {"name": "A show"},
        "images": [{"url": "https://elsewhere.example/x", "width": 64}],
        "external_urls": {"spotify": "javascript:alert(1)"}}}
    assert song_from(episode) == {"title": "Part 1", "by": "A show", "art": "",
                                  "link": "https://open.spotify.com/", "playing": True}


def test_spotify_is_asked_at_most_every_ten_seconds(linked, clock):
    service, fake, _tokens = linked
    for _ in range(5):
        assert service.song("al") == SHOWN
    assert len(fake.asked) == 1
    clock[0] += module.ASK_SECONDS
    service.song("al")
    assert len(fake.asked) == 2
    assert len(fake.forms) == 1  # one access token does for the hour


def test_a_new_refresh_token_is_kept_and_none_keeps_the_old(linked, clock):
    service, fake, tokens = linked
    service.song("al")
    assert tokens["al"] == "r1"
    fake.token = (200, {"access_token": "a2", "expires_in": 3600})
    clock[0] += 3600
    service.song("al")
    assert tokens["al"] == "r1" and fake.asked[-1] == "Bearer a2"


def test_a_refused_access_token_is_renewed_once(linked):
    service, fake, _tokens = linked
    fake.playing = [(401, {}, {}), (200, TRACK, {})]
    assert service.song("al") == SHOWN
    assert len(fake.forms) == 2


def test_told_to_slow_down_it_waits_as_long_as_told(linked, clock):
    service, fake, _tokens = linked
    fake.playing = [(429, {}, {"Retry-After": "120"})]
    assert service.song("al") is None
    clock[0] += 119
    service.song("al")
    assert len(fake.asked) == 1
    clock[0] += 1
    service.song("al")
    assert len(fake.asked) == 2


@pytest.mark.parametrize("answer", [(204, None, {}), (403, {}, {}), (500, {}, {})])
def test_other_answers_hide_the_song(linked, answer):
    service, fake, tokens = linked
    fake.playing = [answer]
    assert service.song("al") is None and tokens["al"]


def test_no_connection_hides_the_song_and_waits(linked, clock):
    service, fake, _tokens = linked
    calls = []

    def down(request):
        calls.append(request)
        raise FailedError

    service.send = down
    assert service.song("al") is None
    clock[0] += DEFAULT_WAIT - 1
    service.song("al")
    assert len(calls) == 1


def test_a_link_spotify_ended_is_forgotten(linked):
    service, fake, tokens = linked
    fake.token = (400, {"error": "invalid_grant"})
    assert service.song("al") is None
    assert tokens["al"] == "" and fake.asked == []


def test_linking_checks_the_state_the_person_and_the_time(linked, clock):
    service, fake, tokens = linked
    tokens.clear()

    def start(person="al"):
        query = urllib.parse.urlsplit(service.link_address(person)).query
        return dict(urllib.parse.parse_qsl(query))

    asked = start()
    assert asked["scope"] == module.SCOPE
    assert asked["code_challenge_method"] == "S256" and asked["show_dialog"] == "true"
    assert not service.linked("al", "made-up", "code")
    assert not service.linked("bo", start()["state"], "code")
    late = start()["state"]
    clock[0] += module.LINK_SECONDS + 1
    assert not service.linked("al", late, "code")
    state = start()["state"]
    assert service.linked("al", state, "code") and tokens == {"al": "r1"}
    assert fake.forms[-1]["client_id"] == "client" and "client_secret" not in fake.forms[-1]
    assert not service.linked("al", state, "code")  # once only


# Through the pages -----------------------------------------------------------


@pytest.fixture
def app(server):
    app = server.RequestHandlerClass.app
    app.spotify.client_id, app.spotify.send = "client", FakeSpotify()
    return app


def link(client):
    where = client.get("/spotify/link")[0].headers["Location"]
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(where).query))["state"]
    return client.get(f"/spotify/callback?code=abc&state={state}")[0].headers["Location"]


def test_link_play_and_disconnect(client, app, store):
    client.set_up()
    assert json.loads(client.get("/api/music")[1])["song"] is None
    assert link(client) == "/spotify"
    assert "Linked." in client.get("/spotify")[1]
    assert json.loads(client.get("/api/music")[1])["song"] == SHOWN
    client.post("/spotify/unlink")
    assert store.spotify_token("al") == ""
    assert json.loads(client.get("/api/music")[1])["song"] is None


def test_a_refusal_at_spotify_says_so(client, app):
    client.set_up()
    where = client.get("/spotify/callback?error=access_denied")[0].headers["Location"]
    assert where == "/spotify?failed=1" and "didn't link" in client.get(where)[1].replace(
        "&#x27;", "'")


def test_songs_and_tokens_stay_with_their_person(client, other, app):
    client.set_up("al")
    link(client)
    other.set_up("bo")
    assert json.loads(other.get("/api/music")[1])["song"] is None
    assert "r1" not in client.get("/export.json")[1]


def test_without_a_client_id_it_says_so(client, server):
    client.set_up()
    assert "Not set up on homehub" in client.get("/spotify")[1]
    assert client.get("/spotify/link")[0].status == 404


def test_the_policy_lets_in_artwork_and_nothing_else(client):
    policy = client.get("/")[0].headers["Content-Security-Policy"]
    assert "img-src 'self' https://i.scdn.co;" in policy and policy.count("https://") == 1


# Going back and forward -------------------------------------------------------------


@pytest.mark.parametrize(("at", "by", "asked"), [
    (60_000, 15, f"{module.SEEK}75000"), (60_000, -15, f"{module.SEEK}45000"),
    (4_000, -15, f"{module.SEEK}0"), (None, 15, f"{module.SEEK}15000"),
    (190_000, 15, module.NEXT),
])
def test_seeking_stays_inside_the_song(linked, at, by, asked):
    service, spotify, _ = linked
    where = {"progress_ms": at, "item": {"duration_ms": 200_000}}
    sent = []

    def send(request):
        if request.full_url in (module.TOKEN, module.PLAYING):
            spotify.playing = [(200, where, {})]
            return spotify(request)
        sent.append(request.full_url)
        return 204, b"", {}

    service.send = send
    service.seek("al", by)
    assert sent == [asked]


@pytest.mark.parametrize(("answer", "problem"), [
    ((204, None, {}), module.NoDeviceError), ((403, {}, {}), module.RefusedError),
    ((500, {}, {}), module.FailedError), ((200, {"item": None}, {}), module.NoDeviceError),
])
def test_seeking_with_nothing_to_seek_says_so(linked, answer, problem):
    service, spotify, _ = linked
    spotify.playing = [answer]
    with pytest.raises(problem):
        service.seek("al", 15)


# Keeping up -------------------------------------------------------------------------


def playing_for(seconds_left, name="One", link="1"):
    return {
        "is_playing": True, "progress_ms": 200_000 - seconds_left * 1000,
        "item": {"type": "track", "name": name, "artists": [], "duration_ms": 200_000,
                 "external_urls": {"spotify": f"https://open.spotify.com/track/{link}"}},
    }


def test_it_asks_again_soon_and_sooner_as_a_song_ends(linked, clock):
    service, spotify, _ = linked
    spotify.playing = [(200, playing_for(60), {}), (200, playing_for(1), {}),
                       (200, playing_for(90, "Two", "2"), {})]
    assert service.song("al")["title"] == "One"
    assert service.ends_in("al") == pytest.approx(60.4)
    clock[0] += 2.9
    service.song("al")
    assert len(spotify.asked) == 1
    clock[0] += 0.2
    service.song("al")
    clock[0] += 1.5
    assert service.song("al")["title"] == "Two"


def test_nothing_playing_has_no_end(linked):
    service, spotify, _ = linked
    spotify.playing = [(204, None, {})]
    service.song("al")
    assert service.ends_in("al") is None


def test_the_song_after_is_asked_for_once_a_song(linked):
    service, spotify, _ = linked
    song = {"link": "https://open.spotify.com/track/1"}
    spotify.playing = [(200, {"queue": [playing_for(1, "Two", "2")["item"]]}, {})]
    assert service.following("al", song)["title"] == "Two"
    service.following("al", song)
    assert len(spotify.asked) == 1
    service.following("al", {"link": "https://open.spotify.com/track/2"})
    assert len(spotify.asked) == 2


@pytest.mark.parametrize("answer", [(403, {}, {}), (200, {"queue": []}, {}), (200, {}, {})])
def test_without_a_queue_there_is_simply_no_next(linked, answer):
    service, spotify, _ = linked
    spotify.playing = [answer]
    assert service.following("al", {"link": "https://open.spotify.com/track/1"}) is None


def test_being_told_to_slow_down_is_heeded(linked, clock):
    service, spotify, _ = linked
    spotify.playing = [(429, {}, {"retry-after": "30"}), (200, playing_for(9), {})]
    song = {"link": "https://open.spotify.com/track/1"}
    service.following("al", song)
    service.following("al", {"link": "https://open.spotify.com/track/2"})
    assert len(spotify.asked) == 1


def test_a_change_is_waited_for_until_it_shows(linked, monkeypatch):
    service, spotify, _ = linked
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    spotify.playing = [(200, playing_for(60), {}), (200, playing_for(60), {}),
                       (200, playing_for(60), {}), (200, playing_for(90, "Two", "2"), {})]
    before = service.song("al")
    service.settle("al", before)
    assert service.song("al")["title"] == "Two" and len(spotify.asked) == 4
