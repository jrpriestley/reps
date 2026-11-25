"""Copies of the database and the git repos, encrypted on homehub and kept in Proton Drive.

Two jobs, so that Proton's program never sees anything readable:

    reps_offsite.py pack    as root: encrypt what's new into the outbox
    reps_offsite.py send    as reps-offsite: upload the outbox, check each, tidy the old ones

To get one back, download it from Proton and, with the passphrase (the README says where):

    gpg -d reps-20261125.db.gz.gpg | gunzip > reps.db
    gpg -d code-20261125.tar.gz.gpg | tar xz
"""

import datetime as dt
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

BACKUPS = Path("/var/lib/reps/backups")
REPOS = Path("/home/user/git")
OUTBOX = Path("/var/lib/reps-outbox")
DRIVE = "/opt/reps-offsite/proton-drive"
FOLDER = "homehub-backups"
KEPT = {"reps": 30, "code": 8}
NAME = re.compile(r"(reps|code)-\d{8}\.(db|tar)\.gz\.gpg")
PATIENCE = 600  # seconds for one command of Proton's


def passphrase_file() -> Path:
    return Path(os.environ["CREDENTIALS_DIRECTORY"]) / "offsite.key"


def encrypt(plain: bytes, passphrase: Path) -> bytes:
    with tempfile.TemporaryDirectory() as home:
        return subprocess.run(
            ["gpg", "--homedir", home, "--batch", "--quiet", "--pinentry-mode", "loopback",
             "--passphrase-file", str(passphrase), "--cipher-algo", "AES256",
             "--compress-algo", "none", "--symmetric"],
            input=plain, capture_output=True, check=True,
        ).stdout


def place(folder: Path, name: str, content: bytes) -> None:
    """Write a file whole or not at all, never through a link left in its place."""
    part = folder / f".{name}.part"
    part.unlink(missing_ok=True)
    handle = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    with os.fdopen(handle, "wb") as file:
        file.write(content)
    part.rename(folder / name)


def refs_of(repos: Path) -> str:
    """Return what every branch and tag points at, as one digest."""
    digest = hashlib.sha256()
    for repo in sorted(repos.glob("*.git")):
        for file in [repo / "packed-refs", *sorted((repo / "refs").rglob("*"))]:
            if file.is_file():
                digest.update(f"{file.relative_to(repos)}\n".encode() + file.read_bytes())
    return digest.hexdigest()


def tarred(repos: Path) -> bytes:
    held = io.BytesIO()
    with tarfile.open(fileobj=held, mode="w:gz") as tar:
        for repo in sorted(repos.glob("*.git")):
            tar.add(repo, arcname=repo.name)
    return held.getvalue()


def pack(backups: Path, repos: Path, outbox: Path, passphrase: Path, today: dt.date) -> None:
    newest = max(backups.glob("reps-*.db"))
    place(outbox, f"reps-{today:%Y%m%d}.db.gz.gpg",
          encrypt(gzip.compress(newest.read_bytes()), passphrase))
    # The code is a hundred times the size, so it goes only when something was pushed.
    refs, packed = refs_of(repos), outbox / ".code-packed"
    if not packed.exists() or packed.read_text() != refs:
        place(outbox, f"code-{today:%Y%m%d}.tar.gz.gpg", encrypt(tarred(repos), passphrase))
        place(outbox, ".code-packed", refs.encode())


class Drive:
    """Proton's command line program."""

    def __init__(self, program: str = DRIVE) -> None:
        self.program = program

    def run(self, *args: str) -> str:
        done = subprocess.run(
            [self.program, "filesystem", *args, "--json"], capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=PATIENCE,
        )
        if done.returncode:
            said = (done.stderr.strip() or done.stdout.strip())[-500:]
            raise RuntimeError(f"{args[0]}: {said}")
        return done.stdout

    def names(self) -> list[str]:
        return [_name(each) for each in json.loads(self.run("list", f"/my-files/{FOLDER}"))]

    def make_folder(self) -> None:
        if FOLDER not in [_name(each) for each in json.loads(self.run("list", "/my-files"))]:
            self.run("create-folder", "/my-files", FOLDER)

    def upload(self, file: Path) -> None:
        self.run("upload", str(file), f"/my-files/{FOLDER}",
                 "--file-conflict-strategy", "create-new-revision", "--skip-thumbnails")

    def download(self, name: str, folder: Path) -> Path:
        self.run("download", f"/my-files/{FOLDER}/{name}", str(folder),
                 "--file-conflict-strategy", "remove")
        return folder / name

    def trash(self, names: list[str]) -> None:
        self.run("trash", *(f"/my-files/{FOLDER}/{name}" for name in names))


def _name(node: dict) -> str:
    """Return a listed item's name, which is wrapped with whether it could be read."""
    name = node.get("name", "")
    if isinstance(name, dict):
        name = name.get("value", "") if name.get("ok") else ""
    return name


def outdated(names: list[str]) -> list[str]:
    """Return the copies no longer kept. Anything else in the folder is not ours to touch."""
    ours = sorted(name for name in names if NAME.fullmatch(name))
    return [
        name
        for kind, kept in KEPT.items()
        for name in [each for each in ours if each.startswith(f"{kind}-")][:-kept]
    ]


def send(outbox: Path, drive: Drive, now: dt.datetime) -> list[str]:
    """Upload what waits, and say it was sent only once the copy there reads back the same."""
    waiting = sorted(file for file in outbox.iterdir() if NAME.fullmatch(file.name))
    if not waiting:
        return []
    drive.make_folder()
    for file in waiting:
        drive.upload(file)
        with tempfile.TemporaryDirectory() as folder:
            if drive.download(file.name, Path(folder)).read_bytes() != file.read_bytes():
                raise RuntimeError(f"{file.name} came back different")
        file.unlink()
    sent = [file.name for file in waiting]
    mark = outbox / "sent"
    mark.write_text(f"{now.timestamp():.0f}\n")
    mark.chmod(0o644)  # reps reads it, to warn when the copies stop
    if old := outdated(drive.names()):
        drive.trash(old)
    return sent


def main() -> None:
    if sys.argv[1:] == ["pack"]:
        pack(BACKUPS, REPOS, OUTBOX, passphrase_file(), dt.date.today())
    elif sys.argv[1:] == ["send"]:
        print("sent", *send(OUTBOX, Drive(), dt.datetime.now()) or ["nothing"])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
