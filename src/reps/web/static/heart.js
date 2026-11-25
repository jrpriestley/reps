// The heart rate, on every screen. It fills each [data-heart] element.

const EVERY_MS = 3000;
const KEPT = "reps-heart"; // so the next page starts with what this one knew
const KEPT_MS = 10000; // and never with an old reading
const root = document.documentElement;

function words(heart) {
  if (heart?.unsure) {
    return "♥ ?";
  }
  return heart?.bpm ? `♥ ${heart.bpm}` : "♥ –";
}

let last = null;

// The heart beats at the rate it shows.
function show(heart) {
  const said = words(heart);
  try {
    sessionStorage.setItem(KEPT, JSON.stringify({ heart, at: Date.now() }));
  } catch {
    // Private windows may refuse; the next page waits for the server instead.
  }
  root.style.setProperty("--beat", heart?.bpm ? `${60 / heart.bpm}s` : "0s");
  root.dataset.monitor = heart?.set ? "set" : "none";
  for (const place of document.querySelectorAll("[data-heart]")) {
    place.classList.toggle("idle", !heart?.bpm);
    if (place.dataset.said === said) {
      continue;
    }
    place.dataset.said = said;
    place.title = heart?.unsure ? "Two with that name nearby: turn one off" : "Heart rate";
    const beat = document.createElement("span");
    beat.className = "beat";
    beat.textContent = said.slice(0, 1);
    place.replaceChildren(beat, said.slice(1));
  }
}

async function read() {
  if (document.visibilityState !== "visible") {
    return;
  }
  try {
    const response = await fetch("/api/heart");
    last = response.ok ? await response.json() : null;
  } catch {
    last = last && { ...last, bpm: null };
  }
  show(last);
}

function recall() {
  try {
    const { heart, at } = JSON.parse(sessionStorage.getItem(KEPT)) ?? {};
    if (heart && Date.now() - at < KEPT_MS) {
      last = heart;
    }
  } catch {
    // Nothing kept.
  }
  show(last);
}

recall();
read();
setInterval(read, EVERY_MS);
document.addEventListener("visibilitychange", read);
document.addEventListener("session-drawn", () => show(last));
