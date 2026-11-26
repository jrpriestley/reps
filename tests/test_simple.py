"""Simple workouts: a setting, a list for now, Adjust, and quieter beeps."""

import json

SAID = "rower 15, curl 3x"  # read here, without Claude


def simple(client, sounds="quiet"):
    client.set_up()
    client.post("/setup/style", {"style": "simple", "sounds": sounds})
    return client


def test_the_style_is_a_setting(client, store):
    client.set_up()
    assert "Guided, loud beeps" in client.get("/me")[1]
    client.post("/setup/style", {"style": "simple", "sounds": "off"})
    assert (store.person("al")["style"], store.person("al")["sounds"]) == ("simple", "off")
    assert "Simple, no beeps" in client.get("/me")[1]
    client.post("/setup/style", {"style": "odd", "sounds": "deafening"})
    assert (store.person("al")["style"], store.person("al")["sounds"]) == ("simple", "off")


def test_described_in_simple_is_for_now_until_saved(client, store):
    simple(client)
    response, _ = client.post("/describe", {"words": SAID})
    assert response.headers["Location"] == "/"
    assert store.workouts("al") == [] and "/workouts/" not in client.get("/workouts")[1]
    today = client.get("/")[1]
    assert "Described for now" in today and "Curl" in today
    number = store.workouts("al", every=True)[0]["id"]
    assert f"/workouts/{number}/keep" in today
    client.post(f"/workouts/{number}/keep")
    assert [each["id"] for each in store.workouts("al")] == [number]
    assert "Saved workout" in client.get("/")[1]


def test_another_one_for_now_replaces_the_last(client, store):
    simple(client)
    client.post("/describe", {"words": SAID})
    client.post("/describe", {"words": "curl 4x"})
    assert [each["name"] for each in store.workouts("al", every=True)] == ["curl 4x"]


def test_guided_and_from_me_still_save(client, store):
    client.set_up()
    assert client.post("/describe", {"words": SAID})[0].headers["Location"].startswith(
        "/workouts/")
    client.post("/setup/style", {"style": "simple", "sounds": "loud"})
    client.post("/me/describe", {"words": "curl 4x"})
    assert len(store.workouts("al")) == 2


def test_adjust_keeps_a_weight_and_the_reps_where_they_belong(client, store):
    simple(client)
    client.post("/describe", {"words": SAID})
    page = client.get("/adjust")[1]
    assert 'name="load-curl"' in page and "Reps per set" in page
    client.post("/adjust", {"load-curl": "25", "was-curl": "10", "reps": "12", "minutes": "45"})
    assert store.weights("al")["curl"].load == 25
    assert store.workouts("al", every=True)[0]["reps"] == 12
    assert store.person("al")["reps"] == 0
    assert "25 lb" in client.get("/")[1]


def test_adjust_on_the_coachs_plan_changes_the_usual_reps(client, store):
    client.set_up()
    client.post("/adjust", {"reps": "10", "minutes": "45", "load-curl": "999"})
    assert store.person("al")["reps"] == 10 and "curl" not in store.weights("al")


def test_the_session_knows_the_style_and_the_sounds(client):
    simple(client)
    client.post("/start", {"minutes": "45"})
    session = json.loads(client.get("/api/session")[1])
    assert session["simple"] is True and session["sounds"] == "quiet"
