"""The HTTPS server: pages, forms and the session screen's data."""

import datetime as dt
import hmac
import http.server
import json
import math
import re
import secrets
import socket
import sqlite3
import ssl
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict, replace

from reps import describe
from reps.coach import custom, pace, progress, shorthand, stages
from reps.coach.generate import FOCUSES, Order, as_blocks, focuses, generate, machines
from reps.coach.intervals import LEVELS, STYLES, is_levelled
from reps.coach.plan import (
    CORE_SESSIONS,
    GOALS,
    LEAST_MINUTES,
    MOST_MINUTES,
    Profile,
    Session,
    available,
    follow,
    next_session,
    swap,
    working_sets,
)
from reps.conductor import OPEN_SPOTIFY, Conductor
from reps.config import Config, copied_at, heart_key
from reps.guides import guide_for
from reps.gym import DIAL_LOADS
from reps.library import EQUIPMENT, EXERCISES, PATTERNS
from reps.spotify import (
    ARTWORK_HOST,
    FailedError,
    NoDeviceError,
    RefusedError,
    Spotify,
    UnlinkedError,
)
from reps.store import LOGIN_SECONDS, Store, local_day, week_number, week_start
from reps.web import music_pages, pages, progress_pages, ui, workout_pages
from reps.web.auth import Lockout, Shadow
from reps.web.heart import LEAST_BPM, MOST_BPM, Heart
from reps.web.icons import ICON_SIZES, icon_png, manifest
from reps.web.layout import ASSETS, BARS, THEMES, asset, login_page

COOKIE = "__Host-reps"
THEME_COOKIE = "reps_theme"
HISTORY_COOKIE = "reps_history"
HISTORY_VIEWS = ("calendar", "list", "progress")
MAX_BODY = 64 * 1024
LATER = "/me/describe"  # described from Me, to keep; from Today, to have now
DESCRIBE_PATHS = ("/describe", LATER)
SEEKS = {"back": -15, "forward": 15}  # seconds
MOST_TALK = 12
MOST_PROMPTS = 50
MOST_ASKED = 30  # descriptions sent to Claude, per person and day
TLS_HANDSHAKE = b"\x16"
SPOTIFY_LOGO = re.compile(r"spotify-(?:black|white)\.png")
EMBER_FILE = re.compile(r"ember-(?:display-)?\d{3}\.woff2")
SCAN_REFRESH_SECONDS = 4

CONTENT_POLICY = (
    f"default-src 'self'; img-src 'self' https://{ARTWORK_HOST}; "
    "object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)


class App:
    """What every request shares."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.store = Store(config.database)
        self.shadow = Shadow(config.shadow_file, config.users)
        self.lockout = Lockout()
        self.heart = Heart()
        self.heart_key = heart_key()
        self.spotify = Spotify(
            config.spotify_client,
            f"https://{config.host}/spotify/callback",
            self.store.spotify_token,
            self.store.save_spotify_token,
        )
        self.conductor = Conductor(self.spotify)
        self.asked: dict[tuple[str, dt.date], int] = {}
        self.asked_lock = threading.Lock()

    def usual_minutes(self, person: str, today: dt.date) -> int:
        setup = self.store.person(person)
        return setup["day_minutes"].get(str(today.weekday())) or setup["minutes"]

    def plan(self, person: str, minutes: int, today: dt.date) -> Session:
        done = self.store.done_since(person, week_start(today))
        counted = sum(row["kind"] != "extra" for row in done)
        history = self.store.history(person)
        profile = self.store.profile(person, minutes)
        return next_session(profile, counted, week_number(today), history, today)

    def music(self, person: str) -> dict:
        """Return the music widget's state, having moved a session's music on if it is time to."""
        store = self.store
        linked = bool(store.spotify_token(person)) and bool(self.spotify.client_id)
        song, note = self.spotify.song(person), ""
        setup, session = store.person(person), store.active(person)
        mix = store.mix(person, session["mix"] if session else setup["mix"])
        if linked and session:
            moment, wanted = self.cue(session, mix)
            playing = bool(song and song["playing"])
            if wanted or mix:
                note = self.conductor.follow(
                    person, session["id"], moment, wanted, playing, setup["speaker"]
                )
        mixes = [{"id": each["id"], "name": each["name"]} for each in store.mixes(person)]
        shown = {"id": mix["id"], "name": mix["name"]} if mix else None
        after = self.spotify.following(person, song) if linked else None
        soon = self.spotify.ends_in(person) if linked else None
        return {"linked": linked, "song": song, "note": note, "mix": shown, "mixes": mixes,
                "next": after, "soon": soon}

    def cue(self, session: sqlite3.Row, mix: dict | None) -> tuple[str, dict | None]:
        """Return the moment a session is at for music, and its playlist: the block's own first."""
        sets: dict[str, int] = {}
        for row in self.store.logged(session["id"]):
            sets[row["item"]] = sets.get(row["item"], 0) + 1
        items = json.loads(session["plan"])["items"]
        states = self.store.states(session["id"])
        now = stages.current_item(items, sets, states)
        stage = stages.stage_of(items, sets, states)
        if now and now.get("music"):
            return f"{stage} {now['music']['uri']}", now["music"]
        return stage, stages.playlist_for(mix["stages"], stage, session["id"]) if mix else None

    def control_music(self, person: str, action: str, number: object) -> str:
        """Do what a button on the widget asks; return what to tell the person, or ''."""
        if action == "mix":
            return self.use_mix(person, None if number is None else int(number))
        if action == "next":
            return self.changing(person, lambda: self.spotify.skip(person))
        if action == "previous":
            return self.changing(person, lambda: self.spotify.previous(person))
        if action in SEEKS:
            return self.trying(person, lambda: self.spotify.seek(person, SEEKS[action]))
        if action == "toggle":
            return self.trying(person, lambda: self.toggle_music(person))
        raise ValueError("action")

    def changing(self, person: str, command: Callable[[], None]) -> str:
        """Run a command that changes the song, and wait a moment to be able to show it."""
        before = self.spotify.song(person)
        note = self.trying(person, command)
        if not note:
            self.spotify.settle(person, before)
        return note

    def trying(self, person: str, command: Callable[[], None]) -> str:
        """Run a Spotify command; return what went wrong in words, or ''."""
        try:
            command()
        except NoDeviceError:
            speaker = self.store.person(person)["speaker"]
            return f"Spotify can't see {speaker}" if speaker else OPEN_SPOTIFY
        except RefusedError:
            return "Spotify won't do that just now"
        except (FailedError, UnlinkedError):
            return "Spotify didn't answer"
        return ""

    def use_mix(self, person: str, number: int | None) -> str:
        """Choose a mix and start it, or (None) leave the music to the person."""
        if number is not None and self.store.mix(person, number) is None:
            return ""
        self.store.use_mix(person, number)
        if number is None:
            return ""
        return self.trying(person, lambda: self.start_music(person))

    def use_speaker(self, person: str, name: str) -> str:
        """Choose where music starts, and move what is playing there now."""
        self.store.save_person(person, speaker=name)
        song = self.spotify.song(person)
        if not name or not song or not song["playing"]:
            return ""
        return self.trying(person, lambda: self.spotify.move(person, name))

    def toggle_music(self, person: str) -> None:
        song = self.spotify.song(person)
        if song and song["playing"]:
            self.spotify.pause(person)
            return
        try:
            if not song:
                raise NoDeviceError
            self.spotify.resume(person)
        except NoDeviceError:
            self.start_music(person)

    def start_music(self, person: str) -> None:
        """Start the mix on the chosen device; with no mix, just what was playing."""
        setup, session = self.store.person(person), self.store.active(person)
        mix = self.store.mix(person, session["mix"] if session else setup["mix"])
        if session and (mix or self.cue(session, None)[1]):
            self.conductor.restart(person)  # it starts the stage's playlist as the widget asks
            return
        wanted = stages.any_playlist(mix["stages"], int(time.time())) if mix else None
        if wanted:
            self.spotify.play(person, wanted["uri"], setup["speaker"])
        else:
            self.spotify.resume(person)

    def ask_claude(
        self, person: str, profile: Profile, words: str, workout: dict | None = None
    ) -> dict:
        """Ask for a workout, so many times a day: each costs a little."""
        today = dt.date.today()
        with self.asked_lock:
            asked = self.asked.get((person, today), 0)
            if asked >= MOST_ASKED:
                return {"error": "That's enough for today. Build or generate one instead."}
            self.asked = {(person, today): asked + 1} | {
                key: count for key, count in self.asked.items()
                if key[1] == today and key[0] != person
            }
        try:
            return describe.ask(
                self.config.describe_socket, describe.request_for(profile, words, workout)
            )
        except describe.NotSetUpError:
            return {"error": "Describing isn't available now. Short ones still work: "
                             "rower 15, deadlift or curl 3x."}

    def generated(self, person: str, order: Order, today: dt.date) -> Session:
        profile = self.store.profile(person, order.minutes)
        return generate(profile, order, self.store.history(person), today)

    def custom(self, person: str, workout: dict, today: dt.date) -> Session:
        profile = self.store.profile(person, self.usual_minutes(person, today))
        counts = bool(workout["counts"])
        history = self.store.history(person)
        profile = replace(profile, reps=workout["reps"] or profile.reps)
        return custom.build(
            profile, workout["name"], workout["blocks"], counts, history, today
        )


def read_date(text: str, otherwise: dt.date) -> dt.date:
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return otherwise


def clamp_minutes(value: str, usual: int) -> int:
    try:
        minutes = round(int(value) / 5) * 5
    except ValueError:
        return usual
    return max(LEAST_MINUTES, min(MOST_MINUTES, minutes))


class Handler(http.server.BaseHTTPRequestHandler):
    app: App
    server_version = "reps"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 60

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 (stdlib name)
        path = getattr(self, "path", "")
        asked_often = path.startswith(("/feed/", "/api/heart", "/api/music"))
        holds_a_code = path.startswith("/spotify/callback")
        if not asked_often and not holds_a_code:
            sys.stderr.write(f"{self.client_address[0]} {format % args}\n")

    # Requests ---------------------------------------------------------------

    def cookie(self, name: str) -> str | None:
        for part in self.headers.get("Cookie", "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return None

    def known_host(self) -> bool:
        """Refuse other names: homehub's share cookies with its other services."""
        host = urllib.parse.urlsplit(f"//{self.headers.get('Host', '')}").hostname
        return host == self.app.config.host

    def same_origin(self) -> bool:
        origin = urllib.parse.urlsplit(self.headers.get("Origin", ""))
        return origin.netloc == self.headers.get("Host", "")

    def body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self.close_connection = True
            return None
        return self.rfile.read(length)

    # Responses --------------------------------------------------------------

    def send(
        self,
        status: int,
        data: bytes = b"",
        content_type: str = "",
        headers: dict[str, str] | None = None,
        cache: str = "no-store",
    ) -> None:
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CONTENT_POLICY)
        self.send_header("Referrer-Policy", "same-origin")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def theme(self) -> str:
        """Return the theme this browser keeps."""
        theme = self.cookie(THEME_COOKIE)
        return theme if theme in THEMES else "system"

    def send_page(
        self, text: str, status: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        theme = self.cookie(THEME_COOKIE)
        if theme in BARS:
            text = text.replace('<html lang="en">', f'<html lang="en" data-theme="{theme}">', 1)
            text = text.replace('content="light dark"', f'content="{THEMES[theme][1]}"', 1)
            for colour in BARS.values():
                text = text.replace(f'"theme-color" content="{colour}"',
                                    f'"theme-color" content="{BARS[theme]}"', 1)
        copied = copied_at(self.app.config)
        if copied and pages.copies_late(copied, time.time()):  # a dot on Me
            text = text.replace('<body class="', '<body class="copies-late ', 1)
        self.send(status, text.encode(), "text/html; charset=utf-8", headers)

    def send_json(self, data: object, status: int = 200) -> None:
        self.send(status, json.dumps(data).encode(), "application/json")

    def redirect(self, where: str, cookie: str = "") -> None:
        headers = {"Location": where}
        if cookie:
            headers["Set-Cookie"] = cookie
        self.send(303, headers=headers)

    def missing(self) -> None:
        self.send_page(pages.message_page("There's nothing here."), 404)

    # Routes -----------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (stdlib name)
        if not self.known_host():
            self.send_page(pages.message_page(f"Use https://{self.app.config.host}."), 421)
            return
        url = urllib.parse.urlsplit(self.path)
        query = {key: values[0] for key, values in urllib.parse.parse_qs(url.query).items()}
        if self.send_public(url.path):
            return
        if url.path.startswith("/feed/"):
            self.feed(url.path, None)
            return
        person = self.app.store.login_person(self.cookie(COOKIE))
        if person is None or person not in self.app.config.users:
            if url.path.startswith("/api/"):
                self.send_json({"error": "signed out"}, 401)
            else:
                self.send_page(login_page(), 200 if url.path == "/" else 401)
            return
        setup = self.app.store.person(person)
        if not setup["ready"] and not url.path.startswith("/setup/"):
            self.redirect(f"/setup/{pages.FIRST_STEPS[0]}")
            return
        self.get_page(person, setup, url.path, query)

    do_HEAD = do_GET  # noqa: N815 (stdlib name)

    def send_public(self, path: str) -> bool:
        icons = {f"/icon-{size}.png": size for size in ICON_SIZES}
        day, month, year = "max-age=86400", "max-age=2592000", "max-age=31536000, immutable"
        name = path.lstrip("/")
        if path in icons:
            self.send(200, icon_png(icons[path]), "image/png", cache=day)
        elif name in ASSETS:
            self.send(200, asset(name), ASSETS[name], cache=year)
        elif path == "/manifest.webmanifest":
            self.send(200, manifest().encode(), "application/manifest+json", cache=day)
        elif EMBER_FILE.fullmatch(name) and (self.app.config.ember_dir / name).is_file():
            font = (self.app.config.ember_dir / name).read_bytes()
            self.send(200, font, "font/woff2", cache=month)
        elif SPOTIFY_LOGO.fullmatch(name) and (self.app.config.spotify_dir / name).is_file():
            logo = (self.app.config.spotify_dir / name).read_bytes()
            self.send(200, logo, "image/png", cache=month)
        else:
            return False
        return True

    def get_page(self, person: str, setup: dict, path: str, query: dict[str, str]) -> None:
        store, today = self.app.store, dt.date.today()
        if path == "/":
            self.today(person, query, today)
        elif path == "/week":
            done = store.done_since(person, week_start(today))
            pick = store.picked(person, today)
            order = store.picked_order(person, today)
            if order:
                pick = {"name": FOCUSES[Order.read(order).focus]}
            self.send_page(pages.week_page(person, setup, done, today, pick))
        elif path == "/history":
            self.history(person, query, today)
        elif path.startswith("/how/"):
            self.how(person, path.removeprefix("/how/"))
        elif path.startswith("/progress/"):
            key = path.removeprefix("/progress/")
            line = next((each for each in progress.lines(store.history(person))
                         if each.exercise.key == key), None)
            if line:
                self.send_page(progress_pages.exercise_page(person, line))
            else:
                self.missing()
        elif path.startswith("/history/"):
            row = self.own_session(person, path.removeprefix("/history/"))
            if row is None:
                self.missing()
            else:
                self.send_page(pages.past_session_page(person, store, row, "sure" in query))
        elif path == "/theme":
            self.send_page(pages.theme_page(person, self.theme()))
        elif path == "/me":
            mix = store.mix(person, setup["mix"])
            self.send_page(pages.me_page(
                person, setup, len(store.workouts(person)), mix["name"] if mix else "",
                bool(store.spotify_token(person)), pages.missing_patterns(store, person),
                copied_at(self.app.config), self.theme(),
            ))
        elif path == "/setup":
            self.redirect("/me")
        elif path == "/pick":
            self.send_page(self.picker(person))
        elif path == "/generate":
            self.send_page(self.order_page(person))
        elif path in DESCRIBE_PATHS:
            wanted = query.get("prompt", "")
            words = next((each["words"] for each in store.prompts(person)
                          if str(each["id"]) == wanted), "")
            self.send_page(self.describe_form(person, path == LATER, words))
        elif path == "/weights":
            self.send_page(self.weights_form(person))
        elif path == "/gym":
            gym = store.gym()
            self.send_page(
                pages.gym_page(person, gym.step_limit_lb, gym.step_confirmed, gym.step_allows_lb)
            )
        elif path.removeprefix("/setup/") in pages.STEPS and path.startswith("/setup/"):
            self.send_page(pages.setup_page(person, path.removeprefix("/setup/"), setup))
        elif path == "/session":
            if store.active(person):
                self.send_page(pages.session_page())
            else:
                self.redirect("/")
        elif path == "/api/session":
            self.send_session(person)
        elif path == "/api/heart":
            self.app.heart.mark_here(person)
            bpm = self.app.heart.now(person)
            unsure = bpm is None and self.app.heart.unsure(person)
            self.send_json({"set": bool(setup["heart_name"]), "bpm": bpm, "unsure": unsure})
        elif path == "/api/music":
            self.send_json(self.app.music(person))
        elif path == "/music" or path.startswith("/music/"):
            self.get_music(person, setup, path, query)
        elif path.startswith("/spotify"):
            self.get_spotify(person, path, query)
        elif path == "/heart":
            self.heart_setup(person, setup, query)
        elif path == "/workouts" or path.startswith("/workouts/"):
            self.get_workouts(person, path, query)
        elif path == "/export.json":
            data = json.dumps(store.export(person), indent=1).encode()
            name = {"Content-Disposition": f'attachment; filename="reps-{person}.json"'}
            self.send(200, data, "application/json", name)
        else:
            self.missing()

    def how(self, person: str, key: str) -> None:
        guide = guide_for(key)
        if guide is None:
            self.missing()
            return
        plain = key.removeprefix("finisher-")
        name = EXERCISES[plain].name if plain in EXERCISES else custom.CARDIO[plain]
        self.send_page(pages.how_page(person, name, plain, guide))

    def history(self, person: str, query: dict[str, str], today: dt.date) -> None:
        """Show past sessions as a calendar or a list, remembering which was last chosen."""
        sessions = self.app.store.finished(person)
        view = query.get("view") or self.cookie(HISTORY_COOKIE)
        view = view if view in HISTORY_VIEWS else "calendar"
        headers = None
        if "view" in query:
            kept = f"{HISTORY_COOKIE}={view}; Path=/; Max-Age=31536000; Secure; SameSite=Lax"
            headers = {"Set-Cookie": kept}
        month = read_date(query.get("month", "") + "-01", today).replace(day=1)
        if "day" in query:
            day = read_date(query["day"], today)
            shown = [row for row in sessions if local_day(row["started"]) == day]
            text = pages.history_day_page(person, shown, day)
        elif view == "progress":
            done = self.app.store.history(person)
            views = pages.history_views(view, f"{month:%Y-%m}")
            text = progress_pages.progress_page(
                person, views, progress.by_week(done, today), progress.lines(done)
            )
        elif view == "list":
            text = pages.history_list_page(person, sessions, f"{month:%Y-%m}")
        else:
            text = pages.history_calendar_page(person, sessions, month, today)
        self.send_page(text, headers=headers)

    def own_session(self, person: str, number: str):
        return self.app.store.session(person, int(number)) if number.isdecimal() else None

    def today(self, person: str, query: dict[str, str], today: dt.date) -> None:
        usual = self.app.usual_minutes(person, today)
        minutes = clamp_minutes(query.get("minutes", ""), usual)
        order = self.app.store.picked_order(person, today)
        pick = None if order else self.app.store.picked(person, today)
        plan = self.app.custom(person, pick, today) if pick else None
        if order:
            plan = self.app.generated(person, Order.read(order), today)
        if plan is None or not plan.items:  # nothing picked, or nothing in it can be done now
            order, pick, plan = None, None, self.app.plan(person, minutes, today)
        active = self.app.store.active(person) is not None
        done = self.app.store.done_since(person, week_start(today))
        counted = sum(row["kind"] != "extra" for row in done)
        self.send_page(
            pages.today_page(
                person, plan, active, "why" in query, pick, counted, order is not None
            )
        )

    def send_session(self, person: str) -> None:
        store = self.app.store
        active = store.active(person)
        if active is None:
            self.send_json({"error": "no session"}, 404)
            return
        plan = json.loads(active["plan"])
        profile = store.profile(person, plan["minutes"])
        for item in plan["items"]:
            exercise = EXERCISES.get(item["key"])
            item["loads"] = profile.gym.loads(exercise, profile.body_kg) if exercise else ()
            item["swappable"] = exercise is not None
            item["tone"] = ui.tone_of_item(item["key"], item.get("work", 0))
            item["levels"] = LEVELS if is_levelled(item["key"]) else ()
            item["pace"] = None
            if item["kind"] == "cardio":
                load, seconds = item["sets"][0]
                asked = pace.Pace(load, item.get("incline", 0))
                item["pace"] = pace.steps(
                    item["key"], asked, profile.experience, seconds / 60, item.get("work", 0) > 0
                )
            guide = guide_for(item["key"])
            item["how"] = guide.as_dict() if guide else None
        self.send_json({
            "id": active["id"],
            "title": plan["title"],
            "items": plan["items"],
            "sets": [dict(row) for row in store.logged(active["id"])],
            "states": store.states(active["id"]),
            "paused": active["paused_at"] is not None,
        })

    def do_POST(self) -> None:  # noqa: N802 (stdlib name)
        data = self.body()
        if data is None:
            self.send(413)
            return
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/feed/") and self.known_host():
            self.feed(path, data)
            return
        if not self.known_host() or not self.same_origin():
            self.send_page(pages.message_page("That came from another site."), 403)
            return
        if path.startswith("/api/"):
            self.post_api(path, data)
            return
        form = urllib.parse.parse_qs(data.decode(errors="replace"), keep_blank_values=True)
        if path == "/login":
            self.login(form)
            return
        person = self.app.store.login_person(self.cookie(COOKIE))
        if person is None or person not in self.app.config.users:
            self.send_page(login_page(), 401)
            return
        self.post_form(person, path, form)

    def login(self, form: dict[str, list[str]]) -> None:
        address = self.client_address[0]
        if self.app.lockout.locked(address):
            self.send_page(login_page("Too many tries. Wait ten minutes."), 429)
            return
        user = self.app.shadow.person(form.get("user", [""])[0].strip().lower())
        if not self.app.shadow.verify(user, form.get("password", [""])[0]):
            self.app.lockout.record(address)
            self.send_page(login_page("That user and password don't match."), 403)
            return
        self.app.lockout.clear(address)
        token = secrets.token_urlsafe(32)
        self.app.store.add_login(token, user)
        self.redirect("/", f"{COOKIE}={token}; Path=/; Max-Age={LOGIN_SECONDS}; "
                           "HttpOnly; Secure; SameSite=Lax")

    def post_form(self, person: str, path: str, form: dict[str, list[str]]) -> None:
        store = self.app.store
        if path == "/logout":
            store.end_login(self.cookie(COOKIE))
            self.redirect("/", f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax")
        elif path == "/theme":
            theme = form.get("theme", ["system"])[0]
            theme = theme if theme in THEMES else "system"
            self.redirect("/theme", f"{THEME_COOKIE}={theme}; Path=/; Max-Age=31536000; "
                                 "Secure; SameSite=Lax")
        elif path == "/start":
            self.start(person, form)
        elif path == "/weights":
            self.save_weights(person, form)
            self.redirect("/weights")
        elif path == "/gym":
            self.save_gym(form)
        elif path == "/heart":
            self.save_heart(person, form)
        elif path == "/music" or path.startswith("/music/"):
            self.post_music(person, path, form)
        elif path == "/spotify/devices":
            self.choose_device(person, form)
        elif path == "/spotify/unlink":
            self.app.spotify.unlink(person)
            self.redirect("/spotify")
        elif path == "/pick":
            self.pick(person, form)
        elif path == "/generate" or path.startswith("/generate/"):
            self.post_generate(person, path, form)
        elif path in DESCRIBE_PATHS:
            self.describe(person, path == LATER, form.get("words", [""])[0].strip())
        elif path.removesuffix("/forget") in DESCRIBE_PATHS:
            number = form.get("prompt", [""])[0]
            if number.isdecimal():
                store.delete_prompt(person, int(number))
            self.redirect(path.removesuffix("/forget"))
        elif path == "/workouts" or path.startswith("/workouts/"):
            self.post_workouts(person, path, form)
        elif path.startswith("/setup/") and path.removeprefix("/setup/") in pages.STEPS:
            self.save_setup(person, path.removeprefix("/setup/"), form)
        elif path.startswith("/history/") and path.endswith("/delete"):
            number = path.removeprefix("/history/").removesuffix("/delete")
            if self.own_session(person, number) is None:
                self.missing()
                return
            store.delete_session(person, int(number))
            self.redirect("/history")
        else:
            self.missing()

    def start(self, person: str, form: dict[str, list[str]]) -> None:
        if self.app.store.active(person) is None:
            today = dt.date.today()
            usual = self.app.usual_minutes(person, today)
            minutes = clamp_minutes(form.get("minutes", [""])[0], usual)
            plan = self.app.plan(person, minutes, today)
            self.app.store.start(person, plan, self.app.store.mix_for(person))
        self.redirect("/session")

    # Built workouts -----------------------------------------------------------

    def own_workout(self, person: str, path: str) -> tuple[dict | None, list[str]]:
        """Return the workout a path names, if it is this person's, and the rest of the path."""
        number, *rest = path.removeprefix("/workouts/").split("/")
        found = self.app.store.workout(person, int(number)) if number.isdecimal() else None
        return found, rest

    def get_workouts(self, person: str, path: str, query: dict[str, str]) -> None:
        if path == "/workouts":
            workouts = self.app.store.workouts(person)
            self.send_page(workout_pages.workouts_page(person, workouts))
            return
        workout, rest = self.own_workout(person, path)
        if workout is None:
            self.missing()
            return
        to = block_place(query.get("to", ""), workout)
        at = insert_place(query.get("at", ""), workout)
        profile = self.app.store.profile(person, LEAST_MINUTES)
        match rest:
            case []:
                plan = self.app.custom(person, workout, dt.date.today())
                offered = {
                    key: custom.choices(profile, key) for key in workout_pages.CATEGORIES
                }
                said = query.get("said", "")[:160]
                self.send_page(workout_pages.workout_page(
                    person, workout, plan, offered, said, self.prompt_saved(person, workout)
                ))
            case ["refine"]:
                saved = self.prompt_saved(person, workout)
                self.send_page(workout_pages.refine_page(person, workout, "", "", saved))
            case ["settings"]:
                mixes = self.app.store.mixes(person)
                mine = self.app.store.person(person)["reps"]
                self.send_page(
                    workout_pages.settings_page(
                        person, workout, mixes, mine, "sure" in query
                    )
                )
            case ["add"]:
                categories = [
                    key
                    for key in workout_pages.CATEGORIES
                    if fits(workout, to, key == "cardio") and custom.choices(profile, key)
                ]
                page = workout_pages.categories_page(person, workout, categories, to, at)
                self.send_page(page)
            case ["add", category] if category in workout_pages.CATEGORIES:
                keys = custom.choices(profile, category)
                page = workout_pages.choices_page(person, workout, category, keys, to, at)
                self.send_page(page)
            case ["block", place] if block_place(place, workout) is not None:
                mine = self.app.store.person(person)["reps"]
                self.send_page(workout_pages.block_page(person, workout, int(place), mine))
            case ["block", place, "music"] if block_place(place, workout) is not None:
                found = self.app.spotify.playlists(person)
                page = workout_pages.block_music_page(
                    person, workout, int(place), found, "problem" in query
                )
                self.send_page(page)
            case _:
                self.missing()

    def prompt_saved(self, person: str, workout: dict) -> bool:
        if not workout["talk"]:
            return False
        words = describe.whole_prompt(workout["talk"])
        return any(each["words"] == words for each in self.app.store.prompts(person))

    def picker(self, person: str) -> str:
        store, today = self.app.store, dt.date.today()
        workouts = store.workouts(person)
        plans = {each["id"]: self.app.custom(person, each, today) for each in workouts}
        pick = store.picked(person, today)
        chosen = pick is not None or store.picked_order(person, today) is not None
        return workout_pages.pick_page(
            person, workouts, plans, pick["id"] if pick else None, chosen
        )

    def order_page(self, person: str) -> str:
        """Return the generator's form, as last asked or as the person's setup suggests."""
        store, today = self.app.store, dt.date.today()
        usual = self.app.usual_minutes(person, today)
        given = {"minutes": usual} | store.person(person)["ordered"]
        profile = store.profile(person, usual)
        offered = focuses(profile)
        order = Order.read(given)
        if order.focus not in offered:
            order = replace(order, focus=offered[0])
        chosen = store.picked(person, today) or store.picked_order(person, today)
        return workout_pages.generate_page(
            person, order, offered, machines(profile), profile.reps, chosen is not None
        )

    def post_generate(self, person: str, path: str, form: dict[str, list[str]]) -> None:
        store, today = self.app.store, dt.date.today()
        if path == "/generate":
            asked = Order.read({key: values[0] for key, values in form.items()})
            store.pick_generated(person, replace(asked, turn=0).as_dict())
            self.redirect("/")
            return
        picked = store.picked_order(person, today)
        if picked is None:
            self.redirect("/generate")
            return
        order = Order.read(picked)
        plan = self.app.generated(person, order, today)
        if path == "/generate/another":
            store.pick_generated(person, replace(order, turn=order.turn + 1).as_dict())
            self.redirect("/")
        elif path == "/generate/save":
            self.save_generated(person, plan, order.reps)
        elif path == "/generate/start":
            if store.active(person) is None:
                kind = "core" if "counts" in form else "extra"
                store.start(person, replace(plan, kind=kind), store.mix_for(person))
            self.redirect("/session")
        else:
            self.missing()

    def weights_form(self, person: str) -> str:
        store, today = self.app.store, dt.date.today()
        profile = store.profile(person, self.app.usual_minutes(person, today))
        history, groups = store.history(person), []
        for pattern, label in PATTERNS.items():
            found = [
                {
                    "key": each.key,
                    "name": each.name,
                    "loads": profile.gym.loads(each, profile.body_kg),
                    "now": follow(profile, each, history, today, working_sets(profile))[0].load,
                    "start": each.start,
                    "set": each.key in profile.weights,
                }
                for each in EXERCISES.values()
                if each.kind == "load" and each.pattern == pattern and available(profile, each)
            ]
            groups += [(label, found)] if found else []
        return pages.weights_page(person, groups, profile.reps)

    def save_weights(self, person: str, form: dict[str, list[str]]) -> None:
        """Keep the weights that were changed on the page, and only those."""
        profile = self.app.store.profile(person, 45)
        for key, exercise in EXERCISES.items():
            given, was = form.get(key, [""])[0], form.get(f"was-{key}", [""])[0]
            if given == was or exercise.kind != "load":
                continue
            offered = profile.gym.loads(exercise, profile.body_kg)
            load = next((each for each in (0, *offered) if f"{each:g}" == given), None)
            if load is not None:
                self.app.store.set_weight(person, key, load, profile.reps)

    def describe_form(
        self, person: str, later: bool, words: str = "", problem: str = ""
    ) -> str:
        store, today = self.app.store, dt.date.today()
        chosen = store.picked(person, today) or store.picked_order(person, today)
        prompts = store.prompts(person)
        return workout_pages.describe_page(
            person, words, problem, chosen is not None, prompts, later
        )

    def describe(self, person: str, later: bool, words: str) -> None:
        """Make a saved workout of a description, read here if it is plain, else by Claude."""
        app = self.app
        words = words[:describe.MOST_WORDS]
        profile = app.store.profile(person, app.usual_minutes(person, dt.date.today()))
        blocks, name, said = shorthand.parse(words), "", ""
        problem = "" if words else "Say what you want first."
        if blocks is None and not problem:
            answer = app.ask_claude(person, profile, words)
            problem = str(answer.get("error", ""))
            blocks = describe.blocks_from(answer, profile) if not problem else []
            name, said = str(answer.get("name", "")), str(answer.get("note", ""))[:160]
        elif blocks:
            blocks = [each for each in blocks if any(
                custom.can_do(profile, key) for key in each["options"])]
        blocks = describe.in_fives(describe.ease(blocks or [], words, profile), words)
        blocks, eased = describe.reasonable(blocks, profile)
        said = " ".join(part for part in (said, eased) if part)
        if not problem and not blocks:
            problem = "Nothing in that fits your equipment."
        if not problem and len(app.store.workouts(person)) >= workout_pages.MOST_WORKOUTS:
            problem = "You have as many saved workouts as fit. Delete one first."
        if problem:
            self.send_page(self.describe_form(person, later, words, problem), 400)
            return
        name = " ".join((name or words).split())[:workout_pages.NAME_LENGTH].strip() or "Workout"
        number = app.store.add_workout(person, name)
        app.store.save_workout(person, app.store.workout(person, number) | {"blocks": blocks})
        app.store.save_talk(person, number, [{"you": words, "note": said}])
        tail = f"?said={urllib.parse.quote(said)}" if said else ""
        self.redirect(f"/workouts/{number}{tail}")

    def refine(self, person: str, workout: dict, words: str) -> str:
        """Have Claude change a workout as asked; return what went wrong, or ''."""
        app = self.app
        words = words[:describe.MOST_WORDS]
        if not words:
            return "Say what to change first."
        profile = app.store.profile(person, app.usual_minutes(person, dt.date.today()))
        answer = app.ask_claude(person, profile, words, workout)
        if answer.get("error"):
            return str(answer["error"])
        blocks = describe.ease(describe.blocks_from(answer, profile), words, profile)
        asked = " ".join([words, *(each["you"] for each in workout["talk"])])
        blocks, eased = describe.reasonable(describe.in_fives(blocks, asked), profile)
        if not blocks:
            return "Nothing was left of it. Try other words."
        blocks = describe.keep_music(blocks, workout["blocks"])
        said = " ".join(part for part in (str(answer.get("note", ""))[:160], eased) if part)
        said = said or "Done."
        talk = [*workout["talk"], {"you": words, "note": said}][-MOST_TALK:]
        app.store.refine_workout(person, workout, blocks, talk)
        return ""

    def post_refine(self, person: str, workout: dict, form: dict[str, list[str]]) -> None:
        store, here = self.app.store, f"/workouts/{workout['id']}/refine"
        if "undo" in form:
            store.undo_refine(person, workout)
            self.redirect(here)
            return
        words = form.get("words", [""])[0].strip()
        problem = self.refine(person, workout, words)
        if problem:
            self.send_page(
                workout_pages.refine_page(person, workout, words, problem, False), 400
            )
            return
        self.redirect(here)

    def save_prompt(self, person: str, workout: dict) -> None:
        store = self.app.store
        if workout["talk"] and len(store.prompts(person)) < MOST_PROMPTS:
            store.save_prompt(person, describe.whole_prompt(workout["talk"]))
        self.redirect(f"/workouts/{workout['id']}")

    def save_generated(self, person: str, plan: Session, reps: int) -> None:
        """Keep a generated workout among the saved ones, and open it there."""
        store = self.app.store
        if len(store.workouts(person)) >= workout_pages.MOST_WORKOUTS:
            self.redirect("/workouts")
            return
        name = f"{plan.title} {dt.date.today():%-d %b}"[:workout_pages.NAME_LENGTH]
        number = store.add_workout(person, name)
        blocks = as_blocks(plan, custom.MOST_BLOCKS)
        made = {"blocks": blocks, "reps": reps}
        store.save_workout(person, store.workout(person, number) | made)
        self.redirect(f"/workouts/{number}")

    def pick(self, person: str, form: dict[str, list[str]]) -> None:
        """Make a saved workout today's plan, or (with none given) go back to the coach's."""
        given = form.get("workout", [""])[0]
        if not given:
            self.app.store.pick_workout(person, None)
        elif given.isdecimal() and self.app.store.workout(person, int(given)):
            self.app.store.pick_workout(person, int(given))
        self.redirect("/")

    def post_workouts(self, person: str, path: str, form: dict[str, list[str]]) -> None:
        store = self.app.store
        name = " ".join(form.get("name", [""])[0].split())[:workout_pages.NAME_LENGTH]
        if path == "/workouts":
            if name and len(store.workouts(person)) < workout_pages.MOST_WORKOUTS:
                self.redirect(f"/workouts/{store.add_workout(person, name)}")
            else:
                self.redirect("/workouts")
            return
        workout, rest = self.own_workout(person, path)
        if workout is None:
            self.missing()
            return
        base = f"/workouts/{workout['id']}"
        match rest:
            case ["start"]:
                self.start_workout(person, workout, "counts" in form)
            case ["settings"]:
                workout["name"] = name or workout["name"]
                music = form.get("mix", [""])[0]
                mine = {"", "off"} | {str(each["id"]) for each in store.mixes(person)}
                workout["mix"] = music if music in mine else ""
                workout["reps"] = read_reps(form)
                store.save_workout(person, workout)
                self.redirect(base)
            case ["refine"]:
                self.post_refine(person, workout, form)
            case ["prompt"]:
                self.save_prompt(person, workout)
            case ["delete"]:
                store.delete_workout(person, workout["id"])
                self.redirect("/workouts")
            case ["add"]:
                place = add_block(workout, form)
                store.save_workout(person, workout)
                self.redirect(base if place is None else f"{base}/block/{place}")
            case ["block", number] if block_place(number, workout) is not None:
                place = change_block(workout, int(number), form)
                profile = store.profile(person, 45)
                workout["blocks"], said = describe.reasonable(workout["blocks"], profile)
                store.save_workout(person, workout)
                tail = f"?said={urllib.parse.quote(said)}" if said else ""
                self.redirect(f"{base}{tail}" if place is None else f"{base}/block/{place}")
            case ["block", number, "music"] if block_place(number, workout) is not None:
                problem = self.tag_block(person, workout, int(number), form)
                here = f"{base}/block/{number}/music?problem=1"
                self.redirect(here if problem else base)
            case _:
                self.missing()

    def tag_block(
        self, person: str, workout: dict, place: int, form: dict[str, list[str]]
    ) -> bool:
        """Put a playlist on a block (or more); return whether that failed."""
        scope = form.get("scope", ["one"])[0]
        scope = scope if scope in custom.SCOPES else "one"
        music = None
        if "clear" not in form:
            found, problem = self.offered(person, form)
            wanted = form.get("add", [""])[0]
            music = next((each for each in found if each["uri"] == wanted), None)
            if problem or music is None:
                return True
        custom.tag(workout["blocks"], place, music, scope)
        self.app.store.save_workout(person, workout)
        return False

    def arrange(self, person: str, number: str, sent: dict) -> None:
        """Keep a workout's blocks as dragged, and send back how they now look."""
        store = self.app.store
        workout = store.workout(person, int(number)) if number.isdecimal() else None
        if workout is None:
            self.send_json({"error": "no workout"}, 404)
            return
        workout["blocks"] = custom.clean_blocks(sent["blocks"])
        store.save_workout(person, workout)
        plan = self.app.custom(person, workout, dt.date.today())
        self.send_json({
            "blocks": workout["blocks"],
            "flow": workout_pages.flow(workout, plan),
            "saved": workout_pages.saved_words(plan, workout["reps"]),
        })

    def start_workout(self, person: str, workout: dict, counts: bool) -> None:
        store = self.app.store
        workout["counts"] = int(counts)
        store.save_workout(person, workout)
        plan = self.app.custom(person, workout, dt.date.today())
        if not plan.items:
            self.redirect(f"/workouts/{workout['id']}")
            return
        if store.active(person) is None:
            store.start(person, plan, store.mix_for(person, workout))
        self.redirect("/session")

    # Music --------------------------------------------------------------------

    def own_mix(self, person: str, path: str) -> tuple[dict | None, list[str]]:
        number, *rest = path.removeprefix("/music/").split("/")
        found = self.app.store.mix(person, int(number)) if number.isdecimal() else None
        return found, rest

    def get_music(
        self, person: str, setup: dict, path: str, query: dict[str, str]
    ) -> None:
        store = self.app.store
        if path == "/music":
            linked = bool(store.spotify_token(person))
            usual = setup["mix"] if store.mix(person, setup["mix"]) else None
            self.send_page(
                music_pages.mixes_page(
                    person, store.mixes(person), usual, linked, query.get("note", "")[:60]
                )
            )
            return
        mix, rest = self.own_mix(person, path)
        if mix is None:
            self.missing()
            return
        match rest:
            case []:
                self.send_page(music_pages.mix_page(person, mix))
            case ["settings"]:
                self.send_page(music_pages.mix_settings_page(person, mix, "sure" in query))
            case [stage] if stage in stages.STAGES:
                page = music_pages.stage_page(person, mix, stage, "problem" in query)
                self.send_page(page)
            case [stage, "add"] if stage in stages.STAGES:
                found = self.app.spotify.playlists(person)
                self.send_page(music_pages.playlists_page(person, mix, stage, found))
            case _:
                self.missing()

    def offered(
        self, person: str, form: dict[str, list[str]]
    ) -> tuple[list[dict], bool]:
        """Return the playlists a form may add from, and whether a pasted link failed."""
        if not form.get("link", [""])[0].strip():  # the box comes along empty when a row is tapped
            return self.app.spotify.playlists(person), False
        found = self.app.spotify.playlist(person, form["link"][0])
        if found is None:
            return [], True
        form["add"] = [found["uri"]]
        return [found], False

    def post_music(self, person: str, path: str, form: dict[str, list[str]]) -> None:
        store = self.app.store
        name = " ".join(form.get("name", [""])[0].split())[:workout_pages.NAME_LENGTH]
        if path == "/music":
            if name and len(store.mixes(person)) < music_pages.MOST_MIXES:
                self.redirect(f"/music/{store.add_mix(person, name)}")
            else:
                self.redirect("/music")
            return
        if path == "/music/usual":
            given = form.get("mix", [""])[0]
            number = int(given) if given.isdecimal() else None
            note = self.app.use_mix(person, number) if number or not given else ""
            self.redirect("/music" + (f"?note={urllib.parse.quote(note)}" if note else ""))
            return
        mix, rest = self.own_mix(person, path)
        if mix is None:
            self.missing()
            return
        base = f"/music/{mix['id']}"
        match rest:
            case ["settings"]:
                mix["name"] = name or mix["name"]
                store.save_mix(person, mix)
                self.redirect(base)
            case ["delete"]:
                store.delete_mix(person, mix["id"])
                self.redirect("/music")
            case [stage] if stage in stages.STAGES:
                playlists, problem = self.offered(person, form)
                change_stage(mix, stage, form, playlists)
                store.save_mix(person, mix)
                self.redirect(f"{base}/{stage}" + ("?problem=1" if problem else ""))
            case _:
                self.missing()

    # Heart rate ---------------------------------------------------------------

    def heart_setup(self, person: str, setup: dict, query: dict[str, str]) -> None:
        """Show what is nearby, redrawn every few seconds while looking goes on."""
        heart = self.app.heart
        if "scan" in query:
            heart.scan()
        looking = heart.scanning()
        text = workout_pages.heart_page(person, setup["heart_name"], heart.nearby(), looking)
        if looking:
            self.send_page(text, headers={"Refresh": f"{SCAN_REFRESH_SECONDS}; url=/heart"})
        else:
            self.send_page(text)

    def save_heart(self, person: str, form: dict[str, list[str]]) -> None:
        if "forget" in form:
            self.app.store.save_person(person, heart_name="", heart_address="")
        address, _, name = form.get("source", [""])[0].partition(" ")
        if (name, address) in self.app.heart.nearby():
            self.app.store.save_person(person, heart_name=name, heart_address=address)
        self.redirect("/me" if "source" in form else "/heart")

    def device_list(self, person: str) -> str:
        devices, problem = [], ""
        try:
            devices = self.app.spotify.devices(person)
        except RefusedError:
            problem = "Link again so reps may see your devices."
        except (FailedError, UnlinkedError):
            problem = "Spotify didn't answer. Try again, or link again."
        speaker = self.app.store.person(person)["speaker"]
        return workout_pages.devices_page(person, speaker, devices, problem)

    def choose_device(self, person: str, form: dict[str, list[str]]) -> None:
        name = form.get("device", [""])[0]
        try:
            known = {each["name"] for each in self.app.spotify.devices(person)}
        except (FailedError, RefusedError, UnlinkedError):
            known = set()
        if not name or name in known:
            self.app.use_speaker(person, name)
        self.redirect("/spotify/devices")

    def get_spotify(self, person: str, path: str, query: dict[str, str]) -> None:
        spotify = self.app.spotify
        if path == "/spotify/link" and spotify.client_id:
            self.redirect(spotify.link_address(person))
        elif path == "/spotify/callback":
            worked = spotify.linked(person, query.get("state", ""), query.get("code", ""))
            if worked:
                self.app.store.mark_spotify_linked(person)
            self.redirect("/spotify" if worked else "/spotify?failed=1")
        elif path == "/spotify/devices":
            self.send_page(self.device_list(person))
        elif path == "/spotify":
            note = "That didn't link. Try again." if "failed" in query else ""
            linked = self.app.store.spotify_linked(person)
            ready = bool(spotify.client_id)
            speaker = self.app.store.person(person)["speaker"]
            self.send_page(workout_pages.spotify_page(person, ready, linked, speaker, note))
        else:
            self.missing()

    def feed(self, path: str, data: bytes | None) -> None:
        """Serve the heart rate receiver, which signs each request with its key."""
        app = self.app
        given = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if not app.heart_key or not hmac.compare_digest(given.encode(), app.heart_key.encode()):
            self.send_json({"error": "not allowed"}, 401)
            return
        try:
            sent = json.loads(data) if data else {}
            if path == "/feed/wanted":
                watch = [
                    source | {"here": app.heart.here(source["person"])
                              or app.store.active(source["person"]) is not None}
                    for source in app.store.heart_sources()
                ]
                self.send_json({"watch": watch, "scan": app.heart.scanning()})
            elif path == "/feed/nearby":
                app.heart.found(sent["devices"])
                self.send_json({})
            elif path == "/feed/unsure":
                app.heart.mark_unsure(str(sent["person"]))
                self.send_json({})
            elif path == "/feed/heart":
                self.take_reading(str(sent["person"]), int(sent["bpm"]))
            else:
                self.send_json({"error": "unknown"}, 404)
        except (AttributeError, KeyError, TypeError, ValueError):
            self.send_json({"error": "bad request"}, 400)

    def take_reading(self, person: str, bpm: int) -> None:
        app = self.app
        if person not in app.config.users or not LEAST_BPM <= bpm <= MOST_BPM:
            raise ValueError("reading")
        app.heart.reading(person, bpm)
        active = app.store.active(person)
        if active:
            app.store.keep_heart(active["id"], bpm)
        self.send_json({})

    def save_gym(self, form: dict[str, list[str]]) -> None:
        try:
            limit = float(form.get("step_limit_lb", [""])[0])
        except ValueError:
            limit = 0
        if 50 <= limit <= 1500:
            confirmed = "step_confirmed" in form
            self.app.store.save_gym(step_limit_lb=limit, step_confirmed=confirmed)
        self.redirect("/gym")

    def save_setup(self, person: str, step: str, form: dict[str, list[str]]) -> None:
        store = self.app.store
        setup = store.person(person)
        fields, error = read_setup(step, form, setup)
        if error:
            self.send_page(pages.setup_page(person, step, setup | fields, error), 400)
            return
        store.save_person(person, **fields)
        if setup["ready"]:
            self.redirect("/me")
            return
        place = pages.FIRST_STEPS.index(step) + 1 if step in pages.FIRST_STEPS else 0
        if place < len(pages.FIRST_STEPS):
            self.redirect(f"/setup/{pages.FIRST_STEPS[place]}")
        else:
            store.save_person(person, ready=1)
            self.redirect("/")

    def post_api(self, path: str, data: bytes) -> None:
        store = self.app.store
        person = store.login_person(self.cookie(COOKIE))
        if person is None or person not in self.app.config.users:
            self.send_json({"error": "signed out"}, 401)
            return
        try:
            sent = json.loads(data)
            if path == "/api/music":
                note = self.app.control_music(person, str(sent["action"]), sent.get("mix"))
                state = self.app.music(person)
                self.send_json(state | {"note": note or state["note"]})
                return
            if path.startswith("/api/workouts/"):
                self.arrange(person, path.removeprefix("/api/workouts/"), sent)
                return
            session = store.session(person, int(sent["session"]))
            if session is None:
                self.send_json({"error": "no session"}, 404)
            elif path == "/api/events":
                for event in sent["events"]:
                    check_event(event, json.loads(session["plan"]))
                    store.apply_event(person, session["id"], event)
                self.send_json({"kept": [event["cid"] for event in sent["events"]]})
            elif path == "/api/swap":
                self.swap(person, session, str(sent["item"]))
            elif path == "/api/discard":
                if session["ended"] is None:
                    store.discard_active(person)
                self.send_json({})
            else:
                self.send_json({"error": "unknown"}, 404)
        except (KeyError, TypeError, ValueError) as problem:
            self.send_json({"error": f"bad request: {problem}"}, 400)

    def swap(self, person: str, session, key: str) -> None:
        store = self.app.store
        plan = json.loads(session["plan"])
        used = {item["key"] for item in plan["items"]}
        if key not in used or key not in EXERCISES:
            raise ValueError("not swappable")
        replaced = next(item for item in plan["items"] if item["key"] == key)
        profile = store.profile(person, plan["minutes"])
        profile = replace(profile, reps=replaced.get("reps", 0))
        today = dt.date.today()
        other = swap(profile, key, used, store.history(person), today)
        if other is None:
            self.send_json({"error": "Nothing else for this with your equipment."}, 409)
            return
        plan["items"] = [
            asdict(other) if item["key"] == key else item for item in plan["items"]
        ]
        store.replace_plan(session["id"], plan)
        self.send_session(person)


def block_place(text: str, workout: dict) -> int | None:
    if text.isdecimal() and int(text) < len(workout["blocks"]):
        return int(text)
    return None


def insert_place(text: str, workout: dict) -> int | None:
    if text.isdecimal() and int(text) <= len(workout["blocks"]):
        return int(text)
    return None


def fits(workout: dict, to: int | None, cardio: bool) -> bool:
    """Return whether cardio, or an exercise, may join block ``to`` (a new block takes either)."""
    return to is None or custom.is_cardio(workout["blocks"][to]) == cardio


def add_block(workout: dict, form: dict[str, list[str]]) -> int | None:
    """Add an exercise as a new block, or as a choice in block ``to``; return its place."""
    key = form.get("key", [""])[0]
    if key not in EXERCISES and key not in custom.CARDIO:
        return None
    blocks = workout["blocks"]
    to = block_place(form.get("to", [""])[0], workout)
    if to is not None:
        options = blocks[to]["options"]
        room = len(options) < custom.MOST_OPTIONS
        if room and key not in options and fits(workout, to, key in custom.CARDIO):
            options.append(key)
        return to
    if len(blocks) >= custom.MOST_BLOCKS:
        return None
    at = insert_place(form.get("at", [""])[0], workout)
    at = len(blocks) if at is None else at
    blocks.insert(at, custom.new_block(key))
    return at


def change_block(workout: dict, place: int, form: dict[str, list[str]]) -> int | None:
    """Apply a block's form; return the block to show next, or None for the workout."""
    blocks = workout["blocks"]
    block = blocks[place]
    if "sets" in block and "reps" in form:
        block["reps"] = read_reps(form)
    for field, most in (("minutes", custom.MOST_MINUTES), ("sets", custom.MOST_SETS)):
        given = form.get(field, [""])[0]
        if field in block and given.isdecimal():
            block[field] = max(1, min(most, int(given)))
    style = form.get("style", [""])[0]
    if "minutes" in block and style in STYLES:
        block["style"] = style
    level = form.get("level", [""])[0]
    if "minutes" in block and level.isdecimal() and int(level) <= len(LEVELS):
        block["level"] = int(level)
    if "minutes" in block:
        for field in ("watts", "speed", "incline"):
            if field in form:
                block[field] = read_pace(field, form[field][0])
    if "remove" in form:
        del blocks[place]
        return None
    if "drop" in form:
        key = form["drop"][0]
        if key in block["options"] and len(block["options"]) > 1:
            block["options"].remove(key)
        return place
    step = {"up": -1, "down": 1}.get(form.get("move", [""])[0], 0)
    if step and 0 <= place + step < len(blocks):
        blocks[place], blocks[place + step] = blocks[place + step], blocks[place]
        return place + step
    return None


def read_reps(form: dict[str, list[str]]) -> int:
    given = form.get("reps", [""])[0]
    return custom.clamp_reps(int(given)) if given.isdecimal() else 0


def read_pace(field: str, text: str) -> float:
    try:
        return pace.clean({field: float(text)})[field]
    except ValueError:
        return 0


def change_stage(
    mix: dict, stage: str, form: dict[str, list[str]], playlists: list[dict]
) -> None:
    """Add one of the person's own playlists to a stage, or take one out."""
    chosen = mix["stages"].setdefault(stage, [])
    if "drop" in form:
        mix["stages"][stage] = [each for each in chosen if each["uri"] != form["drop"][0]]
        return
    wanted = form.get("add", [""])[0]
    found = next((each for each in playlists if each["uri"] == wanted), None)
    room = len(chosen) < music_pages.MOST_CHOICES
    if found and room and found not in chosen:
        chosen.append(found)


def check_event(event: dict, plan: dict) -> None:
    """Refuse an event that isn't about this session's exercises, or that can't be true."""
    if not isinstance(event.get("cid"), str) or not 8 <= len(event["cid"]) <= 64:
        raise ValueError("cid")
    if event["type"] == "finish":
        return
    if event["type"] in ("pause", "resume"):
        if not math.isfinite(float(event["at"])):
            raise ValueError("at")
        return
    keys = {item["key"] for item in plan["items"]}
    if event["item"] not in keys:
        raise ValueError("item")
    if event["type"] == "set":
        if not 0 <= int(event["n"]) < 20 or not 0 < int(event["amount"]) <= 7200:
            raise ValueError("set")
        kinds = {item["key"]: item["kind"] for item in plan["items"]}
        load, incline = float(event["load"]), float(event.get("incline", 0))
        if kinds[event["item"]] == "cardio":
            known = pace.possible(event["item"], load, incline)
        else:
            known = load in (0, *DIAL_LOADS) and incline == 0
        if not known:
            raise ValueError("load")


def read_setup(
    step: str, form: dict[str, list[str]], setup: dict
) -> tuple[dict[str, object], str]:
    """Return a setup screen's fields, and what's wrong with them ('' if nothing)."""
    if step == "goals":
        goals = [goal for goal in GOALS if goal in form.get("goals", [])]
        kept = [goal for goal in setup["goals"] if goal in goals]
        goals = kept + [goal for goal in goals if goal not in kept]
        fields = {"goals": goals} | ({"reps": read_reps(form)} if "reps" in form else {})
        if "hold" in form:
            fields["hold"] = int(form["hold"][0] == "1")
        return fields, "" if 1 <= len(goals) <= 2 else "Pick one or two goals."
    if step == "equipment":
        return {"equipment": [key for key in EQUIPMENT if key in form.get("equipment", [])]}, ""
    if step == "time":
        chosen = {
            str(day): clamp_minutes(form.get(f"day{day}", [""])[0], 0) for day in range(7)
        }
        return {
            "minutes": clamp_minutes(form.get("minutes", [""])[0], 45),
            "day_minutes": {day: minutes for day, minutes in chosen.items() if minutes},
        }, ""
    if step == "days":
        days = [day for day in range(7) if str(day) in form.get("days", [])]
        enough = len(days) >= CORE_SESSIONS
        return {"days": days}, "" if enough else "Pick three days or more."
    if step == "body":
        return _read_body(form)
    avoid = [key for key in PATTERNS if key in form.get("avoid", [])]
    hurts = [key for key in setup["hurts"] if key in form.get("hurts", [])]
    return {"avoid": avoid, "hurts": hurts}, ""


def _read_body(form: dict[str, list[str]]) -> tuple[dict[str, object], str]:
    experience = form.get("experience", ["returning"])[0]
    fields: dict[str, object] = {
        "experience": experience if experience in pages.EXPERIENCE else "returning"
    }
    typed = form.get("body_kg", [""])[0].strip().replace(",", ".")
    if not typed:
        return fields | {"body_kg": None}, ""
    try:
        weight = float(typed)
    except ValueError:
        weight = 0
    if not 30 <= weight <= 250:
        return fields, "Body weight in kilograms, or leave it empty."
    return fields | {"body_kg": weight}, ""


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config: Config, context: ssl.SSLContext | None) -> None:
        self.context = context
        handler = type("BoundHandler", (Handler,), {"app": App(config)})
        super().__init__((config.bind, config.port), handler)

    def process_request_thread(self, request, client_address) -> None:
        """Shake hands here, in the connection's own thread, so a slow one holds up nobody."""
        if self.context:
            try:
                request.settimeout(20)
                if request.recv(1, socket.MSG_PEEK) != TLS_HANDSHAKE:
                    self.send_to_https(request)
                    return
                request = self.context.wrap_socket(request, server_side=True)
            except OSError:
                request.close()
                return
        super().process_request_thread(request, client_address)


    def handle_error(self, request, client_address) -> None:
        """Browsers drop idle connections all the time; only real faults are worth a trace."""
        if not isinstance(sys.exception(), ConnectionError | TimeoutError):
            super().handle_error(request, client_address)

    def send_to_https(self, connection: socket.socket) -> None:
        """Answer plain HTTP on the HTTPS port (http://reps) with the way in."""
        host = self.RequestHandlerClass.app.config.host
        asked = re.search(
            rb"^Host:[ \t]*[^\s:]+(:\d{1,5})?[ \t]*\r?$", connection.recv(4096), re.M | re.I
        )
        port = asked.group(1).decode() if asked and asked.group(1) else ""
        connection.sendall(
            f"HTTP/1.1 301 Moved Permanently\r\nLocation: https://{host}{port}/\r\n"
            "Content-Length: 0\r\nConnection: close\r\n\r\n".encode()
        )
        connection.close()


def serve(config: Config) -> None:
    context = None
    if config.tls_dir:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(config.tls_dir / "server.crt", config.tls_dir / "server.key")
    server = Server(config, context)
    scheme = "https" if context else "http"
    print(f"reps on {scheme}://{config.host}:{config.port}", file=sys.stderr, flush=True)
    server.serve_forever()
