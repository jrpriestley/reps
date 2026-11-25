"""Heart rate as it arrives: the latest reading for each person, and what is nearby."""

import threading
import time

STALE_SECONDS = 15  # HeartCast sends every 5, so this rides out two missed
NEARBY_SECONDS = 300
# Long enough to switch to the relay app, which can be found only while it is in front, and back.
SCAN_SECONDS = 120
# An iPhone app in the background can be read but not found again, so a link is kept through
# a spell away from reps rather than dropped.
PRESENT_SECONDS = 600
UNSURE_SECONDS = 25  # the receiver says so again each time it looks
LEAST_BPM, MOST_BPM = 25, 250


class Heart:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.latest: dict[str, tuple[int, float]] = {}
        self.seen: dict[str, tuple[str, float]] = {}  # address -> name, when
        self.scan_until = 0.0
        self.unsure_at: dict[str, float] = {}
        self.here_at: dict[str, float] = {}

    def reading(self, person: str, bpm: int) -> None:
        with self.lock:
            self.latest[person] = (bpm, time.time())

    def now(self, person: str) -> int | None:
        """Return the reading, or None once it is too old to be called current."""
        with self.lock:
            bpm, at = self.latest.get(person, (0, 0.0))
        return bpm if time.time() - at <= STALE_SECONDS else None

    def mark_here(self, person: str) -> None:
        with self.lock:
            self.here_at[person] = time.time()

    def here(self, person: str) -> bool:
        """Return whether someone has reps open, or had it open just now."""
        with self.lock:
            return time.time() - self.here_at.get(person, 0.0) <= PRESENT_SECONDS

    def mark_unsure(self, person: str) -> None:
        with self.lock:
            self.unsure_at[person] = time.time()

    def unsure(self, person: str) -> bool:
        """Return whether more than one source nearby could be this person's."""
        with self.lock:
            return time.time() - self.unsure_at.get(person, 0.0) <= UNSURE_SECONDS

    def scan(self) -> None:
        with self.lock:
            self.scan_until = time.time() + SCAN_SECONDS

    def scanning(self) -> bool:
        with self.lock:
            return time.time() < self.scan_until

    def found(self, devices: list[dict]) -> None:
        with self.lock:
            for device in devices[:20]:
                name, address = str(device["name"])[:40], str(device["address"])[:40]
                self.seen[address] = (name or address, time.time())

    def nearby(self) -> list[tuple[str, str]]:
        """Return (name, address) of the sources seen lately."""
        cutoff = time.time() - NEARBY_SECONDS
        with self.lock:
            self.seen = {key: kept for key, kept in self.seen.items() if kept[1] > cutoff}
            return sorted((name, address) for address, (name, _) in self.seen.items())
