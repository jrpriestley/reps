"""reps-describe: turns a described workout into blocks, by asking Claude.

Its own service and venv, so reps itself keeps to the standard library and works without it. It is
sent the words typed, the exercises that person can do, and their usual time, experience and
goals. Nothing else about them.
"""

import json
import os
import socketserver
import sys
from pathlib import Path

import anthropic

MODEL = os.environ.get("DESCRIBE_MODEL", "claude-opus-5-5")
SOCKET = os.environ.get("DESCRIBE_SOCKET", "/run/reps-describe/sock")
MOST_REQUEST = 32 * 1024
TIMEOUT_SECONDS = 60
OUT_OF_MONEY = ("usage limits", "credit balance")

SYSTEM = """\
You turn a person's description of a home workout into a list of blocks for an app.

A block is one thing done at that point in the workout:
- cardio: one machine (or the bodyweight circuit, or the mobility flow), with minutes, a style
  and, for the spin bike, a level from 1 (easy) to 5 (all out), 0 to leave it to the app.
  Intervals such as "1 min on, 1 min off" are a style, not many blocks. An effort, where one is
  asked for: the rower's average watts, the treadmill's speed in km/h (change mph to it) and
  incline level (0 to 12, each about a percent; a number said to be degrees or percent is still
  the level its display shows), 0 for each that isn't asked for. With intervals they are for the
  hard parts.
  Keep them reasonable for the person: steep means slow, a walk of 6 km/h or less from 10 up.
- strength: one exercise, with sets, and reps a set (5 to 15) only where they are asked for, else
  0.

"This or that" is one block with both in its options, at most three. All of a block's options are
cardio, or all are strength.

The app chooses weights itself, and repetitions where none are asked for. Use only the exercises
listed; where the description names one that isn't there, use the closest listed and say so in the
note.
"Bench" means the exercises on the step, where they are listed, else the floor ones. A time given
for a part ("25 min cardio", "20 min upper body") is the time that part's blocks add up to. Cardio
is steady unless intervals, sprints or HIIT are asked for. A strength exercise takes about 1 minute
to set up and 2.5 for each set with its rest (3 for one-sided ones), so 20 minutes is about 7 sets
in all, over two or three exercises. A time with no parts named includes a 3-minute warm-up. Fit
the time asked for. Give cardio in fives of minutes (25 is 15 and 10, not 13 and 12), but for a
time that was typed. Order blocks as described, or sensibly if it doesn't say: easy start, main
lifts, smaller work, finisher.

When there is a workout so far, the description is a change to it. Change what is asked and keep
the rest as it is, block for block.

Their usual time, experience and goals fill in what the description leaves out; the description
always wins. Without a time, aim for their usual. A new lifter gets 2 sets, others 3. With a
fitness goal, strength work ends in a short cardio finisher where it fits the time; with a mobility
goal, a 5-minute mobility flow. Don't add to a workout that was described block by block.

Reply with a short name for the workout (24 characters at most), the blocks, and a note: one short
sentence on anything you changed or assumed, or an empty string."""


def schema(request: dict) -> dict:
    """Return the shape of an answer: only these exercises, and only these styles."""
    keys = [each["key"] for each in request["exercises"]]
    block = {
        "type": "object",
        "properties": {
            "options": {
                "type": "array",
                "items": {"type": "string", "enum": keys},
            },
            "sets": {"type": "integer", "description": "strength only, else 0"},
            "reps": {"type": "integer", "description": "strength, if asked for, else 0"},
            "minutes": {"type": "integer", "description": "cardio only, else 0"},
            "style": {"type": "string", "enum": [each["key"] for each in request["styles"]]},
            "level": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
            "watts": {"type": "integer", "description": "rower only, if asked for, else 0"},
            "speed": {"type": "number", "description": "treadmill, km/h, if asked for, else 0"},
            "incline": {"type": "integer", "description": "treadmill level, else 0"},
        },
        "required": [
            "options", "sets", "reps", "minutes", "style", "level", "watts", "speed", "incline",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "note": {"type": "string"},
            "blocks": {"type": "array", "items": block},
        },
        "required": ["name", "note", "blocks"],
        "additionalProperties": False,
    }


def question(request: dict) -> str:
    exercises = "\n".join(
        f"- {each['key']}: {each['name']} ({each['group']})" for each in request["exercises"]
    )
    styles = "\n".join(f"- {each['key']}: {each['name']}" for each in request["styles"])
    return (
        f"Exercises:\n{exercises}\n\nCardio styles:\n{styles}\n\n"
        f"At most {request['most_blocks']} blocks.\n\n{usual(request['usual'])}\n\n"
        f"{so_far(request)}<description>\n{request['words']}\n</description>"
    )


def usual(person: dict) -> str:
    goals = ", ".join(person["goals"]).lower()
    return (
        f"Usual time: {person['minutes']} minutes. Experience: {person['experience']}. "
        f"Goals: {goals}."
    )


def so_far(request: dict) -> str:
    """Return the workout being changed and what was said about it, or nothing."""
    if not request.get("blocks"):
        return ""
    said = "\n".join(f"- {words}" for words in request.get("said", []))
    asked = f"Asked for so far:\n{said}\n\n" if said else ""
    return f"Workout so far:\n{json.dumps(request['blocks'])}\n\n{asked}"


def ask(client: anthropic.Anthropic, request: dict) -> dict:
    """Return Claude's blocks for a request, or {"error": words}."""
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": question(request)}],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": schema(request)},
        },
        # A request declined by mistake is tried again on the model Anthropic suggests.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    usage = response.usage
    print(f"{response.model}: {usage.input_tokens} in, {usage.output_tokens} out", file=sys.stderr)
    if response.stop_reason == "refusal":
        return {"error": "Claude wouldn't answer that one. Try other words."}
    if response.stop_reason == "max_tokens":
        return {"error": "That came out too long. Try something shorter."}
    text = next((block.text for block in response.content if block.type == "text"), "")
    return json.loads(text)


def answer(client: anthropic.Anthropic, request: dict) -> dict:
    """Return the answer, with any trouble in words a person can act on."""
    try:
        return ask(client, request)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        return {"error": "homehub's Claude key isn't accepted."}
    except anthropic.RateLimitError:
        return {"error": "Claude is busy, or this month's limit is reached. Try later."}
    except anthropic.BadRequestError as problem:
        print(f"{problem.status_code}: {problem.message}", file=sys.stderr)
        if any(words in problem.message for words in OUT_OF_MONEY):
            return {"error": "The Claude credit or monthly limit is used up."}
        return {"error": "Claude couldn't answer. Try again."}
    except anthropic.APIConnectionError:
        return {"error": "Claude couldn't be reached."}
    except anthropic.APIStatusError as problem:
        print(f"{problem.status_code}: {problem.message}", file=sys.stderr)
        return {"error": "Claude couldn't answer. Try again."}
    except (KeyError, TypeError, ValueError) as problem:
        print(f"odd answer: {problem!r}", file=sys.stderr)
        return {"error": "Claude's answer made no sense. Try again."}


class Handler(socketserver.StreamRequestHandler):
    """One request, one answer, each a line of JSON."""

    client: anthropic.Anthropic
    timeout = TIMEOUT_SECONDS + 10

    def handle(self) -> None:
        try:
            request = json.loads(self.rfile.readline(MOST_REQUEST))
        except ValueError:
            return
        self.wfile.write(json.dumps(answer(self.client, request)).encode() + b"\n")


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main() -> None:
    key = (Path(os.environ["CREDENTIALS_DIRECTORY"]) / "claude.key").read_text().strip()
    Handler.client = anthropic.Anthropic(api_key=key, timeout=TIMEOUT_SECONDS, max_retries=1)
    Path(SOCKET).unlink(missing_ok=True)
    os.umask(0o117)  # for reps' group, and nobody else
    with Server(SOCKET, Handler) as server:
        print(f"reps-describe with {MODEL}", file=sys.stderr, flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
