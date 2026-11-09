// A panel that slides in from the right on any page, to set an alert while looking at the data.
//
// It doesn't cover the page with a backdrop: the numbers that gave you the idea stay
// readable and scrollable next to the form.
import { DEFAULT_MEASURE, alertForm, alertsChanged, savedDraft } from "./alert-form.js";
import { api } from "./api.js";
import { MISSING, fmtDate, h, replace } from "./dom.js";
import { info } from "./guide.js";
import { emptyBox, errorBox, loadingBox } from "./ui.js";

const PANEL_ID = "alert-drawer";
let panel = null;
let body = null;
let trigger = null;
let form = null;
let catalog = null;
let opener = null;
let contextFor = () => null;
let loadId = 0;

function timeText(seconds) {
  if (!seconds) return MISSING;
  const date = new Date(seconds * 1000);
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return date.toDateString() === new Date().toDateString() ? `Today ${time}` : `${fmtDate(date.toISOString().slice(0, 10))} ${time}`;
}

/** What to pre-fill: the stock or the first signal of the page the user is looking at. */
export function routeContext(page, params, signalCatalog) {
  if ((page === "stock" || page === "chart" || page === "options") && params[0]) {
    return { kind: "stock", symbol: params[0].toUpperCase(), label: params[0].toUpperCase() };
  }
  if (!signalCatalog) return null;
  const first = signalCatalog.groups.flatMap((group) => group.signals).find((item) => (item.page || "").split("/")[0] === page);
  return first ? { kind: "signal", signal: first.id, label: first.label } : null;
}

function startingDraft(context) {
  const draft = savedDraft();
  if (!context) return draft;
  const { label, ...values } = context;
  // A stock taken from the page starts as a price alert, the most common kind.
  return { ...draft, ...(values.kind === "stock" ? { metric: DEFAULT_MEASURE } : {}), ...values };
}

function contextLine(context) {
  if (!context) return "Pick a stock or a market signal, and the level to watch for.";
  return context.kind === "stock"
    ? `Started from ${context.label || context.symbol}, the stock on this page. Change anything you like.`
    : `Started from ${context.label}, a signal on this page. Pick any other signal below.`;
}

function feed(data) {
  const rows = data.events.slice(0, 6).map((event) => {
    const alert = data.alerts.find((item) => item.id === event.alert_id);
    return h("div", { class: `feed-row ${event.read ? "" : "is-unread"}`, style: { gridTemplateColumns: "84px minmax(0, 1fr)" } },
      h("span", { class: "feed-date" }, timeText(event.at)),
      h("span", { class: "feed-text" },
        event.read ? null : h("span", { class: "unread-dot", "aria-label": "New" }),
        alert ? h("a", { href: alert.link, style: { color: "inherit" } }, event.message) : h("span", {}, event.message)));
  });
  const markRead = data.unread
    ? h("button", {
      class: "btn btn-xs",
      type: "button",
      id: "drawer-mark-read",
      onclick: async () => {
        await api.post("/api/alerts/read");
        alertsChanged();
      },
    }, "Mark as read")
    : null;
  return h("section", { class: "drawer-section" },
    h("div", { class: "card-head" },
      h("h3", { class: "card-title" }, "Latest notifications", data.unread ? h("span", { class: "sub-text", style: { fontWeight: 400 } }, ` · ${data.unread} new`) : null),
      markRead),
    rows.length ? h("div", { class: "feed" }, rows) : emptyBox(data.alerts.length ? "Nothing has fired yet." : "No alerts yet. Create your first one above."));
}

async function fill(context) {
  const id = ++loadId;
  replace(body, loadingBox());
  try {
    const [signals, data] = await Promise.all([catalog ? Promise.resolve(catalog) : api.get("/api/signals"), api.get("/api/alerts")]);
    catalog = signals;
    if (id !== loadId) return;
    const resolved = context ?? contextFor(catalog);
    const page = (location.hash.replace(/^#\/?/, "").split("/")[0]) || "overview";
    form = alertForm({
      catalog,
      meta: data,
      draft: startingDraft(resolved),
      ids: "drawer-alert",
      createId: "drawer-create-alert",
      stacked: true,
      pagePrefix: page,
      persist: false,
    });
    const feedBox = h("div", {}, feed(data));
    replace(body,
      h("p", { class: "sub-text drawer-context" }, contextLine(resolved)),
      form.el,
      feedBox,
      h("p", { class: "drawer-foot" },
        h("a", { href: "#/alerts", onclick: () => close() }, "Manage all your alerts"),
        h("span", { class: "sub-text" }, " · Alerts are checked whenever new data arrives, while the dashboard runs.")));
    form.focus();
  } catch (error) {
    if (id === loadId) replace(body, errorBox(error.message, () => fill(context)));
  }
}

async function refreshFeed() {
  if (!isOpen()) return;
  try {
    const data = await api.get("/api/alerts");
    const box = body.querySelector(".drawer-section")?.parentElement;
    if (box) replace(box, feed(data));
  } catch {
    /* the next open reloads everything */
  }
}

export function isOpen() {
  return !!panel && !panel.hidden;
}

/** Open the drawer. Without a context it is filled in from the page on screen. */
export function openAlertDrawer(context = null) {
  if (!panel) return;
  if (!isOpen()) opener = document.activeElement;
  panel.hidden = false;
  requestAnimationFrame(() => panel.classList.add("is-open"));
  trigger?.setAttribute("aria-expanded", "true");
  document.body.classList.add("drawer-open");
  fill(context);
}

export function close() {
  if (!isOpen()) return;
  panel.classList.remove("is-open");
  trigger?.setAttribute("aria-expanded", "false");
  document.body.classList.remove("drawer-open");
  // Matches the slide-out transition in app.css.
  setTimeout(() => { if (!panel.classList.contains("is-open")) panel.hidden = true; }, 180);
  if (opener && document.contains(opener)) opener.focus();
  opener = null;
}

/** The page changed: follow it, unless the user already started filling in the form. */
export function pageChanged() {
  if (isOpen() && form && !form.touched()) fill(null);
}

/**
 * Create the drawer once, for the whole app.
 *   button       the top-bar button that opens and closes it
 *   getContext   returns what to pre-fill for the current page (given the signal catalog)
 */
export function initAlertDrawer({ button, getContext }) {
  trigger = button;
  contextFor = getContext;
  body = h("div", { class: "drawer-body" });
  panel = h("aside", { class: "drawer", id: PANEL_ID, role: "dialog", "aria-modal": "false", "aria-labelledby": "alert-drawer-title" },
    h("div", { class: "drawer-head" },
      h("h2", { class: "drawer-title", id: "alert-drawer-title" }, "New alert", info("alerts")),
      h("button", { class: "btn btn-ghost btn-xs", type: "button", id: "close-alert-drawer", "aria-label": "Close the alert panel", title: "Close (Esc)", onclick: close }, "×")),
    body);
  panel.hidden = true;
  document.body.append(panel);
  button.setAttribute("aria-controls", PANEL_ID);
  button.setAttribute("aria-expanded", "false");
  button.addEventListener("click", () => (isOpen() ? close() : openAlertDrawer()));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen() && !event.defaultPrevented) close();
  });
  document.addEventListener("alerts-changed", refreshFeed);
}
