// Where money is moving: a rotation map, relative performance and risk-appetite ratios.
import { api, query } from "../lib/api.js";
import { baseOption, chartCard, colors, lineChart, tooltipRows } from "../lib/charts.js";
import { MISSING, fmt, h, prefs, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { dataTable, deltaSpan, field, fillSelect, filters, heatCell, pageHead, segmented, select, sparkline, statusBadge, symbolLink, tile } from "../lib/ui.js";

const QUADRANT_TONE = { Leading: "good", Weakening: "warning", Lagging: "critical", Improving: "neutral" };
const QUADRANT_NOTE = {
  Improving: "Weak, but gaining momentum",
  Leading: "Strong and getting stronger",
  Weakening: "Strong, but losing momentum",
  Lagging: "Weak and getting weaker",
};

function mapChart(points, highlighted) {
  if (!points.length) return { empty: "Not enough history to place these symbols on the map yet." };
  const c = colors();
  const all = points.flatMap((point) => point.trail);
  // Whole-number bounds, centred on 100, so the quadrants stay equal and the ticks clean.
  const reach = Math.ceil(Math.max(1.5, ...all.map((p) => Math.abs(p.x - 100)), ...all.map((p) => Math.abs(p.y - 100))) * 1.08);
  const min = 100 - reach;
  const max = 100 + reach;
  const wash = (color) => `color-mix(in srgb, ${color} 8%, transparent)`;

  // Many tails at once: all in gray with direct labels at the head, one highlighted on hover.
  const series = [{
    type: "scatter",
    data: [],
    silent: true,
    markArea: {
      silent: true,
      label: { show: true, position: "insideTopLeft", color: c.muted, fontSize: 11 },
      data: [
        [{ coord: [100, 100], name: "Leading", itemStyle: { color: wash(c.up) } }, { coord: [max, max] }],
        [{ coord: [100, min], name: "Weakening", label: { position: "insideBottomRight" }, itemStyle: { color: wash(c.warning) } }, { coord: [max, 100] }],
        [{ coord: [min, min], name: "Lagging", label: { position: "insideBottomLeft" }, itemStyle: { color: wash(c.down) } }, { coord: [100, 100] }],
        [{ coord: [min, 100], name: "Improving", label: { position: "insideTopRight" }, itemStyle: { color: wash(c.muted) } }, { coord: [100, max] }],
      ],
    },
    markLine: {
      silent: true,
      symbol: "none",
      lineStyle: { color: c.axis, width: 1, type: "solid" },
      label: { show: false },
      data: [{ xAxis: 100 }, { yAxis: 100 }],
    },
  }];
  for (const point of points) {
    const isHot = point.symbol === highlighted;
    const color = isHot ? c.accent : c.deemph;
    series.push({
      name: point.symbol,
      type: "line",
      data: point.trail.map((p) => [p.x, p.y, p.date]),
      showSymbol: false,
      symbol: "circle",
      symbolSize: 4,
      z: isHot ? 5 : 2,
      lineStyle: { width: isHot ? 2 : 1, color, opacity: isHot || !highlighted ? 1 : 0.45 },
      itemStyle: { color },
      emphasis: { disabled: true },
      silent: true,
    });
    const head = point.trail[point.trail.length - 1];
    series.push({
      name: point.symbol,
      type: "scatter",
      data: [{ value: [head.x, head.y], name: point.name, ticker: point.symbol, quadrant: point.quadrant, date: head.date }],
      symbolSize: isHot ? 12 : 9,
      z: isHot ? 6 : 3,
      itemStyle: { color: isHot ? c.accent : c.ink2, borderColor: c.surface, borderWidth: 2 },
      label: { show: true, position: "right", distance: 4, color: c.ink, fontSize: 11, fontWeight: isHot ? 650 : 500, formatter: () => point.symbol },
      labelLayout: { hideOverlap: false, moveOverlap: "shiftY" },
      emphasis: { scale: 1.3 },
    });
  }
  const axis = (name) => ({
    type: "value",
    name,
    nameLocation: "middle",
    nameGap: 26,
    nameTextStyle: { color: c.ink2 },
    min,
    max,
    interval: reach > 4 ? 2 : 1,
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: { show: true, lineStyle: { color: c.grid, width: 1, type: "solid" } },
    axisLabel: { color: c.muted, formatter: (value) => value.toFixed(1) },
  });
  const option = {
    ...baseOption(),
    grid: { left: 54, right: 26, top: 10, bottom: 40 },
    xAxis: axis("Relative strength (RS-Ratio)"),
    yAxis: { ...axis("Relative momentum (RS-Momentum)"), nameGap: 40 },
    series,
  };
  option.tooltip = {
    ...option.tooltip,
    trigger: "item",
    formatter: (item) => {
      if (!item.data || !item.data.ticker) return "";
      return tooltipRows(`${item.data.ticker} · ${item.data.name}`, [
        { color: c.accent, value: item.data.quadrant, name: "Quadrant" },
        { color: c.deemph, value: item.data.value[0].toFixed(2), name: "RS-Ratio" },
        { color: c.deemph, value: item.data.value[1].toFixed(2), name: "RS-Momentum" },
      ]);
    },
  };
  return {
    option,
    table: {
      columns: [{ label: "Symbol" }, { label: "Name" }, { label: "Quadrant" }, { label: "RS-Ratio", fmt: "num2" }, { label: "RS-Momentum", fmt: "num2" }],
      rows: points.map((point) => [point.symbol, point.name, point.quadrant, point.rs_ratio, point.rs_momentum]),
    },
  };
}

function performanceColumns(mode, benchmark) {
  const prefix = mode === "relative" ? "rel_" : "ret_";
  const scales = { "1d": 3, "5d": 6, "1m": 12, "3m": 20, "6m": 30, ytd: 35, "1y": 45 };
  const labels = { "1d": "1 day", "5d": "1 week", "1m": "1 month", "3m": "3 months", "6m": "6 months", ytd: "YTD", "1y": "1 year" };
  const periods = Object.keys(labels).map((period) => ({
    key: `${prefix}${period}`,
    label: labels[period],
    metric: period === "1d" ? "relative_performance" : undefined,
    render: (row) => heatCell(row[`${prefix}${period}`], scales[period]),
  }));
  return [
    { key: "name", label: mode === "relative" ? `vs ${benchmark}` : "Name", left: true, render: (row) => h("span", {}, symbolLink(row.symbol), " ", h("span", { class: "sub-text" }, row.name)) },
    ...periods,
    { key: "quadrant", label: "Quadrant", metric: "quadrant", render: (row) => (row.quadrant ? statusBadge(QUADRANT_TONE[row.quadrant], row.quadrant) : MISSING) },
    { key: "above_50", label: "50-day", metric: "sma", value: (row) => (row.above_50 === null ? null : Number(row.above_50)), render: (row) => (row.above_50 === null ? MISSING : row.above_50 ? "Above" : "Below") },
    { key: "above_200", label: "200-day", metric: "sma", value: (row) => (row.above_200 === null ? null : Number(row.above_200)), render: (row) => (row.above_200 === null ? MISSING : row.above_200 ? "Above" : "Below") },
    { key: "rvol", label: "Rel. volume", metric: "rvol", render: (row) => (row.rvol === null ? MISSING : `${row.rvol.toFixed(1)}x`) },
    { key: "breadth_pct50", label: "Members above 50-day", metric: "pct_above_50", render: (row) => fmt(row.breadth_pct50, "pct0") },
  ];
}

function ratioTile(ratio) {
  const on = ratio.state === "risk_on";
  return h("div", { class: "tile", dataset: { ratio: ratio.id } },
    h("div", { class: "tile-label" }, ratio.label, info(ratio.metric)),
    h("div", { style: { display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "6px" } },
      statusBadge(on ? "good" : "critical", on ? "Risk-on" : "Risk-off"),
      h("span", { class: "sub-text mono" }, deltaSpan(`${Math.abs(ratio.chg_1m ?? 0).toFixed(1)}% 1m`, ratio.chg_1m, "good"))),
    sparkline(ratio.spark),
    h("div", { class: "tile-foot" }, h("span", {}, `${ratio.vs_sma50 >= 0 ? "+" : ""}${ratio.vs_sma50.toFixed(1)}% vs 50d`), h("span", {}, `${ratio.a} / ${ratio.b}`)));
}

export default {
  title: () => "Rotation",
  async mount(root) {
    const state = { ...{ universe: "sectors", benchmark: "SPY", lens: "weekly", tail: 4, mode: "relative" }, ...prefs.get("rotation", {}) };
    let data = null;
    let highlighted = null;

    const mapCard = chartCard({ title: "Rotation map", metric: "rotation_map", subtitle: "Things tend to rotate clockwise. Select a dot, chip or table row to highlight its trail", height: 520 });
    const quadrantBox = h("section", { class: "card" });
    const performanceBox = h("section", { class: "card" });
    const ratiosBox = h("div", { class: "grid cols-5" });
    const riskBox = h("div", { class: "split-3-1" });
    const riskChart = chartCard({ title: "Risk-on share over the past year", metric: "risk_on_share", height: 130 });

    const universeSelect = select([{ value: state.universe, label: state.universe }], state.universe, (value) => {
      state.universe = value;
      changed();
    }, { "aria-label": "Universe" });

    let mapClicks = false;
    const drawMap = () => {
      mapCard.render(mapChart(data.points, highlighted));
      if (!mapClicks && mapCard.chart) {
        mapClicks = true;
        mapCard.chart.on("click", (event) => {
          const ticker = event.data?.ticker;
          if (!ticker) return;
          highlighted = highlighted === ticker ? null : ticker;
          drawMap();
          drawQuadrants();
          drawPerformance();
        });
      }
    };

    const drawQuadrants = () => {
      const groups = { Improving: [], Leading: [], Weakening: [], Lagging: [] };
      for (const point of data.points) groups[point.quadrant].push(point);
      replace(quadrantBox,
        h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "By quadrant", info("quadrant")),
        Object.entries(groups).map(([name, points]) => h("div", { style: { marginBottom: "8px" } },
          statusBadge(QUADRANT_TONE[name], name),
          h("p", { class: "quadrant-note" }, QUADRANT_NOTE[name]),
          h("div", { class: "chips", style: { marginTop: "3px" } },
            points.length ? points.map((point) => h("button", {
              class: "chip",
              type: "button",
              title: `Highlight ${point.name}`,
              style: { cursor: "pointer", fontWeight: point.symbol === highlighted ? "700" : "500" },
              onclick: () => {
                highlighted = highlighted === point.symbol ? null : point.symbol;
                drawMap();
                drawQuadrants();
              },
            }, point.symbol)) : h("span", { class: "sub-text" }, "None")))));
    };

    const drawPerformance = () => {
      replace(performanceBox,
        h("div", { class: "card-head" },
          h("h3", { class: "card-title" }, `Performance: ${data.universe_label}`),
          segmented([{ value: "relative", label: "Relative" }, { value: "absolute", label: "Absolute" }], state.mode, (value) => {
            state.mode = value;
            prefs.set("rotation", state);
            drawPerformance();
          }, "Performance mode")),
        dataTable({
          columns: performanceColumns(state.mode, data.benchmark),
          rows: data.table,
          rowKey: (row) => row.symbol,
          selectedKey: highlighted,
          sort: { key: `${state.mode === "relative" ? "rel" : "ret"}_1m`, dir: "desc" },
          empty: "No symbols in this universe yet.",
          onRow: (row) => {
            highlighted = highlighted === row.symbol ? null : row.symbol;
            drawMap();
            drawQuadrants();
            drawPerformance();
          },
        }));
    };

    const draw = () => {
      fillSelect(universeSelect, data.universes.map((item) => ({ value: item.id, label: item.label })), state.universe);
      drawMap();
      drawQuadrants();
      drawPerformance();
      replace(ratiosBox, data.ratios.map(ratioTile));
      riskChart.render(lineChart({ dates: data.risk_on_history.dates, series: [{ name: "Risk-on share", values: data.risk_on_history.values, area: true }], yFmt: "pct0", min: 0, max: 100 }));
      const row = data.risk_on;
      replace(riskBox,
        tile({
          label: "Risk-on ratios above their 50-day average",
          metric: "risk_on_share",
          value: row?.value,
          fmtCode: "pct0",
          delta: row && row.change_1w !== null ? deltaSpan(`${Math.abs(row.change_1w).toFixed(0)} points in 1 week`, row.change_1w, "good") : null,
          foot: h("span", {}, `${data.ratios.filter((item) => item.state === "risk_on").length} of ${data.ratios.length} ratios are risk-on`),
        }),
        riskChart.el);
    };

    const load = async () => {
      mapCard.refreshing();
      data = await api.get(`/api/rotation${query({ universe: state.universe, benchmark: state.benchmark, lens: state.lens, tail: state.tail })}`);
      if (highlighted && !data.points.some((point) => point.symbol === highlighted)) highlighted = null;
      draw();
    };

    function changed() {
      prefs.set("rotation", state);
      load().catch((error) => console.warn(error));
    }

    replace(root,
      pageHead("Rotation", "Which parts of the market are gaining or losing strength against a benchmark, and whether investors are reaching for risk or hiding from it."),
      filters(
        field("Universe", universeSelect),
        field("Benchmark", select(["SPY", "QQQ", "IWM", "RSP"].map((value) => ({ value, label: value })), state.benchmark, (value) => {
          state.benchmark = value;
          changed();
        }, { "aria-label": "Benchmark" })),
        field("Lens", segmented([{ value: "weekly", label: "Weekly" }, { value: "daily", label: "Daily" }], state.lens, (value) => {
          state.lens = value;
          changed();
        }, "Lens")),
        field("Trail length", segmented([{ value: 4, label: "4" }, { value: 8, label: "8" }, { value: 12, label: "12" }], state.tail, (value) => {
          state.tail = value;
          changed();
        }, "Trail length"))),
      h("div", { class: "split-2-1" }, mapCard.el, quadrantBox),
      h("div", { class: "mt" }, performanceBox),
      h("div", { class: "section-title" }, "Risk appetite ", info("risk_ratios"), h("span", { class: "section-sub" }, "All ratios are set so that rising means more risk-taking")),
      riskBox,
      h("div", { class: "mt" }, ratiosBox));

    await load();
    return { refresh: load };
  },
};
