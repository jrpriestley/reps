// Dragging a workout's blocks about. The server draws the chart and keeps it; this only works out
// the new order. Without it every block still opens, moves and takes choices by tapping.

const flow = document.getElementById("flow");
const MOST_BLOCKS = Number(flow.dataset.most);
const MOST_OPTIONS = Number(flow.dataset.options);
const EDGE = 56; // how near the frame's edge a drag starts to scroll it
const EDGE_STEP = 6;
const TOWARDS = 24;
const STARTS_AFTER = 5; // pixels, so a tap on a grip is not a drag

let blocks = JSON.parse(document.getElementById("blocks").textContent);
let drag = null;

const isCardio = (block) => block.minutes !== undefined;
const copy = (value) => JSON.parse(JSON.stringify(value));

function newBlock(key, cardio) {
  return cardio ? { options: [key], minutes: 15, style: "steady", level: 0 }
    : { options: [key], sets: 3 };
}

// What is being moved --------------------------------------------------------------

function sourceOf(grip) {
  if (grip.dataset.grip === "new") {
    const cardio = grip.dataset.cardio === "1";
    return { kind: "new", block: newBlock(grip.dataset.key, cardio), node: grip };
  }
  const place = Number(grip.closest(".block").dataset.place);
  if (grip.dataset.grip === "option") {
    const block = { ...copy(blocks[place]), options: [grip.dataset.key] };
    return { kind: "option", place, block, node: grip.closest(".step") };
  }
  return { kind: "block", place, block: copy(blocks[place]), node: grip.closest(".block") };
}

function rectangles() {
  return [...flow.querySelectorAll(".block")].map((node) => ({
    node, place: Number(node.dataset.place), box: node.getBoundingClientRect(),
  }));
}

// Over the middle of a block that can take it: an "or". Else the nearest place between blocks.
function targetAt(x, y) {
  const frame = flow.getBoundingClientRect();
  if (x < frame.left - 20 || x > frame.right + 20 || y < frame.top - 40 || y > frame.bottom + 40) {
    return null;
  }
  const found = rectangles();
  for (const { node, place, box } of found) {
    const third = box.height * 0.28;
    if (y > box.top + third && y < box.bottom - third && canJoin(place)) {
      return { kind: "join", place, node };
    }
  }
  const after = found.filter(({ box }) => y > box.top + box.height / 2).length;
  if (drag.source.kind !== "block" && !roomFor()) {
    return null;
  }
  return { kind: "gap", place: after, node: flow.querySelector(`[data-gap="${after}"]`) };
}

// An option pulled out of its block makes one more, unless it was the block's only one.
function roomFor() {
  return blocks.length < MOST_BLOCKS;
}

function canJoin(place) {
  const { source } = drag;
  if (source.kind !== "new" && source.place === place) {
    return false;
  }
  const target = blocks[place];
  if (isCardio(target) !== isCardio(source.block)) {
    return false;
  }
  const joined = new Set([...target.options, ...source.block.options]);
  return joined.size > target.options.length && joined.size <= MOST_OPTIONS;
}

function arranged(source, target) {
  const next = copy(blocks);
  let place = target.place;
  let removed = -1;
  if (source.kind === "block") {
    removed = source.place;
  } else if (source.kind === "option") {
    const from = next[source.place];
    from.options = from.options.filter((key) => key !== source.block.options[0]);
    removed = from.options.length ? -1 : source.place;
  }
  if (target.kind === "join") {
    const into = next[place];
    into.options = [...new Set([...into.options, ...source.block.options])];
    if (removed >= 0) {
      next.splice(removed, 1);
    }
    return next;
  }
  if (removed >= 0) {
    next.splice(removed, 1);
    place -= removed < place ? 1 : 0;
  }
  next.splice(place, 0, source.block);
  return next;
}

// Dragging ----------------------------------------------------------------------------

function begin(event) {
  const grip = event.target.closest("[data-grip]");
  if (!grip || event.button > 0 || drag) {
    return;
  }
  event.preventDefault();
  drag = {
    source: sourceOf(grip), grip, pointer: event.pointerId, x: event.clientX, y: event.clientY,
    ghost: null, target: null,
  };
  grip.setPointerCapture(event.pointerId);
}

function lift() {
  const box = drag.source.node.getBoundingClientRect();
  const ghost = drag.source.node.cloneNode(true);
  ghost.classList.add("ghost");
  ghost.style.width = `${box.width}px`;
  ghost.removeAttribute("data-place");
  drag.offset = { x: drag.x - box.left, y: drag.y - box.top };
  drag.ghost = ghost;
  document.body.append(ghost);
  drag.source.node.classList.add("lifted");
  document.body.classList.add("dragging");
  navigator.vibrate?.(15);
}

function move(event) {
  if (!drag || event.pointerId !== drag.pointer) {
    return;
  }
  if (!drag.ghost) {
    if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < STARTS_AFTER) {
      return;
    }
    lift();
  }
  drag.at = { x: event.clientX, y: event.clientY };
  drag.ghost.style.transform =
    `translate(${event.clientX - drag.offset.x}px, ${event.clientY - drag.offset.y}px)`;
  aim();
}

function aim() {
  const target = targetAt(drag.at.x, drag.at.y);
  if (target?.node !== drag.target?.node) {
    drag.target?.node?.classList.remove("into");
    target?.node?.classList.add("into");
  }
  drag.target = target;
}

function scrollNearEdge() {
  if (drag?.at) {
    const frame = flow.getBoundingClientRect();
    const moved = drag.at.y - drag.y;
    // Towards the edge only: a block picked up there must not run away at once.
    if (drag.at.y < frame.top + EDGE && moved < -TOWARDS) {
      flow.scrollTop -= EDGE_STEP;
    } else if (drag.at.y > frame.bottom - EDGE && moved > TOWARDS) {
      flow.scrollTop += EDGE_STEP;
    }
    aim();
  }
  requestAnimationFrame(scrollNearEdge);
}

function end(event) {
  if (!drag || event.pointerId !== drag.pointer) {
    return;
  }
  const { source, target, ghost } = drag;
  ghost?.remove();
  source.node.classList.remove("lifted");
  target?.node?.classList.remove("into");
  document.body.classList.remove("dragging");
  drag = null;
  if (ghost && target && event.type === "pointerup") {
    keep(arranged(source, target), true);
  }
}

// Keeping it -----------------------------------------------------------------------------

async function keep(next, undoable) {
  const before = blocks;
  if (JSON.stringify(next) === JSON.stringify(before)) {
    return;
  }
  try {
    const response = await fetch(`/api/workouts/${flow.dataset.workout}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ blocks: next }),
    });
    if (!response.ok) {
      throw new Error(String(response.status));
    }
    const answer = await response.json();
    blocks = answer.blocks;
    const top = flow.scrollTop;
    flow.innerHTML = answer.flow;
    flow.scrollTop = top;
    const words = document.querySelector(".head .sub");
    if (words) {
      words.textContent = answer.saved;
    }
    toast(undoable ? "Saved" : "Put back", undoable ? () => keep(before, false) : null);
  } catch {
    toast("Not saved: no connection", null);
  }
}

let shown = null;

function toast(words, undo) {
  shown?.remove();
  const note = document.createElement("div");
  note.className = "toast";
  note.setAttribute("role", "status");
  note.append(words);
  if (undo) {
    const button = document.createElement("button");
    button.textContent = "Undo";
    button.addEventListener("click", () => { note.remove(); undo(); });
    note.append(button);
  }
  document.body.append(note);
  shown = note;
  setTimeout(() => note.remove(), 6000);
}

// The grips also work from a keyboard.
function nudge(event) {
  const grip = event.target.closest?.('[data-grip="block"]');
  const step = { ArrowUp: -1, ArrowDown: 1 }[event.key];
  if (!grip || !step) {
    return;
  }
  event.preventDefault();
  const place = Number(grip.closest(".block").dataset.place);
  const other = place + step;
  if (other >= 0 && other < blocks.length) {
    const next = copy(blocks);
    [next[place], next[other]] = [next[other], next[place]];
    keep(next, true).then(() =>
      flow.querySelector(`[data-place="${other}"] [data-grip="block"]`)?.focus());
  }
}

document.addEventListener("pointerdown", begin);
document.addEventListener("pointermove", move);
document.addEventListener("pointerup", end);
document.addEventListener("pointercancel", end);
document.addEventListener("keydown", nudge);
requestAnimationFrame(scrollNearEdge);
