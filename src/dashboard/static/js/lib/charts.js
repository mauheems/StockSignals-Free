// ECharts wrappers that apply one consistent chart system: thin marks, hairline grids,
// a crosshair tooltip, a legend for two or more series, selective end labels, and a
// table view for every chart. Charts always use one y-axis per plot.
import { axisLabel, esc, fmt, fmtDate, h, replace } from "./dom.js";
import { info } from "./guide.js";

/* global echarts */

const liveCharts = new Set();

export function disposeCharts() {
  for (const handle of liveCharts) handle.dispose();
  liveCharts.clear();
}

export function colors() {
  const styles = getComputedStyle(document.documentElement);
  const read = (name) => styles.getPropertyValue(name).trim();
  return {
    surface: read("--surface"),
    ink: read("--ink"),
    ink2: read("--ink-2"),
    muted: read("--muted"),
    grid: read("--grid"),
    axis: read("--axis"),
    border: read("--border"),
    deemph: read("--deemph"),
    accent: read("--accent"),
    up: read("--up"),
    down: read("--down"),
    mid: read("--mid"),
    good: read("--good"),
    warning: read("--warning"),
    serious: read("--serious"),
    critical: read("--critical"),
    series: [1, 2, 3, 4, 5, 6, 7, 8].map((slot) => read(`--s${slot}`)),
  };
}

export function mount(el) {
  const chart = echarts.init(el, null, { renderer: "canvas" });
  const observer = new ResizeObserver(() => chart.resize());
  observer.observe(el);
  const handle = {
    chart,
    dispose() {
      observer.disconnect();
      chart.dispose();
      liveCharts.delete(handle);
    },
  };
  liveCharts.add(handle);
  return handle;
}

export function axisDate(value) {
  // Dates arrive as YYYY-MM-DD (daily) or YYYY-MM-DD HH:MM (intraday).
  if (!value) return "";
  if (value.length > 10) return `${fmtDate(value)} ${value.slice(11)}`;
  return fmtDate(value);
}

export function baseOption() {
  const c = colors();
  return {
    animation: false,
    backgroundColor: "transparent",
    textStyle: { fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif', color: c.ink2, fontSize: 11 },
    tooltip: {
      confine: true,
      backgroundColor: c.surface,
      borderColor: c.border,
      borderWidth: 1,
      padding: [7, 9],
      textStyle: { color: c.ink, fontSize: 12 },
      extraCssText: "box-shadow: var(--shadow); border-radius: 8px;",
    },
  };
}

export function tooltipRows(header, rows) {
  const lines = rows.map(
    (row) => `<div style="display:flex;align-items:center;gap:7px;line-height:1.55">
      <span style="width:12px;height:2px;border-radius:1px;background:${row.color};flex:none"></span>
      <b style="font-weight:620;font-variant-numeric:tabular-nums">${esc(row.value)}</b>
      <span style="opacity:.75">${esc(row.name)}</span></div>`,
  );
  return `<div style="font-size:11.5px;opacity:.8;margin-bottom:2px">${esc(header)}</div>${lines.join("")}`;
}

/** A chart card with title, explanation and a table-view twin. */
export function chartCard({ title, metric, subtitle, height = 230, foot }) {
  const canvas = h("div", { class: "chart-canvas", style: { height: `${height}px` }, role: "img", "aria-label": title });
  const tableBox = h("div", { class: "table-wrap", style: { maxHeight: `${height}px` }, hidden: true });
  const emptyBox = h("div", { class: "chart-empty", hidden: true });
  const subEl = h("p", { class: "card-sub" }, subtitle || "");
  subEl.hidden = !subtitle;
  const footEl = h("div", { class: "chart-foot" }, foot || "");
  footEl.hidden = !foot;
  let showingTable = false;
  let handle = null;
  let table = null;

  const toggle = h("button", {
    class: "btn btn-ghost btn-xs",
    type: "button",
    "aria-pressed": "false",
    title: "Show this chart as a table of numbers",
    onclick: () => {
      showingTable = !showingTable;
      toggle.textContent = showingTable ? "Chart" : "Table";
      toggle.setAttribute("aria-pressed", String(showingTable));
      sync();
    },
  }, "Table");

  const el = h(
    "figure",
    { class: "card chart-card", dataset: { chart: title } },
    h("div", { class: "card-head" },
      h("div", {}, h("h3", { class: "card-title" }, title, metric ? info(metric) : null), subEl),
      h("div", { class: "card-actions" }, toggle)),
    canvas, tableBox, emptyBox, footEl,
  );

  function sync() {
    const isEmpty = !emptyBox.hidden;
    canvas.hidden = isEmpty || showingTable;
    tableBox.hidden = isEmpty || !showingTable;
    toggle.hidden = isEmpty;
    if (!canvas.hidden && handle) handle.chart.resize();
    if (showingTable && table) drawTable();
  }

  function drawTable() {
    const limit = 300;
    const rows = table.rows.slice(0, limit);
    const headers = h("tr", {}, table.columns.map((column, index) => h("th", { class: index === 0 ? "left" : "", scope: "col" }, column.label)));
    const body = rows.map((row) => h("tr", {}, table.columns.map((column, index) => {
      const value = row[index];
      return h("td", { class: index === 0 ? "left" : "" }, column.fmt ? fmt(value, column.fmt) : value ?? "–");
    })));
    replace(tableBox, h("table", { class: "data" }, h("thead", {}, headers), h("tbody", {}, body)));
    if (table.rows.length > limit) tableBox.append(h("div", { class: "table-note" }, `Showing the latest ${limit} of ${table.rows.length} rows.`));
  }

  return {
    el,
    setSubtitle(text) {
      subEl.textContent = text || "";
      subEl.hidden = !text;
    },
    setFoot(text) {
      footEl.replaceChildren();
      if (text) footEl.append(text);
      footEl.hidden = !text;
    },
    render(result) {
      el.classList.remove("is-refreshing");
      if (!result || result.empty) {
        emptyBox.textContent = result?.empty || "No data yet. This fills in once its source has loaded.";
        emptyBox.hidden = false;
        sync();
        return;
      }
      emptyBox.hidden = true;
      table = result.table;
      canvas.hidden = false;
      if (!handle) handle = mount(canvas);
      handle.chart.setOption(result.option, true);
      sync();
    },
    refreshing() {
      el.classList.add("is-refreshing");
    },
    get chart() {
      return handle ? handle.chart : null;
    },
  };
}

function hasData(values) {
  return Array.isArray(values) && values.some((value) => value !== null && value !== undefined);
}

function lastValue(values) {
  for (let i = values.length - 1; i >= 0; i -= 1) if (values[i] !== null && values[i] !== undefined) return values[i];
  return null;
}

function dateAxis(dates, c, overrides = {}) {
  return {
    type: "category",
    data: dates,
    boundaryGap: true,
    axisLine: { lineStyle: { color: c.axis, width: 1 } },
    axisTick: { show: false },
    axisLabel: {
      color: c.muted,
      hideOverlap: true,
      fontSize: 11,
      formatter: (value) => {
        const [year, month, day] = value.slice(0, 10).split("-");
        return dates.length > 300
          ? `${fmtDate(value).split(" ")[0]} '${year.slice(2)}`
          : `${fmtDate(`${year}-${month}-${day}`)}`;
      },
    },
    axisPointer: { type: "line", lineStyle: { color: c.muted, width: 1, type: "solid" }, label: { show: false } },
    ...overrides,
  };
}

function valueAxis(c, code, overrides = {}) {
  return {
    type: "value",
    scale: true,
    position: "left",
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: { lineStyle: { color: c.grid, width: 1, type: "solid" } },
    axisLabel: { color: c.muted, fontSize: 11, formatter: (value) => axisLabel(value, code) },
    ...overrides,
  };
}

function thresholdMarks(thresholds, c) {
  if (!thresholds || !thresholds.length) return undefined;
  return {
    silent: true,
    symbol: "none",
    animation: false,
    lineStyle: { color: c.axis, width: 1, type: "solid" },
    label: {
      show: true,
      position: "insideEndTop",
      color: c.ink2,
      fontSize: 10.5,
      backgroundColor: c.surface,
      padding: [1, 3],
      borderRadius: 2,
      formatter: (item) => item.name || "",
    },
    data: thresholds.map((item) => ({ yAxis: item.y, name: item.label || "" })),
  };
}

/**
 * Line chart on one axis. Series: {name, short, values, slot (palette index), muted, area, width}.
 * Returns {option, table} for chartCard.render.
 */
export function lineChart({ dates, series, yFmt = "num2", thresholds, min, max, endLabels = true, emptyText }) {
  const live = series.filter((item) => hasData(item.values));
  if (!dates || !dates.length || !live.length) return { empty: emptyText || "No data yet. This fills in once its source has loaded." };
  const c = colors();
  const colorOf = (item, index) => (item.muted ? c.deemph : c.series[item.slot ?? index]);

  // Direct end labels only when they will not collide; legend and tooltip cover the rest.
  const lasts = live.map((item) => lastValue(item.values));
  const allValues = live.flatMap((item) => item.values).filter((value) => value !== null && value !== undefined);
  const span = Math.max(...allValues) - Math.min(...allValues) || 1;
  const sortedLasts = [...lasts].sort((a, b) => a - b);
  const separated = sortedLasts.every((value, index) => index === 0 || value - sortedLasts[index - 1] > span * 0.07);
  const showEnd = endLabels && live.length <= 4 && separated;
  const wantsNames = live.length > 1;
  // A value alone identifies itself when only one line is labelled (the others are context).
  const labelled = live.filter((item) => !item.muted);
  const endText = (item) => {
    const value = fmt(lastValue(item.values), yFmt);
    return labelled.length > 1 ? `${item.short ?? item.name} ${value}` : value;
  };

  const seriesOption = live.map((item, index) => ({
    name: item.name,
    type: "line",
    data: item.values,
    showSymbol: dates.length <= 5,
    connectNulls: true,
    symbol: "circle",
    symbolSize: 8,
    lineStyle: { width: item.width ?? 2, color: colorOf(item, index), cap: "round", join: "round" },
    itemStyle: { color: colorOf(item, index), borderColor: c.surface, borderWidth: 2 },
    emphasis: { disabled: true },
    areaStyle: item.area ? { color: colorOf(item, index), opacity: 0.1 } : undefined,
    endLabel: showEnd && !item.muted ? {
      show: true,
      color: c.ink2,
      fontSize: 11,
      distance: 6,
      formatter: () => endText(item),
    } : undefined,
    markLine: index === 0 ? thresholdMarks(thresholds, c) : undefined,
  }));
  const rightRoom = showEnd
    ? Math.min(170, 14 + 6.2 * Math.max(...labelled.map((item) => endText(item).length), 1))
    : 14;

  const option = {
    ...baseOption(),
    grid: { left: 8, right: rightRoom, top: wantsNames ? 28 : 10, bottom: 4, containLabel: true },
    legend: wantsNames ? {
      top: 0,
      left: 0,
      itemWidth: 14,
      itemHeight: 3,
      icon: "roundRect",
      textStyle: { color: c.ink2, fontSize: 11 },
      data: live.map((item) => item.name),
    } : undefined,
    xAxis: dateAxis(dates, c),
    yAxis: valueAxis(c, yFmt, { min, max }),
    series: seriesOption,
  };
  option.tooltip = {
    ...option.tooltip,
    trigger: "axis",
    formatter: (items) => tooltipRows(axisDate(items[0].axisValue), items
      .filter((item) => item.value !== null && item.value !== undefined)
      .map((item) => ({ color: item.color, value: fmt(item.value, yFmt), name: item.seriesName }))),
  };
  const table = {
    columns: [{ label: "Date" }, ...live.map((item) => ({ label: item.name, fmt: yFmt }))],
    rows: dates.map((date, index) => [date, ...live.map((item) => item.values[index])]).reverse(),
  };
  return { option, table };
}

/** Bars above and below zero in the diverging pair: blue up, red down. */
export function divergingBars({ dates, name, values, yFmt = "num1", thresholds, emptyText }) {
  if (!dates || !dates.length || !hasData(values)) return { empty: emptyText || "No data yet. This fills in once its source has loaded." };
  const c = colors();
  const radius = dates.length <= 60 ? 4 : 1;
  const option = {
    ...baseOption(),
    grid: { left: 8, right: 14, top: 10, bottom: 4, containLabel: true },
    xAxis: dateAxis(dates, c),
    yAxis: valueAxis(c, yFmt),
    series: [{
      name,
      type: "bar",
      barMaxWidth: 24,
      barCategoryGap: dates.length > 120 ? "0%" : "25%",
      itemStyle: { color: c.up },
      emphasis: { disabled: true },
      data: values.map((value) => ({
        value,
        itemStyle: {
          color: value >= 0 ? c.up : c.down,
          borderRadius: value >= 0 ? [radius, radius, 0, 0] : [0, 0, radius, radius],
        },
      })),
      markLine: thresholdMarks(thresholds, c),
    }],
  };
  option.tooltip = {
    ...option.tooltip,
    trigger: "axis",
    axisPointer: { type: "line", lineStyle: { color: c.muted, width: 1, type: "solid" } },
    formatter: (items) => tooltipRows(axisDate(items[0].axisValue), [{
      color: items[0].value >= 0 ? c.up : c.down,
      value: fmt(items[0].value, yFmt),
      name,
    }]),
  };
  return {
    option,
    table: { columns: [{ label: "Date" }, { label: name, fmt: yFmt }], rows: dates.map((date, index) => [date, values[index]]).reverse() },
  };
}

/** Ranked horizontal bars for one measure across named items. */
export function rankedBars({ items, yFmt = "spct1", name = "Change", emptyText }) {
  const live = items.filter((item) => item.value !== null && item.value !== undefined).sort((a, b) => a.value - b.value);
  if (!live.length) return { empty: emptyText || "No data yet. This fills in once its source has loaded." };
  const c = colors();
  // Leave room past the longest bar on each side, so value labels never hit the names.
  const lowest = Math.min(0, ...live.map((item) => item.value));
  const highest = Math.max(0, ...live.map((item) => item.value));
  const room = (highest - lowest || 1) * 0.22;
  const option = {
    ...baseOption(),
    grid: { left: 8, right: 8, top: 2, bottom: 2, containLabel: true },
    xAxis: { type: "value", show: false, scale: false, min: lowest < 0 ? lowest - room : 0, max: highest > 0 ? highest + room : 0 },
    yAxis: {
      type: "category",
      data: live.map((item) => item.label),
      axisLine: { lineStyle: { color: c.axis } },
      axisTick: { show: false },
      axisLabel: { color: c.ink2, fontSize: 11.5 },
    },
    series: [{
      name,
      type: "bar",
      barMaxWidth: 14,
      emphasis: { disabled: true },
      label: { show: true, position: "right", color: c.ink2, fontSize: 11, formatter: (item) => fmt(item.value, yFmt) },
      data: live.map((item) => ({
        value: item.value,
        label: { position: item.value >= 0 ? "right" : "left" },
        itemStyle: {
          color: item.value >= 0 ? c.up : c.down,
          borderRadius: item.value >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4],
        },
      })),
    }],
  };
  option.tooltip = {
    ...option.tooltip,
    trigger: "item",
    formatter: (item) => tooltipRows(item.name, [{ color: item.color, value: fmt(item.value, yFmt), name }]),
  };
  return {
    option,
    table: { columns: [{ label: "Name" }, { label: name, fmt: yFmt }], rows: [...live].reverse().map((item) => [item.label, item.value]) },
  };
}

/** Lines over a category axis that isn't time, such as maturity or strike. */
export function categoryLines({ categories, series, yFmt = "num1", xName, emptyText }) {
  const live = series.filter((item) => hasData(item.values));
  if (!categories.length || !live.length) return { empty: emptyText || "No data yet. This fills in once its source has loaded." };
  const c = colors();
  const option = {
    ...baseOption(),
    grid: { left: 8, right: 14, top: live.length > 1 ? 28 : 10, bottom: xName ? 18 : 4, containLabel: true },
    legend: live.length > 1 ? {
      top: 0, left: 0, itemWidth: 14, itemHeight: 3, icon: "roundRect", textStyle: { color: c.ink2, fontSize: 11 }, data: live.map((item) => item.name),
    } : undefined,
    xAxis: {
      type: "category",
      data: categories,
      name: xName,
      nameLocation: "middle",
      nameGap: 24,
      nameTextStyle: { color: c.muted },
      axisLine: { lineStyle: { color: c.axis } },
      axisTick: { show: false },
      axisLabel: { color: c.muted, fontSize: 11, hideOverlap: true },
      axisPointer: { type: "line", lineStyle: { color: c.muted, width: 1, type: "solid" }, label: { show: false } },
    },
    yAxis: valueAxis(c, yFmt),
    series: live.map((item, index) => {
      const color = item.muted ? c.deemph : c.series[item.slot ?? index];
      return {
        name: item.name,
        type: "line",
        data: item.values,
        connectNulls: true,
        symbol: "circle",
        symbolSize: 8,
        showSymbol: item.symbols ?? true,
        lineStyle: { width: 2, color },
        itemStyle: { color, borderColor: c.surface, borderWidth: 2 },
        emphasis: { disabled: true },
      };
    }),
  };
  option.tooltip = {
    ...option.tooltip,
    trigger: "axis",
    formatter: (items) => tooltipRows(items[0].axisValue, items
      .filter((item) => item.value !== null && item.value !== undefined)
      .map((item) => ({ color: item.color, value: fmt(item.value, yFmt), name: item.seriesName }))),
  };
  return {
    option,
    table: {
      columns: [{ label: xName || "Point" }, ...live.map((item) => ({ label: item.name, fmt: yFmt }))],
      rows: categories.map((category, index) => [category, ...live.map((item) => item.values[index])]),
    },
  };
}

export { dateAxis, valueAxis, thresholdMarks };
