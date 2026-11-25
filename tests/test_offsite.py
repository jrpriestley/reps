"""The encrypted copies in Proton Drive, against a stand-in for Proton's program."""

import datetime as dt
import gzip
import hashlib
import io
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "offsite"))

import reps_offsite  # noqa: E402
from reps_offsite import Drive, outdated, pack, send  # noqa: E402

TODAY = dt.date(2026, 11, 25)
NOW = dt.datetime(2026, 11, 25, 3, 25)
needs_gpg = pytest.mark.skipif(not shutil.which("gpg"), reason="gpg isn't installed")

# Keeps "Proton Drive" in a folder beside itself, with the commands and the JSON of the real one.
STAND_IN = '''#!{python}
import json, shutil, sys
from pathlib import Path
here = Path(__file__).parent
drive = here / "drive"
drive.mkdir(exist_ok=True)
command, *args = sys.argv[2:]
args = [each for each in args if not each.startswith("--")
        and each not in ("create-new-revision", "remove")]
def at(path):
    return drive / path.removeprefix("/my-files").lstrip("/")
if (here / "signed-out").exists():
    sys.exit("Not signed in")
if command == "list":
    found = [{{"name": {{"ok": True, "value": each.name}}}} for each in at(args[0]).iterdir()]
    print(json.dumps(found + [{{"name": {{"ok": False, "error": "unreadable"}}}}]))
elif command == "create-folder":
    (at(args[0]) / args[1]).mkdir()
elif command == "upload":
    shutil.copy(args[0], at(args[1]))
    if (here / "spoils").exists():
        (at(args[1]) / Path(args[0]).name).write_bytes(b"spoilt")
elif command == "download":
    shutil.copy(at(args[0]), args[1])
elif command == "trash":
    for each in args:
        at(each).unlink()
'''


@pytest.fixture
def drive(tmp_path):
    program = tmp_path / "proton" / "proton-drive"
    program.parent.mkdir()
    program.write_text(STAND_IN.format(python=sys.executable))
    program.chmod(0o755)
    return Drive(str(program))


@pytest.fixture
def outbox(tmp_path):
    folder = tmp_path / "outbox"
    folder.mkdir()
    return folder


@pytest.fixture
def source(tmp_path):
    """What gets copied: two nights of backups and a repo."""
    backups, repos = tmp_path / "backups", tmp_path / "git"
    backups.mkdir()
    (backups / "reps-20261124.db").write_bytes(b"older")
    (backups / "reps-20261125.db").write_bytes(b"newest")
    (repos / "reps.git" / "refs" / "heads").mkdir(parents=True)
    (repos / "reps.git" / "refs" / "heads" / "main").write_text("abc\n")
    key = tmp_path / "offsite.key"
    key.write_text("a long passphrase\n")
    return backups, repos, key


def opened(file, key, tmp_path):
    home = tmp_path / "gpg-home"
    home.mkdir(mode=0o700, exist_ok=True)
    return subprocess.run(
        ["gpg", "--homedir", str(home), "--batch", "--quiet", "--pinentry-mode", "loopback",
         "--passphrase-file", str(key), "--decrypt", str(file)],
        capture_output=True, check=True,
    ).stdout


def names(folder):
    return sorted(file.name for file in folder.iterdir() if not file.name.startswith("."))


@needs_gpg
def test_packing_encrypts_the_newest_backup_and_the_repos(source, outbox, tmp_path):
    backups, repos, key = source
    pack(backups, repos, outbox, key, TODAY)
    assert names(outbox) == ["code-20261125.tar.gz.gpg", "reps-20261125.db.gz.gpg"]
    for file in outbox.glob("*.gpg"):
        assert b"newest" not in file.read_bytes() and not file.stat().st_mode & 0o007
    data = opened(outbox / "reps-20261125.db.gz.gpg", key, tmp_path)
    assert gzip.decompress(data) == b"newest"
    code = io.BytesIO(opened(outbox / "code-20261125.tar.gz.gpg", key, tmp_path))
    with tarfile.open(fileobj=code) as tar:
        assert "reps.git/refs/heads/main" in tar.getnames()


@needs_gpg
def test_the_code_is_packed_only_when_something_was_pushed(source, outbox):
    backups, repos, key = source
    pack(backups, repos, outbox, key, TODAY)
    (outbox / "code-20261125.tar.gz.gpg").unlink()
    pack(backups, repos, outbox, key, TODAY)
    assert names(outbox) == ["reps-20261125.db.gz.gpg"]
    (repos / "reps.git" / "refs" / "heads" / "main").write_text("def\n")
    pack(backups, repos, outbox, key, TODAY)
    assert "code-20261125.tar.gz.gpg" in names(outbox)


@needs_gpg
def test_packing_never_writes_through_a_link(source, outbox, tmp_path):
    backups, repos, key = source
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("untouched")
    for name in ("reps-20261125.db.gz.gpg", ".reps-20261125.db.gz.gpg.part"):
        (outbox / name).symlink_to(elsewhere)
    pack(backups, repos, outbox, key, TODAY)
    assert elsewhere.read_text() == "untouched"
    assert not (outbox / "reps-20261125.db.gz.gpg").is_symlink()


def test_sending_empties_the_outbox_and_says_when(outbox, drive):
    (outbox / "reps-20261125.db.gz.gpg").write_bytes(b"encrypted")
    (outbox / ".code-packed").write_text("refs")
    assert send(outbox, drive, NOW) == ["reps-20261125.db.gz.gpg"]
    assert names(outbox) == ["sent"]
    assert float((outbox / "sent").read_text()) == NOW.timestamp()
    assert drive.names()[0] == "reps-20261125.db.gz.gpg"
    assert send(outbox, drive, NOW) == []


@pytest.mark.parametrize("trouble", ["signed-out", "spoils"])
def test_what_did_not_arrive_whole_is_kept_for_the_next_night(outbox, drive, trouble):
    (Path(drive.program).parent / trouble).touch()
    (outbox / "reps-20261125.db.gz.gpg").write_bytes(b"encrypted")
    with pytest.raises(RuntimeError):
        send(outbox, drive, NOW)
    assert names(outbox) == ["reps-20261125.db.gz.gpg"]


def test_only_our_old_copies_are_put_in_the_trash(outbox, drive):
    drive.make_folder()
    drive.make_folder()  # it's there already
    folder = Path(drive.program).parent / "drive" / reps_offsite.FOLDER
    for day in range(1, 31):
        (folder / f"reps-202610{day:02}.db.gz.gpg").touch()
    (folder / "holiday.jpg").touch()
    (outbox / "reps-20261125.db.gz.gpg").write_bytes(b"encrypted")
    send(outbox, drive, NOW)
    left = names(folder)
    assert "reps-20261001.db.gz.gpg" not in left and "reps-20261002.db.gz.gpg" in left
    assert "holiday.jpg" in left and len(left) == 31


def test_each_kind_is_kept_by_its_own_count():
    code = [f"code-202610{day:02}.tar.gz.gpg" for day in range(1, 11)]
    backups = [f"reps-202610{day:02}.db.gz.gpg" for day in range(1, 11)]
    assert outdated(code + backups + ["notes.txt"]) == code[:2]


def test_the_digest_changes_with_the_branches(source):
    _, repos, _ = source
    before = reps_offsite.refs_of(repos)
    (repos / "reps.git" / "packed-refs").write_text("def refs/tags/one\n")
    assert reps_offsite.refs_of(repos) != before
    assert len(before) == len(hashlib.sha256().hexdigest())


def test_me_says_when_the_copies_last_left_and_marks_when_they_stop(client, server):
    client.set_up()
    sent = server.RequestHandlerClass.app.config.copies_sent
    assert "Backup" not in client.get("/me")[1]
    sent.write_text(f"{time.time():.0f}")
    page = client.get("/me")[1]
    assert "Today, to Proton Drive" in page and "copies-late" not in page
    sent.write_text(f"{time.time() - 4 * 24 * 3600:.0f}")
    page = client.get("/me")[1]
    assert "None since" in page and '<body class="copies-late ' in page
