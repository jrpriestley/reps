"""reps-heart: reads heart rate monitors over Bluetooth and passes the readings to reps.

It connects only to the sources people have chosen. A phone's relay app can be found only while it
is in front, which is often before reps is opened, so it looks for them all the time. A phone
changes its Bluetooth address for privacy, so a source is known by its name as well.
"""

import asyncio
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HEART_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"
POLL_SECONDS = 4
SCAN_SECONDS = 5
QUIET_POLL_SECONDS = 10  # with nobody here, so the radio rests more
IDLE_SECONDS = 300  # a link with no readings, and nobody here, is let go


def beats(data: bytes) -> int | None:
    """Return a Heart Rate Measurement's beats per minute, or None if it isn't a reading."""
    if len(data) < 2:
        return None
    wide = data[0] & 0x01
    if wide:
        return int.from_bytes(data[1:3], "little") if len(data) >= 3 else None
    off_the_skin = data[0] & 0x06 == 0x04  # the sensor can tell, and says no contact
    return None if off_the_skin else data[1]


def matches(wanted: dict, name: str, address: str) -> bool:
    if wanted["address"] and wanted["address"].lower() == address.lower():
        return True
    return bool(name) and name == wanted["name"]


def choose(wanted: dict, monitors: list[tuple], rivals: list[dict]):
    """Return the monitor that is surely this person's, or None.

    Phones change their addresses and relay apps share a name, so a name counts only when one
    monitor, and one person waiting, has it.
    """
    for device, _name in monitors:
        if wanted["address"] and wanted["address"].lower() == device.address.lower():
            return device
    named = [device for device, name in monitors if matches(wanted, name, device.address)]
    shared = any(each["name"] == wanted["name"] for each in rivals)
    return named[0] if len(named) == 1 and not shared else None


class Reps:
    """The feed at reps."""

    def __init__(self, url: str, key: str, ca: str) -> None:
        self.url, self.key = url, key
        self.context = ssl.create_default_context(cafile=ca)

    def call(self, path: str, data: dict | None = None) -> dict:
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(self.url + path, body)
        request.add_header("Authorization", f"Bearer {self.key}")
        with urllib.request.urlopen(request, timeout=5, context=self.context) as answer:
            return json.loads(answer.read() or b"{}")

    async def send(self, path: str, data: dict | None = None) -> dict:
        return await asyncio.to_thread(self.call, path, data)


class Receiver:
    def __init__(self, reps: Reps, scanner, client) -> None:
        self.reps, self.scanner, self.client = reps, scanner, client
        self.links: dict[str, object] = {}  # person -> connected client
        self.held: dict[str, object] = {}  # name -> client, found before it was known whose
        self.heard: dict[int, float] = {}  # id of a client -> when it last said anything
        self.quiet = True

    async def round(self) -> None:
        wanted = await self.reps.send("/feed/wanted")
        people = {each["person"]: each for each in wanted["watch"]}
        here = {person for person, each in people.items() if each.get("here", True)}
        self.quiet = not here and not wanted["scan"]
        for person in list(self.links):
            if person not in people or (person not in here and self.idle(self.links[person])):
                await self.drop(person)
        for name in [each for each, link in self.held.items() if self.idle(link)]:
            await _quietly(self.held.pop(name).disconnect())
        for known in (self.links, self.held):
            for key in [each for each, link in known.items() if not link.is_connected]:
                del known[key]
        self.claim(people, here)
        waiting = [each for person, each in people.items() if person not in self.links]
        if waiting or wanted["scan"]:
            await self.look(waiting, wanted["scan"], here)

    def idle(self, link) -> bool:
        return time.monotonic() - self.heard.get(id(link), 0.0) > IDLE_SECONDS

    def claim(self, people: dict[str, dict], here: set[str]) -> None:
        """Give a held link to the one person here that its name could be for."""
        for name in list(self.held):
            able = [person for person in here
                    if people[person]["name"] == name and person not in self.links]
            if len(able) == 1:
                self.links[able[0]] = self.held.pop(name)
                print(f"{able[0]}: here, so theirs", file=sys.stderr)

    async def look(self, waiting: list[dict], report: bool, here: set[str]) -> None:
        found = await self.scanner.discover(timeout=SCAN_SECONDS, return_adv=True)
        monitors = [
            (device, advert.local_name or device.name or "")
            for device, advert in found.values()
            if HEART_SERVICE in advert.service_uuids
            or any(matches(each, advert.local_name or "", device.address) for each in waiting)
        ]
        if report:
            devices = [{"name": name, "address": device.address} for device, name in monitors]
            await self.reps.send("/feed/nearby", {"devices": devices})
        taken = {link.address.lower()
                 for link in (*self.links.values(), *self.held.values())}
        free = [(device, name) for device, name in monitors if device.address.lower() not in taken]
        # Those here come first: one of two with the same name is more likely theirs.
        for each in sorted(waiting, key=lambda one: one["person"] not in here):
            present = each["person"] in here
            rivals = [other for other in waiting if other["person"] != each["person"]
                      and other["person"] not in self.links
                      and (other["person"] in here or not present)]
            device = choose(each, free, rivals)
            if device:
                await self.connect(each["person"], device)
                free = [(other, name) for other, name in free if other is not device]
            elif not present:
                free = await self.hold(each, free)
            elif any(matches(each, name, other.address) for other, name in free):
                print(f"{each['person']}: more than one could be theirs", file=sys.stderr)
                await self.reps.send("/feed/unsure", {"person": each["person"]})

    async def hold(self, wanted: dict, free: list[tuple]) -> list[tuple]:
        """Connect to a monitor several people could claim, until one of them is here."""
        named = [device for device, name in free if name and name == wanted["name"]]
        if len(named) != 1 or wanted["name"] in self.held:
            return free
        link = await self.connected(named[0], wanted["name"])
        if link:
            self.held[wanted["name"]] = link
        return [(device, name) for device, name in free if device is not named[0]]

    async def connect(self, person: str, device) -> None:
        link = await self.connected(device, person)
        if link:
            self.links[person] = link

    async def connected(self, device, label: str):
        """Return a link whose readings go to whoever it is theirs, or None."""
        link = self.client(device)
        try:
            await link.connect()
            loop = asyncio.get_running_loop()

            def arrived(_sender, data: bytearray) -> None:
                self.heard[id(link)] = time.monotonic()
                bpm = beats(bytes(data))
                owner = next((each for each, its in self.links.items() if its is link), None)
                if bpm and owner:
                    loop.create_task(self.pass_on(owner, bpm))

            await link.start_notify(HEART_MEASUREMENT, arrived)
        except Exception as problem:  # any radio trouble: say so, and try again next round
            print(f"{label}: could not connect: {problem!r}", file=sys.stderr)
            await _quietly(link.disconnect())
            return None
        self.heard = {id(each): self.heard.get(id(each), 0.0)
                      for each in (*self.links.values(), *self.held.values())}
        self.heard[id(link)] = time.monotonic()
        print(f"{label}: connected", file=sys.stderr)
        return link

    async def pass_on(self, person: str, bpm: int) -> None:
        await _quietly(self.reps.send("/feed/heart", {"person": person, "bpm": bpm}))

    async def drop(self, person: str) -> None:
        link = self.links.pop(person)
        await _quietly(link.disconnect())
        print(f"{person}: let go", file=sys.stderr)


async def _quietly(work) -> None:
    try:
        await work
    except Exception as problem:
        print(f"ignored: {problem!r}", file=sys.stderr)


async def run() -> None:
    from bleak import BleakClient, BleakScanner

    key = (Path(os.environ["CREDENTIALS_DIRECTORY"]) / "heart.key").read_text().strip()
    reps = Reps(os.environ.get("REPS_URL", "https://reps:8090"), key, "/etc/reps/ca.crt")
    receiver = Receiver(reps, BleakScanner, BleakClient)
    trouble = ""
    while True:
        try:
            await receiver.round()
            trouble = ""
        except Exception as problem:  # reps restarting, the radio busy: carry on
            if repr(problem) != trouble:
                trouble = repr(problem)
                print(f"trouble: {trouble}", file=sys.stderr)
        await asyncio.sleep(QUIET_POLL_SECONDS if receiver.quiet else POLL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run())
