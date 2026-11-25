"""Drive every screen in a headless browser with a lived-in database and a pretend Spotify.

Reports anything where the page scrolls, something is cut off, controls overlap or a target is
too small. Screenshots go to $OUT. Frames marked .scroll may scroll inside.

    OUT=/tmp/shots ~/.cache/reps-tools/v/bin/python tools/check_screens.py [light|dark]
"""

import datetime as dt
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from playwright.sync_api import sync_playwright  # noqa: E402

from reps.config import Config  # noqa: E402
from reps.web import auth  # noqa: E402
from reps.web.server import Server  # noqa: E402

VIEWS = {"se": (375, 553), "phone": (390, 760), "wide": (561, 900),
         "tablet": (768, 1024), "desk": (1280, 720)}
SONG = {"title": "A Fairly Long Song Title, Extended", "by": "First Artist, Second Artist",
        "link": "https://open.spotify.com/track/1", "art": "", "playing": True}

MEASURE = """() => {
  const bad = [], H = innerHeight, W = innerWidth, root = document.documentElement;
  if (root.scrollHeight > H + 1 || root.scrollWidth > W + 1)
    bad.push(`page ${root.scrollWidth}x${root.scrollHeight}`);
  const held = (el) => el.closest('.scroll');
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('svg, option, .party') || !el.getClientRects().length) continue;
    const box = el.getBoundingClientRect(), label = `${el.tagName}.${el.className}`;
    const words = el.textContent.trim().slice(0, 16);
    if (!held(el) && (box.bottom > H + 1 || box.right > W + 1 || box.top < -1 || box.left < -1))
      bad.push(`outside ${label} "${words}"`);
    if (!held(el) && el.clientHeight && el.scrollHeight > el.clientHeight + 2
        && ['MAIN', 'SECTION', 'UL', 'FORM', 'DIALOG'].includes(el.tagName))
      bad.push(`overflows ${label} ${el.scrollHeight}>${el.clientHeight}`);
    const target = ['BUTTON', 'A', 'SELECT'].includes(el.tagName) || el.matches('label.check');
    // On the smallest phone a whole month fits only with its days 36 px tall.
    const least = el.matches('.cal-day') ? 35.5 : 43.5;
    if (target && box.height < least && !el.matches('.brand, .s-line, .song'))
      bad.push(`small ${label} "${words}" ${Math.round(box.height)}`);
  }
  for (const line of document.querySelectorAll('.song-words > *')) {
    const box = line.getBoundingClientRect(), words = line.firstChild;
    if (box.width < 150) bad.push(`song squeezed to ${Math.round(box.width)}px`);
    if (words.offsetWidth > box.width + 1 && !line.classList.contains('glides'))
      bad.push(`song cut off "${line.textContent.slice(0, 16)}"`);
  }
  const player = document.querySelector('.player')?.getBoundingClientRect();
  for (const el of player ? document.querySelectorAll('main > *, main section, main ul') : []) {
    if (!el.getClientRects().length || el.closest('.player, dialog')) continue;
    if (el.compareDocumentPosition(document.querySelector('.player')) & 2) continue; // below it
    let bottom = el.getBoundingClientRect().bottom;
    for (let up = el.parentElement; up; up = up.parentElement)
      if (getComputedStyle(up).overflowY !== 'visible')
        bottom = Math.min(bottom, up.getBoundingClientRect().bottom);
    if (bottom > player.top + 1)
      bad.push(`under the player by ${Math.round(bottom - player.top)} ${el.className}`);
    if (el.matches('.scroll') && el.clientHeight && el.clientHeight < 40)
      bad.push(`a ${el.clientHeight}px frame`);
  }
  for (const frame of document.querySelectorAll('.scroll')) {
    const edge = frame.getBoundingClientRect().right;
    for (const el of frame.querySelectorAll('*'))
      if (!el.closest('svg') && el.getClientRects().length
          && el.getBoundingClientRect().right > edge + 1)
        bad.push(`past its frame ${el.tagName}.${el.className}`);
  }
  const bar = document.querySelector('.topbar')?.getBoundingClientRect();
  const main = document.querySelector('main').getBoundingClientRect();
  if (bar && Math.abs(main.top - bar.bottom) > 1)
    bad.push(`main is ${Math.round(main.top - bar.bottom)}px from the top bar`);
  const head = document.querySelector('.s-head')?.getBoundingClientRect();
  for (const el of document.querySelectorAll('.s-body > *')) {
    const box = el.getBoundingClientRect();
    if (head && el.getClientRects().length && box.top < head.bottom - 1)
      bad.push(`into the head by ${Math.round(head.bottom - box.top)} ${el.className}`);
  }
  const controls = [...document.querySelectorAll('main button, main a, main label, main select')]
    .filter((el) => el.getClientRects().length && !el.closest('dialog') && !held(el));
  for (const [i, one] of controls.entries()) for (const two of controls.slice(i + 1)) {
    if (one.contains(two) || two.contains(one)) continue;
    const a = one.getBoundingClientRect(), b = two.getBoundingClientRect();
    if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 2
        && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 2)
      bad.push(`overlap "${one.textContent.slice(0, 14)}" / "${two.textContent.slice(0, 14)}"`);
  }
  return [...new Set(bad)];
}"""


def seeded() -> tuple[Server, object]:
    auth.Shadow.verify = lambda self, user, password: user in self.users and password == "right"
    config = Config(
        host="localhost", port=0, bind="127.0.0.1", state_dir=Path(tempfile.mkdtemp()),
        tls_dir=None, spotify_client="client", copies_sent=Path(tempfile.mkdtemp()) / "sent",
    )
    config.copies_sent.write_text(str(time.time() - 5 * 24 * 3600))  # late, the longest words
    server = Server(config, None)
    server.RequestHandlerClass.log_message = lambda *_: None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    app = server.RequestHandlerClass.app
    app.spotify.song = lambda _who: dict(SONG)
    after = {**SONG, "title": "The Next One", "link": SONG["link"] + "2"}
    app.spotify.following = lambda *_a: after
    app.spotify.ends_in = lambda _who: None
    app.spotify.settle = lambda *_a: None
    app.spotify.skip = app.spotify.previous = lambda *_a: None
    app.spotify.play = app.spotify.pause = app.spotify.resume = lambda *_a: None
    app.spotify.playlists = lambda _who: [
        {"uri": f"spotify:playlist:{n}", "name": f"Playlist {n} with quite a long name"}
        for n in range(23)
    ]
    app.spotify.devices = lambda _who: [
        {"id": str(n), "name": name, "active": n == 0}
        for n, name in enumerate(("Gym speaker", "iPhone", "Living room TV", "Kitchen"))
    ]
    return server, app


def live_in(app) -> None:
    """Give bo workouts, mixes and a few months of sessions."""
    store = app.store
    store.save_spotify_token("bo", "token")
    blocks = [{"options": ["rower"], "minutes": 15},
              {"options": ["deadlift", "curl", "shoulder-press"], "sets": 3},
              {"options": ["rower"], "minutes": 20}]
    for name in ("Rower sandwich", "Quick arms", "Sunday long row", "Leg day", "Hotel room",
                 "Intervals", "Push and pull", "Easy spin"):
        number = store.add_workout("bo", name)
        store.save_workout("bo", store.workout("bo", number) | {"blocks": blocks})
    stages = {"all": [{"uri": "spotify:playlist:1", "name": "Most Played Liked Songs"}],
              "flatout": [{"uri": f"spotify:playlist:{n}", "name": f"Sprint {n}"}
                          for n in (2, 3, 4)]}
    talk = [{"you": "25 min cardio using both rower and treadmill then 20 min upper body",
             "note": "Used floor presses, as no step is listed."},
            {"you": "less rower, add a finisher", "note": "Rower cut to 8 minutes."}]
    said = next(each for each in store.workouts("bo") if each["name"] == "Leg day")
    store.refine_workout("bo", said, said["blocks"], talk)
    for words in ("30 min upper body, finish with bike sprints", "rower 15, curl 3x, rower 15",
                  "45 minutes of legs, nothing on one leg, and a long easy row to finish"):
        store.save_prompt("bo", words)
    for name in ("Gym", "Easy day", "Loud", "Podcasts", "Sunday", "Rowing", "Old favourites"):
        number = store.add_mix("bo", name)
        store.save_mix("bo", store.mix("bo", number) | {"stages": stages})
    store.use_mix("bo", store.mixes("bo")[1]["id"])
    store.save_person("bo", heart_name="HeartCast-iPhon", speaker="Gym speaker")
    today = dt.date.today()
    for ago in (110, 100, 95, 90, 88, 60, 45, 40, 31, 29, 22, 20, 15, 13, 10, 8, 6, 3, 1):
        plan = app.plan("bo", 45, today - dt.timedelta(days=ago))
        start = dt.datetime.combine(today - dt.timedelta(days=ago), dt.time(7)).timestamp()
        session = store.start("bo", plan)
        store.run("UPDATE session SET started = ?, ended = ?, kind = ? WHERE id = ?",
                  start, start + 2700, "extra" if ago % 10 == 0 else plan.kind, session)
        for item in plan.items:
            for n, (load, amount) in enumerate(item.sets):
                cid = f"{session}-{item.key}-{n}"
                store.run("INSERT INTO set_log VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                          cid, session, item.key, n, load, amount, start + 60 * n)
            store.run("INSERT INTO item_state (session, item, rir) VALUES (?, ?, 2)",
                      session, item.key)
        store.run("INSERT INTO heart VALUES (?, 1, 118), (?, 2, 151)", session, session)


def paced_workout(store) -> int:
    number = store.add_workout("bo", "Hills")
    blocks = [
        {"options": ["rower"], "minutes": 15, "style": "steady", "level": 0, "watts": 100},
        {"options": ["treadmill"], "minutes": 20, "style": "60-60", "level": 0, "speed": 8.0,
         "incline": 3},
    ]
    store.save_workout("bo", store.workout("bo", number) | {"blocks": blocks})
    return number


def check_weight_stays_changed(page) -> None:
    """Raise the first set's weight by three settings: the next set must have kept them."""
    planned = page.evaluate("""async () => {
        const item = (await (await fetch('/api/session')).json()).items[0];
        const at = item.loads.indexOf(item.sets[0][0]);
        return `${item.loads[at + 3]} lb`;
    }""")
    for _ in range(3):
        page.click("[aria-label='More weight']")
    page.click(".s-log")
    page.wait_for_timeout(100)
    shown = page.inner_text(".s-step strong")
    assert shown == planned, (shown, planned)


def check_chart(page) -> None:
    """Touching a column says its week, and moving off it goes back to this one."""
    latest = page.inner_text("[data-words]")
    page.hover(".mark:nth-of-type(9)")
    assert "week of" in page.inner_text("[data-words]")
    page.hover("h1")
    assert page.inner_text("[data-words]") == latest
    page.tap(".mark:nth-of-type(10)")
    assert "week of" in page.inner_text("[data-words]")


def check_sending(page) -> None:
    """Results wait out a lost connection, and one that is refused doesn't hold up the rest."""
    def unsent() -> str:
        return page.inner_text("#unsent")

    def waiting() -> list:
        return page.evaluate("""() => JSON.parse(localStorage.getItem(
            Object.keys(localStorage).find((each) => each.includes('-outbox-'))) ?? '[]')""")

    page.route("**/api/events", lambda route: route.abort())
    page.click(".s-log")
    page.wait_for_timeout(600)
    assert "offline" not in unsent()
    page.wait_for_timeout(9000)
    assert unsent() == "offline, 1 kept", unsent()
    page.unroute("**/api/events")
    page.wait_for_timeout(5000)
    assert waiting() == [] and "offline" not in unsent()
    page.evaluate("""() => {
        const key = Object.keys(localStorage).find((each) => each.includes('-outbox-'));
        localStorage.setItem(key, JSON.stringify([
            { cid: 'refused-0001', type: 'set', item: 'no-such', n: 0, load: 1, amount: 8 },
            { cid: 'kept-000001', type: 'resume', at: 1 }]));
    }""")
    page.reload()
    page.wait_for_selector("dialog[open]", timeout=20000)
    assert "wouldn't take" in page.inner_text("dialog")
    page.click("dialog button.primary")
    page.wait_for_timeout(5000)
    assert waiting() == []
    page.click("button:has-text('Undo')")  # back to where it was
    page.wait_for_timeout(300)


def check_pausing(page, base) -> None:
    """Pause a clock: it must stand still, through a reload and through leaving, until resumed."""
    def left() -> str:
        return page.inner_text(".s-phase strong")

    page.click("button:has-text('Pause')")
    stopped = left()
    for away in (lambda: page.reload(), lambda: leave_and_return(page, base)):
        page.wait_for_timeout(1300)
        assert left() == stopped, (left(), stopped)
        away()
        page.wait_for_selector(".s-phase.runs")
    page.click("button:has-text('Resume')")
    page.wait_for_timeout(1300)
    assert left() != stopped, "it stayed paused"
    page.click("button:has-text('Leave')")  # leaving stops a running clock by itself
    page.click("dialog button:has-text('Pause')")
    page.wait_for_url(base + "/")
    page.wait_for_timeout(500)
    page.click("a:has-text('Resume')")
    page.wait_for_selector(".s-phase.runs")
    assert page.inner_text(".s-phase b") == "PAUSED"


def leave_and_return(page, base) -> None:
    page.click("button:has-text('Leave')")
    page.click("dialog button:has-text('Pause')")
    page.wait_for_url(base + "/")
    page.click("a:has-text('Resume')")


class Checker:
    def __init__(self, page, out: Path) -> None:
        self.page, self.out, self.problems = page, out, 0

    def __call__(self, name: str) -> None:
        for view, (width, height) in VIEWS.items():
            self.page.set_viewport_size({"width": width, "height": height})
            self.page.wait_for_timeout(90)
            bad = self.page.evaluate(MEASURE)
            if bad:
                self.problems += len(bad)
                print(f"{name} {view}:", "; ".join(bad[:6]))
            if view in ("se", "phone", "desk"):
                self.page.screenshot(path=str(self.out / f"{name}-{view}.png"))
        for height in range(560, 901, 12):  # phones' browsers leave every height there is
            self.page.set_viewport_size({"width": 390, "height": height})
            bad = self.page.evaluate(MEASURE)
            if bad:
                self.problems += len(bad)
                print(f"{name} at 390x{height}:", "; ".join(bad[:3]))
        for width in (360, 402, 430, 480, 560):
            self.page.set_viewport_size({"width": width, "height": 664})
            bad = self.page.evaluate(MEASURE)
            if bad:
                self.problems += len(bad)
                print(f"{name} at {width}x664:", "; ".join(bad[:3]))
        self.page.set_viewport_size({"width": 375, "height": 553})


def drag(page, start, end) -> None:
    a, b = start.bounding_box(), end.bounding_box()
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    page.mouse.down()
    page.mouse.move(a["x"] + a["width"] / 2 + 9, a["y"] + a["height"] / 2 + 9)
    page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, steps=8)
    page.wait_for_timeout(120)


def drag_about(page, app, base: str, workout: int, check) -> None:
    """Drag the builder's blocks: reorder, join, pull apart, drag one in, and undo."""
    def options() -> list[list[str]]:
        return [each["options"] for each in app.store.workout("bo", workout)["blocks"]]

    def expect(wanted: list[list[str]], what: str) -> None:
        for _ in range(20):
            if options() == wanted:
                return
            page.wait_for_timeout(60)
        check.problems += 1
        print("drag", what, "gave", options(), "not", wanted)

    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(f"{base}/workouts/{workout}")
    page.wait_for_selector(".block")
    arms = ["deadlift", "curl", "shoulder-press"]
    expect([["rower"], arms, ["rower"]], "start")
    drag(page, page.locator(".block .grip").first, page.locator("[data-gap='2']"))
    check.page.screenshot(path=str(check.out / "flow-dragging-desk.png"))
    page.mouse.up()
    expect([arms, ["rower"], ["rower"]], "reorder")
    page.wait_for_timeout(200)
    page.click(".toast button")
    expect([["rower"], arms, ["rower"]], "undo")
    page.wait_for_timeout(200)
    drag(page, page.locator("[data-grip=option][data-key=curl]"), page.locator("[data-gap='3']"))
    page.mouse.up()
    expect([["rower"], ["deadlift", "shoulder-press"], ["rower"], ["curl"]], "pull apart")
    page.wait_for_timeout(200)
    drag(page, page.locator(".block").nth(3).locator(".grip"), page.locator(".block").nth(1))
    check.page.screenshot(path=str(check.out / "flow-joining-desk.png"))
    page.mouse.up()
    expect([["rower"], ["deadlift", "shoulder-press", "curl"], ["rower"]], "join")
    page.wait_for_timeout(200)
    drag(page, page.locator(".piece[data-key=bike]"), page.locator("[data-gap='0']"))
    page.mouse.up()
    expect([["bike"], ["rower"], ["deadlift", "shoulder-press", "curl"], ["rower"]], "drag in")
    page.wait_for_timeout(200)
    drag(page, page.locator(".block").nth(0).locator(".grip"), page.locator(".block").nth(2))
    page.mouse.up()  # cardio can't join weights, so it lands beside them
    page.wait_for_timeout(300)
    if any({"bike", "curl"} <= set(each) for each in options()):
        check.problems += 1
        print("drag joined cardio to weights")
    stored = app.store.workout("bo", workout)
    music = {"uri": "spotify:playlist:1", "name": "Most Played Liked Songs"}
    for block in stored["blocks"][1:3]:
        block["music"] = music
    app.store.save_workout("bo", stored)
    page.goto(f"{base}/workouts/{workout}")
    page.wait_for_timeout(400)
    check("flow")
    page.goto(f"{base}/workouts/{workout}/block/1/music")
    page.wait_for_timeout(300)
    check("flow-music")
    page.click("label:has-text('From here on')")
    page.click("button[value='spotify:playlist:2']")
    page.wait_for_url(f"**/workouts/{workout}")
    tagged = [each.get("music", {}).get("uri", "")[-1:]
              for each in app.store.workout("bo", workout)["blocks"]]
    if set(tagged[1:]) != {"2"}:
        check.problems += 1
        print("tapping a playlist tagged", tagged)


def main() -> None:
    out = Path(os.environ.get("OUT", "/tmp/reps-shots"))
    out.mkdir(parents=True, exist_ok=True)
    scheme = sys.argv[1] if len(sys.argv) > 1 else "light"
    server, app = seeded()
    base = f"http://localhost:{server.server_address[1]}"
    with sync_playwright() as tool:
        browser = tool.chromium.launch()
        context = browser.new_context(color_scheme=scheme, has_touch=True, device_scale_factor=2,
                                      viewport={"width": 375, "height": 553})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda problem: errors.append(str(problem)))
        check = Checker(page, out)

        def visit(path: str, name: str) -> None:
            page.goto(base + path)
            page.wait_for_timeout(350)
            app.heart.reading("bo", 124)
            check(name)

        visit("/", "login")
        page.fill("[name=user]", "bo")
        page.fill("[name=password]", "right")
        page.click("button.primary")
        for step in ("goals", "equipment", "time", "days", "body"):
            page.wait_for_url(f"**/setup/{step}")
            if step == "equipment":
                for box in page.query_selector_all("input[type=checkbox]"):
                    box.check()
            check(f"first-{step}")
            page.click("form.setup button.primary")
        page.wait_for_url(base + "/")
        visit("/", "today-new")
        visit("/workouts", "workouts-none")
        visit("/music", "music-none")
        live_in(app)
        store = app.store
        workout, mix = store.workouts("bo")[6]["id"], store.mixes("bo")[1]["id"]
        said = next(each["id"] for each in store.workouts("bo") if each["talk"])
        session = store.finished("bo")[0]["id"]
        paced = paced_workout(store)
        for path, name in (
            ("/?minutes=60", "today"), ("/?minutes=60&why=1", "why"), ("/pick", "pick"),
            ("/generate", "generate"), ("/describe", "describe"),
            ("/me/describe", "describe-later"), ("/weights", "weights"),
            (f"/workouts/{paced}/block/1", "block-pace"),
            (f"/workouts/{said}", "described"), (f"/workouts/{said}/refine", "refine"),
            ("/how/front-squat", "how"), ("/week", "week"),
            ("/history?view=calendar", "calendar"), ("/history?month=2026-08", "calendar-six"),
            ("/history?view=progress", "progress"), ("/progress/floor-press", "progress-press"),
            ("/progress/knee-raise", "progress-reps"),
            ("/history?view=list", "list"), (f"/history/{session}", "past"), ("/me", "me"),
            ("/setup", "setup"), ("/setup/goals", "goals"), ("/setup/equipment", "equipment"),
            ("/setup/time", "time"), ("/setup/days", "days"), ("/setup/body", "body"),
            ("/setup/hurts", "avoid"), ("/gym", "gym"), ("/workouts", "workouts"),
            (f"/workouts/{workout}", "workout"), (f"/workouts/{workout}/block/1", "block"),
            (f"/workouts/{workout}/add", "categories"),
            (f"/workouts/{workout}/add/hpush", "choices"),
            (f"/workouts/{workout}/settings", "workout-name"), ("/music", "music"),
            (f"/music/{mix}", "mix"), (f"/music/{mix}/flatout", "stage"),
            (f"/music/{mix}/all", "stage-one"), (f"/music/{mix}/all/add", "playlists"),
            (f"/music/{mix}/settings", "mix-name"), ("/heart", "heart"), ("/spotify", "spotify"),
            ("/spotify/devices", "devices"),
        ):
            visit(path, name)
        page.goto(f"{base}/history?view=progress")
        check_chart(page)
        page.goto(f"{base}/history?view=calendar")
        app.heart.found([{"name": f"HeartCast-{n}", "address": f"AA:{n}"} for n in range(6)])
        visit("/heart", "heart-found")
        store.save_person("bo", hurts=["goblet-squat", "floor-press", "curl", "bent-row"])
        visit("/setup/hurts", "avoid-full")
        store.save_person("bo", hurts=[])
        drag_about(page, app, base, workout, check)
        page.goto(base + "/generate")
        page.click("label:has-text('Cardio')")
        page.select_option("[name=machine]", "bike")
        page.select_option("[name=style]", "60-60")
        check("generate-cardio")
        page.click("button:has-text('Generate')")
        page.wait_for_url(base + "/")
        page.wait_for_timeout(500)
        check("today-generated")
        page.click("button:has-text('Start')")
        page.wait_for_selector(".s-log")
        check("bike-steady")
        page.click("button:has-text('Done')")
        page.wait_for_selector(".s-phase")
        check("hiit-ready")
        page.click("button:has-text('rounds')")
        page.wait_for_timeout(700)
        check("hiit-on")
        check_pausing(page, base)
        check("hiit-paused")
        page.click("button:has-text('Resume')")
        # Move its clock on: near the end of a part, into the rest, and near the very end.
        for name, gone in (("hiit-last", 57.6), ("hiit-off", 62), ("hiit-end", 537.6)):
            page.evaluate("""(gone) => {
                const key = Object.keys(localStorage).find((each) => each.includes('-held-'));
                const held = JSON.parse(localStorage.getItem(key));
                held.until = Date.now() + (held.length - gone) * 1000;
                localStorage.setItem(key, JSON.stringify(held));
            }""", gone)
            page.reload()
            page.wait_for_selector(".s-phase.runs")
            page.wait_for_timeout(250)
            page.screenshot(path=str(out / f"{name}-se.png"))
        page.click("button:has-text('Leave')")
        page.click("dialog button.danger")
        page.wait_for_url(base + "/")
        page.goto(base + "/pick")
        page.click("button:has-text('Hills')")
        page.wait_for_url(base + "/")
        check("today-paced")
        page.click("button:has-text('Start')")
        page.wait_for_selector(".s-log")
        check("pace-watts")
        page.click("button:has-text('Done')")
        page.wait_for_selector(".s-pair")
        for _ in range(30):
            page.click("[aria-label='More incline']")
        check("pace-belt")
        page.click("button:has-text('rounds')")
        page.wait_for_timeout(500)
        check("pace-running")
        page.click("button:has-text('Leave')")
        page.click("dialog button.danger")
        page.wait_for_url(base + "/")
        page.goto(base + "/pick")
        page.click("button:has-text(\"Coach's plan\")")
        page.goto(base + "/?minutes=45")
        page.click("button:has-text('Start')")
        page.wait_for_selector(".s-log")
        app.heart.reading("bo", 137)
        page.wait_for_timeout(3400)
        check("session")
        page.click(".s-tools button:has-text('How')")
        check("session-how")
        page.click("dialog button.primary")
        page.click(".music-button >> nth=0")
        check("session-mixes")
        page.click("dialog button:has-text('Close')")
        check_sending(page)
        check_weight_stays_changed(page)
        for _ in range(60):
            if page.query_selector("dialog[open]"):
                page.click("dialog button.primary")
            elif page.query_selector(".s-rir"):
                check("session-effort")
                page.click(".s-rir button:nth-child(3)")
            elif page.query_selector("button:has-text('Finish')"):
                break
            elif page.query_selector("button:has-text('Done')"):
                check("session-block")
                page.click("button:has-text('Done')")
            else:
                page.click(".s-log")
            page.wait_for_timeout(50)
        check("session-finish")
        page.click("button:has-text('Finish')")
        page.wait_for_url(base + "/")
        print("errors:", errors, "| problems:", check.problems)
        browser.close()


if __name__ == "__main__":
    main()
