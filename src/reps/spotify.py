"""The song playing on a person's Spotify: linking, tokens and asking.

Sign-in is Authorization Code with PKCE, so there is no client secret. The song playing is asked
for only while a session screen is open, and isn't kept with workouts. Playlists are read when
building a mix, and started when a session's mix says so.
"""

import base64
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

AUTHORIZE = "https://accounts.spotify.com/authorize"
TOKEN = "https://accounts.spotify.com/api/token"
PLAYING = "https://api.spotify.com/v1/me/player/currently-playing?additional_types=track,episode"
PLAY = "https://api.spotify.com/v1/me/player/play"
PLAYER = "https://api.spotify.com/v1/me/player"
PAUSE = "https://api.spotify.com/v1/me/player/pause"
NEXT = "https://api.spotify.com/v1/me/player/next"
PREVIOUS = "https://api.spotify.com/v1/me/player/previous"
QUEUE = "https://api.spotify.com/v1/me/player/queue"
SEEK = "https://api.spotify.com/v1/me/player/seek?position_ms="
PLAYLIST = "https://api.spotify.com/v1/playlists/"
PLAYLIST_LINK = re.compile(
    r"(?:https://open\.spotify\.com/(?:intl-\w+/)?playlist/|spotify:playlist:)([A-Za-z0-9]{22})\b"
)
PLAYLISTS = "https://api.spotify.com/v1/me/playlists?limit=50&offset="
DEVICES = "https://api.spotify.com/v1/me/player/devices"
SCOPE = (
    "user-read-currently-playing user-read-playback-state user-modify-playback-state "
    "playlist-read-private"
)

ASK_SECONDS = 3
LEAST_WAIT = 10  # once Spotify says to slow down
SETTLE_SECONDS = 1
AFTER_END = 0.4  # seconds after a song for the next to have begun
SETTLE_WAITS = (0.2, 0.3, 0.4, 0.5, 0.6)  # between looks for the song to have changed
LINK_SECONDS = 600  # how long a person has to say yes at Spotify
DEFAULT_WAIT = 30
PLAYLISTS_SECONDS = 300
MOST_PLAYLISTS = 200
ARTWORK_HOST = "i.scdn.co"


class UnlinkedError(Exception):
    """Spotify no longer accepts this person's link."""


class NoDeviceError(Exception):
    """Spotify isn't open anywhere it can play."""


class RefusedError(Exception):
    """Spotify won't do this for this person (no Premium, or not on the app's list)."""


class FailedError(Exception):
    """Spotify couldn't answer; ``wait`` is how long to leave it alone."""

    def __init__(self, wait: float = DEFAULT_WAIT) -> None:
        super().__init__(f"no answer, wait {wait:.0f} s")
        self.wait = wait


def call(request: urllib.request.Request) -> tuple[int, bytes, dict[str, str]]:
    """Return a response's status, body and headers, whatever the status."""
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:
            return answer.status, answer.read(), dict(answer.headers)
    except urllib.error.HTTPError as problem:
        return problem.code, problem.read(), dict(problem.headers)
    except OSError as problem:
        raise FailedError from problem


def song_from(playing: dict) -> dict | None:
    """Return what to show for Spotify's answer: a song or episode, playing or paused, or None."""
    item = playing.get("item")
    if not item:
        return None
    if item.get("type") == "episode":
        by, images = item.get("show", {}).get("name", ""), item.get("images", [])
    else:
        by = ", ".join(artist["name"] for artist in item.get("artists", []))
        images = item.get("album", {}).get("images", [])
    link = item.get("external_urls", {}).get("spotify", "")
    if not link.startswith("https://open.spotify.com/"):
        link = "https://open.spotify.com/"  # a local file has no page of its own
    return {"title": item.get("name", ""), "by": by, "link": link, "art": _smallest(images),
            "playing": bool(playing.get("is_playing"))}


def _end_of(playing: dict) -> float:
    """Return when the song playing ends, or 0."""
    length = (playing.get("item") or {}).get("duration_ms")
    if not playing.get("is_playing") or not length:
        return 0.0
    return time.time() + max(0, length - (playing.get("progress_ms") or 0)) / 1000


def _smallest(images: list[dict]) -> str:
    ours = [
        image["url"]
        for image in sorted(images, key=lambda each: each.get("width") or 0)
        if urllib.parse.urlsplit(image["url"]).hostname == ARTWORK_HOST
    ]
    return ours[0] if ours else ""


@dataclass
class _Person:
    access: str = ""
    access_until: float = 0.0
    song: dict | None = None
    ask_at: float = 0.0
    ends_at: float = 0.0  # when the song playing will, 0 if it isn't known
    following: dict | None = None  # the song after it
    following_of: str = ""
    queue_at: float = 0.0
    playlists: list[dict] | None = None
    playlists_until: float = 0.0


class Spotify:
    """``load`` and ``save`` keep each person's refresh token; saving '' forgets it."""

    def __init__(
        self,
        client_id: str,
        redirect: str,
        load: Callable[[str], str],
        save: Callable[[str, str], None],
        send: Callable = call,
    ) -> None:
        self.client_id, self.redirect = client_id, redirect
        self.load, self.save, self.send = load, save, send
        self.lock = threading.Lock()
        self.asking: dict[str, threading.Lock] = {}
        self.people: dict[str, _Person] = {}
        self.waiting: dict[str, tuple[str, str, float]] = {}  # state -> person, verifier, until

    # Linking ----------------------------------------------------------------

    def link_address(self, person: str) -> str:
        """Return where to send a person to say yes."""
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(64)
        with self.lock:
            now = time.time()
            self.waiting = {key: kept for key, kept in self.waiting.items() if kept[2] > now}
            self.waiting[state] = (person, verifier, now + LINK_SECONDS)
        digest = hashlib.sha256(verifier.encode()).digest()
        return AUTHORIZE + "?" + urllib.parse.urlencode({
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect,
            "state": state,
            "scope": SCOPE,
            "show_dialog": "true",  # else Spotify silently reuses whoever is signed in there
            "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(digest).decode().rstrip("="),
        })

    def linked(self, person: str, state: str, code: str) -> bool:
        """Finish linking with what Spotify sent back, if it is this person's own request."""
        with self.lock:
            asked, verifier, until = self.waiting.pop(state, ("", "", 0.0))
        if asked != person or time.time() > until or not code:
            return False
        try:
            self._tokens(person, {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect,
                "code_verifier": verifier,
            })
        except (FailedError, UnlinkedError):
            return False
        return bool(self.load(person))

    def unlink(self, person: str) -> None:
        with self._asking(person):
            self._forget(person)

    def _asking(self, person: str) -> threading.Lock:
        with self.lock:
            return self.asking.setdefault(person, threading.Lock())

    def _forget(self, person: str) -> None:
        self.save(person, "")
        self.people.pop(person, None)

    # Asking -----------------------------------------------------------------

    def song(self, person: str) -> dict | None:
        """Return the song playing, asking Spotify at most every ``ASK_SECONDS``."""
        if not self.client_id or not self.load(person):
            return None
        with self._asking(person):
            kept = self.people.setdefault(person, _Person())
            if time.time() >= kept.ask_at:
                kept.ask_at = time.time() + ASK_SECONDS
                try:
                    kept.song = self._ask(person, kept)
                    if kept.ends_at:  # not a moment late for the next one
                        kept.ask_at = min(kept.ask_at, kept.ends_at + AFTER_END)
                except FailedError as problem:
                    kept.song, kept.ask_at = None, time.time() + problem.wait
                except UnlinkedError:
                    self._forget(person)
                    return None
            return kept.song

    def _ask(self, person: str, kept: _Person) -> dict | None:
        status, body, headers = self._signed(person, kept, PLAYING)
        kept.ends_at = 0.0
        if status == 200:
            playing = _json(body) if body else {}
            kept.ends_at = _end_of(playing)
            return song_from(playing)
        if status == 204:
            return None
        raise FailedError(_wait(headers))

    def _signed(
        self, person: str, kept: _Person, url: str, method: str = "GET", data: dict | None = None
    ) -> tuple[int, bytes, dict[str, str]]:
        """Make a request as a person, renewing their access once if Spotify refuses it."""
        body = None if data is None else json.dumps(data).encode()
        if time.time() >= kept.access_until:
            self._refresh(person)
        for last in (False, True):
            request = urllib.request.Request(url, body, method=method)
            request.add_header("Authorization", f"Bearer {kept.access}")
            if body:
                request.add_header("Content-Type", "application/json")
            answer = self.send(request)
            if answer[0] != 401 or last:
                break
            self._refresh(person)
        return answer

    def ends_in(self, person: str) -> float | None:
        """Return the seconds until the song playing ends, or None if that isn't known."""
        ends_at = self.people.setdefault(person, _Person()).ends_at
        return max(0.0, ends_at - time.time()) + AFTER_END if ends_at else None

    def following(self, person: str, song: dict | None) -> dict | None:
        """Return the song after this one, asked for once a song, to show at once if wanted."""
        if not song or not song.get("link") or not self.client_id or not self.load(person):
            return None
        with self._asking(person):
            kept = self.people.setdefault(person, _Person())
            if kept.following_of == song["link"] or time.time() < kept.queue_at:
                return kept.following if kept.following_of == song["link"] else None
            kept.following, kept.following_of = None, song["link"]
            try:
                status, body, headers = self._signed(person, kept, QUEUE)
                if status == 200:
                    queue = _json(body).get("queue") or [None]
                    kept.following = song_from({"item": queue[0], "is_playing": True})
                elif status == 429:
                    kept.queue_at = time.time() + _wait(headers)
            except (FailedError, UnlinkedError):
                pass
            return kept.following

    def settle(self, person: str, before: dict | None) -> None:
        """Wait, a moment at most, for Spotify to say the song has changed."""
        for wait in SETTLE_WAITS:
            time.sleep(wait)
            with self._asking(person):
                self.people.setdefault(person, _Person()).ask_at = 0.0
            now = self.song(person)
            if now and (not before or now["link"] != before["link"]):
                return

    def devices(self, person: str) -> list[dict]:
        """Return the Connect devices reps may play on, as {id, name, active}."""
        if not self.client_id or not self.load(person):
            return []
        with self._asking(person):
            kept = self.people.setdefault(person, _Person())
            try:
                status, body, headers = self._signed(person, kept, DEVICES)
            except UnlinkedError:
                self._forget(person)
                raise
        if status in (401, 403):
            raise RefusedError  # linked before reps asked for this permission
        if status != 200:
            raise FailedError(_wait(headers))
        return [
            {"id": each["id"], "name": each["name"], "active": bool(each.get("is_active"))}
            for each in _json(body).get("devices", [])
            if each.get("id") and not each.get("is_restricted")
        ]

    def play(self, person: str, uri: str, device: str = "") -> None:
        """Start a playlist on the device with this name, or wherever Spotify is active."""
        address = PLAY
        if device:
            # A device's id changes, so the name is what's kept.
            found = [each for each in self.devices(person) if each["name"] == device]
            if not found:
                raise NoDeviceError
            address += "?" + urllib.parse.urlencode({"device_id": found[0]["id"]})
        self._command(person, "PUT", address, {"context_uri": uri})

    def move(self, person: str, device: str) -> None:
        """Move what is playing (or paused) to the device with this name."""
        found = [each for each in self.devices(person) if each["name"] == device]
        if not found:
            raise NoDeviceError
        if not found[0]["active"]:
            self._command(person, "PUT", PLAYER, {"device_ids": [found[0]["id"]]})

    def resume(self, person: str) -> None:
        self._command(person, "PUT", PLAY)

    def pause(self, person: str) -> None:
        self._command(person, "PUT", PAUSE)

    def skip(self, person: str) -> None:
        self._command(person, "POST", NEXT)

    def previous(self, person: str) -> None:
        self._command(person, "POST", PREVIOUS)

    def seek(self, person: str, seconds: int) -> None:
        """Go forward in what is on, or back; past its end is the next one."""
        with self._asking(person):
            kept = self.people.setdefault(person, _Person())
            try:
                status, body, headers = self._signed(person, kept, PLAYING)
            except UnlinkedError:
                self._forget(person)
                raise
        if status in (401, 403):
            raise RefusedError
        if status not in (200, 204):
            raise FailedError(_wait(headers))
        playing = _json(body) if status == 200 and body else {}
        if not playing.get("item"):
            raise NoDeviceError
        to = max(0, (playing.get("progress_ms") or 0) + seconds * 1000)
        length = playing["item"].get("duration_ms")
        if length and to >= length:
            self.skip(person)
        else:
            self._command(person, "PUT", f"{SEEK}{to}")

    def _command(
        self, person: str, method: str, address: str, data: dict | None = None
    ) -> None:
        with self._asking(person):
            kept = self.people.setdefault(person, _Person())
            try:
                status, _body, headers = self._signed(person, kept, address, method, data or {})
            except UnlinkedError:
                self._forget(person)
                raise
            kept.ask_at = time.time() + SETTLE_SECONDS  # Spotify takes a moment to say so
        if status == 404:
            raise NoDeviceError
        if status in (401, 403):
            raise RefusedError
        if not 200 <= status < 300:
            raise FailedError(_wait(headers))

    def playlist(self, person: str, link: str) -> dict | None:
        """Return the playlist a pasted link names, if Spotify lets this person open it.

        Prompted playlists are left out of the list Spotify gives, but open by their link.
        """
        named = PLAYLIST_LINK.match(link.strip())
        if not named or not self.client_id or not self.load(person):
            return None
        with self._asking(person):
            kept = self.people.setdefault(person, _Person())
            try:
                address = f"{PLAYLIST}{named[1]}?fields=name,uri"
                status, body, _headers = self._signed(person, kept, address)
                found = _json(body) if status == 200 else {}
            except FailedError:
                return None
            except UnlinkedError:
                self._forget(person)
                return None
        if found.get("uri") != f"spotify:playlist:{named[1]}":
            return None
        return {"uri": found["uri"], "name": found.get("name") or "Untitled"}

    def playlists(self, person: str) -> list[dict]:
        """Return a person's playlists as {uri, name}, asked for at most every few minutes."""
        if not self.client_id or not self.load(person):
            return []
        with self._asking(person):
            kept = self.people.setdefault(person, _Person())
            if kept.playlists is None or time.time() >= kept.playlists_until:
                try:
                    kept.playlists = self._all_playlists(person, kept)
                    kept.playlists_until = time.time() + PLAYLISTS_SECONDS
                except FailedError:
                    return kept.playlists or []
                except UnlinkedError:
                    self._forget(person)
                    return []
            return kept.playlists

    def _all_playlists(self, person: str, kept: _Person) -> list[dict]:
        found: list[dict] = []
        for offset in range(0, MOST_PLAYLISTS, 50):
            status, body, headers = self._signed(person, kept, f"{PLAYLISTS}{offset}")
            if status != 200:
                raise FailedError(_wait(headers))
            page = _json(body)
            found += [
                {"uri": each["uri"], "name": each.get("name") or "Untitled"}
                for each in page.get("items", [])
                if each and each.get("uri", "").startswith("spotify:playlist:")
            ]
            if not page.get("next"):
                break
        return found

    def _refresh(self, person: str) -> None:
        self._tokens(person, {
            "grant_type": "refresh_token", "refresh_token": self.load(person),
        })

    def _tokens(self, person: str, fields: dict[str, str]) -> None:
        body = urllib.parse.urlencode(fields | {"client_id": self.client_id}).encode()
        status, answer, headers = self.send(urllib.request.Request(TOKEN, body))
        if status == 400 and _json(answer).get("error") == "invalid_grant":
            raise UnlinkedError
        if status != 200:
            raise FailedError(_wait(headers))
        tokens = _json(answer)
        kept = self.people.setdefault(person, _Person())
        kept.access = tokens["access_token"]
        kept.access_until = time.time() + float(tokens.get("expires_in", 3600)) - 60
        if tokens.get("refresh_token"):  # when there's no new one, the old one stands
            self.save(person, tokens["refresh_token"])


def _json(body: bytes) -> dict:
    try:
        found = json.loads(body)
    except ValueError as problem:
        raise FailedError from problem
    return found if isinstance(found, dict) else {}


def _wait(headers: dict[str, str]) -> float:
    given = {key.lower(): value for key, value in headers.items()}.get("retry-after", "")
    return max(float(given), LEAST_WAIT) if given.isdecimal() else DEFAULT_WAIT
