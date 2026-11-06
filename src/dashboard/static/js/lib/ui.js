// Shared interface pieces: tiles, badges, filters, tables and page scaffolding.
import { MISSING, append, fmt, fmtDate, h, replace, svg } from "./dom.js";
import { info } from "./guide.js";

export function pageHead(title, subtitle, ...extras) {
  return h("div", { class: "page-head" }, h("h1", { class: "page-title" }, title, extras), subtitle ? h("p", { class: "page-sub" }, subtitle) : null);
}

export function field(label, control, metric) {
  return h("div", { class: "field" }, h("span", { class: "field-label" }, label, metric ? info(metric) : null), control);
}

export function filters(...children) {
  return h("div", { class: "filters", role: "group", "aria-label": "Filters" }, children);
}

export function segmented(options, value, onChange, label) {
  const root = h("div", { class: "segmented", role: "group", "aria-label": label || "Options" });
  let current = value;
  const buttons = options.map((option) => {
    const item = typeof option === "object" ? option : { value: option, label: option };
    const button = h("button", {
      type: "button",
      class: item.value === current ? "is-active" : "",
      "aria-pressed": String(item.value === current),
      dataset: { value: item.value },
      onclick: () => {
        if (item.value === current) return;
        current = item.value;
        for (const other of buttons) {
          const active = other.dataset.value === String(current);
          other.classList.toggle("is-active", active);
          other.setAttribute("aria-pressed", String(active));
        }
        onChange(item.value);
      },
    }, item.label);
    return button;
  });
  append(root, buttons);
  return root;
}

export function select(options, value, onChange, attrs = {}) {
  const el = h("select", { class: "input", ...attrs, onchange: (event) => onChange(event.target.value) });
  fillSelect(el, options, value);
  return el;
}

/** Options may be flat, or grouped with {group, options}. */
export function fillSelect(el, options, value) {
  el.replaceChildren();
  for (const option of options) {
    if (option.group) {
      const group = h("optgroup", { label: option.group });
      for (const item of option.options) group.append(h("option", { value: item.value }, item.label));
      el.append(group);
    } else {
      el.append(h("option", { value: option.value }, option.label));
    }
  }
  if (value !== undefined && value !== null) el.value = value;
}

export function statusBadge(tone, label) {
  const icons = { good: "▲", warning: "◆", serious: "◆", critical: "▼", neutral: "●" };
  return h("span", { class: `badge tone-${tone}` }, h("span", { class: "badge-icon", "aria-hidden": "true" }, icons[tone] || icons.neutral), label);
}

export function scoreBadge(score, contrarian = false) {
  if (score === null || score === undefined) return h("span", { class: "sub-text" }, "Not scored");
  if (contrarian) {
    if (score > 0) return statusBadge("neutral", "Fear");
    if (score < 0) return statusBadge("neutral", "Greed");
    return statusBadge("neutral", "Balanced");
  }
  if (score > 0) return statusBadge("good", "Supportive");
  if (score < 0) return statusBadge("critical", "Risk");
  return statusBadge("neutral", "Neutral");
}

export function deltaSpan(text, value, higherIs) {
  let tone = "";
  if (value !== null && value !== undefined && value !== 0 && higherIs) {
    const good = higherIs === "good" ? value > 0 : value < 0;
    tone = good ? "delta-good" : "delta-bad";
  }
  const arrow = value > 0 ? "▲ " : value < 0 ? "▼ " : "";
  return h("span", { class: tone }, value === null || value === undefined ? MISSING : `${arrow}${text}`);
}

export function sparkline(values, { width = 120, height = 26 } = {}) {
  const clean = (values || []).filter((value) => value !== null && value !== undefined);
  if (clean.length < 2) return null;
  const min = Math.min(...clean);
  const max = Math.max(...clean);
  const span = max - min || 1;
  const pad = 3;
  const points = clean.map((value, index) => {
    const x = pad + (index / (clean.length - 1)) * (width - pad * 2);
    const y = pad + (1 - (value - min) / span) * (height - pad * 2);
    return [x, y];
  });
  const path = points.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join("");
  const [lastX, lastY] = points[points.length - 1];
  return svg(
    "svg",
    { class: "spark", viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "none", "aria-hidden": "true" },
    svg("path", { d: path, fill: "none", stroke: "var(--deemph)", "stroke-width": "1.5", "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round" }),
    svg("circle", { cx: lastX, cy: lastY, r: "2.5", fill: "var(--accent)" }),
  );
}

/** Stat tile: label, value, change against a named period, optional sparkline and footer. */
export function tile({ label, metric, value, valueText, fmtCode, delta, spark, foot, href }) {
  const body = [
    h("div", { class: "tile-label" }, label, metric ? info(metric) : null),
    h("div", { class: "tile-value", title: valueText ?? fmt(value, fmtCode) }, valueText ?? fmt(value, fmtCode)),
    delta ? h("div", { class: "tile-delta" }, delta) : null,
    spark ? sparkline(spark) : null,
    foot ? h("div", { class: "tile-foot" }, foot) : null,
  ];
  return href ? h("a", { class: "tile", href }, body) : h("div", { class: "tile" }, body);
}

/** Tile for a signal row from the server, or an honest placeholder if it has no data yet. */
export function signalTile(row, { label, metric, fmtCode, spark = true, deltaFmt } = {}) {
  if (!row) return tile({ label: label || "Not available", metric, valueText: MISSING, foot: h("span", {}, "No data yet") });
  const code = fmtCode || row.fmt;
  const change = row.change_1w;
  const diffCode = deltaFmt || code.replace(/^(s?)(pct\d)$/, "s$2").replace(/^num(\d)$/, "snum$1").replace(/^usd.*/, code);
  const text = change === null || change === undefined ? null : `${fmt(change, diffCode)} in 1 week`;
  return tile({
    label: label || row.label,
    metric: metric || row.metric,
    value: row.value,
    fmtCode: code,
    delta: text ? deltaSpan(text, change, row.higher_is) : null,
    spark: spark ? row.spark : null,
    foot: [row.score !== null && row.score !== undefined ? scoreBadge(row.score, row.pillar === "crowd") : h("span", {}), h("span", {}, fmtDate(row.date))],
  });
}

export function heatColor(value, maxAbs) {
  // Diverging scale: blue for gains, red for losses, neutral gray at zero.
  const styles = getComputedStyle(document.documentElement);
  const pole = styles.getPropertyValue(value >= 0 ? "--up" : "--down").trim();
  const mid = styles.getPropertyValue("--mid").trim();
  const share = Math.min(1, Math.abs(value) / (maxAbs || 1)) ** 0.8;
  return `color-mix(in oklab, ${pole} ${Math.round(share * 72)}%, ${mid})`;
}

export function heatCell(value, maxAbs, code = "spct1") {
  if (value === null || value === undefined) return h("span", { class: "sub-text" }, MISSING);
  return h("span", { class: "heat", style: { background: heatColor(value, maxAbs) } }, fmt(value, code));
}

/**
 * Sortable data table.
 * columns: [{key, label, metric, fmt, left, sortable, value(row), render(row), wrap}]
 */
export function dataTable({ columns, rows, sort, rowKey, onRow, selectedKey, maxHeight, empty = "Nothing to show.", note }) {
  const wrap = h("div", { class: "table-wrap", style: maxHeight ? { maxHeight: `${maxHeight}px` } : {} });
  let sortState = sort || null;
  let current = rows;
  let selected = selectedKey;

  const valueOf = (column, row) => (column.value ? column.value(row) : row[column.key]);

  function sorted() {
    if (!sortState) return current;
    const column = columns.find((item) => item.key === sortState.key);
    if (!column) return current;
    const factor = sortState.dir === "asc" ? 1 : -1;
    return [...current].sort((a, b) => {
      const x = valueOf(column, a);
      const y = valueOf(column, b);
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      if (typeof x === "string") return factor * x.localeCompare(y);
      return factor * (x - y);
    });
  }

  function draw() {
    if (!current.length) {
      replace(wrap, h("div", { class: "empty" }, empty));
      return;
    }
    const head = h("tr", {}, columns.map((column) => {
      const canSort = column.sortable !== false;
      const direction = sortState && sortState.key === column.key ? (sortState.dir === "asc" ? " ↑" : " ↓") : "";
      return h("th", {
        class: `${column.left ? "left " : ""}${canSort ? "sortable" : ""} ${column.wrap ? "wrap" : ""} ${column.sticky ? "sticky" : ""}`,
        scope: "col",
        "aria-sort": direction ? (sortState.dir === "asc" ? "ascending" : "descending") : null,
        onclick: canSort ? () => {
          const descFirst = !column.left;
          if (sortState && sortState.key === column.key) sortState = { key: column.key, dir: sortState.dir === "asc" ? "desc" : "asc" };
          else sortState = { key: column.key, dir: descFirst ? "desc" : "asc" };
          draw();
        } : null,
      }, h("span", { class: "th-inner" }, `${column.label}${direction}`, column.metric ? info(column.metric) : null));
    }));
    const body = h("tbody", {}, sorted().map((row) => {
      const key = rowKey ? rowKey(row) : null;
      return h("tr", {
        class: `${onRow ? "clickable" : ""} ${key !== null && key === selected ? "is-selected" : ""}`,
        onclick: onRow ? (event) => {
          if (event.target.closest("a, button, input")) return;
          onRow(row);
        } : null,
      }, columns.map((column) => h("td", { class: `${column.left ? "left " : ""}${column.wrap ? "wrap " : ""}${column.sticky ? "sticky" : ""}` },
        column.render ? column.render(row) : fmt(valueOf(column, row), column.fmt))));
    }));
    replace(wrap, h("table", { class: "data" }, h("thead", {}, head), body));
    if (note) wrap.append(h("div", { class: "table-note" }, note));
  }

  draw();
  wrap.update = (nextRows, nextSelected) => {
    current = nextRows;
    if (nextSelected !== undefined) selected = nextSelected;
    draw();
  };
  return wrap;
}

export function symbolLink(symbol) {
  return h("a", { class: "sym", href: `#/stock/${encodeURIComponent(symbol)}` }, symbol);
}

export function errorBox(message, retry) {
  return h("div", { class: "error-box" }, h("span", {}, message), retry ? h("button", { class: "btn btn-xs", type: "button", onclick: retry }, "Try again") : null);
}

export function emptyBox(message) {
  return h("div", { class: "empty" }, message);
}

export function loadingBox(text = "Loading…") {
  return h("div", { class: "loading" }, text);
}

const toastRoot = h("div", { class: "toasts", role: "status", "aria-live": "polite" });
document.addEventListener("DOMContentLoaded", () => document.body.append(toastRoot));

export function toast(message, kind = "") {
  if (!toastRoot.isConnected) document.body.append(toastRoot);
  const el = h("div", { class: `toast ${kind}` }, message);
  toastRoot.append(el);
  setTimeout(() => el.remove(), kind === "error" ? 6000 : 3000);
}

export function kvRow(label, metric, value) {
  return h("div", { class: "kv-row" }, h("span", { class: "kv-label" }, label, metric ? info(metric) : null), h("span", { class: "kv-value" }, value ?? MISSING));
}
