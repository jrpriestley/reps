"""SQLite: people, their setup, sessions and sets."""

import datetime as dt
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from reps.coach import VERSION
from reps.coach.custom import base_key
from reps.coach.plan import Profile, Session
from reps.coach.progression import Chosen, Performance
from reps.gym import Gym

LOGIN_SECONDS = 90 * 86400
BACKUPS_KEPT = 30
STALE_SECONDS = 6 * 3600

MIGRATIONS = [
    """
    CREATE TABLE person (
        name TEXT PRIMARY KEY,
        goals TEXT NOT NULL DEFAULT '["strength", "fitness"]',
        equipment TEXT NOT NULL DEFAULT '[]',
        minutes INTEGER NOT NULL DEFAULT 45,
        day_minutes TEXT NOT NULL DEFAULT '{}',
        days TEXT NOT NULL DEFAULT '[0, 2, 4]',
        experience TEXT NOT NULL DEFAULT 'returning',
        avoid TEXT NOT NULL DEFAULT '[]',
        hurts TEXT NOT NULL DEFAULT '[]',
        body_kg REAL,
        ready INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE gym (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE session (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person TEXT NOT NULL REFERENCES person(name),
        started REAL NOT NULL,
        ended REAL,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        plan TEXT NOT NULL,
        coach INTEGER NOT NULL
    );
    CREATE INDEX session_person ON session(person, started);
    CREATE TABLE set_log (
        cid TEXT PRIMARY KEY,
        session INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
        item TEXT NOT NULL,
        n INTEGER NOT NULL,
        load REAL NOT NULL,
        amount INTEGER NOT NULL,
        at REAL NOT NULL
    );
    CREATE INDEX set_session ON set_log(session, item, n);
    CREATE TABLE item_state (
        session INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
        item TEXT NOT NULL,
        rir INTEGER,
        skipped INTEGER NOT NULL DEFAULT 0,
        hurt INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (session, item)
    );
    CREATE TABLE event (cid TEXT PRIMARY KEY, at REAL NOT NULL);
    CREATE TABLE login (
        token TEXT PRIMARY KEY, person TEXT NOT NULL, expires REAL NOT NULL
    );
    """,
    """
    CREATE TABLE workout (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person TEXT NOT NULL REFERENCES person(name),
        name TEXT NOT NULL,
        blocks TEXT NOT NULL DEFAULT '[]',
        counts INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE heart (
        session INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
        at INTEGER NOT NULL,
        bpm INTEGER NOT NULL,
        PRIMARY KEY (session, at)
    );
    ALTER TABLE person ADD COLUMN heart_name TEXT NOT NULL DEFAULT '';
    ALTER TABLE person ADD COLUMN heart_address TEXT NOT NULL DEFAULT '';
    """,
    """
    ALTER TABLE person ADD COLUMN pick INTEGER;  -- the workout picked for a day
    ALTER TABLE person ADD COLUMN picked_at REAL NOT NULL DEFAULT 0;
    """,
    """
    CREATE TABLE spotify (
        person TEXT PRIMARY KEY, token TEXT NOT NULL, linked REAL NOT NULL
    );
    """,
    """
    CREATE TABLE mix (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person TEXT NOT NULL REFERENCES person(name),
        name TEXT NOT NULL,
        stages TEXT NOT NULL DEFAULT '{}'
    );
    ALTER TABLE person ADD COLUMN mix INTEGER;  -- the one sessions use; NULL is off
    ALTER TABLE workout ADD COLUMN mix TEXT NOT NULL DEFAULT '';  -- '' usual, 'off', or an id
    ALTER TABLE session ADD COLUMN mix INTEGER;
    """,
    """
    ALTER TABLE person ADD COLUMN speaker TEXT NOT NULL DEFAULT '';  -- where music starts
    """,
    """
    ALTER TABLE person ADD COLUMN ordered TEXT NOT NULL DEFAULT '{}';  -- what to generate
    ALTER TABLE person ADD COLUMN made INTEGER NOT NULL DEFAULT 0;  -- it is today's pick
    """,
    """
    ALTER TABLE workout ADD COLUMN talk TEXT NOT NULL DEFAULT '[]';  -- what was asked, and said
    ALTER TABLE workout ADD COLUMN before TEXT NOT NULL DEFAULT '';  -- to undo the last change
    CREATE TABLE prompt (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person TEXT NOT NULL REFERENCES person(name),
        words TEXT NOT NULL,
        UNIQUE (person, words)
    );
    """,
    """
    ALTER TABLE set_log ADD COLUMN incline REAL NOT NULL DEFAULT 0;  -- the treadmill's
    """,
    """
    ALTER TABLE person ADD COLUMN reps INTEGER NOT NULL DEFAULT 0;  -- a set; 0 is the coach's
    ALTER TABLE workout ADD COLUMN reps INTEGER NOT NULL DEFAULT 0;  -- 0 is the person's usual
    """,
    """
    ALTER TABLE session ADD COLUMN paused REAL NOT NULL DEFAULT 0;  -- seconds, all told
    ALTER TABLE session ADD COLUMN paused_at REAL;  -- when it was, if it is now
    """,
    """
    CREATE TABLE weight (  -- set by a person themselves
        person TEXT NOT NULL REFERENCES person(name),
        exercise TEXT NOT NULL,
        load REAL NOT NULL,
        reps INTEGER NOT NULL,
        at REAL NOT NULL,
        PRIMARY KEY (person, exercise)
    );
    """,
    """
    ALTER TABLE person ADD COLUMN hold INTEGER NOT NULL DEFAULT 0;  -- weights go up if they say
    """,
]


def week_start(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def week_number(day: dt.date) -> int:
    """Return a number that goes up by one every Monday."""
    return (week_start(day).toordinal() - 1) // 7


def local_day(stamp: float) -> dt.date:
    return dt.datetime.fromtimestamp(stamp).date()


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.execute("PRAGMA journal_mode = WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            for number, script in enumerate(MIGRATIONS[version:], version + 1):
                db.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def rows(self, query: str, *values: object) -> list[sqlite3.Row]:
        with closing(self.connect()) as db:
            return db.execute(query, values).fetchall()

    def run(self, query: str, *values: object) -> int:
        with closing(self.connect()) as db:
            return db.execute(query, values).lastrowid or 0

    # Sign-in ----------------------------------------------------------------

    def add_login(self, token: str, person: str) -> None:
        self.run("DELETE FROM login WHERE expires < ?", time.time())
        expires = time.time() + LOGIN_SECONDS
        self.run("INSERT INTO login VALUES (?, ?, ?)", _hash(token), person, expires)

    def login_person(self, token: str | None) -> str | None:
        query = "SELECT person FROM login WHERE token = ? AND expires > ?"
        found = self.rows(query, _hash(token or ""), time.time())
        return found[0]["person"] if found else None

    def end_login(self, token: str | None) -> None:
        self.run("DELETE FROM login WHERE token = ?", _hash(token or ""))

    def end_logins(self, person: str) -> None:
        self.run("DELETE FROM login WHERE person = ?", person)

    # People and the gym -----------------------------------------------------

    def person(self, name: str) -> dict:
        self.run("INSERT OR IGNORE INTO person (name) VALUES (?)", name)
        row = dict(self.rows("SELECT * FROM person WHERE name = ?", name)[0])
        for key in ("goals", "equipment", "day_minutes", "days", "avoid", "hurts", "ordered"):
            row[key] = json.loads(row[key])
        return row

    def save_person(self, name: str, **fields: object) -> None:
        for key, value in fields.items():
            if not key.isidentifier():
                raise ValueError(key)
            stored = json.dumps(value) if isinstance(value, list | dict) else value
            self.run(f"UPDATE person SET {key} = ? WHERE name = ?", stored, name)

    def gym(self) -> Gym:
        saved = {row["key"]: json.loads(row["value"]) for row in self.rows("SELECT * FROM gym")}
        return Gym(saved.get("step_limit_lb", 300), saved.get("step_confirmed", False))

    def save_gym(self, **fields: object) -> None:
        for key, value in fields.items():
            self.run("INSERT OR REPLACE INTO gym VALUES (?, ?)", key, json.dumps(value))

    def profile(self, name: str, minutes: int) -> Profile:
        person = self.person(name)
        return Profile(
            goals=tuple(person["goals"]),
            equipment=frozenset(person["equipment"]),
            minutes=minutes,
            experience=person["experience"],
            avoid=frozenset(person["avoid"] + person["hurts"]),
            body_kg=person["body_kg"],
            gym=self.gym(),
            reps=person["reps"],
            hold=bool(person["hold"]),
            weights=self.weights(name),
        )

    def weights(self, person: str) -> dict[str, Chosen]:
        found = self.rows("SELECT * FROM weight WHERE person = ?", person)
        return {row["exercise"]: Chosen(row["load"], row["at"], row["reps"]) for row in found}

    def set_weight(self, person: str, exercise: str, load: float, reps: int) -> None:
        """Keep a weight a person set, or (0) leave it to the coach."""
        if load:
            self.run(
                "INSERT OR REPLACE INTO weight VALUES (?, ?, ?, ?, ?)",
                person, exercise, load, reps, time.time(),
            )
        else:
            self.run("DELETE FROM weight WHERE person = ? AND exercise = ?", person, exercise)

    # Sessions ---------------------------------------------------------------

    def start(self, person: str, plan: Session, mix: int | None = None) -> int:
        self.discard_active(person)
        return self.run(
            "INSERT INTO session (person, started, kind, title, plan, coach, mix)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            person, time.time(), plan.kind, plan.title, json.dumps(plan.as_dict()), VERSION, mix,
        )

    def active(self, person: str) -> sqlite3.Row | None:
        """Return the session in progress, closing one left open for hours."""
        query = "SELECT * FROM session WHERE person = ? AND ended IS NULL ORDER BY id DESC"
        found = self.rows(query, person)
        if not found:
            return None
        session = found[0]
        last = self.rows("SELECT MAX(at) AS at FROM set_log WHERE session = ?", session["id"])
        latest = last[0]["at"] or session["started"]
        if time.time() - latest < STALE_SECONDS:
            return session
        if last[0]["at"]:
            self.finish(session["id"], latest)
        else:
            self.discard_active(person)
        return None

    def discard_active(self, person: str) -> None:
        self.run("DELETE FROM session WHERE person = ? AND ended IS NULL", person)

    def finish(self, session: int, ended: float | None = None) -> None:
        ended = ended or time.time()
        self.run(FINISH, ended, ended, session)

    def session(self, person: str, session: int) -> sqlite3.Row | None:
        query = "SELECT * FROM session WHERE id = ? AND person = ?"
        found = self.rows(query, session, person)
        return found[0] if found else None

    def delete_session(self, person: str, session: int) -> None:
        self.run("DELETE FROM session WHERE id = ? AND person = ?", session, person)

    def finished(self, person: str) -> list[sqlite3.Row]:
        return self.rows(
            "SELECT * FROM session WHERE person = ? AND ended IS NOT NULL ORDER BY started DESC",
            person,
        )

    def done_since(self, person: str, day: dt.date) -> list[sqlite3.Row]:
        """Return the finished sessions since the start of ``day`` that had any work in them."""
        start = dt.datetime.combine(day, dt.time()).timestamp()
        return self.rows(
            "SELECT * FROM session WHERE person = ? AND ended IS NOT NULL AND started >= ?"
            " AND EXISTS (SELECT 1 FROM set_log WHERE session = session.id) ORDER BY started",
            person, start,
        )

    # Built workouts -----------------------------------------------------------

    def workouts(self, person: str) -> list[dict]:
        found = self.rows("SELECT * FROM workout WHERE person = ? ORDER BY name, id", person)
        return [
            dict(row) | {"blocks": json.loads(row["blocks"]), "talk": json.loads(row["talk"])}
            for row in found
        ]

    def workout(self, person: str, number: int) -> dict | None:
        return next((each for each in self.workouts(person) if each["id"] == number), None)

    def pick_workout(self, person: str, number: int | None) -> None:
        self.save_person(person, pick=number, made=0, picked_at=time.time())

    def pick_generated(self, person: str, order: dict) -> None:
        self.save_person(person, pick=None, made=1, ordered=order, picked_at=time.time())

    def _still_picked(self, person: str, setup: dict, today: dt.date) -> bool:
        """Return whether today's pick stands: until a session is done after picking it."""
        if local_day(setup["picked_at"]) != today:
            return False
        since = self.done_since(person, today)
        return not any(row["started"] >= setup["picked_at"] for row in since)

    def picked(self, person: str, today: dt.date) -> dict | None:
        """Return the saved workout picked for today."""
        setup = self.person(person)
        if setup["pick"] is None or not self._still_picked(person, setup, today):
            return None
        return self.workout(person, setup["pick"])

    def picked_order(self, person: str, today: dt.date) -> dict | None:
        """Return the order for the workout generated for today."""
        setup = self.person(person)
        if not setup["made"] or not self._still_picked(person, setup, today):
            return None
        return setup["ordered"]

    def add_workout(self, person: str, name: str) -> int:
        return self.run("INSERT INTO workout (person, name) VALUES (?, ?)", person, name)

    def save_workout(self, person: str, workout: dict) -> None:
        self.run(
            # Changed by hand, so undoing Claude's last change would lose that: it goes.
            "UPDATE workout SET name = ?, blocks = ?, counts = ?, mix = ?, reps = ?,"
            " before = '' WHERE id = ? AND person = ?",
            workout["name"], json.dumps(workout["blocks"]), workout["counts"], workout["mix"],
            workout["reps"], workout["id"], person,
        )

    def refine_workout(
        self, person: str, workout: dict, blocks: list[dict], talk: list[dict]
    ) -> None:
        """Keep a change Claude made, and what was there before it."""
        self.run(
            "UPDATE workout SET blocks = ?, talk = ?, before = ? WHERE id = ? AND person = ?",
            json.dumps(blocks), json.dumps(talk), json.dumps(workout["blocks"]), workout["id"],
            person,
        )

    def undo_refine(self, person: str, workout: dict) -> None:
        if workout["before"]:
            self.run(
                "UPDATE workout SET blocks = before, talk = ?, before = '' WHERE id = ?"
                " AND person = ?",
                json.dumps(workout["talk"][:-1]), workout["id"], person,
            )

    def save_talk(self, person: str, number: int, talk: list[dict]) -> None:
        self.run(
            "UPDATE workout SET talk = ? WHERE id = ? AND person = ?",
            json.dumps(talk), number, person,
        )

    def prompts(self, person: str) -> list[dict]:
        found = self.rows("SELECT * FROM prompt WHERE person = ? ORDER BY id DESC", person)
        return [dict(row) for row in found]

    def save_prompt(self, person: str, words: str) -> None:
        self.run("INSERT OR IGNORE INTO prompt (person, words) VALUES (?, ?)", person, words)

    def delete_prompt(self, person: str, number: int) -> None:
        self.run("DELETE FROM prompt WHERE id = ? AND person = ?", number, person)

    def delete_workout(self, person: str, number: int) -> None:
        self.run("DELETE FROM workout WHERE id = ? AND person = ?", number, person)

    # Music --------------------------------------------------------------------

    def mixes(self, person: str) -> list[dict]:
        found = self.rows("SELECT * FROM mix WHERE person = ? ORDER BY name, id", person)
        return [dict(row) | {"stages": json.loads(row["stages"])} for row in found]

    def mix(self, person: str, number: int | None) -> dict | None:
        return next((each for each in self.mixes(person) if each["id"] == number), None)

    def add_mix(self, person: str, name: str) -> int:
        return self.run("INSERT INTO mix (person, name) VALUES (?, ?)", person, name)

    def save_mix(self, person: str, mix: dict) -> None:
        self.run(
            "UPDATE mix SET name = ?, stages = ? WHERE id = ? AND person = ?",
            mix["name"], json.dumps(mix["stages"]), mix["id"], person,
        )

    def delete_mix(self, person: str, number: int) -> None:
        self.run("DELETE FROM mix WHERE id = ? AND person = ?", number, person)

    def use_mix(self, person: str, number: int | None) -> None:
        """Make a mix the usual one, in a session under way too. None is off."""
        self.save_person(person, mix=number)
        self.run("UPDATE session SET mix = ? WHERE person = ? AND ended IS NULL", number, person)

    def mix_for(self, person: str, workout: dict | None = None) -> int | None:
        """Return the mix a session starts with: the workout's own choice, else the usual."""
        choice = workout["mix"] if workout else ""
        if choice == "off":
            return None
        number = int(choice) if choice else self.person(person)["mix"]
        return number if self.mix(person, number) else None

    # Spotify ------------------------------------------------------------------

    def spotify_token(self, person: str) -> str:
        found = self.rows("SELECT token FROM spotify WHERE person = ?", person)
        return found[0]["token"] if found else ""

    def save_spotify_token(self, person: str, token: str) -> None:
        if not token:
            self.run("DELETE FROM spotify WHERE person = ?", person)
            return
        self.run(
            "INSERT INTO spotify VALUES (?, ?, ?)"
            " ON CONFLICT (person) DO UPDATE SET token = excluded.token",
            person, token, time.time(),
        )

    def mark_spotify_linked(self, person: str) -> None:
        self.run("UPDATE spotify SET linked = ? WHERE person = ?", time.time(), person)

    def spotify_linked(self, person: str) -> dt.date | None:
        found = self.rows("SELECT linked FROM spotify WHERE person = ?", person)
        return local_day(found[0]["linked"]) if found else None

    # Heart rate ---------------------------------------------------------------

    def heart_sources(self) -> list[dict]:
        """Return each person who has a heart rate source, and what it is."""
        found = self.rows(
            "SELECT name AS person, heart_name AS name, heart_address AS address"
            " FROM person WHERE heart_name != ''"
        )
        return [dict(row) for row in found]

    def keep_heart(self, session: int, bpm: int) -> None:
        self.run("INSERT OR REPLACE INTO heart VALUES (?, ?, ?)", session, int(time.time()), bpm)

    def heart_summary(self, session: int) -> tuple[int, int] | None:
        """Return a session's average and highest heart rate."""
        row = self.rows("SELECT AVG(bpm) AS mean, MAX(bpm) AS top FROM heart WHERE session = ?",
                        session)[0]
        return (round(row["mean"]), row["top"]) if row["top"] else None

    def replace_plan(self, session: int, plan: dict) -> None:
        self.run("UPDATE session SET plan = ? WHERE id = ?", json.dumps(plan), session)

    def logged(self, session: int) -> list[sqlite3.Row]:
        return self.rows("SELECT * FROM set_log WHERE session = ? ORDER BY item, n", session)

    def states(self, session: int) -> dict[str, dict]:
        found = self.rows("SELECT * FROM item_state WHERE session = ?", session)
        return {row["item"]: dict(row) for row in found}

    def apply_event(self, person: str, session: int, event: dict) -> None:
        """Apply one change from the phone; the same one sent twice counts once."""
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                seen = db.execute(
                    "INSERT OR IGNORE INTO event VALUES (?, ?)", (event["cid"], time.time())
                )
                if seen.rowcount:
                    _apply(db, person, session, event)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def history(self, person: str) -> dict[str, list[Performance]]:
        """Return every exercise's finished work, oldest first."""
        work: dict[tuple[float, int, str], list[tuple[float, int]]] = {}
        for row in self.rows(
            "SELECT session.started, set_log.* FROM set_log JOIN session"
            " ON session.id = set_log.session WHERE session.person = ?"
            " AND session.ended IS NOT NULL ORDER BY session.started, set_log.n",
            person,
        ):
            key = (row["started"], row["session"], row["item"])
            work.setdefault(key, []).append((row["load"], row["amount"]))
        efforts = {
            (row["session"], row["item"]): row["rir"]
            for row in self.rows(
                "SELECT item_state.* FROM item_state JOIN session"
                " ON session.id = item_state.session WHERE session.person = ?",
                person,
            )
        }
        asked = {
            (row["id"], item["key"]): item["reps"]
            for row in self.rows(  # few sessions ask for reps, so the rest aren't read
                "SELECT id, plan FROM session WHERE person = ? AND plan GLOB ?",
                person, '*"reps": [1-9]*',
            )
            for item in json.loads(row["plan"])["items"]
        }
        history: dict[str, list[Performance]] = {}
        for (started, session, item), sets in work.items():
            effort = efforts.get((session, item))
            reps = asked.get((session, item), 0)
            done = Performance(local_day(started), tuple(sets), effort, reps, started)
            history.setdefault(base_key(item), []).append(done)
        return history

    # Keeping and taking away ------------------------------------------------

    def export(self, person: str) -> dict:
        sessions = []
        for row in self.finished(person):
            record = dict(row)
            record["plan"] = json.loads(record["plan"])
            record["sets"] = [dict(each) for each in self.logged(row["id"])]
            record["items"] = list(self.states(row["id"]).values())
            beats = self.rows("SELECT at, bpm FROM heart WHERE session = ? ORDER BY at", row["id"])
            record["heart"] = [[beat["at"], beat["bpm"]] for beat in beats]
            sessions.append(record)
        return {
            "person": self.person(person),
            "workouts": self.workouts(person),
            "mixes": self.mixes(person),
            "prompts": self.prompts(person),
            "weights": {key: asdict(each) for key, each in self.weights(person).items()},
            "sessions": sessions,
        }

    def backup(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"reps-{dt.date.today():%Y%m%d}.db"
        target.unlink(missing_ok=True)
        self.run("VACUUM INTO ?", str(target))
        for old in sorted(folder.glob("reps-*.db"))[:-BACKUPS_KEPT]:
            old.unlink()
        return target


FINISH = (
    "UPDATE session SET ended = ?, paused = paused + MAX(0, ? - COALESCE(paused_at, 1e12)),"
    " paused_at = NULL WHERE id = ? AND ended IS NULL"
)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _apply(db: sqlite3.Connection, person: str, session: int, event: dict) -> None:
    item = str(event.get("item", ""))
    match event["type"]:
        case "set":
            db.execute(
                "INSERT OR REPLACE INTO set_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event["cid"], session, item, int(event["n"]), float(event["load"]),
                 int(event["amount"]), time.time(), float(event.get("incline", 0))),
            )
        case "unset":
            db.execute(
                "DELETE FROM set_log WHERE cid = ? AND session = ?", (event["target"], session)
            )
            db.execute(
                "UPDATE item_state SET rir = NULL WHERE session = ? AND item = ?", (session, item)
            )
        case "rir":
            _set_state(db, session, item, "rir", max(0, min(4, int(event["value"]))))
        case "skip":
            _set_state(db, session, item, "skipped", 1)
        case "hurt":
            _set_state(db, session, item, "hurt", 1)
            row = db.execute("SELECT hurts FROM person WHERE name = ?", (person,)).fetchone()
            hurts = sorted({*json.loads(row["hurts"]), base_key(item)})
            db.execute("UPDATE person SET hurts = ? WHERE name = ?", (json.dumps(hurts), person))
        case "finish":
            now = time.time()
            db.execute(FINISH, (now, now, session))
        case "pause":
            # The phone's time, as it may have been out of reach; never outside the session.
            db.execute(
                "UPDATE session SET paused_at = MAX(started, MIN(?, ?))"
                " WHERE id = ? AND ended IS NULL AND paused_at IS NULL",
                (float(event["at"]), time.time(), session),
            )
        case "resume":
            at = min(float(event["at"]), time.time())
            db.execute(
                "UPDATE session SET paused = paused + MAX(0, ? - paused_at), paused_at = NULL"
                " WHERE id = ? AND ended IS NULL AND paused_at IS NOT NULL",
                (at, session),
            )
        case unknown:
            raise ValueError(f"unknown event {unknown!r}")


def _set_state(db: sqlite3.Connection, session: int, item: str, field: str, value: int) -> None:
    db.execute(
        f"INSERT INTO item_state (session, item, {field}) VALUES (?, ?, ?)"
        f" ON CONFLICT (session, item) DO UPDATE SET {field} = excluded.{field}",
        (session, item, value),
    )
