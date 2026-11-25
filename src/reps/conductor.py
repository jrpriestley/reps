"""Starts a mix's playlists as a session moves through its stages.

It acts only when the stage changes, so whatever a person plays in between is left alone, and it
never starts music they have stopped.
"""

import threading
import time
from dataclasses import dataclass

from reps.spotify import FailedError, NoDeviceError, RefusedError, Spotify, UnlinkedError

DEVICE_RETRY_SECONDS = 15
OPEN_SPOTIFY = "Open Spotify on your phone and press play"
REFUSED = "Spotify won't let reps change the music"


@dataclass
class _Playing:
    session: int = 0
    moment: str = ""
    uri: str = ""
    retry_at: float = 0.0
    note: str = ""


class Conductor:
    def __init__(self, spotify: Spotify) -> None:
        self.spotify = spotify
        self.lock = threading.Lock()
        self.people: dict[str, _Playing] = {}

    def restart(self, person: str) -> None:
        """Start the stage's playlist afresh the next time, as they have just asked for music."""
        with self.lock:
            self.people.pop(person, None)

    def follow(
        self,
        person: str,
        session: int,
        moment: str,
        wanted: dict | None,
        playing: bool,
        device: str = "",
    ) -> str:
        """Start the wanted playlist if the moment is new; return what to tell the person, or ''.

        A moment is a stage, or a block with a playlist of its own.

        The chosen device is where a session's music starts. After that it stays wherever they
        have it, and goes back to the chosen one only if nothing is active.
        """
        with self.lock:
            now = self.people.get(person)
            if now is None or now.session != session:
                now = self.people[person] = _Playing(session)
            if moment == now.moment or time.time() < now.retry_at:
                return now.note
            if now.uri and not playing:
                return now.note  # they stopped it; the right playlist waits until they resume
            now.retry_at = time.time() + DEVICE_RETRY_SECONDS
        now.note = ""
        if wanted and wanted["uri"] != now.uri:
            try:
                self._play(person, wanted["uri"], device, moved=bool(now.uri))
            except NoDeviceError:
                now.note = f"Spotify can't see {device}" if device else OPEN_SPOTIFY
                return now.note
            except (FailedError, UnlinkedError):
                return ""
            except RefusedError:
                now.note = REFUSED
            now.uri = wanted["uri"]
        now.moment, now.retry_at = moment, 0.0
        return now.note

    def _play(self, person: str, uri: str, device: str, moved: bool) -> None:
        if device and not moved:
            self.spotify.play(person, uri, device)
            return
        try:
            self.spotify.play(person, uri)
        except NoDeviceError:
            if not device:
                raise
            self.spotify.play(person, uri, device)
