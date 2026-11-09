// Where money is moving: a rotation map, relative performance and risk-appetite ratios.
import { api, query } from "../lib/api.js";
import { baseOption, chartCard, colors, lineChart, tooltipRows } from "../lib/charts.js";
import { MISSING, fmt, fmtDate, h, prefs, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { dataTable, deltaSpan, field, fillSelect, filters, heatCell, pageHead, segmented, select, sparkline, statusBadge, symbolLink, tile } from "../lib/ui.js";

const QUADRANT_TONE = { Leading: "good", Weakening: "warning", Lagging: "critical", Improving: "info" };
const QUADRANT_NOTE = {
  Improving: "Weak, but gaining momentum",
  Leading: "Strong and getting stronger",
  Weakening: "Strong, but losing momentum",
  Lagging: "Weak and getting weaker",
};
// Same layout as the map: momentum up top, strength on the right.
const QUADRANT_GRID = ["Improving", "Leading", "Lagging", "Weakening"];

/** The usual rotation-chart colours: green leading, amber weakening, red lagging, blue improving. */
function quadrantColor(c, quadrant) {
  return { Leading: c.good, Weakening: c.warning, Lagging: c.critical, Improving: c.accent }[quadrant] || c.muted;
}

function alpha(hex, opacity) {
  const value = hex.replace("#", "");
  const full = value.length === 3 ? value.split("").map((part) => part + part).join("") : value;
  const [r, g, b] = [0, 2, 4].map((offset) => parseInt(full.slice(offset, offset + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${opacity})`;
}

function mapChart(points, highlighted, showTails, lensWord) {
  if (!points.length) return { empty: "Not enough history to place these symbols on the map yet." };
  const c = colors();
  const shown = showTails ? points.flatMap((point) => point.trail) : points.map((point) => point.trail[point.trail.length - 1]);
  // Bounds centred on 100, so the four quadrants are the same size.
  const reach = Math.ceil(Math.max(1.5, ...shown.map((p) => Math.abs(p.x - 100)), ...shown.map((p) => Math.abs(p.y - 100))) * 1.1);
  const min = 100 - reach;
  const max = 100 + reach;
  const corner = (name, position) => ({
    name,
    itemStyle: { color: alpha(quadrantColor(c, name), 0.09) },
    label: { position, color: quadrantColor(c, name), fontSize: 12.5, fontWeight: 700, distance: 8 },
  });

  const tails = [];
  const steps = [];
  const heads = [];
  for (const point of points) {
    const color = quadrantColor(c, point.quadrant);
    const hot = point.symbol === highlighted;
    const dim = highlighted && !hot ? 0.22 : 1;
    const trail = point.trail;
    const last = trail.length - 1;
    if (showTails) {
      // Older segments are thinner and fainter, so each tail shows where it came from.
      for (let i = 1; i <= last; i += 1) {
        const age = i / last;
        tails.push({
          coords: [[trail[i - 1].x, trail[i - 1].y], [trail[i].x, trail[i].y]],
          lineStyle: { color, width: (hot ? 1.6 : 1) + 1.6 * age, opacity: (0.2 + 0.65 * age) * dim, cap: "round" },
        });
      }
      for (let i = 0; i < last; i += 1) {
        steps.push({
          value: [trail[i].x, trail[i].y],
          ticker: point.symbol,
          name: point.name,
          date: trail[i].date,
          itemStyle: { color, opacity: (0.3 + 0.55 * (i / last)) * dim },
          label: hot ? { show: true, formatter: fmtDate(trail[i].date), position: "top", color: c.ink2, fontSize: 10 } : { show: false },
        });
      }
    }
    const head = trail[last];
    heads.push({
      value: [head.x, head.y],
      ticker: point.symbol,
      name: point.name,
      quadrant: point.quadrant,
      date: head.date,
      symbolSize: hot ? 15 : 11,
      itemStyle: { color, opacity: highlighted && !hot ? 0.4 : 1, borderColor: c.surface, borderWidth: 2 },
      label: { color: highlighted && !hot ? c.muted : c.ink, fontWeight: hot ? 700 : 600 },
    });
  }

  // Labels sit right of their dot, unless another dot is in the way: then left, or above.
  // Sizes are rough label dimensions in data units, for a plot about 700 pixels wide.
  const unit = (2 * reach) / 700;
  const blocked = (head, dx0, dx1, dy) => heads.some((other) => other !== head
    && other.value[0] - head.value[0] > dx0 && other.value[0] - head.value[0] < dx1
    && Math.abs(other.value[1] - head.value[1]) < dy);
  for (const head of heads) {
    const width = (10 + head.ticker.length * 7.5) * unit;
    const height = 14 * unit;
    if (!blocked(head, 0, width, height)) head.label.position = "right";
    else if (!blocked(head, -width, 0, height)) head.label.position = "left";
    else head.label.position = "top";
  }

  const series = [
    {
      type: "scatter",
      data: [],
      silent: true,
      markArea: {
        silent: true,
        data: [
          [{ coord: [min, 100], ...corner("Improving", "insideTopLeft") }, { coord: [100, max] }],
          [{ coord: [100, 100], ...corner("Leading", "insideTopRight") }, { coord: [max, max] }],
          [{ coord: [min, min], ...corner("Lagging", "insideBottomLeft") }, { coord: [100, 100] }],
          [{ coord: [100, min], ...corner("Weakening", "insideBottomRight") }, { coord: [max, 100] }],
        ],
      },
      markLine: {
        silent: true,
        symbol: "none",
        lineStyle: { color: c.axis, width: 1, type: "solid" },
        label: { show: false },
        data: [{ xAxis: 100 }, { yAxis: 100 }],
      },
    },
    { type: "lines", coordinateSystem: "cartesian2d", polyline: false, data: tails, silent: true, z: 2, animation: false },
    { type: "scatter", data: steps, symbolSize: 5, z: 3, emphasis: { disabled: true }, animation: false },
    {
      type: "scatter",
      data: heads,
      z: 5,
      label: { show: true, position: "right", distance: 5, fontSize: 11.5, formatter: (item) => item.data.ticker },
      labelLayout: { hideOverlap: false, moveOverlap: "shiftY" },
      emphasis: { scale: 1.25 },
      animation: false,
    },
  ];
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
    axisLabel: { color: c.muted, formatter: (value) => value.toFixed(0) },
  });
  const option = {
    ...baseOption(),
    grid: { left: 50, right: 30, top: 10, bottom: 40 },
    xAxis: axis("Relative strength (RS-Ratio) →"),
    yAxis: { ...axis("Momentum (RS-Momentum) →"), nameGap: 34 },
    series,
  };
  option.tooltip = {
    ...option.tooltip,
    trigger: "item",
    formatter: (item) => {
      if (!item.data || !item.data.ticker) return "";
      const isHead = !!item.data.quadrant;
      return tooltipRows(`${item.data.ticker} · ${item.data.name}`, [
        { color: c.deemph, value: isHead ? `Latest (${fmtDate(item.data.date)})` : `${fmtDate(item.data.date)}, earlier ${lensWord}`, name: "Reading" },
        ...(isHead ? [{ color: quadrantColor(c, item.data.quadrant), value: item.data.quadrant, name: "Quadrant" }] : []),
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

    const mapCard = chartCard({ title: "Rotation map", metric: "rotation_map", height: 540 });
    const quadrantBox = h("section", { class: "card quad-card" });
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
      const lensWord = state.lens === "weekly" ? "week" : "day";
      mapCard.setSubtitle(state.tail
        ? `Each dot is the latest ${lensWord}; its tail fades back through the previous ${state.tail} ${lensWord}s. Things tend to rotate clockwise. Select a dot, chip or row to follow one.`
        : `Each dot is the latest ${lensWord}. Things tend to rotate clockwise. Select a dot, chip or row to highlight it.`);
      mapCard.render(mapChart(data.points, highlighted, state.tail > 0, lensWord));
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
      const c = colors();
      const groups = { Improving: [], Leading: [], Weakening: [], Lagging: [] };
      for (const point of data.points) groups[point.quadrant].push(point);
      const pick = (symbol) => {
        highlighted = highlighted === symbol ? null : symbol;
        drawMap();
        drawQuadrants();
        drawPerformance();
      };
      replace(quadrantBox,
        h("h3", { class: "card-title", style: { marginBottom: "8px" } }, "By quadrant", info("quadrant")),
        h("div", { class: "quad-grid" }, QUADRANT_GRID.map((name) => h("div", {
          class: "quad-cell",
          style: { background: alpha(quadrantColor(c, name), 0.09), borderColor: alpha(quadrantColor(c, name), 0.35) },
        },
        h("div", { class: "quad-head" }, statusBadge(QUADRANT_TONE[name], name), h("span", { class: "sub-text" }, String(groups[name].length))),
        h("p", { class: "quadrant-note" }, QUADRANT_NOTE[name]),
        groups[name].length
          ? h("div", { class: "quad-list" }, groups[name].map((point) => h("button", {
            class: `quad-item ${point.symbol === highlighted ? "is-selected" : ""}`,
            type: "button",
            title: `Highlight ${point.name} on the map`,
            "aria-pressed": String(point.symbol === highlighted),
            onclick: () => pick(point.symbol),
          }, h("b", {}, point.symbol), h("span", { class: "sub-text" }, point.name))))
          : h("span", { class: "sub-text" }, "None right now")))));
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
      data = await api.get(`/api/rotation${query({ universe: state.universe, benchmark: state.benchmark, lens: state.lens, tail: state.tail || 4 })}`);
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
        field("Trail length", segmented([{ value: 0, label: "Off" }, { value: 4, label: "4" }, { value: 8, label: "8" }, { value: 12, label: "12" }], state.tail, (value) => {
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
