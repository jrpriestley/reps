// While Claude is thinking: say so, and don't ask twice.

const form = document.getElementById("describe");

form?.addEventListener("submit", () => {
  const button = form.querySelector("button.primary");
  button.disabled = true;
  button.textContent = form.classList.contains("ask") ? "Thinking…" : "Building your workout…";
  form.classList.add("thinking");
});

// The latest of a conversation is what to read.
const talk = document.querySelector(".talk");
if (talk) {
  talk.scrollTop = talk.scrollHeight;
}
