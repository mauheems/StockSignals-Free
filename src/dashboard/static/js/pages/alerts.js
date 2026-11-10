// Your own alerts: watch a signal or a stock, and see notifications when a level is hit.
import { DEFAULT_MEASURE, alertForm, alertsChanged, savedDraft, signalRule, stockRule } from "../lib/alert-form.js";
import { api } from "../lib/api.js";
import { MISSING, fmt, fmtDate, h, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import * as notify from "../lib/notify.js";
import { dataTable, emptyBox, pageHead, statusBadge, toast } from "../lib/ui.js";

export { alertsChanged };

export function timeText(seconds) {
  if (!seconds) return MISSING;
  const date = new Date(seconds * 1000);
  const today = new Date();
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return date.toDateString() === today.toDateString() ? `Today ${time}` : `${fmtDate(date.toISOString().slice(0, 10))} ${time}`;
}

const EXAMPLES = [
  { label: "VIX above 30", rule: signalRule("vix", "above", 30) },
  { label: "SPY crosses below its 200-day average", rule: stockRule("SPY", "close_vs_sma200", "crosses_below", 0) },
  { label: "Under 20% of stocks above their 50-day", rule: signalRule("pct_above_50", "below", 20) },
  { label: "NVDA RSI below 30", rule: stockRule("NVDA", "rsi14", "below", 30) },
];

function stateBadge(alert) {
  const status = alert.status;
  if (!alert.enabled) return statusBadge("neutral", "Paused");
  if (!status.available) return statusBadge("neutral", "No data yet");
  const up = alert.rule.op === "above" || alert.rule.op === "crosses_above";
  if (alert.rule.op.startsWith("crosses")) {
    return statusBadge("neutral", status.active ? (up ? "Above level now" : "Below level now") : (up ? "Below level now" : "Above level now"));
  }
  return status.active ? statusBadge("warning", "Condition met") : statusBadge("neutral", "Waiting");
}

export default {
  title: () => "Alerts",
  async mount(root, { params }) {
    const signals = await api.get("/api/signals");
    let data = await api.get("/api/alerts");
    const draft = savedDraft();
    if (params[0] === "new" && params[1]) {
      Object.assign(draft, { kind: "stock", symbol: params[1].toUpperCase(), metric: DEFAULT_MEASURE });
    }

    const feedBox = h("section", { class: "card" });
    const listBox = h("section", { class: "card" });
    const form = alertForm({ catalog: signals, meta: data, draft, examples: EXAMPLES });

    // ------------------------------------------------------------- notifications
    const drawFeed = () => {
      const rows = data.events.map((event) => {
        const alert = data.alerts.find((item) => item.id === event.alert_id);
        return h("div", { class: `feed-row ${event.read ? "" : "is-unread"}`, style: { gridTemplateColumns: "110px minmax(0, 1fr)" } },
          h("span", { class: "feed-date" }, timeText(event.at)),
          h("span", { class: "feed-text" },
            event.read ? null : h("span", { class: "unread-dot", "aria-label": "New" }),
            alert ? h("a", { href: alert.link, style: { color: "inherit" } }, event.message) : h("span", {}, event.message),
            alert?.rule.note ? h("span", { class: "sub-text" }, `· ${alert.rule.note}`) : null));
      });
      replace(feedBox,
        h("div", { class: "card-head" },
          h("h3", { class: "card-title" }, "Notifications", info("alerts"), data.unread ? h("span", { class: "sub-text", style: { fontWeight: 400 } }, ` · ${data.unread} new`) : null),
          h("div", { class: "card-actions" },
            h("a", { class: "btn btn-ghost btn-xs", href: "#/settings", title: "Pop-up notifications on this computer when an alert fires" },
              notify.enabled() ? "Desktop notifications: on" : "Get desktop notifications"),
            h("button", {
              class: "btn btn-xs",
              type: "button",
              id: "mark-read",
              disabled: !data.unread,
              onclick: async () => {
                await api.post("/api/alerts/read");
                alertsChanged();
              },
            }, "Mark all as read"),
            h("button", {
              class: "btn btn-ghost btn-xs",
              type: "button",
              id: "clear-notifications",
              dataset: { danger: "true" },
              disabled: !data.events.length,
              onclick: async () => {
                if (!window.confirm("Delete all notifications? Your alerts stay.")) return;
                await api.del("/api/alerts/events");
                alertsChanged();
              },
            }, "Clear all"))),
        rows.length
          ? h("div", { class: "feed" }, rows)
          : emptyBox(data.alerts.length ? "Nothing has fired yet. Notifications appear here when one of your alerts is hit." : "Create an alert above, and notifications will appear here."));
    };

    // ------------------------------------------------------------------ alert list
    const drawList = () => {
      const columns = [
        {
          key: "subject", label: "Watching", left: true, wrap: true,
          render: (alert) => h("span", { style: { display: "inline-flex", alignItems: "center", gap: "4px", flexWrap: "wrap" } },
            h("a", { href: alert.link, style: { color: "inherit", fontWeight: 560 } }, alert.subject), info(alert.metric),
            alert.rule.note ? h("span", { class: "sub-text" }, `· ${alert.rule.note}`) : null),
        },
        { key: "condition", label: "When it", metric: "alert_conditions", left: true, render: (alert) => `${alert.condition} ${fmt(alert.rule.value, alert.fmt)}` },
        { key: "now", label: "Now", value: (alert) => alert.status.value, render: (alert) => (alert.status.available ? fmt(alert.status.value, alert.fmt) : MISSING) },
        { key: "state", label: "State", left: true, sortable: false, render: stateBadge },
        { key: "last_triggered", label: "Last fired", render: (alert) => timeText(alert.last_triggered) },
        {
          key: "enabled", label: "On", sortable: false,
          render: (alert) => h("input", {
            type: "checkbox",
            checked: alert.enabled,
            "aria-label": `Turn the alert for ${alert.subject} on or off`,
            style: { accentColor: "var(--accent)" },
            onchange: async (event) => {
              try {
                await fetch(`/api/alerts/${alert.id}`, {
                  method: "PATCH",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ enabled: event.target.checked }),
                }).then((response) => { if (!response.ok) throw new Error("Could not change the alert."); });
                alertsChanged();
              } catch (error) {
                toast(error.message, "error");
              }
            },
          }),
        },
        {
          key: "delete", label: "", sortable: false,
          render: (alert) => h("button", {
            class: "btn btn-ghost btn-xs",
            type: "button",
            dataset: { danger: "true", alert: String(alert.id) },
            "aria-label": `Delete the alert for ${alert.subject}`,
            onclick: async () => {
              try {
                await api.del(`/api/alerts/${alert.id}`);
                toast("Alert deleted.");
                alertsChanged();
              } catch (error) {
                toast(error.message, "error");
              }
            },
          }, "Delete"),
        },
      ];
      replace(listBox,
        h("h3", { class: "card-title", style: { marginBottom: "6px" } }, `Your alerts${data.alerts.length ? ` (${data.alerts.length})` : ""}`),
        dataTable({
          id: "alerts",
          columns,
          rows: data.alerts,
          rowKey: (alert) => alert.id,
          itemName: "alert",
          reorder: (ids) => {
            data.alerts = ids.map((alertId) => data.alerts.find((alert) => alert.id === alertId)).filter(Boolean);
            api.post("/api/alerts/order", { ids }).catch((error) => {
              toast(error.message, "error");
              load();
            });
          },
          empty: "No alerts yet. Create one above, or start from an example.",
        }));
    };

    async function load() {
      data = await api.get("/api/alerts");
      drawFeed();
      drawList();
    }

    replace(root,
      pageHead("Alerts", "Get a notification when a market signal or a stock reaches a level you choose. Alerts are checked every time new data arrives, while the dashboard is running. Notifications show up here, in the top bar and on the Overview.", info("alerts")),
      h("section", { class: "card", style: { marginBottom: "10px" } }, h("h3", { class: "card-title", style: { marginBottom: "8px" } }, "New alert"), form.el),
      h("div", { class: "split-1-2", style: { alignItems: "start" } }, feedBox, listBox));

    drawFeed();
    drawList();
    // Alerts created from the drawer, or notifications read there, show up here straight away.
    const onChanged = () => load().catch(() => null);
    document.addEventListener("alerts-changed", onChanged);
    return { refresh: load, unmount: () => document.removeEventListener("alerts-changed", onChanged) };
  },
};
