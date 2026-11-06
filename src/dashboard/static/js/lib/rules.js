// A "signal, comparison, value" condition row, shared by chart shading, backtests and studies.
import { api } from "./api.js";
import { fmt, h } from "./dom.js";
import { fillSelect, select } from "./ui.js";

const OPERATORS = [">", "<", ">=", "<="].map((value) => ({ value, label: value }));

let currentValues = null;

/** Latest value and 1-year range of each signal, used to suggest sensible thresholds. */
export async function loadCurrentValues() {
  if (!currentValues) currentValues = await api.get("/api/signals/current");
  return currentValues;
}

export function signalOptions(catalog, selfSignals = []) {
  const groups = catalog.groups.map((group) => ({
    group: group.group,
    options: group.signals.map((item) => ({ value: item.id, label: item.label })),
  }));
  if (selfSignals.length) {
    groups.unshift({ group: "This symbol's own technicals", options: selfSignals.map((item) => ({ value: item.id, label: item.label })) });
  }
  return groups;
}

/** Text such as "Now 26.9, past year 13.5 to 35.3" for a chosen signal. */
export function hintText(signalId) {
  const row = currentValues?.[signalId];
  if (!row) return "";
  return `Now ${fmt(row.value, row.fmt)}, past year ${fmt(row.low_1y, row.fmt)} to ${fmt(row.high_1y, row.fmt)}`;
}

export function suggestedValue(signalId) {
  const row = currentValues?.[signalId];
  return row ? Number(row.value.toPrecision(3)) : 0;
}

/**
 * One editable condition. `condition` is {signal, op, value} and is changed in place.
 * Returns the row element, which also has a .fill() to refresh options.
 */
export function conditionRow({ condition, options, onChange, onRemove }) {
  const hint = h("div", { class: "rule-hint" }, hintText(condition.signal));
  const signalSelect = select(options, condition.signal, (value) => {
    condition.signal = value;
    condition.value = suggestedValue(value);
    valueInput.value = condition.value;
    hint.textContent = hintText(value);
    onChange?.();
  }, { "aria-label": "Signal" });
  const opSelect = select(OPERATORS, condition.op, (value) => {
    condition.op = value;
    onChange?.();
  }, { "aria-label": "Comparison" });
  const valueInput = h("input", {
    class: "input",
    type: "number",
    step: "any",
    value: condition.value,
    "aria-label": "Value",
    onchange: (event) => {
      condition.value = Number(event.target.value);
      onChange?.();
    },
  });
  const removeButton = onRemove
    ? h("button", { class: "btn btn-ghost btn-xs", type: "button", "aria-label": "Remove condition", title: "Remove condition", onclick: onRemove }, "×")
    : h("span", {});
  const row = h("div", { class: "rule-row" }, signalSelect, opSelect, valueInput, removeButton, hint);
  row.fill = (nextOptions) => fillSelect(signalSelect, nextOptions, condition.signal);
  return row;
}
