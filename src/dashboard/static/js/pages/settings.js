// Settings: notifications, appearance, backups and where your data lives.
import { api } from "../lib/api.js";
import { compact, h, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import * as notify from "../lib/notify.js";
import { field, pageHead, segmented, toast } from "../lib/ui.js";

const PREFIX = "ms.";

/** Everything this browser remembers for the dashboard (layouts, chosen tabs, sorting). */
function browserPrefs() {
  const prefs = {};
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (key.startsWith(PREFIX) && !key.startsWith(`${PREFIX}notify.`)) prefs[key] = localStorage.getItem(key);
  }
  return prefs;
}

function section(title, metric, ...body) {
  return h("section", { class: "card settings-card" }, h("h3", { class: "card-title", style: { marginBottom: "8px" } }, title, metric ? info(metric) : null), body);
}

export default {
  title: () => "Settings",
  async mount(root) {
    // -------------------------------------------------------------- notifications
    const notifyState = h("p", { class: "sub-text", id: "notify-state" });
    const notifyBox = h("div", {});
    const drawNotify = () => {
      const on = notify.enabled();
      const permission = notify.permission();
      replace(notifyState, permission === "unsupported"
        ? "This browser can't show desktop notifications."
        : permission === "denied"
          ? "The browser is blocking notifications for this site. Allow them in the browser's site settings first."
          : on ? "On: a notification pops up when one of your alerts fires, while a dashboard tab is open (it may be in the background)." : "Off: alerts show only inside the app.");
      replace(notifyBox,
        h("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
          h("button", {
            class: `btn ${on ? "" : "btn-primary"}`,
            type: "button",
            id: "notify-toggle",
            disabled: permission === "unsupported",
            onclick: async () => {
              if (notify.enabled()) {
                notify.disable();
                toast("Desktop notifications are off.");
              } else {
                toast(await notify.enable());
              }
              drawNotify();
            },
          }, on ? "Turn off" : "Turn on desktop notifications"),
          h("button", {
            class: "btn",
            type: "button",
            id: "notify-test",
            disabled: !on,
            onclick: () => toast(notify.test() ? "Sent a test notification." : "The browser didn't show the notification. Check its notification settings."),
          }, "Send a test")));
    };
    drawNotify();

    // ------------------------------------------------------------------- appearance
    const themeBox = h("div", {});
    const drawTheme = () => replace(themeBox, segmented(
      [{ value: "light", label: "Light" }, { value: "dark", label: "Dark" }],
      document.documentElement.dataset.theme === "dark" ? "dark" : "light",
      (value) => document.dispatchEvent(new CustomEvent("set-theme", { detail: value })),
      "Theme",
    ));
    drawTheme();

    // -------------------------------------------------------------- backup & restore
    const download = h("button", {
      class: "btn btn-primary",
      type: "button",
      id: "backup-download",
      onclick: async () => {
        try {
          const data = await api.get("/api/backup");
          data.browser = browserPrefs();
          const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
          const link = h("a", { href: URL.createObjectURL(blob), download: `market-signals-backup-${new Date().toLocaleDateString("en-CA")}.json` });
          document.body.append(link);
          link.click();
          link.remove();
          setTimeout(() => URL.revokeObjectURL(link.href), 1000);
          toast("Backup downloaded.");
        } catch (error) {
          toast(error.message, "error");
        }
      },
    }, "Download a backup");
    let mode = "merge";
    const fileInput = h("input", { type: "file", accept: "application/json,.json", id: "backup-file", "aria-label": "Backup file" });
    const restore = h("button", {
      class: "btn",
      type: "button",
      id: "backup-restore",
      dataset: { danger: "true" },
      onclick: async () => {
        const file = fileInput.files?.[0];
        if (!file) {
          toast("Choose a backup file first.", "error");
          return;
        }
        if (mode === "replace" && !window.confirm("Replace your watchlist, alerts, positions, journal, lists and notes with this backup?")) return;
        try {
          const data = JSON.parse(await file.text());
          const result = await api.post("/api/backup/restore", { data, mode });
          if (data.browser && typeof data.browser === "object") {
            Object.entries(data.browser).forEach(([key, value]) => { if (key.startsWith(PREFIX)) localStorage.setItem(key, value); });
          }
          const counts = result.restored;
          toast(`Restored: ${counts.watchlist} watchlist stocks, ${counts.alerts} alerts, ${counts.positions} positions, ${counts.journal} journal entries.`);
        } catch (error) {
          toast(error instanceof SyntaxError ? "That file isn't valid JSON." : error.message, "error");
        }
      },
    }, "Restore");
    const modeBox = segmented([{ value: "merge", label: "Add to what I have" }, { value: "replace", label: "Replace what I have" }], mode, (value) => { mode = value; }, "Restore mode");

    // ------------------------------------------------------------------------ storage
    const storageBox = h("p", { class: "sub-text" }, "Checking…");
    api.get("/api/settings/storage").then((result) => {
      replace(storageBox, `Your data is in ${result.path} (${compact(result.bytes)}B). It holds downloaded market data plus everything you set up yourself. Deleting it is safe: market data downloads again, but your own lists, positions and journal are lost unless you have a backup.`);
    }).catch(() => replace(storageBox, "Couldn't read where the data is stored."));

    replace(root,
      pageHead("Settings", "Notifications, appearance and backups. Settings like chart layouts are remembered by this browser; your lists, alerts, positions and journal are kept in the app's own data file."),
      h("div", { class: "grid grid-2", style: { alignItems: "start" } },
        section("Desktop notifications", "desktop_notifications", notifyState, notifyBox),
        section("Appearance", null, field("Theme", themeBox)),
        section("Back up and restore", "backup",
          h("p", { class: "sub-text" }, "One file with your watchlist (and its order), lists, notes, alerts, positions, journal, Key signals and this browser's layout choices. Market data isn't included: it downloads again."),
          h("div", { style: { margin: "8px 0 14px" } }, download),
          field("Restore from a file", fileInput),
          h("div", { class: "filters", style: { margin: "8px 0 0" } }, field("How", modeBox), h("div", { style: { alignSelf: "flex-end" } }, restore))),
        section("Data on this computer", null, storageBox,
          h("p", { class: "sub-text" }, "The app only answers on this computer and sends nothing about you anywhere. See ", h("a", { href: "#/sources" }, "Data Sources"), " for where market data comes from."))));
    return {};
  },
};
