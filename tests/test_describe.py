import json
import sys
import threading
import types
from pathlib import Path

import pytest

from reps import describe
from reps.coach import shorthand
from reps.coach.plan import Profile
from reps.library import EQUIPMENT

EVERYTHING = Profile(equipment=frozenset(EQUIPMENT), body_kg=75)
AL = ("25 min cardio using both rower and treadmill then for strength 20 min upper body using "
      "dumbbell and bench")


def short(words):
    blocks = shorthand.parse(words)
    if blocks is None:
        return None
    return [(block["options"], block.get("minutes") or block.get("sets"), block.get("style"))
            for block in blocks]


@pytest.mark.parametrize(
    ("words", "blocks"),
    [
        ("rower 15, deadlift or curl 3x, rower 15",
         [(["rower"], 15, "steady"), (["deadlift", "curl"], 3, None), (["rower"], 15, "steady")]),
        ("Air rower 15 min then goblet squat 4 sets",
         [(["rower"], 15, "steady"), (["goblet-squat"], 4, None)]),
        ("bike 20 1 on 1 off hard", [(["bike"], 20, "60-60")]),
        ("treadmill 10 min 30s on 30s off", [(["treadmill"], 10, "30-30")]),
        ("row 12 hiit", [(["rower"], 12, "60-60")]),
        ("plank 3\nleg raise 3x10\nstretch 5", [(["plank"], 3, None), (["leg-raise"], 3, None),
                                                (["flow"], 5, "steady")]),
        ("bench press 3x8 or push-up", [(["floor-press", "push-up"], 3, None)]),
    ],
)
def test_short_ones_are_read_here(words, blocks):
    assert short(words) == blocks


@pytest.mark.parametrize(
    "words",
    [AL, "", "30 min upper body, finish with bike sprints", "rower 15 but go easy on the knee",
     "curl or rower", "something nice", "bike 10 7 on 3 off", "a, b, c", "rower " * 40,
     ", ".join(["rower 5"] * 20)],
)
def test_anything_less_plain_is_left_to_claude(words):
    assert shorthand.parse(words) is None


def test_the_bikes_level_is_read():
    assert shorthand.parse("bike 20 all out")[0]["level"] == 5


# What is sent -----------------------------------------------------------------


def test_only_the_words_what_they_can_do_and_preferences_are_sent():
    profile = Profile(equipment=frozenset({"dumbbells", "rower"}), body_kg=77.5, minutes=35,
                      goals=("muscle", "mobility"), experience="new",
                      avoid=frozenset({"curl", "vpush"}))
    request = describe.request_for(profile, AL)
    assert set(request) == {"words", "exercises", "styles", "most_blocks", "usual"}
    assert request["usual"] == {"minutes": 35, "experience": "new",
                                "goals": ["Muscle", "Mobility"]}
    keys = {each["key"] for each in request["exercises"]}
    assert "rower" in keys and not keys & {"curl", "shoulder-press", "bike", "treadmill"}
    sent = json.dumps(request)
    assert "77.5" not in sent and "avoid" not in sent and "curl" not in sent
    assert len(describe.request_for(profile, "x" * 9999)["words"]) == describe.MOST_WORDS


def test_an_answer_is_checked_like_a_persons_blocks():
    answer = {"blocks": [
        {"options": ["rower", "curl"], "sets": 3, "minutes": 999, "style": "x", "level": 9},
        {"options": ["made-up"], "sets": 3, "minutes": 0, "style": "steady", "level": 0},
        {"options": ["curl", "curl", "triceps-press"], "sets": 99, "minutes": 5,
         "style": "steady", "level": 0},
    ]}
    blocks = describe.blocks_from(answer, EVERYTHING)
    assert [block["options"] for block in blocks] == [["rower"], ["curl", "triceps-press"]]
    assert blocks[0]["minutes"] == 60 and blocks[0]["style"] == "steady"
    assert blocks[1]["sets"] <= 6 and "minutes" not in blocks[1]
    assert describe.blocks_from({}, EVERYTHING) == []


BLOCKS = [{"options": ["curl"], "sets": 4}, {"options": ["rower"], "minutes": 10}]


@pytest.mark.parametrize(
    ("experience", "words", "sets"),
    [("new", "arms and a row", 2), ("new", "arms 4 sets and a row", 4), ("new", "curl 4x", 4),
     ("new", "4x curl", 4), ("new", "20 min arms", 2), ("regular", "arms and a row", 4)],
)
def test_a_new_lifter_keeps_to_the_coachs_sets_unless_they_say(experience, words, sets):
    profile = Profile(equipment=frozenset(EQUIPMENT), experience=experience)
    curl, rower = describe.ease(BLOCKS, words, profile)
    assert curl["sets"] == sets and rower == BLOCKS[1]


# Through the page -------------------------------------------------------------

ANSWER = {
    "name": "Cardio then upper body!!",
    "note": "Used floor presses, as the step needs your weight.",
    "blocks": [
        {"options": ["rower"], "sets": 0, "minutes": 12, "style": "steady", "level": 0},
        {"options": ["treadmill"], "sets": 0, "minutes": 13, "style": "steady", "level": 0},
        {"options": ["floor-press"], "sets": 3, "minutes": 0, "style": "steady", "level": 0},
        {"options": ["bent-row"], "sets": 3, "minutes": 0, "style": "steady", "level": 0},
    ],
}


@pytest.fixture
def claude(monkeypatch):
    """Stand in for the service: what it was sent, and what it says."""
    stand_in = types.SimpleNamespace(sent=[], answer=ANSWER)

    def ask(_path, request):
        stand_in.sent.append(request)
        if isinstance(stand_in.answer, Exception):
            raise stand_in.answer
        return stand_in.answer

    monkeypatch.setattr(describe, "ask", ask)
    return stand_in


def ready(client):
    return client.set_up(equipment=("dumbbells", "rower", "treadmill"))


def test_a_description_becomes_a_saved_workout_to_look_over(client, claude, store):
    ready(client)
    where = client.post("/describe", {"words": AL})[0].headers["Location"]
    assert claude.sent[0]["words"] == AL
    page = client.get(where)[1]
    assert "Air rower" in page and "Floor press" in page and "Used floor presses" in page
    workout = store.workouts("al")[0]
    assert workout["name"] == "Cardio then upper body!!" and len(workout["blocks"]) == 4


def test_a_short_one_never_leaves_homehub(client, claude, store):
    ready(client)
    client.post("/describe", {"words": "rower 15, curl 3x, rower 10"})
    assert claude.sent == [] and len(store.workouts("al")[0]["blocks"]) == 3


@pytest.mark.parametrize(
    ("answer", "words"),
    [({"error": "Claude couldn't be reached."}, "be reached"),
     ({"blocks": []}, "Nothing in that fits"), (describe.NotSetUpError(), "available now")],
)
def test_trouble_is_said_and_the_words_are_kept(client, claude, store, answer, words):
    ready(client)
    claude.answer = answer
    response, page = client.post("/describe", {"words": AL})
    assert response.status == 400 and words in page and "rower and treadmill" in page
    assert store.workouts("al") == []


def test_a_persons_own_settings_are_used(client, claude, store):
    ready(client)
    client.post("/setup/body", {"experience": "new", "body_kg": ""})
    client.post("/setup/goals", {"goals": ["muscle"]})
    client.post("/setup/time", {"minutes": "30"})
    client.post("/describe", {"words": AL})
    assert claude.sent[0]["usual"] == {"minutes": 30, "experience": "new", "goals": ["Muscle"]}
    sets = [block["sets"] for block in store.workouts("al")[0]["blocks"] if "sets" in block]
    assert sets == [2, 2]
    client.post("/describe", {"words": "curl or triceps press"})
    newest = max(store.workouts("al"), key=lambda each: each["id"])
    assert [block["sets"] for block in newest["blocks"]] == [2]


def test_nothing_said_asks_for_something(client, claude):
    ready(client)
    assert client.post("/describe", {"words": "  "})[0].status == 400 and claude.sent == []


def test_so_many_a_day_each(client, other, claude, server, monkeypatch):
    monkeypatch.setattr("reps.web.server.MOST_ASKED", 2)
    ready(client)
    for _ in range(3):
        _response, page = client.post("/describe", {"words": AL})
    assert "enough for today" in page and len(claude.sent) == 2
    other.sign_in("bo")
    other.set_up("bo", equipment=("rower",))
    assert other.post("/describe", {"words": AL})[0].status == 303


def test_what_claude_says_is_never_markup(client, claude):
    ready(client)
    claude.answer = ANSWER | {"name": "<b>x</b>", "note": "<script>alert(1)</script>"}
    where = client.post("/describe", {"words": AL})[0].headers["Location"]
    assert "<script>alert" not in client.get(where)[1] and "<b>x</b>" not in client.get(where)[1]


@pytest.mark.parametrize(("minutes", "words", "kept"), [
    (13, "25 min cardio", 15), (12, "25 min cardio", 10), (12, "row 12 min", 12),
    (1, "a short row", 5), (20, "", 20),
])
def test_cardio_is_in_fives_unless_a_time_was_typed(minutes, words, kept):
    block = {"options": ["rower"], "minutes": minutes, "style": "steady", "level": 0}
    assert describe.in_fives([block], words)[0]["minutes"] == kept


# Refining and saving ----------------------------------------------------------


def described(client, store):
    ready(client)
    client.post("/describe", {"words": AL})
    return store.workouts("al")[0]


def refined(*blocks, note="Shorter row."):
    return {"name": "Ignored", "note": note, "blocks": list(blocks)}


ROW = {"options": ["rower"], "sets": 0, "minutes": 5, "style": "steady", "level": 0}
PRESS = {"options": ["floor-press"], "sets": 3, "minutes": 0, "style": "steady", "level": 0}


def test_a_workout_is_refined_and_undone(client, claude, store):
    workout = described(client, store)
    claude.answer = refined(ROW, PRESS)
    here = f"/workouts/{workout['id']}/refine"
    assert client.post(here, {"words": "less rower"})[0].headers["Location"] == here
    after = store.workouts("al")[0]
    assert [block.get("minutes") for block in after["blocks"]] == [5, None]
    assert after["name"] == workout["name"]
    page = client.get(here)[1]
    assert "less rower" in page and "Shorter row." in page and AL in page
    client.post(here, {"undo": "1"})
    back = store.workouts("al")[0]
    assert back["blocks"] == workout["blocks"] and len(back["talk"]) == 1
    assert "Undo" not in client.get(here)[1]


def test_refining_sends_the_blocks_and_what_was_said_but_not_the_music(client, claude, store):
    workout = described(client, store)
    music = {"uri": "spotify:playlist:abc", "name": "Private songs"}
    workout["blocks"][0]["music"] = music
    store.save_workout("al", workout)
    claude.answer = refined(ROW, PRESS)
    client.post(f"/workouts/{workout['id']}/refine", {"words": "less rower"})
    sent = claude.sent[-1]
    assert sent["said"] == [AL] and sent["words"] == "less rower"
    assert sent["blocks"][0]["options"] == ["rower"] and "Private" not in json.dumps(sent)
    assert store.workouts("al")[0]["blocks"][0]["music"] == music


def test_a_change_by_hand_takes_the_undo_away(client, claude, store):
    workout = described(client, store)
    claude.answer = refined(ROW, PRESS)
    here = f"/workouts/{workout['id']}/refine"
    client.post(here, {"words": "less rower"})
    client.post(f"/workouts/{workout['id']}/block/0", {"minutes": "9", "save": "1"})
    client.post(here, {"undo": "1"})
    assert store.workouts("al")[0]["blocks"][0]["minutes"] == 9


def test_one_built_by_hand_can_be_refined_too(client, claude, store):
    ready(client)
    base = client.post("/workouts", {"name": "Mine"})[0].headers["Location"]
    client.post(f"{base}/add", {"key": "rower"})
    assert "Built by hand" in client.get(f"{base}/refine")[1]
    claude.answer = refined(ROW, PRESS)
    client.post(f"{base}/refine", {"words": "add a press"})
    assert len(store.workouts("al")[0]["blocks"]) == 2


@pytest.mark.parametrize("answer", [{"error": "Claude couldn't be reached."}, {"blocks": []}])
def test_a_refine_that_fails_changes_nothing(client, claude, store, answer):
    workout = described(client, store)
    claude.answer = answer
    response, page = client.post(f"/workouts/{workout['id']}/refine", {"words": "less rower"})
    assert response.status == 400 and "less rower" in page
    assert store.workouts("al")[0] == workout


def test_a_prompt_is_saved_used_again_and_deleted(client, claude, store):
    workout = described(client, store)
    base = f"/workouts/{workout['id']}"
    assert "Save prompt" in client.get(base)[1]
    client.post(f"{base}/prompt")
    client.post(f"{base}/prompt")
    prompts = store.prompts("al")
    assert [each["words"] for each in prompts] == [AL]
    assert "Prompt saved" in client.get(base)[1]
    assert AL in client.get("/describe")[1]
    again = client.get(f"/describe?prompt={prompts[0]['id']}")[1]
    assert f">{AL}</textarea>" in again
    client.post("/describe/forget", {"prompt": str(prompts[0]["id"])})
    assert store.prompts("al") == []


def test_a_saved_prompt_holds_its_refinements(client, claude, store):
    workout = described(client, store)
    claude.answer = refined(ROW, PRESS)
    client.post(f"/workouts/{workout['id']}/refine", {"words": "less rower"})
    client.post(f"/workouts/{workout['id']}/prompt")
    assert store.prompts("al")[0]["words"] == f"{AL}. Then: less rower"


def test_prompts_and_talk_are_each_persons_own(client, other, claude, store):
    workout = described(client, store)
    client.post(f"/workouts/{workout['id']}/prompt")
    number = store.prompts("al")[0]["id"]
    other.set_up("bo", equipment=("rower",))
    assert "rower and treadmill" not in other.get(f"/describe?prompt={number}")[1]
    other.post("/describe/forget", {"prompt": str(number)})
    assert len(store.prompts("al")) == 1
    for path in ("refine", "prompt"):
        assert other.post(f"/workouts/{workout['id']}/{path}", {"words": "x"})[0].status == 404
    assert other.get(f"/workouts/{workout['id']}/refine")[0].status == 404


def test_a_hand_built_workout_has_no_prompt_to_save(client, store):
    ready(client)
    base = client.post("/workouts", {"name": "Mine"})[0].headers["Location"]
    assert "Save prompt" not in client.get(base)[1]
    client.post(f"{base}/prompt")
    assert store.prompts("al") == []


# The service ------------------------------------------------------------------


@pytest.fixture
def service(monkeypatch):
    """The service's module, with a stand-in for Anthropic's library."""
    library = types.ModuleType("anthropic")
    library.Anthropic = object
    for name in ("APIStatusError", "APIConnectionError"):
        setattr(library, name, type(name, (Exception,), {"status_code": 500, "message": "x"}))
    for name in ("AuthenticationError", "BadRequestError", "PermissionDeniedError",
                 "RateLimitError"):
        setattr(library, name, type(name, (library.APIStatusError,), {}))
    monkeypatch.setitem(sys.modules, "anthropic", library)
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "describe"))
    sys.modules.pop("reps_describe", None)
    import reps_describe

    return reps_describe


class FakeClient:
    def __init__(self, text="{}", stop="end_turn", raises=None):
        self.beta = types.SimpleNamespace(messages=self)
        self.text, self.stop, self.raises, self.asked = text, stop, raises, {}

    def create(self, **asked):
        self.asked = asked
        if self.raises:
            raise self.raises
        block = types.SimpleNamespace(type="text", text=self.text)
        usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)
        return types.SimpleNamespace(content=[block], stop_reason=self.stop, usage=usage,
                                     model="m")


REQUEST = describe.request_for(EVERYTHING, AL)


def test_the_question_holds_the_words_and_the_answer_is_bound_to_the_list(service):
    client = FakeClient(json.dumps(ANSWER))
    assert service.answer(client, REQUEST) == ANSWER
    asked = client.asked
    assert AL in asked["messages"][0]["content"]
    assert "Usual time: 45 minutes" in asked["messages"][0]["content"]
    shape = asked["output_config"]["format"]["schema"]["properties"]["blocks"]["items"]
    assert "rower" in shape["properties"]["options"]["items"]["enum"]
    assert asked["model"] == "claude-opus-5-5" and asked["fallbacks"] == "default"


def test_a_change_is_asked_with_the_workout_so_far(service):
    client = FakeClient(json.dumps(ANSWER))
    request = REQUEST | {"words": "less rower", "said": [AL], "blocks": [ROW]}
    service.answer(client, request)
    content = client.asked["messages"][0]["content"]
    assert "Workout so far" in content and AL in content and "less rower" in content
    service.answer(client, REQUEST)
    assert "Workout so far" not in client.asked["messages"][0]["content"]


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_an_answer_cut_short_is_an_error(service, stop):
    assert "error" in service.answer(FakeClient("{", stop), REQUEST)


@pytest.mark.parametrize(
    ("name", "words"),
    [("AuthenticationError", "key"), ("RateLimitError", "limit"),
     ("APIConnectionError", "reached"), ("APIStatusError", "Try again")],
)
def test_each_kind_of_trouble_has_its_words(service, name, words):
    problem = getattr(sys.modules["anthropic"], name)()
    assert words in service.answer(FakeClient(raises=problem), REQUEST)["error"]


@pytest.mark.parametrize(
    ("message", "words"),
    [("You have reached your specified API usage limits.", "used up"),
     ("Your credit balance is too low", "used up"), ("schema is wrong", "Try again")],
)
def test_a_spent_limit_is_said_plainly(service, message, words):
    problem = sys.modules["anthropic"].BadRequestError()
    problem.message = message
    assert words in service.answer(FakeClient(raises=problem), REQUEST)["error"]


def test_odd_answers_and_requests_are_survived(service):
    assert "error" in service.answer(FakeClient("not json"), REQUEST)
    assert "error" in service.answer(FakeClient("{}"), {})


def test_reps_and_the_service_speak_over_a_socket(service, tmp_path):
    path = tmp_path / "sock"
    service.Handler.client = FakeClient(json.dumps(ANSWER))
    server = service.Server(str(path), service.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    assert describe.ask(path, REQUEST) == ANSWER
    server.shutdown()
    server.server_close()
    with pytest.raises(describe.NotSetUpError):
        describe.ask(tmp_path / "nothing", REQUEST)
