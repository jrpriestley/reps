// The session screen. Every set is kept on the phone first, then sent; the server counts each
// one once, so a lost connection or a reload loses nothing.

const app = document.getElementById("app");
const SEND_EVERY_MS = 4000;
const UNSENT_MS = 5000;
const REFUSALS = 2; // before one is given up on

let session = null;
let choice = null; // the load and amount on the steppers
let rest = null; // {until} or null
let justLogged = null;
let bumped = ""; // the number just changed, to make it jump
let sleepy = false; // the screen can't be kept on
let held = null; // a timed block's clock: {key, until}, and {paused: seconds left} while stopped
let audio = null;
let sending = false;
let offline = false;
let oneByOne = false; // after a refusal, to find which

// Kept on the phone -----------------------------------------------------------

const storageKey = (name) => `reps-${name}-${session.id}`;

function keep(name, value) {
  try {
    localStorage.setItem(storageKey(name), JSON.stringify(value));
  } catch {
    // Private windows may refuse storage; sending still works.
  }
}

function kept(name, fallback) {
  try {
    return JSON.parse(localStorage.getItem(storageKey(name))) ?? fallback;
  } catch {
    return fallback;
  }
}

function forget() {
  try {
    for (const name of ["outbox", "rest", "held"]) {
      localStorage.removeItem(storageKey(name));
    }
  } catch {
    // Nothing was kept.
  }
}

let outbox = [];

function newId() {
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function record(event) {
  const full = { cid: newId(), made: Date.now(), ...event };
  outbox.push(full);
  keep("outbox", outbox);
  applyLocally(full);
  send();
  return full;
}

function applyLocally(event) {
  const state = (session.states[event.item] ??= {});
  if (event.type === "set" && !session.sets.some((set) => set.cid === event.cid)) {
    session.sets.push({
      cid: event.cid, item: event.item, n: event.n, load: event.load, amount: event.amount,
    });
  } else if (event.type === "unset") {
    session.sets = session.sets.filter((set) => set.cid !== event.target);
    state.rir = null;
  } else if (event.type === "rir") {
    state.rir = event.value;
  } else if (event.type === "skip") {
    state.skipped = 1;
  } else if (event.type === "hurt") {
    state.hurt = 1;
  } else if (event.type === "pause" || event.type === "resume") {
    session.paused = event.type === "pause";
  }
}

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session: session.id, ...body }),
  });
  const answer = await response.json();
  return { ok: response.ok, status: response.status, answer };
}

async function send() {
  if (sending || !outbox.length) {
    return;
  }
  sending = true;
  const batch = outbox.slice(0, oneByOne ? 1 : 50);
  try {
    const { ok, status } = await post("/api/events", { events: batch });
    offline = false;
    if (ok) {
      takeOut(batch);
    } else if (status === 401) {
      location.href = "/";
    } else if (status === 400) {
      refused(batch);
    } else if (status === 404) {
      await sessionGone();
    }
  } catch {
    offline = true; // the outbox is sent again shortly
  } finally {
    sending = false;
    showUnsent();
  }
}

function takeOut(events) {
  const gone = new Set(events.map((event) => event.cid));
  outbox = outbox.filter((event) => !gone.has(event.cid));
  oneByOne = oneByOne && outbox.length > 0;
  keep("outbox", outbox);
}

// One homehub won't have mustn't hold up the rest: they are sent singly to find it, and it is set
// aside once refused again.
function refused(batch) {
  oneByOne = true;
  const [event] = batch;
  if (batch.length > 1) {
    return;
  }
  event.refusals = (event.refusals ?? 0) + 1;
  if (event.refusals < REFUSALS) {
    return;
  }
  takeOut([event]);
  if (event.type === "set") {
    session.sets = session.sets.filter((set) => set.cid !== event.cid);
    tell("Not saved", "homehub wouldn't take one result, so it isn't in your history. "
      + "Log it again if you need it.");
    draw();
  }
}

async function sessionGone() {
  takeOut(outbox);
  await tell("No longer open", "This workout was discarded, maybe on another device.");
  forget();
  location.href = "/";
}

// Said only once something has waited a while: a moment's wait is normal.
function showUnsent() {
  const mark = document.getElementById("unsent");
  if (!mark) {
    return;
  }
  const waiting = outbox.length && Date.now() - (outbox[0].made ?? 0) > UNSENT_MS;
  mark.textContent = waiting ? (offline ? `offline, ${outbox.length} kept` : "saving…")
    : sleepy ? "may sleep" : "";
}

// Where we are ----------------------------------------------------------------

const setsOf = (item) =>
  session.sets.filter((set) => set.item === item.key).sort((a, b) => a.n - b.n);
const stateOf = (item) => session.states[item.key] ?? {};

function needsEffort(item) {
  return item.kind !== "cardio" && setsOf(item).length >= item.sets.length &&
    stateOf(item).rir == null;
}

function isDone(item) {
  const state = stateOf(item);
  if (state.skipped || state.hurt) {
    return true;
  }
  return setsOf(item).length >= item.sets.length && !needsEffort(item);
}

const current = () => session.items.find((item) => !isDone(item));

// Words -----------------------------------------------------------------------

function element(tag, options = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(options)) {
    if (key === "onclick") {
      node.addEventListener("click", value);
    } else if (key === "class") {
      node.className = value;
    } else if (key === "disabled") {
      node.disabled = value;
    } else {
      node.setAttribute(key, value);
    }
  }
  node.append(...children);
  return node;
}

const pounds = (load) => `${load} lb`;

function clock(seconds) {
  const whole = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

function targetWords(item, [load, amount]) {
  if (item.kind === "cardio") {
    return `${Math.round(amount / 60)} min`;
  }
  const unit = item.kind === "time" ? "s" : "";
  return load ? `${pounds(load)} × ${amount}` : `${amount}${unit}`;
}

// Drawing ---------------------------------------------------------------------

function draw() {
  if (session.simple) {
    drawSimple();
    return;
  }
  const item = current();
  app.replaceChildren();
  app.className = item?.tone ?? "cooldown";
  app.append(header(item));
  if (!item) {
    drawFinish();
  } else if (needsEffort(item)) {
    drawEffort(item);
  } else if (item.kind === "cardio") {
    drawBlock(item);
  } else {
    drawSet(item);
  }
  showUnsent();
  tick();
  bumped = "";
  document.dispatchEvent(new Event("session-drawn"));
}

function header(item) {
  const place = item ? session.items.indexOf(item) + 1 : session.items.length;
  const track = element("div", {
    class: "s-track", role: "img", "aria-label": `${place} of ${session.items.length}`,
  }, ...session.items.map((each) =>
    element("i", { class: `${each.tone} ${each === item ? "here" : isDone(each) ? "done" : ""}` })));
  return element("div", { class: "s-head" }, track,
    element("button", { class: "heart", "data-heart": "", "aria-label": "Heart rate",
      onclick: explainHeart }),
    element("small", { class: "muted", id: "unsent" }),
    element("button", { onclick: confirmLeave }, "Leave"));
}

function titleLines(item) {
  const side = item.per_side ? "Each side" : "";
  const pair = item.dumbbells === 2 ? "Two dumbbells" : item.dumbbells === 1 ? "One dumbbell" : "";
  const extra = [pair, side].filter(Boolean).join(", ");
  return element("div", { class: "s-title" },
    element("div", { class: "s-name" }, item.name),
    element("button", { class: "s-line", onclick: () => explain(item) }, item.why),
    element("div", { class: "s-line" },
      [extra, item.last ? `last time ${item.last}` : ""].filter(Boolean).join(" · ")));
}

function dots(item) {
  const done = setsOf(item);
  const row = element("div", { class: "s-dots" });
  item.sets.forEach((target, place) => {
    const set = done[place];
    const css = set ? "done" : place === done.length ? "now" : "";
    const fresh = justLogged === item.key && place === done.length - 1 ? " fresh" : "";
    row.append(element("span", { class: css + fresh }, String(set ? set.amount : target[1])));
  });
  return row;
}

function jump(label) {
  const which = ["weight", "level", "watts", "speed"].includes(label) ? "load"
    : label === "incline" ? "incline" : "amount";
  return bumped === which ? "bump" : "";
}

function stepper(label, value, small, less, more) {
  return element("div", { class: "s-step" },
    element("button", { onclick: less, "aria-label": `Less ${label}` }, "−"),
    element("div", {}, element("strong", { class: jump(label) }, value), element("small", {}, small)),
    element("button", { onclick: more, "aria-label": `More ${label}` }, "+"));
}

function drawSet(item) {
  const number = setsOf(item).length;
  const id = `${item.key}-${number}`;
  if (choice?.id !== id) {
    const [, amount] = item.sets[number];
    choice = { id, load: nextLoad(item, number), amount };
  }
  const body = element("div", { class: "s-body" }, titleLines(item), live(null), dots(item));
  if (item.kind === "load") {
    body.append(stepper("weight", pounds(choice.load),
      item.dumbbells === 2 ? "each dumbbell" : "one dumbbell",
      () => moveLoad(item, -1), () => moveLoad(item, 1)));
  }
  const step = item.kind === "time" ? 5 : 1;
  const unit = item.kind === "time" ? "seconds" : "reps";
  if (item.kind === "time") {
    body.append(isRunning(item) ? pauseButton()
      : element("button", { onclick: () => startHold(item, choice.amount) }, "Start the clock"));
  }
  body.append(
    stepper(unit, String(choice.amount), unit, () => moveAmount(-step), () => moveAmount(step)),
    element("button", { class: "primary s-log", onclick: () => logSet(item) },
      `Log set ${number + 1}`));
  app.append(body, timerRow(), strip(), tools(item));
}

function drawBlock(item) {
  const [level, seconds] = item.sets[0];
  const running = held?.key === item.key;
  if (choice?.id !== item.key) {
    choice = { id: item.key, load: level, incline: item.incline ?? 0 };
  }
  const body = element("div", { class: "s-body" }, titleLines(item));
  if (item.cue && !item.work) {
    body.append(element("p", { class: "s-cue" }, item.cue));
  }
  body.append(live(item.work ? element("div", { class: "s-phase", id: "phase" })
    : element("div", { class: "s-timer", id: "timer", "data-big": clock(seconds) })));
  if (item.levels.length) {
    body.append(stepper("level", item.levels[choice.load - 1], item.work ? "on the hard parts" : "level",
      () => moveLevel(item, -1), () => moveLevel(item, 1)));
  }
  if (item.pace) {
    body.append(paceSteppers(item));
  }
  if (!running) {
    body.append(element("button", {
      class: "primary s-log", onclick: () => startHold(item, seconds),
    }, startWords(item, seconds)));
  }
  const done = element("button", {
    class: running ? "primary s-log" : "s-log", onclick: () => logBlock(item),
  }, "Done");
  body.append(isRunning(item) ? element("div", { class: "s-run" }, pauseButton(), done) : done);
  app.append(body, strip(), tools(item));
}

// What to see from across the room: the time, where there is one, and the heart rate.
function live(time) {
  const heart = element("div", { class: "s-box" },
    element("button", { class: "s-heart", "data-heart": "", "aria-label": "Heart rate",
      onclick: explainHeart }));
  const boxes = time ? [element("div", { class: "s-box" }, time), heart] : [heart];
  return element("div", { class: time ? "s-live" : "s-live spare" }, ...boxes);
}

function pauseButton() {
  const stopped = held.paused !== undefined;
  return element("button", { class: "s-pause", onclick: stopped ? resumeClock : pauseClock },
    stopped ? "Resume" : "Pause");
}

function paceSteppers(item) {
  const where = item.work ? "on the hard parts" : "";
  if (item.pace.unit === "watts") {
    return stepper("watts", `${choice.load} W`, where || "average",
      () => movePace(item, -1), () => movePace(item, 1));
  }
  const speed = choice.load ? choice.load.toFixed(1) : "–";
  const unit = choice.load ? "km/h" : "my own pace";
  return element("div", { class: "s-pair" },
    stepper("speed", speed, unit, () => movePace(item, -1), () => movePace(item, 1)),
    stepper("incline", String(choice.incline), "incline",
      () => moveIncline(item, -1), () => moveIncline(item, 1)));
}

function startWords(item, seconds) {
  if (!item.work) {
    return `Start ${Math.round(seconds / 60)} minutes`;
  }
  return `Start ${Math.ceil(seconds / (item.work + item.off))} rounds`;
}

// Where an interval block is: which round, on or off, and what is left of that part.
function phaseOf(item, gone) {
  const round = item.work + item.off;
  const into = gone % round;
  const on = into < item.work;
  return {
    round: Math.floor(gone / round) + 1,
    of: Math.ceil(item.sets[0][1] / round),
    on,
    left: on ? item.work - into : round - into,
  };
}

function showPhase(item) {
  const place = document.getElementById("phase");
  if (!place || !item?.work) {
    return;
  }
  const seconds = item.sets[0][1];
  const left = held?.key === item.key ? leftOn(held) : null;
  if (left === null || left <= 0) {
    place.className = "s-phase";
    place.dataset.words = "";
    place.replaceChildren(element("small", {}, left === null
      ? `${clock(item.work)} on, ${clock(item.off)} off` : "All rounds done"));
    return;
  }
  const phase = phaseOf(item, seconds - left);
  const part = `${phase.round}-${phase.on}`;
  if (held.part !== part) {
    if (held.part) {
      sound(phase.on ? "on" : "off");
    }
    held.part = part;
    held.ticked = 0;
    keep("held", held);
  }
  const stopped = held.paused !== undefined;
  const last = !stopped && phase.left <= LAST_SECONDS;
  place.className = `s-phase runs ${phase.on ? "on" : "off"} ${last ? "last" : ""}`;
  place.style.setProperty("--left", phase.left / (phase.on ? item.work : item.off));
  const now = stopped ? "PAUSED" : phase.on ? "ON" : "OFF";
  const words = `${now}|${clock(phase.left)}|round ${phase.round} of ${phase.of}`;
  if (place.dataset.words !== words) {
    place.dataset.words = words;
    const [what, time, round] = words.split("|");
    place.replaceChildren(element("b", {}, what),
      element("strong", {}, element("span", { class: "tock" }, time)),
      element("small", {}, round));
  }
  if (!stopped) {
    countDown(held, phase.left);
  }
}

function drawEffort(item) {
  const row = element("div", { class: "s-rir" });
  for (const left of [0, 1, 2, 3, 4]) {
    row.append(element("button", { onclick: () => answerEffort(item, left) },
      left === 4 ? "4+" : String(left)));
  }
  app.append(
    element("div", { class: "s-body" },
      element("div", { class: "s-title s-name" }, item.name), live(null), dots(item),
      element("p", { class: "s-ask" }, "How many more could you have done on the last set?"),
      element("p", { class: "s-cue" }, item.kind === "load" ? "It decides your next weight."
        : "It decides what comes next."),
      row),
    timerRow(), strip(), tools(item));
}

let celebrated = false;

function celebrate() {
  if (celebrated) {
    return;
  }
  celebrated = true;
  sound("done");
  const party = element("div", { class: "party", "aria-hidden": "true" });
  const tones = ["lifting", "flatout", "steady", "cooldown", "warmup"];
  for (let piece = 0; piece < 36; piece += 1) {
    const bit = element("i", { class: tones[piece % tones.length] });
    bit.style.setProperty("--x", `${Math.random() * 100}vw`);
    bit.style.setProperty("--wait", `${Math.random() * 0.7}s`);
    bit.style.setProperty("--turn", `${360 + Math.random() * 720}deg`);
    party.append(bit);
  }
  document.body.append(party);
  setTimeout(() => party.remove(), 4000);
}

function drawFinish() {
  const done = session.items.filter((item) => setsOf(item).length);
  if (done.length) {
    celebrate();
  }
  const list = element("ul", { class: "rows" });
  for (const item of session.items) {
    const sets = setsOf(item);
    const words = !sets.length ? "skipped"
      : item.kind === "cardio" ? `${Math.max(1, Math.round(sets[0].amount / 60))} min`
      : sets.map((set) => set.amount).join(" ");
    list.append(element("li", {}, element("div", { class: `row ${item.tone}` },
      element("span", { class: "words" }, element("span", { class: "name" }, item.name)),
      element("span", { class: "big-amount" }, words))));
  }
  app.append(
    element("div", { class: "s-name" }, done.length ? "All done" : "Nothing logged"),
    element("section", { class: "panel scroll grow" }, list),
    element("button", { class: "primary s-log", onclick: finish }, "Finish"), strip());
}

// Simple: the whole list, each ticked off as done as planned. No timers, and no screen per set.
let ticked = null; // the one just ticked, which offers how it went

function amounts(item) {
  if (item.kind === "cardio") {
    return `${Math.round(item.sets[0][1] / 60)} min`;
  }
  const counts = item.sets.map(([, amount]) => amount);
  const unit = item.kind === "time" ? "s" : "";
  return counts.every((count) => count === counts[0])
    ? `${counts.length}×${counts[0]}${unit}` : counts.join(" ") + unit;
}

function simpleUnder(item) {
  const [load] = item.sets[0];
  if (item.kind === "cardio") {
    return item.cue || "";
  }
  return load ? `${pounds(load)}${item.per_side ? ", each side" : ""}`
    : item.per_side ? "Each side" : "";
}

function drawSimple() {
  app.replaceChildren();
  app.className = "simple";
  const list = element("ul", { class: "rows" });
  for (const item of session.items) {
    const done = setsOf(item).length >= item.sets.length;
    const under = simpleUnder(item);
    list.append(element("li", { class: `${item.tone}${done ? " done" : ""}` },
      element("button", {
        class: "row", "aria-pressed": String(done), onclick: () => toggle(item),
      },
      element("span", { class: "tick", "aria-hidden": "true" }),
      element("span", { class: "words" }, element("span", { class: "name" }, item.name),
        ...(under ? [element("span", { class: "under" }, under)] : [])),
      element("span", { class: "big-amount" }, amounts(item)))));
    if (done && ticked === item.key && item.kind !== "cardio") {
      list.append(howItWent(item));
    }
  }
  const left = session.items.filter((item) => setsOf(item).length < item.sets.length).length;
  app.append(header(null),
    element("div", { class: "s-name" }, session.title),
    element("p", { class: "muted s-left" }, left ? `${left} to go. Tap each as it's done.`
      : "All done."),
    element("section", { class: "panel scroll grow" }, list),
    element("button", { class: `${left ? "" : "primary "}s-log`, onclick: finish }, "Finish"),
    strip());
  showUnsent();
  document.dispatchEvent(new Event("session-drawn"));
}

// Optional: unanswered, it counts as two more in reserve, which lets weights rise slowly.
function howItWent(item) {
  const chosen = stateOf(item).rir;
  const choices = [[4, "Easy"], [2, "Fine"], [0, "Hard"]].map(([value, label]) =>
    element("button", {
      "aria-pressed": String(chosen === value),
      onclick: () => { record({ type: "rir", item: item.key, value }); drawSimple(); },
    }, label));
  return element("li", { class: "went" }, element("span", { class: "muted" }, "How was it?"),
    element("div", { class: "segments" }, ...choices));
}

function toggle(item) {
  unlockSound();
  const sets = setsOf(item);
  if (sets.length >= item.sets.length) {
    for (const set of sets) {
      record({ type: "unset", item: item.key, target: set.cid });
    }
    ticked = null;
  } else {
    item.sets.forEach(([load, amount], n) => {
      if (!sets.some((set) => set.n === n)) {
        record({ type: "set", item: item.key, n, load, amount, incline: item.incline ?? 0 });
      }
    });
    if (item.kind !== "cardio") {
      record({ type: "rir", item: item.key, value: 2 });
    }
    ticked = item.key;
    sound("log");
  }
  drawSimple();
}

function timerRow() {
  return element("div", { class: "s-timer", id: "timer" });
}

// The music (music.js fills it). Its space is kept so nothing moves as it comes and goes.
function strip() {
  return element("div", { class: "player", "data-music": "" });
}

function tools(item) {
  const sets = setsOf(item);
  return element("div", { class: "s-tools" },
    element("button", { disabled: !session.sets.length, onclick: undo }, "Undo"),
    element("button", {
      disabled: !item.swappable || sets.length > 0, onclick: () => swapItem(item),
    }, "Swap"),
    element("button", { disabled: !item.how, onclick: () => showHow(item) }, "How"),
    element("button", { onclick: () => skip(item) }, "Skip"),
    element("button", { onclick: () => hurts(item) }, "Hurts"));
}

// Doing -----------------------------------------------------------------------

// A weight changed on one set stays at that for the rest, whatever was planned.
function nextLoad(item, number) {
  const [planned] = item.sets[number];
  const before = setsOf(item).at(-1);
  if (item.kind !== "load" || !before || !item.sets[number - 1]) {
    return planned;
  }
  return before.load === item.sets[number - 1][0] ? planned : before.load;
}

function moveLoad(item, by) {
  const place = item.loads.indexOf(choice.load);
  const next = item.loads[place + by];
  if (next !== undefined) {
    choice.load = next;
    bumped = "load";
    draw();
  }
}

function moveLevel(item, by) {
  choice.load = Math.max(1, Math.min(item.levels.length, choice.load + by));
  bumped = "load";
  draw();
}

function movePace(item, by) {
  const { loads } = item.pace;
  const place = Math.max(0, Math.min(loads.length - 1, loads.indexOf(choice.load) + by));
  choice.load = loads[place];
  choice.incline = Math.min(choice.incline, steepest(item));
  bumped = "load";
  draw();
}

function moveIncline(item, by) {
  choice.incline = Math.max(0, Math.min(steepest(item), choice.incline + by));
  bumped = "incline";
  draw();
}

// The faster, the less steep it may be.
function steepest(item) {
  return item.pace.steepest?.[item.pace.loads.indexOf(choice.load)] ?? 0;
}

function moveAmount(by) {
  choice.amount = Math.max(1, choice.amount + by);
  bumped = "amount";
  draw();
}

function logSet(item) {
  unlockSound();
  stopHold();
  const n = nextNumber(item);
  record({ type: "set", item: item.key, n, load: choice.load, amount: choice.amount });
  startRest(item.rest);
  sound("log");
  justLogged = item.key;
  draw();
  justLogged = null;
}

function nextNumber(item) {
  const last = setsOf(item).at(-1);
  return last ? last.n + 1 : 0;
}

function logBlock(item) {
  const [, seconds] = item.sets[0];
  const left = held?.key === item.key ? Math.max(0, leftOn(held)) : 0;
  const amount = Math.max(1, Math.round(seconds - left));
  stopHold();
  const load = item.levels.length || item.pace ? choice.load : 0;
  const incline = item.pace ? choice.incline : 0;
  record({ type: "set", item: item.key, n: 0, load, incline, amount });
  choice = null;
  draw();
}

function answerEffort(item, value) {
  record({ type: "rir", item: item.key, value });
  const next = current();
  if (next?.kind === "load") {
    remindPlates(next);
  }
  draw();
}

function undo() {
  const last = ordered().at(-1);
  record({ type: "unset", item: last.item, target: last.cid });
  choice = null;
  rest = null;
  keep("rest", rest);
  draw();
}

// Sets in the order they were done: exercises in plan order, then set number.
function ordered() {
  const place = (set) => session.items.findIndex((item) => item.key === set.item);
  return [...session.sets].sort((a, b) => place(a) - place(b) || a.n - b.n);
}

function skip(item) {
  record({ type: "skip", item: item.key });
  stopHold();
  draw();
}

async function hurts(item) {
  if (await ask(`${item.name} hurts`, "It stays out of your plan until you clear it in Me.",
    "Stop this exercise")) {
    record({ type: "hurt", item: item.key });
    stopHold();
    draw();
  }
}

async function swapItem(item) {
  try {
    const { ok, answer } = await post("/api/swap", { item: item.key });
    if (ok) {
      adopt(answer);
      choice = null;
      draw();
    } else {
      await tell("No swap", answer.error);
    }
  } catch {
    await tell("No connection", "Swapping needs homehub. Skip works without it.");
  }
}

async function finish() {
  if (!session.sets.length) {
    await discard();
    return;
  }
  record({ type: "finish" });
  for (let tries = 0; tries < 5 && outbox.length; tries += 1) {
    await send();
    if (outbox.length) {
      await new Promise((done) => setTimeout(done, 800));
    }
  }
  if (outbox.length) {
    await tell("Not sent yet", "Your sets are safe on this phone. Keep this page open near Wi-Fi.");
    return;
  }
  forget();
  location.href = "/";
}

async function confirmLeave() {
  const answer = await ask("Leave?",
    "Pause stops the clock and keeps this session for later. Discard deletes it.",
    "Pause", "Discard");
  if (answer === "first") {
    pauseClock();
    await Promise.race([send(), new Promise((done) => setTimeout(done, LEAVE_MS))]);
    location.href = "/";
  } else if (answer === "second") {
    await discard();
  }
}

function explainHeart() {
  if (document.documentElement.dataset.monitor !== "set") {
    tell("Heart rate", "No monitor is chosen yet: leave, then Me, Heart rate.");
    return;
  }
  tell("Heart rate", "No reading? Open HeartCast and keep it in front for 20 seconds, then come "
    + "back here. homehub keeps looking, and stays connected once it has found it.");
}

async function discard() {
  try {
    await post("/api/discard", {});
    forget();
    location.href = "/";
  } catch {
    await tell("No connection", "Discarding needs homehub.");
  }
}

// Dialogs ---------------------------------------------------------------------

function ask(title, words, first, second) {
  return new Promise((resolve) => {
    const dialog = element("dialog");
    const close = (value) => { dialog.close(); dialog.remove(); resolve(value); };
    const buttons = element("div", { class: "actions" },
      element("button", { onclick: () => close(null) }, "Back"));
    if (second) {
      buttons.append(element("button", { class: "danger", onclick: () => close("second") }, second));
    }
    buttons.append(element("button", { class: "primary", onclick: () => close("first") }, first));
    dialog.append(element("b", {}, title), element("p", {}, words), buttons);
    dialog.addEventListener("cancel", () => close(null));
    document.body.append(dialog);
    dialog.showModal();
  });
}

function tell(title, words) {
  return new Promise((resolve) => {
    const dialog = element("dialog");
    const close = () => { dialog.close(); dialog.remove(); resolve(); };
    dialog.append(element("b", {}, title), element("p", {}, words),
      element("div", { class: "actions" },
        element("button", { class: "primary", onclick: close }, "OK")));
    dialog.addEventListener("cancel", close);
    document.body.append(dialog);
    dialog.showModal();
  });
}

function showHow(item) {
  const dialog = element("dialog", { class: "guide" });
  const close = () => { dialog.close(); dialog.remove(); };
  dialog.append(element("b", {}, item.name),
    element("ol", {}, ...item.how.steps.map((step) => element("li", {}, step))),
    element("p", {}, element("b", {}, "Watch for: "), item.how.watch),
    element("div", { class: "actions" },
      element("a", {
        class: "button", href: item.how.link, target: "_blank", rel: "noopener noreferrer",
      }, item.how.source),
      element("button", { class: "primary", onclick: close }, "OK")));
  dialog.addEventListener("cancel", close);
  document.body.append(dialog);
  dialog.showModal();
}

const explain = (item) => tell(item.name, [item.why, item.cue].filter(Boolean).join(" "));

// The 552 was recalled in the US for plates coming loose, hence the reminder.
let lastDial = null;
function remindPlates(item) {
  const [load] = item.sets[0];
  if (lastDial !== null && lastDial !== load) {
    tell("Change the dumbbells",
      `Set ${item.dumbbells === 2 ? "both dials" : "the dial"} to ${pounds(load)} `
      + "and check every plate is seated before lifting clear.");
  }
  lastDial = load;
}

// Timers, sound and the screen --------------------------------------------------

function startRest(seconds) {
  rest = { until: Date.now() + seconds * 1000, length: seconds, rung: false };
  keep("rest", rest);
}

function startHold(item, seconds) {
  unlockSound();
  rest = null;
  held = { key: item.key, until: Date.now() + seconds * 1000, length: seconds, rung: false };
  sound("on");
  keep("held", held);
  draw();
}

function stopHold() {
  held = null;
  keep("held", held);
  markResumed();
}

// What is left on a clock, stopped or running.
const leftOn = (timer) => timer.paused ?? (timer.until - Date.now()) / 1000;

const isRunning = (item) => held?.key === item.key && leftOn(held) > 0;

// Stops the clock, if one is running, and the session's time with it.
function pauseClock() {
  if (held && held.paused === undefined && leftOn(held) > 0) {
    held.paused = leftOn(held);
    keep("held", held);
  }
  if (!session.paused) {
    record({ type: "pause", at: Date.now() / 1000 });
  }
  draw();
}

function resumeClock() {
  unlockSound();
  held.until = Date.now() + held.paused * 1000;
  delete held.paused;
  keep("held", held);
  markResumed();
  draw();
}

function markResumed() {
  if (session.paused) {
    record({ type: "resume", at: Date.now() / 1000 });
  }
}

const LEAVE_MS = 1500; // how long leaving waits for homehub to hear of it
const NEAR_SECONDS = 10;
const LAST_SECONDS = 3;

function tick() {
  const item = current();
  showPhase(item);
  const timer = held ?? rest;
  const left = timer ? leftOn(timer) : 0;
  showTimer(timer, left);
  if (!timer) {
    return;
  }
  // In intervals each part has its own countdown, so only the very end is this timer's.
  if (left > 0 && timer.paused === undefined && !(held && item?.work)) {
    countDown(timer, left);
  }
  if (left <= 0 && !timer.rung) {
    timer.rung = true;
    keep(held ? "held" : "rest", timer);
    sound("end");
    if (held) {
      draw(); // there is nothing left to pause
    }
  }
}

function showTimer(timer, left) {
  const row = document.getElementById("timer");
  if (!row) {
    return;
  }
  const whole = row.dataset.big; // a block's clock, shown before it starts too
  if (!timer) {
    row.className = "s-timer";
    if (whole) {
      showSeconds(row, whole);
    } else {
      row.replaceChildren();
    }
    return;
  }
  const over = left <= 0;
  const stopped = timer.paused !== undefined;
  row.className = `s-timer runs ${over ? "over" : stopped ? "" : urgency(left)}`;
  row.style.setProperty("--left", over ? 1 : Math.max(0, left / (timer.length || left)));
  const label = stopped ? "Paused" : held ? "" : "Rest";
  showSeconds(row, over ? (held ? "Time" : "Go") : clock(left), over ? "" : label);
}

function urgency(left) {
  return left <= LAST_SECONDS ? "near last" : left <= NEAR_SECONDS ? "near" : "";
}

// Redrawn only as the words change, so each second's beat plays once.
function showSeconds(place, words, label = "") {
  const all = `${label} ${words}`;
  if (place.dataset.words !== all || !place.firstChild) {
    place.dataset.words = all;
    place.replaceChildren(...(label ? [element("small", {}, label)] : []),
      element("span", { class: "tock" }, words));
  }
}

// A tick for each of the last three seconds.
function countDown(timer, left) {
  const second = Math.ceil(left);
  if (second <= LAST_SECONDS && second > 0 && timer.ticked !== second) {
    timer.ticked = second;
    sound("tick");
  }
}

function unlockSound() {
  try {
    audio ??= new AudioContext();
    audio.resume();
  } catch {
    audio = null;
  }
}

// Each sound is notes: when, pitch, how long. Pitched and shaped to carry over music.
const SOUNDS = {
  tick: [[0, 1175, 0.13]],
  log: [[0, 880, 0.08]],
  on: [[0, 1320, 0.16], [0.18, 1760, 0.4]],
  off: [[0, 1320, 0.16], [0.18, 880, 0.4]],
  end: [[0, 1320, 0.2], [0.24, 1320, 0.2], [0.48, 1760, 0.55]],
  done: [[0, 523, 0.14], [0.14, 659, 0.14], [0.28, 784, 0.14], [0.42, 1047, 0.45]],
};
const LOUD = 0.95;
const LEVELS = { loud: 1, quiet: 0.3, off: 0 }; // as each person chose in Me
const QUIETER = { log: 0.4, done: 0.5 };
const BUZZES = { tick: 30, log: 20, on: [160, 60, 160], off: 220, end: [200, 100, 200] };

function sound(name) {
  navigator.vibrate?.(BUZZES[name] ?? [80, 40, 80]);
  const level = LEVELS[session?.sounds] ?? 1;
  if (!audio || !level) {
    return;
  }
  for (const [at, pitch, length] of SOUNDS[name]) {
    const tone = audio.createOscillator();
    const volume = audio.createGain();
    const start = audio.currentTime + at;
    tone.type = "square";
    tone.frequency.value = pitch;
    const loudness = (QUIETER[name] ?? LOUD) * level;
    volume.gain.setValueAtTime(loudness, start);
    volume.gain.setValueAtTime(loudness, start + length * 0.7);
    volume.gain.exponentialRampToValueAtTime(0.001, start + length);
    tone.connect(volume).connect(audio.destination);
    tone.start(start);
    tone.stop(start + length + 0.02);
  }
}

// Safari grants this only after a tap, and every browser takes it back when the page is hidden, so
// it is asked for on each tap until it is held.
let awake = null;
let asking = false;
async function stayAwake(tapped = false) {
  if (awake || asking || document.visibilityState !== "visible") {
    return;
  }
  asking = true;
  try {
    awake = await navigator.wakeLock.request("screen");
    awake.addEventListener("release", () => { awake = null; });
    sleepy = false;
  } catch {
    sleepy = tapped; // low battery mode, or an old browser
  } finally {
    asking = false;
    showUnsent();
  }
}

// Start -----------------------------------------------------------------------

function adopt(answer) {
  session = answer;
  outbox = kept("outbox", []);
  outbox.forEach(applyLocally);
}

async function begin() {
  const response = await fetch("/api/session");
  if (!response.ok) {
    location.href = "/";
    return;
  }
  adopt(await response.json());
  rest = kept("rest", null);
  held = kept("held", null);
  if (held?.paused === undefined) {
    markResumed(); // nothing was stopped part way, so it goes on from here
  }
  const first = current();
  if (first?.kind === "load") {
    [lastDial] = first.sets[0];
  }
  draw();
  send();
  setInterval(send, SEND_EVERY_MS);
  setInterval(showUnsent, 1000);
  setInterval(tick, 100);
  stayAwake();
  document.addEventListener("pointerdown", unlockSound);
  document.addEventListener("click", () => stayAwake(true));
  document.addEventListener("visibilitychange", () => { stayAwake(); tick(); send(); });
}

begin();
