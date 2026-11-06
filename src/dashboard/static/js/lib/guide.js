// Every metric on screen gets an info button that explains it, drawn from one catalogue.
import { h } from "./dom.js";

let metrics = {};
let categories = [];
let openPopover = null;

export function setCatalog(payload) {
  metrics = payload.metrics;
  categories = payload.categories;
}

export function allMetrics() {
  return metrics;
}

export function allCategories() {
  return categories;
}

function closePopover() {
  if (openPopover) {
    openPopover.el.remove();
    openPopover.button.setAttribute("aria-expanded", "false");
    openPopover = null;
  }
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && openPopover) {
    const { button } = openPopover;
    closePopover();
    button.focus();
  }
});
document.addEventListener("click", (event) => {
  if (openPopover && !openPopover.el.contains(event.target) && !openPopover.button.contains(event.target)) closePopover();
});
window.addEventListener("hashchange", closePopover);

export function helpBlocks(entry) {
  const blocks = [
    ["What it is", entry.what],
    ["How to read it", entry.read],
    entry.score ? ["How it counts toward the score", entry.score] : null,
    ["How it's calculated", entry.calc],
    entry.caveat ? ["Keep in mind", entry.caveat] : null,
    ["Source", entry.source],
  ];
  return blocks.filter(Boolean).map(([label, text]) => h("div", { class: "help-block" }, h("div", { class: "help-label" }, label), h("div", {}, text)));
}

function place(popover, button) {
  const rect = button.getBoundingClientRect();
  const width = popover.offsetWidth;
  const height = popover.offsetHeight;
  let left = rect.left;
  if (left + width > window.innerWidth - 12) left = window.innerWidth - width - 12;
  let top = rect.bottom + 6;
  if (top + height > window.innerHeight - 12) top = Math.max(12, rect.top - height - 6);
  popover.style.left = `${Math.max(12, left)}px`;
  popover.style.top = `${top}px`;
}

/** A small "i" button that opens the metric's explanation. */
export function info(id) {
  const entry = metrics[id];
  if (!entry) {
    console.error(`No Metric Guide entry for "${id}"`);
    return null;
  }
  const button = h("button", {
    class: "info-btn",
    type: "button",
    title: `What is ${entry.name}?`,
    "aria-label": `Explain ${entry.name}`,
    "aria-expanded": "false",
    dataset: { metric: id },
    onclick: (event) => {
      event.preventDefault();
      event.stopPropagation();
      const wasOpenHere = openPopover && openPopover.button === button;
      closePopover();
      if (wasOpenHere) return;
      const popover = h(
        "div",
        { class: "popover", role: "dialog", "aria-label": entry.name },
        h("button", { class: "btn btn-ghost btn-xs popover-close", type: "button", "aria-label": "Close", onclick: closePopover }, "×"),
        h("h4", {}, entry.name),
        helpBlocks(entry),
        h("a", { class: "popover-link", href: `#/guide/${id}` }, "Open in Metric Guide →"),
      );
      popover.addEventListener("click", (e) => e.stopPropagation());
      document.body.append(popover);
      place(popover, button);
      button.setAttribute("aria-expanded", "true");
      openPopover = { el: popover, button };
    },
  }, "i");
  return button;
}
