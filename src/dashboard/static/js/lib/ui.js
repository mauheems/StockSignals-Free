// Shared interface pieces: tiles, badges, filters, tables and page scaffolding.
import { MISSING, append, fmt, fmtDate, h, prefs, replace, svg } from "./dom.js";
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
    foot: [row.score !== null && row.score !== undefined ? scoreBadge(row.score, row.pillar === "crowd") : h("span", {}), dateMark(row)],
  });
}

/** The date of a reading, marked when the source has stopped delivering new ones. */
export function dateMark(row) {
  if (!row?.date) return h("span", {}, MISSING);
  if (!row.stale) return h("span", { title: `Latest reading: ${row.date}` }, fmtDate(row.date));
  return h("span", { class: "stale-mark", title: `No new reading for ${row.age} trading sessions, longer than usual. Check Data Sources.` }, `${fmtDate(row.date)} · stale`);
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
 *
 * Clicking a column title sorts by it, again flips the direction, and a third time goes back
 * to the original order. With an `id`, the table remembers the chosen sort between visits.
 *
 * With `reorder(keys)`, rows get a handle to drag them (or move them with the arrow keys),
 * for lists the user curates. The new order of row keys is passed to `reorder` to save.
 */
export function dataTable({ columns, rows, sort, rowKey, onRow, selectedKey, maxHeight, empty = "Nothing to show.", note, id, reorder, itemName = "row" }) {
  const wrap = h("div", { class: "table-wrap", style: maxHeight ? { maxHeight: `${maxHeight}px` } : {} });
  const remembered = id ? prefs.get(`table:${id}`, null) : null;
  // A remembered choice wins, including "no sort" (the user's own order).
  let sortState = remembered ? remembered.sort : (reorder ? null : sort || null);
  let current = rows;
  let selected = selectedKey;
  let dragFrom = null;

  const valueOf = (column, row) => (column.value ? column.value(row) : row[column.key]);
  const keyOf = (row) => String(rowKey(row));

  function setSort(next) {
    sortState = next;
    if (id) prefs.set(`table:${id}`, { sort: sortState });
    draw();
  }

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

  /** Move a row within what is on screen; that becomes the saved order. */
  function moveRow(from, to) {
    const order = sorted();
    if (to < 0 || to >= order.length || from === to) return;
    const next = [...order];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    current = next;
    sortState = null;
    if (id) prefs.set(`table:${id}`, { sort: null });
    draw();
    reorder(next.map(rowKey));
    return keyOf(item);
  }

  function handleFor(row, index, total) {
    const handle = h("button", {
      class: "row-handle",
      type: "button",
      "aria-label": `Move ${keyOf(row)}. Use the up and down arrow keys`,
      title: "Drag to reorder, or select and use the arrow keys",
      dataset: { handle: keyOf(row) },
      onkeydown: (event) => {
        if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
        event.preventDefault();
        const moved = moveRow(index, index + (event.key === "ArrowUp" ? -1 : 1));
        if (moved) wrap.querySelector(`[data-handle="${CSS.escape(moved)}"]`)?.focus();
      },
      onclick: (event) => event.stopPropagation(),
    }, "⠿");
    handle.addEventListener("mousedown", () => { handle.closest("tr").draggable = true; });
    handle.disabled = total < 2;
    return handle;
  }

  function sortBar() {
    if (!reorder) return null;
    if (!sortState) {
      return h("div", { class: "table-tools" }, h("span", { class: "sub-text" }, `Drag ⠿ to put your ${itemName}s in your own order, or select a column title to sort.`));
    }
    const column = columns.find((item) => item.key === sortState.key);
    return h("div", { class: "table-tools" },
      h("span", { class: "sub-text" }, `Sorted by ${column?.label || sortState.key}, ${sortState.dir === "asc" ? "low to high" : "high to low"}.`),
      h("button", {
        class: "btn btn-xs",
        type: "button",
        dataset: { keepOrder: "true" },
        title: "Save this sorted order as your own order",
        onclick: () => {
          current = sorted();
          setSort(null);
          reorder(current.map(rowKey));
        },
      }, "Keep this order"),
      h("button", { class: "btn btn-ghost btn-xs", type: "button", onclick: () => setSort(null) }, "Back to my order"));
  }

  function draw() {
    if (!current.length) {
      replace(wrap, h("div", { class: "empty" }, empty));
      return;
    }
    const head = h("tr", {}, columns.map((column) => {
      const canSort = column.sortable !== false;
      const active = sortState && sortState.key === column.key;
      const direction = active ? (sortState.dir === "asc" ? " ↑" : " ↓") : "";
      return h("th", {
        class: `${column.left ? "left " : ""}${canSort ? "sortable" : ""} ${column.wrap ? "wrap" : ""} ${column.sticky ? "sticky" : ""}`,
        scope: "col",
        "aria-sort": active ? (sortState.dir === "asc" ? "ascending" : "descending") : null,
        title: canSort ? "Sort by this column (select again to reverse, a third time to undo)" : null,
        onclick: canSort ? () => {
          const first = column.left ? "asc" : "desc";
          if (!active) setSort({ key: column.key, dir: first });
          else if (sortState.dir === first) setSort({ key: column.key, dir: first === "asc" ? "desc" : "asc" });
          else setSort(null);
        } : null,
      }, h("span", { class: "th-inner" }, `${column.label}${direction}`, column.metric ? info(column.metric) : null));
    }));
    const shown = sorted();
    const body = h("tbody", {}, shown.map((row, index) => {
      const key = rowKey ? rowKey(row) : null;
      const cells = columns.map((column, position) => {
        const content = column.render ? column.render(row) : fmt(valueOf(column, row), column.fmt);
        return h("td", { class: `${column.left ? "left " : ""}${column.wrap ? "wrap " : ""}${column.sticky ? "sticky" : ""}` },
          reorder && position === 0 ? h("span", { class: "row-lead" }, handleFor(row, index, shown.length), content) : content);
      });
      const tr = h("tr", {
        class: `${onRow ? "clickable" : ""} ${key !== null && key === selected ? "is-selected" : ""}`,
        onclick: onRow ? (event) => {
          if (event.target.closest("a, button, input")) return;
          onRow(row);
        } : null,
      }, cells);
      if (reorder) {
        tr.addEventListener("dragstart", (event) => {
          dragFrom = index;
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", String(index));
          tr.classList.add("is-dragging");
        });
        tr.addEventListener("dragend", () => {
          tr.draggable = false;
          tr.classList.remove("is-dragging");
          dragFrom = null;
        });
        tr.addEventListener("dragover", (event) => {
          if (dragFrom === null) return;
          event.preventDefault();
          const box = tr.getBoundingClientRect();
          const after = event.clientY > box.top + box.height / 2;
          tr.classList.toggle("drop-before", !after);
          tr.classList.toggle("drop-after", after);
        });
        tr.addEventListener("dragleave", () => tr.classList.remove("drop-before", "drop-after"));
        tr.addEventListener("drop", (event) => {
          if (dragFrom === null) return;
          event.preventDefault();
          const after = tr.classList.contains("drop-after");
          tr.classList.remove("drop-before", "drop-after");
          let to = index + (after ? 1 : 0);
          if (dragFrom < to) to -= 1;
          moveRow(dragFrom, to);
        });
      }
      return tr;
    }));
    replace(wrap, sortBar(), h("table", { class: "data" }, h("thead", {}, head), body));
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
