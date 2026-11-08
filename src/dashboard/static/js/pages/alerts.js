// Your own alerts: watch a signal or a stock, and see notifications when a level is hit.
import { api } from "../lib/api.js";
import { MISSING, debounce, fmt, fmtDate, h, prefs, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { signalOptions } from "../lib/rules.js";
import { dataTable, emptyBox, field, fillSelect, filters, pageHead, segmented, select, statusBadge, toast } from "../lib/ui.js";

/** Tell the shell to re-read the unread count shown in the top bar. */
export function alertsChanged() {
  document.dispatchEvent(new CustomEvent("alerts-changed"));
}

export function timeText(seconds) {
  if (!seconds) return MISSING;
  const date = new Date(seconds * 1000);
  const today = new Date();
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return date.toDateString() === today.toDateString() ? `Today ${time}` : `${fmtDate(date.toISOString().slice(0, 10))} ${time}`;
}

// Stock measure ids, as used by the API (not Metric Guide ids).
const DEFAULT_MEASURE = "close";
const stockRule = (symbol, measure, op, value) => ({ kind: "stock", symbol, metric: measure, op, value });
const signalRule = (signal, op, value) => ({ kind: "signal", signal, op, value });

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
    const saved = prefs.get("alertDraft", {});
    const draft = {
      kind: "stock",
      symbol: "SPY",
      metric: DEFAULT_MEASURE,
      signal: "vix",
      op: "crosses_above",
      value: "",
      note: "",
      ...saved,
    };
    if (params[0] === "new" && params[1]) {
      draft.kind = "stock";
      draft.symbol = params[1].toUpperCase();
      draft.value = "";
    }

    const formBox = h("div", {});
    const hint = h("div", { class: "rule-hint", style: { margin: "6px 0 0" } });
    const feedBox = h("section", { class: "card" });
    const listBox = h("section", { class: "card" });
    const createButton = h("button", { class: "btn btn-primary", type: "button", id: "create-alert" }, "Create alert");

    // ------------------------------------------------------------------ the form
    const ruleBody = () => ({
      kind: draft.kind,
      symbol: draft.symbol,
      metric: draft.metric,
      signal: draft.signal,
      op: draft.op,
      value: draft.value === "" ? null : Number(draft.value),
      note: draft.note,
    });

    let previewId = 0;
    const preview = debounce(async () => {
      const id = ++previewId;
      prefs.set("alertDraft", { ...draft, value: "" });
      const body = ruleBody();
      if (body.value === null) body.value = 0;
      if (draft.kind === "stock" && !draft.symbol.trim()) {
        hint.textContent = "Type a ticker to see its current value.";
        return;
      }
      try {
        const result = await api.post("/api/alerts/preview", body);
        if (id !== previewId) return;
        hint.textContent = result.status.available
          ? `Now ${fmt(result.status.value, result.fmt)} (latest reading, ${fmtDate(result.status.date)}).`
          : draft.kind === "stock"
            ? `No stored prices for ${draft.symbol} yet. They are downloaded when you create the alert.`
            : "No data yet for this signal.";
        if (draft.value === "" && result.status.available) {
          // Start from the current value, so the level is in a sensible range.
          valueInput.placeholder = String(Number(result.status.value.toPrecision(4)));
        }
      } catch (error) {
        if (id === previewId) hint.textContent = error.message;
      }
    }, 300);

    const valueInput = h("input", {
      class: "input input-num",
      type: "number",
      step: "any",
      value: draft.value,
      id: "alert-value",
      "aria-label": "Level",
      oninput: (event) => { draft.value = event.target.value; },
    });
    const symbolInput = h("input", {
      class: "input",
      style: { width: "100px" },
      value: draft.symbol,
      id: "alert-symbol",
      spellcheck: false,
      "aria-label": "Ticker",
      oninput: (event) => {
        draft.symbol = event.target.value.toUpperCase();
        preview();
      },
    });
    const measureSelect = select(data.stock_measures.map((item) => ({ value: item.value, label: item.label })), draft.metric, (value) => {
      draft.metric = value;
      preview();
    }, { "aria-label": "Measure", id: "alert-measure" });
    const signalSelect = h("select", { class: "input", "aria-label": "Signal", id: "alert-signal", onchange: (event) => { draft.signal = event.target.value; preview(); } });
    fillSelect(signalSelect, signalOptions(signals), draft.signal);
    const opSelect = select(data.operators, draft.op, (value) => {
      draft.op = value;
      preview();
    }, { "aria-label": "Condition", id: "alert-op" });
    const noteInput = h("input", {
      class: "input",
      style: { width: "220px" },
      value: draft.note,
      placeholder: "Optional, for yourself",
      "aria-label": "Note",
      maxlength: 200,
      oninput: (event) => { draft.note = event.target.value; },
    });

    const drawForm = () => {
      const subject = draft.kind === "stock"
        ? [field("Ticker", symbolInput), field("Measure", measureSelect)]
        : [field("Signal", signalSelect)];
      replace(formBox,
        filters(
          field("Watch", segmented([{ value: "stock", label: "A stock" }, { value: "signal", label: "A market signal" }], draft.kind, (value) => {
            draft.kind = value;
            drawForm();
            preview();
          }, "What to watch")),
          subject,
          field("When it", opSelect, "alert_conditions"),
          field("Level", valueInput),
          field("Note", noteInput),
          h("div", { style: { alignSelf: "flex-end" } }, createButton)),
        hint,
        h("div", { class: "chips", style: { marginTop: "8px" } },
          h("span", { class: "sub-text" }, "Examples:"),
          EXAMPLES.map((example) => h("button", {
            class: "chip",
            type: "button",
            style: { cursor: "pointer" },
            onclick: () => {
              Object.assign(draft, example.rule, { value: String(example.rule.value), note: "" });
              valueInput.value = draft.value;
              symbolInput.value = draft.symbol;
              measureSelect.value = draft.metric;
              signalSelect.value = draft.signal;
              opSelect.value = draft.op;
              noteInput.value = "";
              drawForm();
              preview();
            },
          }, example.label))));
    };

    createButton.addEventListener("click", async () => {
      if (draft.value === "" || Number.isNaN(Number(draft.value))) {
        toast("Enter the level the alert should watch for.", "error");
        valueInput.focus();
        return;
      }
      createButton.disabled = true;
      try {
        const alert = await api.post("/api/alerts", ruleBody());
        toast(`Alert created: ${alert.label}.`);
        draft.value = "";
        draft.note = "";
        valueInput.value = "";
        noteInput.value = "";
        await load();
        alertsChanged();
      } catch (error) {
        toast(error.message, "error");
      }
      createButton.disabled = false;
    });

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
            h("button", {
              class: "btn btn-xs",
              type: "button",
              id: "mark-read",
              disabled: !data.unread,
              onclick: async () => {
                await api.post("/api/alerts/read");
                await load();
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
                await load();
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
                await load();
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
                await load();
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
        dataTable({ columns, rows: data.alerts, empty: "No alerts yet. Create one above, or start from an example." }));
    };

    async function load() {
      data = await api.get("/api/alerts");
      drawFeed();
      drawList();
    }

    replace(root,
      pageHead("Alerts", "Get a notification when a market signal or a stock reaches a level you choose. Alerts are checked every time new data arrives, while the dashboard is running. Notifications show up here, in the top bar and on the Overview.", info("alerts")),
      h("section", { class: "card", style: { marginBottom: "10px" } }, h("h3", { class: "card-title", style: { marginBottom: "8px" } }, "New alert"), formBox),
      h("div", { class: "split-1-2", style: { alignItems: "start" } }, feedBox, listBox));

    drawForm();
    drawFeed();
    drawList();
    preview();
    return { refresh: load };
  },
};
