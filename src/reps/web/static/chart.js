// Touching, hovering or focusing a mark shows its words over the chart; leaving shows the last's.

for (const chart of document.querySelectorAll("[data-chart]")) {
  const words = chart.querySelector("[data-words]");
  const latest = words.textContent;
  const show = (mark) => {
    for (const each of chart.querySelectorAll(".mark")) {
      each.classList.toggle("shown", each === mark);
    }
    words.textContent = mark ? mark.dataset.mark : latest;
  };
  const under = (event) => document.elementFromPoint(event.clientX, event.clientY)?.closest(".mark");
  chart.addEventListener("pointerdown", (event) => show(under(event)));
  chart.addEventListener("pointermove", (event) => show(under(event)));
  // A touch leaves as it lifts, and what was touched is still being read.
  chart.addEventListener("pointerleave", (event) => {
    if (event.pointerType === "mouse") {
      show(null);
    }
  });
  chart.addEventListener("focusin", (event) => show(event.target.closest(".mark")));
}
