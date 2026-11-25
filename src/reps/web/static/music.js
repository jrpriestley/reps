// The music widget, on every screen: the mix, the song, previous, back, play or pause, forward,
// and next. It fills each [data-music] element. Spotify's rules: their logo, a link back, and the artwork as they made it.

const EVERY_MS = 3000;
const AFTER_CONTROL_MS = 1200;
const MIXES_SHOWN = 4;
const SONGS_KEPT = 20;

const KEPT = "reps-music"; // so the next page starts with what this one knew

let state = null; // what the server last said
let drawnAs = "";
let busy = false;
let earlier = []; // the songs before this one, newest last
let ending = 0; // the timer for when the song ends

function element(tag, options = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(options)) {
    if (key === "onclick") {
      node.addEventListener("click", value);
    } else if (key === "disabled") {
      node.disabled = value;
    } else {
      node.setAttribute(key, value);
    }
  }
  node.append(...children);
  return node;
}

function sign(name) {
  const paths = {
    play: "M8 5v14l11-7z", pause: "M7 5h4v14H7zM13 5h4v14h-4z", next: "M6 5v14l9-7zM16 5h3v14h-3z",
    previous: "M18 5v14l-9-7zM5 5h3v14H5z",
    back: "M11.500 5.500v13l-9-6.500zM21 5.500v13l-9-6.500z",
    forward: "M3 5.500v13l9-6.500zM12.500 5.500v13l9-6.500z",
    mix: "M9 18V6l10-2v12M9 18a3 2.5 0 1 1-3-2.5A3 2.5 0 0 1 9 18zM19 16a3 2.5 0 1 1-3-2.5 3 2.5 0 0 1 3 2.5z",
  };
  const space = "http://www.w3.org/2000/svg";
  const picture = document.createElementNS(space, "svg");
  picture.setAttribute("viewBox", "0 0 24 24");
  picture.setAttribute("class", `music-sign${name === "mix" ? " line" : ""}`);
  picture.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(space, "path");
  path.setAttribute("d", paths[name]);
  picture.append(path);
  return picture;
}

function button(name, label, onclick) {
  return element("button", { class: "music-button", "aria-label": label, title: label, onclick,
    disabled: busy }, sign(name));
}

function words(first, second) {
  return element("span", { class: "song-words" },
    element("b", {}, element("span", {}, first)), element("i", {}, element("span", {}, second)));
}

// A line too long for its place glides from end to end, so all of it can be read.
function glide(place) {
  for (const line of place.querySelectorAll(".song-words > *")) {
    const over = line.firstChild.offsetWidth - line.clientWidth;
    line.classList.toggle("glides", over > 1);
    line.style.setProperty("--over", `${-over}px`);
    line.style.setProperty("--time", `${6 + over / 18}s`);
  }
}

function middle(song) {
  if (!state.linked) {
    return element("a", { class: "song", href: "/spotify" }, words("Music", "Link Spotify"));
  }
  if (!song) {
    const mix = state.mix ? state.mix.name : "Music";
    return element("a", { class: "song", href: "/music" },
      words(mix, state.note || (state.mix ? "Press play" : "No mix")));
  }
  const link = element("a", {
    class: "song", href: song.link, target: "_blank", rel: "noopener noreferrer",
    title: `${song.title}, ${song.by}: open Spotify`,
  });
  if (song.art) {
    link.append(element("img", {
      class: "song-art", src: song.art, alt: "", width: 44, height: 44,
    }));
  }
  link.append(element("span", { class: "song-logo", role: "img", "aria-label": "Spotify" }),
    words(song.title, state.note || song.by));
  return link;
}

function fill(place) {
  if (!state) {
    return;
  }
  const song = state.song;
  const playing = Boolean(song?.playing);
  const parts = [middle(song)];
  if (state.linked) {
    parts.push(
      button("mix", "Choose a mix", chooseMix),
      button("previous", "Previous song", () => control("previous")),
      button("back", "Back 15 seconds", () => control("back")),
      button(playing ? "pause" : "play", playing ? "Pause" : "Play", () => control("toggle")),
      button("forward", "Forward 15 seconds", () => control("forward")),
      button("next", "Next song", () => control("next")));
  }
  place.replaceChildren(...parts);
  glide(place);
}

function draw() {
  const { soon: _soon, ...shown } = state ?? {}; // how long is left changes nothing on screen
  const now = JSON.stringify([shown, busy]);
  if (now === drawnAs) {
    return;
  }
  drawnAs = now;
  remember();
  document.querySelectorAll("[data-music]").forEach(fill);
}

async function read() {
  if (document.visibilityState !== "visible" || busy) {
    return;
  }
  try {
    const response = await fetch("/api/music");
    if (response.ok && !busy) {
      take(await response.json());
    }
  } catch {
    // No connection: what was last known stays.
  }
  draw();
}

// Takes in what the server said, and looks again just as the song ends.
function take(fresh) {
  const old = state?.song;
  if (old && fresh.song && old.link !== fresh.song.link && earlier.at(-1)?.link !== old.link) {
    earlier = [...earlier, old].slice(-SONGS_KEPT);
  }
  state = fresh;
  clearTimeout(ending);
  if (fresh.soon != null && fresh.soon * 1000 < EVERY_MS) {
    ending = setTimeout(read, fresh.soon * 1000);
  }
}

// What a press will bring, shown before Spotify has said so.
function expect(action) {
  const song = state.song;
  if (action === "next" && state.next) {
    if (song) {
      earlier = [...earlier, song].slice(-SONGS_KEPT);
    }
    state = { ...state, song: { ...state.next, playing: true }, next: null };
  } else if (action === "previous" && earlier.length) {
    state = { ...state, song: { ...earlier.at(-1), playing: true }, next: song };
    earlier = earlier.slice(0, -1);
  }
}

async function control(action, more = {}) {
  if (busy) {
    return;
  }
  busy = true;
  if (action === "toggle" && state.song) {
    state.song.playing = !state.song.playing;
  }
  expect(action);
  draw();
  try {
    const response = await fetch("/api/music", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...more }),
    });
    if (response.ok) {
      const song = state.song;
      const fresh = await response.json();
      state = { ...state, song: fresh.song }; // already counted among the earlier ones
      take(song && !fresh.song ? { ...fresh, song } : fresh);
    }
  } catch {
    state.note = "No connection";
  } finally {
    busy = false;
    draw();
    setTimeout(read, AFTER_CONTROL_MS);
  }
}

function chooseMix() {
  const dialog = element("dialog");
  const close = () => { dialog.close(); dialog.remove(); };
  const pick = (number) => () => { close(); control("mix", { mix: number }); };
  const option = (label, number, on) =>
    element("button", { class: on ? "choice primary" : "choice", onclick: pick(number) }, label);
  const list = element("div", { class: "music-choices" },
    option("Off", null, !state.mix),
    ...state.mixes.slice(0, MIXES_SHOWN).map((mix) =>
      option(mix.name, mix.id, state.mix?.id === mix.id)));
  dialog.append(element("b", {}, "Music mix"), list,
    element("div", { class: "actions" },
      element("a", { class: "button", href: "/music" }, "Mixes"),
      element("a", { class: "button", href: "/spotify/devices" }, "Device"),
      element("button", { onclick: close }, "Close")));
  dialog.addEventListener("cancel", close);
  document.body.append(dialog);
  dialog.showModal();
}

function remember() {
  try {
    sessionStorage.setItem(KEPT, JSON.stringify(state));
  } catch {
    // Private windows may refuse; the next page waits for the server instead.
  }
}

function recall() {
  try {
    state = JSON.parse(sessionStorage.getItem(KEPT));
  } catch {
    state = null;
  }
  if (state) {
    draw();
  }
}

recall();
read();
setInterval(read, EVERY_MS);
document.addEventListener("visibilitychange", read);
window.addEventListener("resize", () => {
  document.querySelectorAll("[data-music]").forEach(glide);
});
document.fonts.ready.then(() => document.querySelectorAll("[data-music]").forEach(glide));
document.addEventListener("session-drawn", () => {
  document.querySelectorAll("[data-music]").forEach(fill);
});
