// Desktop notifications for alerts, through the browser. No account or email involved:
// they appear while a dashboard tab is open somewhere, even in the background.
import { api } from "./api.js";
import { prefs } from "./dom.js";

const ENABLED = "notify.enabled";
const LAST_SEEN = "notify.lastEvent";
const MAX_AT_ONCE = 3;
// Some browsers answer a permission request before updating Notification.permission.
let grantedNow = false;

export function supported() {
  return "Notification" in window;
}

export function permission() {
  if (!supported()) return "unsupported";
  return grantedNow ? "granted" : Notification.permission;
}

export function enabled() {
  return permission() === "granted" && prefs.get(ENABLED, false);
}

function show(title, options) {
  try {
    return new Notification(title, options);
  } catch {
    return null; // a browser that refuses at the last moment shouldn't break the page
  }
}

async function latestEventId() {
  const data = await api.get("/api/alerts");
  return data.events.reduce((highest, event) => Math.max(highest, event.id), 0);
}

/** Ask the browser for permission and switch notifications on. Returns the outcome in words. */
export async function enable() {
  if (!supported()) return "This browser can't show notifications.";
  const answer = permission() === "granted" ? "granted" : await Notification.requestPermission();
  grantedNow = answer === "granted";
  if (answer !== "granted") {
    prefs.set(ENABLED, false);
    return answer === "denied"
      ? "The browser blocked notifications for this site. Allow them in the browser's site settings, then try again."
      : "Notifications were not allowed.";
  }
  // Only alerts that fire from now on; older ones are already in the app.
  prefs.set(LAST_SEEN, await latestEventId());
  prefs.set(ENABLED, true);
  return "Desktop notifications are on.";
}

export function disable() {
  prefs.set(ENABLED, false);
}

export function test() {
  if (!enabled()) return false;
  return show("Market Signals", { body: "Notifications work. Alerts will show up like this.", tag: "market-signals-test" }) !== null;
}

let checking = false;

/** Called whenever the shell learns the unread count; shows any alert that fired since last time. */
export async function check(unread) {
  if (!enabled() || !unread || checking) return;
  checking = true;
  try {
    const seen = prefs.get(LAST_SEEN, 0);
    const data = await api.get("/api/alerts");
    const fresh = data.events.filter((event) => event.id > seen).sort((a, b) => a.id - b.id);
    if (!fresh.length) return;
    // Other open tabs read the same mark, so each alert pops up once.
    prefs.set(LAST_SEEN, fresh[fresh.length - 1].id);
    fresh.slice(-MAX_AT_ONCE).forEach((event) => {
      const note = show("Market Signals alert", { body: event.message, tag: `alert-${event.id}` });
      if (note) {
        note.onclick = () => {
          window.focus();
          location.hash = "#/alerts";
        };
      }
    });
    if (fresh.length > MAX_AT_ONCE) {
      show("Market Signals", { body: `And ${fresh.length - MAX_AT_ONCE} more alerts. Open the Alerts page for all of them.`, tag: "market-signals-more" });
    }
  } catch {
    /* the next status poll tries again */
  } finally {
    checking = false;
  }
}
