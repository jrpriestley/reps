"""The ``reps`` command."""

import argparse
import datetime as dt
import json

from reps import __version__
from reps.config import load_config
from reps.store import Store


def main() -> None:
    parser = argparse.ArgumentParser(prog="reps", description="A home workout coach for two.")
    parser.add_argument("--version", action="version", version=f"reps {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="run the web page")
    commands.add_parser("backup", help="copy the database into the backups folder")
    export = commands.add_parser("export", help="print one person's data as JSON")
    export.add_argument("person")
    plan = commands.add_parser("plan", help="print a person's next session, with reasons")
    plan.add_argument("person")
    plan.add_argument("--minutes", type=int)
    given = parser.parse_args()
    config = load_config()
    if given.command == "serve":
        from reps.web.server import serve

        serve(config)
    elif given.command == "backup":
        print(Store(config.database).backup(config.state_dir / "backups"))
    elif given.command == "export":
        print(json.dumps(Store(config.database).export(given.person), indent=1))
    else:
        print_plan(config, given.person, given.minutes)


def print_plan(config, person: str, minutes: int | None) -> None:
    from reps.web.server import App

    app, today = App(config), dt.date.today()
    session = app.plan(person, minutes or app.usual_minutes(person, today), today)
    print(f"{session.title}, {session.minutes} min")
    for item in session.items:
        print(f"  {item.name}: {item.sets}  ({item.why})")
