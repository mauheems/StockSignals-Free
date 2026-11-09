// The "new alert" form, shared by the Alerts page and the alert drawer.
import { api } from "./api.js";
import { debounce, fmt, fmtDate, h, prefs, replace } from "./dom.js";
import { signalOptions } from "./rules.js";
import { field, fillSelect, filters, segmented, select, toast } from "./ui.js";

/** Tell the shell (top-bar count, Overview, Alerts page) that alerts or notifications changed. */
export function alertsChanged() {
  document.dispatchEvent(new CustomEvent("alerts-changed"));
}

// Stock measure ids, as used by the API (not Metric Guide ids).
export const DEFAULT_MEASURE = "close";
export const stockRule = (symbol, measure, op, value) => ({ kind: "stock", symbol, metric: measure, op, value });
export const signalRule = (signal, op, value) => ({ kind: "signal", signal, op, value });

const DRAFT_DEFAULTS = { kind: "stock", symbol: "SPY", metric: DEFAULT_MEASURE, signal: "vix", op: "crosses_above", value: "", note: "" };

export function savedDraft() {
  return { ...DRAFT_DEFAULTS, ...prefs.get("alertDraft", {}), value: "", note: "" };
}

/** Signal choices, with the signals shown on the current page listed first. */
function groupedSignals(catalog, pagePrefix) {
  const groups = signalOptions(catalog);
  if (!pagePrefix) return groups;
  const here = catalog.groups.flatMap((group) => group.signals).filter((item) => (item.page || "").split("/")[0] === pagePrefix);
  return here.length ? [{ group: "On this page", options: here.map((item) => ({ value: item.id, label: item.label })) }, ...groups] : groups;
}

/**
 * Build the form.
 *   catalog   /api/signals
 *   meta      /api/alerts (for operators and stock measures)
 *   draft     starting values
 *   ids       "alert" on the Alerts page, "drawer-alert" in the drawer, so both can be on screen
 *   stacked   one field per line, for the narrow drawer
 *   examples  example chips that fill in the form
 *   pagePrefix  route of the page the user is on, to list its signals first
 */
export function alertForm({ catalog, meta, draft: start, ids = "alert", createId = "create-alert", stacked = false, examples = [], pagePrefix = "", persist = true, onCreated }) {
  const draft = { ...DRAFT_DEFAULTS, ...start };
  let touched = false;
  const el = h("div", { class: stacked ? "alert-form is-stacked" : "alert-form" });
  const hint = h("div", { class: "rule-hint", style: { margin: "6px 0 0" }, "aria-live": "polite" });
  const createButton = h("button", { class: "btn btn-primary", type: "button", id: createId }, "Create alert");
  const width = (normal) => (stacked ? { width: "100%" } : { width: normal });

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
    if (persist) prefs.set("alertDraft", { ...draft, value: "", note: "" });
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
      valueInput.placeholder = result.status.available ? String(Number(result.status.value.toPrecision(4))) : "";
    } catch (error) {
      if (id === previewId) hint.textContent = error.message;
    }
  }, 300);

  const changed = () => { touched = true; };
  const valueInput = h("input", {
    class: "input input-num", type: "number", step: "any", value: draft.value, id: `${ids}-value`, "aria-label": "Level",
    style: stacked ? { width: "100%" } : {},
    oninput: (event) => { draft.value = event.target.value; changed(); },
  });
  const symbolInput = h("input", {
    class: "input", style: width("100px"), value: draft.symbol, id: `${ids}-symbol`, spellcheck: false, "aria-label": "Ticker",
    oninput: (event) => { draft.symbol = event.target.value.toUpperCase(); changed(); preview(); },
  });
  const measureSelect = select(meta.stock_measures.map((item) => ({ value: item.value, label: item.label })), draft.metric, (value) => {
    draft.metric = value;
    changed();
    preview();
  }, { "aria-label": "Measure", id: `${ids}-measure`, style: width("auto") });
  const signalSelect = h("select", {
    class: "input", "aria-label": "Signal", id: `${ids}-signal`, style: width("auto"),
    onchange: (event) => { draft.signal = event.target.value; changed(); preview(); },
  });
  fillSelect(signalSelect, groupedSignals(catalog, pagePrefix), draft.signal);
  const opSelect = select(meta.operators, draft.op, (value) => {
    draft.op = value;
    changed();
    preview();
  }, { "aria-label": "Condition", id: `${ids}-op`, style: width("auto") });
  const noteInput = h("input", {
    class: "input", style: width("220px"), value: draft.note, placeholder: "Optional, for yourself", "aria-label": "Note", maxlength: 200,
    oninput: (event) => { draft.note = event.target.value; changed(); },
  });

  const syncInputs = () => {
    valueInput.value = draft.value;
    symbolInput.value = draft.symbol;
    measureSelect.value = draft.metric;
    signalSelect.value = draft.signal;
    opSelect.value = draft.op;
    noteInput.value = draft.note;
  };

  const draw = () => {
    const kind = field("Watch", segmented([{ value: "stock", label: "A stock" }, { value: "signal", label: "A market signal" }], draft.kind, (value) => {
      draft.kind = value;
      changed();
      draw();
      preview();
    }, "What to watch"));
    const subject = draft.kind === "stock"
      ? [field("Ticker", symbolInput), field("Measure", measureSelect)]
      : [field("Signal", signalSelect)];
    const fields = [kind, subject, field("When it", opSelect, "alert_conditions"), field("Level", valueInput), field("Note", noteInput)];
    const exampleChips = examples.length
      ? h("div", { class: "chips", style: { marginTop: "8px" } },
        h("span", { class: "sub-text" }, "Examples:"),
        examples.map((example) => h("button", {
          class: "chip", type: "button", style: { cursor: "pointer" },
          onclick: () => {
            Object.assign(draft, example.rule, { value: String(example.rule.value), note: "" });
            syncInputs();
            changed();
            draw();
            preview();
          },
        }, example.label)))
      : null;
    replace(el,
      stacked
        ? h("div", { class: "stack-fields" }, fields, h("div", {}, createButton))
        : filters(fields, h("div", { style: { alignSelf: "flex-end" } }, createButton)),
      hint,
      exampleChips);
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
      touched = false;
      alertsChanged();
      await onCreated?.(alert);
    } catch (error) {
      toast(error.message, "error");
    }
    createButton.disabled = false;
  });

  draw();
  preview();
  return {
    el,
    /** Start over from new values (for example when the user moves to another page). */
    apply(values) {
      Object.assign(draft, values);
      syncInputs();
      touched = false;
      draw();
      preview();
    },
    /** Whether the user has changed anything since the form was filled in. */
    touched: () => touched,
    focus() {
      valueInput.focus();
    },
  };
}
