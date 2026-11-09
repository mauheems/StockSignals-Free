// Chart Lab: candles with moving averages, indicator panes, and any signal layered on top.
//
// Layers are drawn on one scale per plot. To put signals "on" the price, the price and the
// signals are converted to a shared scale (indexed or z-score), so no second axis is needed.
import { api, query } from "../lib/api.js";
import { axisDate, baseOption, colors, mount, thresholdMarks, tooltipRows, valueAxis } from "../lib/charts.js";
import { MISSING, axisLabel, compact, fmt, fmtDate, h, prefs, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { conditionRow, loadCurrentValues, signalOptions } from "../lib/rules.js";
import { field, fillSelect, filters, pageHead, segmented, toast } from "../lib/ui.js";

const INTERVALS = [
  { value: "15m", label: "15 min" }, { value: "1h", label: "1 hour" }, { value: "1d", label: "Daily" }, { value: "1wk", label: "Weekly" },
];
const RANGE_LABELS = { "5d": "5D", "1m": "1M", "60d": "60D", "3m": "3M", "6m": "6M", "1y": "1Y", "2y": "2Y", "5y": "5Y", "10y": "10Y", max: "Max" };
const MODES = [
  { value: "panes", label: "Separate panes" },
  { value: "indexed", label: "On price: indexed" },
  { value: "zscore", label: "On price: z-score" },
];

// Color follows the layer: slots from the validated palette, the same across sessions.
const OVERLAYS = [
  { id: "sma20", label: "SMA 20", metric: "sma", keys: ["sma20"], slot: 2 },
  { id: "sma50", label: "SMA 50", metric: "sma", keys: ["sma50"], slot: 1 },
  { id: "sma100", label: "SMA 100", metric: "sma", keys: ["sma100"], slot: 5 },
  { id: "sma200", label: "SMA 200", metric: "sma", keys: ["sma200"], slot: 6 },
  { id: "ema8", label: "EMA 8", metric: "ema", keys: ["ema8"], slot: 4 },
  { id: "ema21", label: "EMA 21", metric: "ema", keys: ["ema21"], slot: 7 },
  { id: "bb", label: "Bollinger Bands", metric: "bollinger", keys: ["bb_upper", "bb_lower"], names: ["BB upper", "BB lower"], muted: true },
  { id: "vwap", label: "VWAP", metric: "vwap", keys: ["vwap"], slot: 3 },
  { id: "range52", label: "52-week high and low", metric: "off_high", keys: ["high_52w", "low_52w"], names: ["52w high", "52w low"], muted: true, dailyOnly: true },
];
const MARKS = [
  { id: "crosses", label: "Golden and death crosses", metric: "golden_cross", dailyOnly: true },
  { id: "gamma", label: "Gamma levels", metric: "gamma_levels" },
  { id: "earnings", label: "Earnings dates", metric: "earnings_marks", dailyOnly: true },
  // Changes which bars are loaded, so toggling it fetches new data instead of only redrawing.
  { id: "extended", label: "Pre-market and after hours", metric: "extended_hours", intradayOnly: true, reload: true },
];
const SESSION_NAMES = { pre: "Pre-market", post: "After hours" };
const PANES = [
  { id: "volume", label: "Volume", metric: "volume_bars" },
  { id: "rvol", label: "Relative volume", metric: "rvol" },
  { id: "rsi", label: "RSI", metric: "rsi" },
  { id: "macd", label: "MACD", metric: "macd" },
  { id: "adx", label: "ADX", metric: "adx" },
  { id: "stoch", label: "Stochastic", metric: "stochastic" },
  { id: "obv", label: "On-balance volume", metric: "obv" },
  { id: "ad", label: "Accumulation/Distribution", metric: "ad" },
  { id: "cmf", label: "Chaikin Money Flow", metric: "cmf" },
  { id: "mfi", label: "Money Flow Index", metric: "mfi" },
  { id: "atr", label: "ATR %", metric: "atr" },
  { id: "hv", label: "Realized volatility", metric: "hv" },
  { id: "rs", label: "Strength vs SPY", metric: "rs_vs_spy" },
];
const SHADE_SLOTS = [7, 3, 6];
const MAX_SIGNALS = 6;

const DEFAULTS = {
  symbol: "SPY",
  interval: "1d",
  range: "1y",
  mode: "panes",
  overlays: { sma50: true, sma200: true },
  marks: { crosses: true, extended: true },
  panes: { volume: true },
  signals: [],
  shades: [],
};

function mean(values) {
  const clean = values.filter((value) => value !== null && value !== undefined);
  return clean.reduce((sum, value) => sum + value, 0) / (clean.length || 1);
}

function deviation(values, center) {
  const clean = values.filter((value) => value !== null && value !== undefined);
  return Math.sqrt(clean.reduce((sum, value) => sum + (value - center) ** 2, 0) / (clean.length || 1)) || 1;
}

function firstValue(values) {
  return values.find((value) => value !== null && value !== undefined) ?? null;
}

/** Spans of consecutive dates on which a condition held, for shading. */
function spansFor(values, op, threshold, dates) {
  const test = { ">": (v) => v > threshold, "<": (v) => v < threshold, ">=": (v) => v >= threshold, "<=": (v) => v <= threshold }[op];
  const spans = [];
  let start = null;
  values.forEach((value, index) => {
    const on = value !== null && value !== undefined && test(value);
    if (on && start === null) start = index;
    if (!on && start !== null) {
      spans.push([dates[start], dates[index - 1]]);
      start = null;
    }
  });
  if (start !== null) spans.push([dates[start], dates[dates.length - 1]]);
  return spans;
}

function nearestDateIndex(dates, iso) {
  for (let i = 0; i < dates.length; i += 1) if (dates[i].slice(0, 10) >= iso) return i;
  return -1;
}

export default {
  title: (params) => `Chart Lab${params[0] ? ` · ${params[0]}` : ""}`,
  async mount(root, { params }) {
    const saved = prefs.get("chartlab", {});
    const state = { ...DEFAULTS, ...saved };
    // Settings saved before extended hours existed get it switched on, like a fresh start.
    state.marks = { extended: true, ...state.marks };
    if (params[0]) state.symbol = params[0].toUpperCase();
    let catalog = { groups: [] };
    let data = null;
    let aligned = { signals: {} };
    let levels = null;
    let earnings = null;
    let handle = null;
    let loadId = 0;
    let showingTable = false;
    let notices = [];

    const save = () => prefs.set("chartlab", state);

    // ------------------------------------------------------------------- layout
    const canvas = h("div", { class: "chart-canvas" });
    const tableBox = h("div", { class: "table-wrap", style: { maxHeight: "680px" }, hidden: true });
    const titleBox = h("h3", { class: "card-title" });
    const subBox = h("p", { class: "card-sub" });
    const noticeBox = h("div", {});
    const tableToggle = h("button", {
      class: "btn btn-ghost btn-xs",
      type: "button",
      "aria-pressed": "false",
      onclick: () => {
        showingTable = !showingTable;
        tableToggle.textContent = showingTable ? "Chart" : "Table";
        tableToggle.setAttribute("aria-pressed", String(showingTable));
        canvas.hidden = showingTable;
        tableBox.hidden = !showingTable;
        if (showingTable && data) drawTable();
        else handle?.chart.resize();
      },
    }, "Table");
    const chartCardEl = h("figure", { class: "card chart-card" },
      h("div", { class: "card-head" }, h("div", {}, titleBox, subBox), h("div", { class: "card-actions" }, tableToggle)),
      noticeBox, canvas, tableBox,
      h("p", { class: "lab-chart-note" }, "Hollow blue candles closed up, solid red candles closed down. Drag or scroll on the chart to zoom. On-price modes rescale everything to share one axis, and the tooltip always shows true values."));

    const symbolInput = h("input", {
      class: "input",
      style: { width: "110px" },
      value: state.symbol,
      "aria-label": "Symbol",
      spellcheck: false,
      onkeydown: (event) => {
        if (event.key === "Enter") setSymbol(event.target.value);
      },
    });
    const goButton = h("button", { class: "btn", type: "button", onclick: () => setSymbol(symbolInput.value) }, "Load");
    const rangeBox = h("div", {});
    const stockLink = h("a", { class: "btn" }, "Stock details");

    function checkRow(label, metric, checked, onChange, keyColor) {
      const id = `lab-${metric}-${label.replace(/\W/g, "")}`;
      return h("div", { class: "check-row" },
        h("label", { for: id },
          h("input", { type: "checkbox", id, checked, onchange: (event) => onChange(event.target.checked) }),
          keyColor ? h("span", { class: "line-key", style: { background: keyColor } }) : null,
          label),
        info(metric));
    }

    const overlaysBox = h("div", { class: "check-grid" });
    const marksBox = h("div", { class: "check-grid" });
    const panesBox = h("div", { class: "check-grid" });
    const signalChips = h("div", { class: "chips", style: { marginTop: "6px" } });
    const shadeList = h("div", { style: { marginTop: "6px" } });
    const signalSelect = h("select", {
      class: "input",
      style: { width: "100%" },
      "aria-label": "Add a signal",
      onchange: (event) => {
        if (event.target.value) addSignal(event.target.value);
        event.target.value = "";
      },
    });
    const compareInput = h("input", {
      class: "input",
      style: { flex: 1, minWidth: 0 },
      placeholder: "Compare ticker, e.g. QQQ",
      "aria-label": "Compare another ticker",
      spellcheck: false,
      onkeydown: (event) => {
        if (event.key === "Enter") addCompare();
      },
    });
    const signalPanel = h("section", { class: "card" });
    const shadePanel = h("section", { class: "card" });
    const shadeDraft = { signal: "vix", op: ">", value: 25 };
    const shadeBuilder = h("div", {});

    function panelTitle(text, metric) {
      return h("div", { class: "panel-title" }, text, metric ? info(metric) : null);
    }

    function drawControls() {
      const c = colors();
      const intraday = state.interval === "15m" || state.interval === "1h";
      replace(overlaysBox, OVERLAYS.filter((item) => !(intraday && item.dailyOnly)).map((item) => checkRow(
        item.label, item.metric, !!state.overlays[item.id],
        (on) => { state.overlays[item.id] = on; save(); draw(); },
        item.muted ? c.deemph : c.series[item.slot],
      )));
      replace(marksBox, MARKS.filter((item) => !(intraday && item.dailyOnly) && !(!intraday && item.intradayOnly)).map((item) => checkRow(
        item.label, item.metric, !!state.marks[item.id],
        (on) => { state.marks[item.id] = on; save(); if (item.reload) load(); else refreshMarks(); },
      )));
      replace(panesBox, PANES.filter((item) => !(intraday && item.id === "rs")).map((item) => checkRow(
        item.label, item.metric, !!state.panes[item.id],
        (on) => { state.panes[item.id] = on; save(); draw(); },
      )));
      signalPanel.hidden = intraday;
      shadePanel.hidden = intraday;
    }

    function drawSignalChips() {
      const c = colors();
      replace(signalChips, state.signals.map((signal) => {
        const entry = aligned.signals[signal.id];
        const label = entry?.label || signal.id.replace(/^px:/, "");
        const metric = entry?.metric || (signal.id.startsWith("px:") ? "compare_ticker" : signal.id);
        return h("span", { class: "chip" },
          h("span", { class: "chip-swatch", style: { background: c.series[signal.slot] } }),
          label,
          info(metric),
          h("button", { type: "button", "aria-label": `Remove ${label}`, title: `Remove ${label}`, onclick: () => removeSignal(signal.id) }, "×"));
      }));
    }

    function drawShadeList() {
      const c = colors();
      replace(shadeList, state.shades.map((rule, index) => {
        const entry = aligned.signals[rule.signal];
        return h("span", { class: "chip", style: { margin: "0 4px 4px 0" } },
          h("span", { class: "chip-swatch", style: { background: c.series[SHADE_SLOTS[index % SHADE_SLOTS.length]], height: "10px" } }),
          `${entry?.label || rule.signal} ${rule.op} ${rule.value}`,
          h("button", {
            type: "button",
            "aria-label": "Remove shading",
            title: "Remove shading",
            onclick: () => {
              state.shades.splice(index, 1);
              save();
              loadSignals().then(draw);
              drawShadeList();
            },
          }, "×"));
      }));
    }

    function nextSlot() {
      const used = new Set(state.signals.map((signal) => signal.slot));
      // Slot 0 stays free for the price itself in on-price modes.
      return [1, 2, 3, 4, 5, 6, 7].find((slot) => !used.has(slot)) ?? 1;
    }

    function addSignal(id) {
      if (state.signals.some((signal) => signal.id === id)) return;
      if (state.signals.length >= MAX_SIGNALS) {
        toast(`Up to ${MAX_SIGNALS} signals at once keeps the chart readable. Remove one first.`);
        return;
      }
      state.signals.push({ id, slot: nextSlot() });
      save();
      loadSignals().then(() => { drawSignalChips(); draw(); });
    }

    function addCompare() {
      const ticker = compareInput.value.trim().toUpperCase();
      if (!ticker) {
        toast("Type a ticker to compare, for example QQQ.");
        compareInput.focus();
        return;
      }
      compareInput.value = "";
      addSignal(`px:${ticker}`);
    }

    function removeSignal(id) {
      state.signals = state.signals.filter((signal) => signal.id !== id);
      save();
      drawSignalChips();
      draw();
    }

    // ---------------------------------------------------------------- data load
    async function loadSignals() {
      const ids = [...new Set([...state.signals.map((signal) => signal.id), ...state.shades.map((rule) => rule.signal)])];
      const intraday = state.interval === "15m" || state.interval === "1h";
      if (!ids.length || intraday || !data) {
        aligned = { signals: {} };
        return;
      }
      try {
        aligned = await api.get(`/api/signals/aligned${query({ symbol: data.symbol, interval: state.interval, range: data.range, ids: ids.join(",") })}`);
      } catch (error) {
        toast(error.message, "error");
        aligned = { signals: {} };
      }
      const failed = state.signals.filter((signal) => aligned.signals[signal.id]?.error);
      for (const signal of failed) toast(aligned.signals[signal.id].error, "error");
      if (failed.length) {
        state.signals = state.signals.filter((signal) => !aligned.signals[signal.id]?.error);
        save();
      }
    }

    async function refreshMarks() {
      const tasks = [];
      if (state.marks.gamma && !levels) {
        tasks.push(api.get(`/api/options/${encodeURIComponent(data.symbol)}/levels`).then((result) => { levels = result; }).catch(() => { levels = { available: false }; }));
      }
      if (state.marks.earnings && !earnings) {
        tasks.push(api.get(`/api/stock/${encodeURIComponent(data.symbol)}/fundamentals`).then((result) => { earnings = result; }).catch(() => { earnings = {}; }));
      }
      if (tasks.length) await Promise.all(tasks);
      draw();
    }

    async function load() {
      const id = ++loadId;
      chartCardEl.classList.add("is-refreshing");
      try {
        const intraday = state.interval === "15m" || state.interval === "1h";
        const extended = intraday && !!state.marks.extended;
        const next = await api.get(`/api/chart/${encodeURIComponent(state.symbol)}${query({ interval: state.interval, range: state.range, extended })}`);
        if (id !== loadId) return;
        const symbolChanged = !data || next.symbol !== data.symbol;
        data = next;
        state.symbol = next.symbol;
        state.range = next.range;
        symbolInput.value = next.symbol;
        stockLink.href = `#/stock/${encodeURIComponent(next.symbol)}`;
        // Keep the address in step, unless the user has already moved to another page.
        if (location.hash.startsWith("#/chart")) history.replaceState(null, "", `#/chart/${encodeURIComponent(next.symbol)}`);
        if (symbolChanged) {
          levels = null;
          earnings = null;
        }
        save();
        drawRange();
        drawControls();
        await loadSignals();
        if (id !== loadId) return;
        drawSignalChips();
        drawShadeList();
        await refreshMarks();
      } catch (error) {
        toast(error.message, "error");
        if (!data) {
          // The saved symbol failed, and nothing is on screen yet: fall back to SPY once.
          if (state.symbol !== "SPY") {
            state.symbol = "SPY";
            await load();
            return;
          }
          throw error;
        }
        symbolInput.value = data.symbol;
        state.symbol = data.symbol;
      } finally {
        chartCardEl.classList.remove("is-refreshing");
      }
    }

    function setSymbol(raw) {
      const symbol = String(raw || "").trim().toUpperCase();
      if (!symbol) {
        toast("Type a ticker to chart, for example NVDA.");
        symbolInput.value = data?.symbol || "";
        return;
      }
      // Loading the same symbol again simply fetches the latest bars.
      state.symbol = symbol;
      load();
    }

    function drawRange() {
      replace(rangeBox, segmented(
        data.ranges.map((value) => ({ value, label: RANGE_LABELS[value] || value })),
        data.range,
        (value) => { state.range = value; load(); },
        "Date range",
      ));
    }

    // ------------------------------------------------------------- building panes
    function paneDefinition(id) {
      const ind = data.indicators;
      const d = data;
      const c = colors();
      switch (id) {
        case "volume":
          return {
            title: "Volume", fmt: "compact", raw: true,
            series: [{
              name: "Volume",
              type: "bar",
              values: d.volume,
              barColor: (index) => (d.close[index] >= d.open[index] ? c.up : c.down),
            }],
          };
        case "rvol":
          return { title: "Relative volume", fmt: "num1", lines: [{ y: 1, label: "Normal" }, { y: 2, label: "" }], series: [{ name: "Relative volume", values: ind.rvol, key: "rvol" }] };
        case "rsi":
          return { title: "RSI (14)", fmt: "num0", min: 0, max: 100, lines: [{ y: 70, label: "70" }, { y: 30, label: "30" }], series: [{ name: "RSI", values: ind.rsi }] };
        case "macd":
          return {
            title: "MACD (12, 26, 9)", fmt: "num2",
            series: [
              { name: "MACD histogram", type: "bar", values: ind.macd_hist, polarity: true },
              { name: "MACD", values: ind.macd, slot: 0 },
              { name: "Signal", values: ind.macd_signal, slot: 1 },
            ],
          };
        case "adx":
          return {
            title: "ADX (14)", fmt: "num0", lines: [{ y: 25, label: "Strong trend" }],
            series: [
              { name: "ADX", values: ind.adx, slot: 0 },
              { name: "+DI", values: ind.plus_di, slot: 2 },
              { name: "-DI", values: ind.minus_di, slot: 1 },
            ],
          };
        case "stoch":
          return {
            title: "Stochastic (14, 3)", fmt: "num0", min: 0, max: 100, lines: [{ y: 80, label: "80" }, { y: 20, label: "20" }],
            series: [{ name: "%K", values: ind.stoch_k, slot: 0 }, { name: "%D", values: ind.stoch_d, slot: 1 }],
          };
        case "obv": return { title: "On-balance volume", fmt: "compact", series: [{ name: "OBV", values: ind.obv }] };
        case "ad": return { title: "Accumulation/Distribution", fmt: "compact", series: [{ name: "A/D line", values: ind.ad_line }] };
        case "cmf": return { title: "Chaikin Money Flow (20)", fmt: "num2", lines: [{ y: 0, label: "" }], series: [{ name: "CMF", values: ind.cmf }] };
        case "mfi": return { title: "Money Flow Index (14)", fmt: "num0", min: 0, max: 100, lines: [{ y: 80, label: "80" }, { y: 20, label: "20" }], series: [{ name: "MFI", values: ind.mfi }] };
        case "atr": return { title: "ATR as % of price", fmt: "pct1", series: [{ name: "ATR %", values: ind.atr_pct }] };
        case "hv": return { title: "Realized volatility (20)", fmt: "pct0", series: [{ name: "Realized vol", values: ind.hv20 }] };
        case "rs": return { title: "Strength vs SPY", fmt: "num3", series: [{ name: "vs SPY", values: ind.rs_spy }] };
        default: return null;
      }
    }

    function draw() {
      if (!data) return;
      const c = colors();
      const d = data;
      const n = d.dates.length;
      notices = [];
      const mode = state.mode;
      const onPrice = mode !== "panes";
      const intraday = state.interval === "15m" || state.interval === "1h";

      // Everything on the price scale passes through this conversion.
      let toScale = (value) => value;
      if (onPrice) {
        if (mode === "indexed") {
          const base = firstValue(d.close) || 1;
          toScale = (value) => (value === null || value === undefined ? null : (value / base) * 100);
        } else {
          const center = mean(d.close);
          const spread = deviation(d.close, center);
          toScale = (value) => (value === null || value === undefined ? null : (value - center) / spread);
        }
      }

      const panes = PANES.filter((item) => state.panes[item.id] && !(intraday && item.id === "rs"))
        .map((item) => paneDefinition(item.id))
        .filter((pane) => pane && pane.series.some((series) => (series.values || []).some((value) => value !== null)));

      const signalEntries = intraday ? [] : state.signals.map((signal) => ({ ...signal, entry: aligned.signals[signal.id] })).filter((item) => item.entry && !item.entry.error);
      const onPriceSignals = [];
      for (const item of signalEntries) {
        const { entry } = item;
        const hasValues = entry.values.some((value) => value !== null);
        if (!hasValues) {
          notices.push(`${entry.label} has no data in this date range${entry.first_date ? ` (its history starts ${fmtDate(entry.first_date, true)})` : ""}.`);
          continue;
        }
        if (mode === "panes" || (mode === "indexed" && !entry.positive_only)) {
          if (mode === "indexed") notices.push(`${entry.label} can be zero or negative, so it can't be indexed and stays in its own pane. Z-score mode can overlay it.`);
          panes.push({ title: entry.label, fmt: entry.fmt, series: [{ name: entry.label, values: entry.values, slot: item.slot, rawFmt: entry.fmt }] });
        } else {
          onPriceSignals.push(item);
        }
      }

      // Grid layout: taller main plot, then one band per pane, then the zoom slider.
      const legendRoom = 32;
      const mainHeight = 460;
      const paneHeight = 92;
      const paneGap = 26;
      const sliderRoom = 44;
      const height = legendRoom + mainHeight + panes.length * (paneHeight + paneGap) + sliderRoom;
      canvas.style.height = `${height}px`;

      const grids = [{ left: 22, right: 66, top: legendRoom, height: mainHeight, containLabel: false }];
      panes.forEach((_, index) => grids.push({ left: 22, right: 66, top: legendRoom + mainHeight + paneGap + index * (paneHeight + paneGap), height: paneHeight }));
      const gridIndexes = grids.map((_, index) => index);

      const dateLabel = (value) => {
        if (intraday) return `${fmtDate(value)} ${value.slice(11)}`;
        return n > 600 ? `${fmtDate(value).split(" ")[0]} '${value.slice(2, 4)}` : fmtDate(value);
      };
      const xAxes = gridIndexes.map((index) => ({
        type: "category",
        gridIndex: index,
        data: d.dates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: c.axis } },
        axisTick: { show: false },
        axisLabel: { show: index === grids.length - 1, color: c.muted, hideOverlap: true, formatter: dateLabel },
        axisPointer: { type: "line", lineStyle: { color: c.muted, width: 1, type: "solid" }, label: { show: false } },
        splitLine: { show: false },
      }));
      const axisRight = (code, extra = {}) => valueAxis(c, code, { position: "right", ...extra });
      const yAxes = [axisRight(onPrice ? (mode === "zscore" ? "num2" : "num0") : "num2", { gridIndex: 0 })];
      panes.forEach((pane, index) => yAxes.push(axisRight(pane.fmt, {
        gridIndex: index + 1,
        min: pane.min,
        max: pane.max,
        splitNumber: 2,
        axisLabel: { color: c.muted, fontSize: 10.5, formatter: (value) => axisLabel(value, pane.fmt) },
      })));

      const raw = {};
      const series = [];
      const legend = [];
      const lineSeries = (name, values, { color, width = 2, xAxisIndex = 0, yAxisIndex = 0, rawValues, rawFmt = "num2", z = 3 }) => {
        raw[name] = { values: rawValues || values, fmt: rawFmt };
        series.push({
          name,
          type: "line",
          xAxisIndex,
          yAxisIndex,
          data: values,
          showSymbol: false,
          symbol: "circle",
          symbolSize: 8,
          connectNulls: true,
          z,
          lineStyle: { width, color, cap: "round", join: "round" },
          itemStyle: { color, borderColor: c.surface, borderWidth: 2 },
          emphasis: { disabled: true },
        });
      };

      // Candles. Up days are hollow, down days solid: direction reads without color.
      const candleName = d.symbol;
      const large = n > 1800;
      const candleData = d.dates.map((_, index) => [toScale(d.open[index]), toScale(d.close[index]), toScale(d.low[index]), toScale(d.high[index])]);
      const marks = [];
      const markLines = [];
      const markAreas = [];

      if (state.marks.crosses && !intraday) {
        for (const cross of d.crosses) {
          const index = nearestDateIndex(d.dates, cross.date);
          if (index < 0) continue;
          const golden = cross.type === "golden";
          marks.push({
            coord: [d.dates[index], golden ? toScale(d.low[index]) : toScale(d.high[index])],
            value: golden ? "Golden cross" : "Death cross",
            symbol: "triangle",
            symbolSize: 11,
            symbolRotate: golden ? 0 : 180,
            symbolOffset: [0, golden ? 10 : -10],
            itemStyle: { color: golden ? c.good : c.critical, borderColor: c.surface, borderWidth: 2 },
            label: { show: false },
          });
        }
      }
      if (state.marks.gamma && levels) {
        if (levels.available) {
          const items = [
            ["Gamma flip", levels.gamma_flip, c.serious],
            ["Call wall", levels.call_wall, c.up],
            ["Put wall", levels.put_wall, c.down],
            ["Max pain", levels.max_pain, c.muted],
          ];
          // Levels almost on top of each other share one line and one label, so text stays readable.
          const merged = [];
          for (const [name, value, color] of items) {
            if (value === null || value === undefined) continue;
            const near = merged.find((item) => Math.abs(item.value - value) / value < 0.004);
            if (near) near.names.push(name);
            else merged.push({ names: [name], value, color });
          }
          for (const level of merged) {
            const text = `${level.names.join(", ")} ${fmt(level.value, "num0")}`;
            markLines.push({
              yAxis: toScale(level.value),
              name: text,
              lineStyle: { color: level.color, width: 1, type: "solid" },
              label: { formatter: text, position: "insideEndTop", color: c.ink2, fontSize: 10.5 },
            });
          }
        } else {
          notices.push(levels.reason || `No gamma levels for ${d.symbol}, as it has no listed options.`);
        }
      }
      if (state.marks.earnings && earnings && !intraday) {
        const dates = [...(earnings.earnings_history || []).map((row) => row.date)];
        if (!dates.length && !earnings.next_earnings) notices.push(`No earnings dates for ${d.symbol}.`);
        for (const iso of dates) {
          const index = nearestDateIndex(d.dates, iso);
          if (index < 0 || iso > d.dates[n - 1].slice(0, 10)) continue;
          marks.push({
            coord: [d.dates[index], toScale(d.low[index])],
            value: "E",
            symbol: "circle",
            symbolSize: 16,
            symbolOffset: [0, 16],
            itemStyle: { color: c.ink2, borderColor: c.surface, borderWidth: 2 },
            label: { show: true, formatter: "E", color: c.surface, fontSize: 10, fontWeight: 700 },
          });
        }
      }
      // Pre-market and after-hours bars sit on a light gray band.
      let extendedBars = 0;
      if (intraday && d.extended) {
        const sessions = d.sessions || [];
        let start = null;
        sessions.forEach((session, index) => {
          const outside = session !== "regular";
          if (outside) extendedBars += 1;
          if (outside && start === null) start = index;
          if ((!outside || index === sessions.length - 1) && start !== null) {
            const end = outside ? index : index - 1;
            markAreas.push([{ xAxis: d.dates[start], itemStyle: { color: c.deemph, opacity: 0.13 } }, { xAxis: d.dates[end] }]);
            start = null;
          }
        });
        if (!extendedBars) notices.push(`${d.symbol} has no pre-market or after-hours trades in this range.`);
      }
      state.shades.forEach((rule, index) => {
        const entry = aligned.signals[rule.signal];
        if (!entry || entry.error) return;
        const color = c.series[SHADE_SLOTS[index % SHADE_SLOTS.length]];
        const spans = spansFor(entry.values, rule.op, rule.value, d.dates);
        if (!spans.length) notices.push(`${entry.label} ${rule.op} ${rule.value} never happened in this date range, so nothing is shaded.`);
        for (const [start, end] of spans) {
          markAreas.push([{ xAxis: start, itemStyle: { color, opacity: 0.12 } }, { xAxis: end }]);
        }
      });

      series.push({
        name: candleName,
        type: "candlestick",
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: candleData,
        large,
        largeThreshold: 1800,
        barMaxWidth: 14,
        z: 2,
        itemStyle: {
          color: large ? c.up : c.surface,
          color0: c.down,
          borderColor: c.up,
          borderColor0: c.down,
          borderWidth: 1,
        },
        emphasis: { disabled: true },
        markPoint: marks.length ? { data: marks, silent: true, animation: false } : undefined,
        markLine: markLines.length ? { data: markLines, symbol: "none", silent: true, animation: false } : undefined,
        markArea: markAreas.length ? { data: markAreas, silent: true, animation: false } : undefined,
      });
      legend.push(candleName);

      // Price-scale overlays.
      for (const overlay of OVERLAYS) {
        if (!state.overlays[overlay.id] || (intraday && overlay.dailyOnly)) continue;
        overlay.keys.forEach((key, index) => {
          const values = d.indicators[key];
          if (!values || !values.some((value) => value !== null)) return;
          const name = overlay.names?.[index] || overlay.label;
          lineSeries(name, values.map(toScale), {
            color: overlay.muted ? c.deemph : c.series[overlay.slot],
            width: overlay.muted ? 1 : 1.5,
            rawValues: values,
            rawFmt: "num2",
          });
          legend.push(name);
        });
      }

      // Signals drawn directly over the price on the shared scale.
      for (const item of onPriceSignals) {
        const { entry } = item;
        let converted;
        if (mode === "indexed") {
          const base = firstValue(entry.values);
          converted = entry.values.map((value) => (value === null ? null : (value / base) * 100));
        } else {
          const center = mean(entry.values);
          const spread = deviation(entry.values, center);
          converted = entry.values.map((value) => (value === null ? null : (value - center) / spread));
        }
        lineSeries(entry.label, converted, { color: c.series[item.slot], rawValues: entry.values, rawFmt: entry.fmt, z: 4 });
        legend.push(entry.label);
      }

      // Lower panes.
      const titles = [];
      panes.forEach((pane, paneIndex) => {
        const axisIndex = paneIndex + 1;
        titles.push({
          text: pane.title,
          left: 18,
          top: grids[axisIndex].top - 17,
          textStyle: { fontSize: 11, fontWeight: 500, color: c.ink2 },
        });
        pane.series.forEach((item, seriesIndex) => {
          if (item.type === "bar") {
            raw[item.name] = { values: item.values, fmt: pane.fmt };
            series.push({
              name: item.name,
              type: "bar",
              xAxisIndex: axisIndex,
              yAxisIndex: axisIndex,
              barMaxWidth: 10,
              large: n > 1800,
              emphasis: { disabled: true },
              data: item.values.map((value, index) => ({
                value,
                itemStyle: { color: item.barColor ? item.barColor(index) : value >= 0 ? c.up : c.down, opacity: item.polarity ? 0.55 : 0.75 },
              })),
              markLine: seriesIndex === 0 ? thresholdMarks(pane.lines, c) : undefined,
            });
          } else {
            const color = pane.series.length === 1 && item.slot === undefined ? c.accent : c.series[item.slot ?? seriesIndex];
            lineSeries(item.name, item.values, { color, xAxisIndex: axisIndex, yAxisIndex: axisIndex, rawFmt: item.rawFmt || pane.fmt, width: 1.5 });
            series[series.length - 1].markLine = seriesIndex === 0 ? thresholdMarks(pane.lines, c) : undefined;
          }
        });
      });

      const previousZoom = handle?.chart.getOption()?.dataZoom?.[0];
      const zoomKey = `${d.symbol}|${d.interval}|${d.range}|${d.extended}`;
      const zoomKeep = previousZoom && canvas.dataset.zoomKey === zoomKey
        ? { start: previousZoom.start, end: previousZoom.end }
        : { start: 0, end: 100 };
      canvas.dataset.zoomKey = zoomKey;

      const option = {
        ...baseOption(),
        title: titles,
        grid: grids,
        xAxis: xAxes,
        yAxis: yAxes,
        axisPointer: { link: [{ xAxisIndex: "all" }] },
        legend: {
          top: 2,
          left: 8,
          right: 70,
          data: legend,
          itemWidth: 14,
          itemHeight: 3,
          icon: "roundRect",
          textStyle: { color: c.ink2, fontSize: 11 },
          type: "scroll",
          pageIconColor: c.ink2,
          pageTextStyle: { color: c.ink2 },
        },
        dataZoom: [
          { type: "inside", xAxisIndex: gridIndexes, ...zoomKeep, zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false },
          {
            type: "slider",
            xAxisIndex: gridIndexes,
            ...zoomKeep,
            height: 16,
            bottom: 2,
            showDetail: false,
            showDataShadow: false,
            brushSelect: false,
            borderColor: c.grid,
            backgroundColor: "transparent",
            fillerColor: "rgba(127,127,127,0.14)",
            handleStyle: { color: c.surface, borderColor: c.axis },
            moveHandleSize: 0,
          },
        ],
        series,
      };
      option.tooltip = {
        ...option.tooltip,
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: c.muted, width: 1, type: "solid" } },
        formatter: (items) => {
          const index = items[0].dataIndex;
          const rows = [
            { color: d.close[index] >= d.open[index] ? c.up : c.down, value: fmt(d.close[index], "num2"), name: "Close" },
            { color: c.deemph, value: `${fmt(d.open[index], "num2")} / ${fmt(d.high[index], "num2")} / ${fmt(d.low[index], "num2")}`, name: "Open / high / low" },
          ];
          for (const item of items) {
            const entry = raw[item.seriesName];
            if (!entry) continue;
            const value = entry.values[index];
            if (value === null || value === undefined) continue;
            rows.push({ color: item.color?.colorStops ? c.accent : (typeof item.color === "string" ? item.color : c.accent), value: entry.fmt === "compact" ? compact(value) : fmt(value, entry.fmt), name: item.seriesName });
          }
          const cross = d.crosses.find((event) => d.dates[index].startsWith(event.date));
          if (cross && state.marks.crosses) rows.push({ color: cross.type === "golden" ? c.good : c.critical, value: cross.type === "golden" ? "Golden cross" : "Death cross", name: "50/200 averages" });
          const session = SESSION_NAMES[d.sessions?.[index]];
          if (session) rows.push({ color: c.deemph, value: session, name: "Session" });
          return tooltipRows(axisDate(d.dates[index]), rows);
        },
      };

      if (!handle) handle = mount(canvas);
      handle.chart.setOption(option, true);
      handle.chart.resize();

      replace(titleBox, `${d.symbol}`, h("span", { class: "sub-text", style: { fontWeight: 400 } }, d.name && d.name !== d.symbol ? ` ${d.name}` : ""));
      const scaleText = mode === "indexed" ? "Everything rebased to 100 at the left edge" : mode === "zscore" ? "Everything shown as standard deviations from its own average" : "True values in each pane";
      const sessionText = extendedBars ? " Gray bands are pre-market and after-hours trading; VWAP counts regular hours only." : "";
      replace(subBox, `${INTERVALS.find((item) => item.value === d.interval)?.label} bars, last price ${fmt(d.close[n - 1], "num2")}. ${scaleText}.${sessionText}`);
      replace(noticeBox, notices.map((text) => h("div", { class: "notice" }, text)));
      if (showingTable) drawTable();
    }

    function drawTable() {
      const d = data;
      const columns = [
        { label: "Date" }, { label: "Open", fmt: "num2" }, { label: "High", fmt: "num2" }, { label: "Low", fmt: "num2" }, { label: "Close", fmt: "num2" }, { label: "Volume", fmt: "compact" },
      ];
      const extra = [];
      for (const overlay of OVERLAYS) {
        if (!state.overlays[overlay.id]) continue;
        overlay.keys.forEach((key, index) => {
          if (d.indicators[key]) extra.push({ label: overlay.names?.[index] || overlay.label, values: d.indicators[key], fmt: "num2" });
        });
      }
      for (const signal of state.signals) {
        const entry = aligned.signals[signal.id];
        if (entry && !entry.error) extra.push({ label: entry.label, values: entry.values, fmt: entry.fmt });
      }
      const all = [...columns, ...extra];
      const limit = 300;
      const n = d.dates.length;
      const rows = [];
      for (let index = n - 1; index >= Math.max(0, n - limit); index -= 1) {
        rows.push(h("tr", {}, all.map((column, position) => {
          let value;
          if (position === 0) value = SESSION_NAMES[d.sessions?.[index]] ? `${d.dates[index]} · ${SESSION_NAMES[d.sessions[index]]}` : d.dates[index];
          else if (position <= 5) value = [d.open, d.high, d.low, d.close, d.volume][position - 1][index];
          else value = extra[position - 6].values[index];
          return h("td", { class: position === 0 ? "left" : "" }, position === 0 ? value : (column.fmt === "compact" ? (value === null ? MISSING : compact(value)) : fmt(value, column.fmt)));
        })));
      }
      replace(tableBox,
        h("table", { class: "data" }, h("thead", {}, h("tr", {}, all.map((column, index) => h("th", { class: index === 0 ? "left" : "", scope: "col" }, column.label)))), h("tbody", {}, rows)),
        n > limit ? h("div", { class: "table-note" }, `Showing the latest ${limit} of ${n} bars.`) : null);
    }

    // ---------------------------------------------------------------- side panel
    catalog = await api.get("/api/signals");
    await loadCurrentValues().catch(() => null);
    fillSelect(signalSelect, [{ value: "", label: "Add a signal…" }, ...signalOptions(catalog)], "");

    const shadeRow = conditionRow({ condition: shadeDraft, options: signalOptions(catalog) });
    replace(shadeBuilder, shadeRow, h("button", {
      class: "btn btn-xs",
      type: "button",
      onclick: () => {
        if (state.shades.length >= SHADE_SLOTS.length) {
          toast(`Up to ${SHADE_SLOTS.length} shading rules at once.`);
          return;
        }
        state.shades.push({ ...shadeDraft });
        save();
        loadSignals().then(() => { drawShadeList(); draw(); });
      },
    }, "Add shading"));

    replace(signalPanel,
      panelTitle("Layer signals", "overlay_modes"),
      signalSelect,
      h("div", { style: { display: "flex", gap: "4px", marginTop: "6px" } }, compareInput, h("button", { class: "btn btn-xs", style: { height: "30px" }, type: "button", onclick: addCompare }, "Add")),
      signalChips);
    replace(shadePanel, panelTitle("Shade price when", "shading"), shadeBuilder, shadeList);

    replace(root,
      pageHead("Chart Lab", "Candles with moving averages and indicators. Layer any market signal over a stock to see how they relate."),
      filters(
        field("Symbol", h("div", { style: { display: "flex", gap: "4px" } }, symbolInput, goButton)),
        field("Bars", segmented(INTERVALS, state.interval, (value) => { state.interval = value; load(); }, "Bar interval")),
        field("Range", rangeBox),
        field("Signal display", segmented(MODES, state.mode, (value) => { state.mode = value; save(); draw(); }, "Signal display mode"), "overlay_modes"),
        h("div", { style: { alignSelf: "flex-end" } }, stockLink)),
      h("div", { class: "lab" },
        chartCardEl,
        h("div", { class: "lab-panel" },
          signalPanel,
          shadePanel,
          h("section", { class: "card" }, panelTitle("On the price"), overlaysBox, h("div", { style: { marginTop: "6px" } }, marksBox)),
          h("section", { class: "card" }, panelTitle("Lower panes"), panesBox))));

    await load();
    return {
      refresh: load,
      afterAttach: () => {
        if (handle) handle.chart.resize();
      },
    };
  },
};
