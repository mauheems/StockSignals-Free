// Trend of the major indices and how many stocks are actually participating.
import { api, query } from "../lib/api.js";
import { chartCard, divergingBars, lineChart } from "../lib/charts.js";
import { MISSING, fmt, fmtDate, h, prefs, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { dataTable, field, filters, heatCell, pageHead, segmented, signalTile, statusBadge, symbolLink, tile } from "../lib/ui.js";

const RANGES = [
  { value: "3m", label: "3M" }, { value: "6m", label: "6M" }, { value: "1y", label: "1Y" },
  { value: "2y", label: "2Y" }, { value: "5y", label: "5Y" },
];

const INDEX_COLUMNS = [
  { key: "name", label: "Index", left: true, render: (row) => h("span", {}, symbolLink(row.symbol), " ", h("span", { class: "sub-text" }, row.name)) },
  { key: "last", label: "Last", fmt: "num0" },
  { key: "change_pct", label: "Today", metric: "period_returns", render: (row) => heatCell(row.change_pct, 3, "spct2") },
  { key: "ret_1m", label: "1 month", render: (row) => heatCell(row.ret_1m, 12) },
  { key: "ret_ytd", label: "YTD", render: (row) => heatCell(row.ret_ytd, 25) },
  { key: "sma50", label: "50-day", metric: "sma", fmt: "num0" },
  { key: "vs_sma50", label: "vs 50-day", metric: "vs_sma", fmt: "spct1" },
  { key: "sma200", label: "200-day", metric: "sma", fmt: "num0" },
  { key: "vs_sma200", label: "vs 200-day", metric: "vs_sma", fmt: "spct1" },
  { key: "trend", label: "Trend", metric: "trend_state", value: (row) => row.trend.label, render: (row) => statusBadge(row.trend.tone, row.trend.label) },
  {
    key: "cross", label: "Last 50/200 cross", metric: "golden_cross",
    value: (row) => row.last_cross?.date,
    render: (row) => (row.last_cross ? `${row.last_cross.type === "golden" ? "Golden" : "Death"}, ${fmtDate(row.last_cross.date, true)}` : MISSING),
  },
  { key: "rsi", label: "RSI", metric: "rsi", fmt: "num0" },
  { key: "off_high", label: "From 52w high", metric: "off_high", fmt: "spct1" },
];

function sectorColumns() {
  const pct = (key, label, metric) => ({
    key,
    label,
    metric,
    // Above 50% participation is shaded as strength, below as weakness.
    render: (row) => (row[key] === null || row[key] === undefined ? MISSING : heatLabel(row[key])),
  });
  return [
    { key: "sector", label: "Sector", left: true },
    { key: "members", label: "Members", fmt: "num0" },
    pct("pct20", "Above 20-day", "pct_above_20"),
    pct("pct50", "Above 50-day", "pct_above_50"),
    { key: "chg50", label: "1-week change", value: (row) => (row.pct50 === null || row.pct50_1w === null ? null : row.pct50 - row.pct50_1w), render: (row) => fmt(row.pct50 === null || row.pct50_1w === null ? null : row.pct50 - row.pct50_1w, "snum0") },
    pct("pct200", "Above 200-day", "pct_above_200"),
    { key: "advancing", label: "Up today", metric: "adv_dec", fmt: "num0" },
    { key: "declining", label: "Down today", metric: "adv_dec", fmt: "num0" },
  ];
}

function heatLabel(value) {
  const cell = heatCell(value - 50, 50, "num0");
  cell.textContent = fmt(value, "pct0");
  return cell;
}

export default {
  title: () => "Trend & Breadth",
  async mount(root) {
    let range = prefs.get("breadth.range", "1y");
    const tilesBox = h("div", { class: "grid cols-5" });
    const indexBox = h("div", { class: "card" });
    const sectorBox = h("div", { class: "card" });

    const cards = {
      spx: chartCard({ title: "S&P 500 with 50- and 200-day averages", metric: "sma", height: 240 }),
      pct: chartCard({ title: "Stocks above their moving averages", metric: "pct_above_50", subtitle: "Share of S&P 500 members", height: 240 }),
      mcclellan: chartCard({ title: "McClellan Oscillator", metric: "mcclellan", subtitle: "Breadth momentum. Above zero is improving", height: 210 }),
      netHighs: chartCard({ title: "Net new 52-week highs", metric: "net_new_highs", subtitle: "New highs minus new lows, daily", height: 210 }),
      adLine: chartCard({ title: "Advance/Decline line", metric: "ad_line", subtitle: "Should confirm new index highs", height: 210 }),
      hlIndex: chartCard({ title: "High-Low Index", metric: "hl_index", subtitle: "10-day smoothed share of new highs", height: 210 }),
      upVolume: chartCard({ title: "Up-volume share", metric: "up_vol_share", subtitle: "Share of volume in rising stocks", height: 210 }),
      rsp: chartCard({ title: "Equal weight vs cap weight", metric: "rsp_spy", subtitle: "RSP / SPY with its 50-day average. Rising means the rally is broadening", height: 210 }),
    };

    const draw = (data) => {
      const rows = data.rows;
      const advance = data.advancing !== null ? `${fmt(data.advancing, "num0")} / ${fmt(data.declining, "num0")}` : MISSING;
      const highs = data.new_highs !== null ? `${fmt(data.new_highs, "num0")} / ${fmt(data.new_lows, "num0")}` : MISSING;
      replace(tilesBox,
        signalTile(rows.pct_above_20),
        signalTile(rows.pct_above_50),
        signalTile(rows.pct_above_200),
        signalTile(rows.mcclellan),
        signalTile(rows.net_highs_10d),
        tile({ label: "Advancers / decliners", metric: "adv_dec", valueText: advance, foot: h("span", {}, "Latest session, S&P 500") }),
        tile({ label: "New highs / new lows", metric: "net_new_highs", valueText: highs, foot: h("span", {}, "Latest session, S&P 500") }),
        signalTile(rows.up_vol_share),
        tile({
          label: "Breadth thrust gauge",
          metric: "breadth_thrust",
          value: data.thrust.value,
          fmtCode: "pct1",
          foot: h("span", {}, data.thrust.last_signal ? `Last signal ${fmtDate(data.thrust.last_signal, true)}` : "No signal in stored history"),
        }),
        signalTile(rows.rsp_spy, { deltaFmt: "snum3" }));
      replace(indexBox,
        h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "Index trend"),
        dataTable({ columns: INDEX_COLUMNS, rows: data.indices, empty: "Index prices are still loading." }));
      replace(sectorBox,
        h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "Breadth by sector"),
        dataTable({ columns: sectorColumns(), rows: data.sectors, empty: "Sector breadth appears after S&P 500 member prices load." }));

      const charts = data.charts;
      cards.spx.render(lineChart({
        dates: charts.spx.dates,
        series: [
          { name: "S&P 500", values: charts.spx.series.close, slot: 0 },
          { name: "50-day", values: charts.spx.series.sma50, slot: 1 },
          { name: "200-day", values: charts.spx.series.sma200, slot: 6 },
        ],
        yFmt: "num0",
      }));
      cards.pct.render(lineChart({
        dates: charts.pct.dates,
        series: [
          { name: "Above 20-day", values: charts.pct.series.above_20, muted: true, width: 1.5 },
          { name: "Above 50-day", values: charts.pct.series.above_50, slot: 0 },
          { name: "Above 200-day", values: charts.pct.series.above_200, slot: 1 },
        ],
        yFmt: "pct0",
        min: 0,
        max: 100,
        thresholds: [{ y: 60 }, { y: 40 }],
      }));
      cards.mcclellan.render(divergingBars({ dates: charts.mcclellan.dates, name: "McClellan Oscillator", values: charts.mcclellan.values, yFmt: "num0" }));
      cards.netHighs.render(divergingBars({ dates: charts.net_highs.dates, name: "Net new highs", values: charts.net_highs.values, yFmt: "num0" }));
      cards.adLine.render(lineChart({ dates: charts.ad_line.dates, series: [{ name: "A/D line", values: charts.ad_line.values }], yFmt: "num0" }));
      cards.hlIndex.render(lineChart({ dates: charts.hl_index.dates, series: [{ name: "High-Low Index", values: charts.hl_index.values }], yFmt: "num0", min: 0, max: 100, thresholds: [{ y: 70 }, { y: 30 }] }));
      cards.upVolume.render(lineChart({
        dates: charts.up_volume.dates,
        series: [
          { name: "Daily", values: charts.up_volume.series.daily, muted: true, width: 1 },
          { name: "10-day average", values: charts.up_volume.series.avg_10d, slot: 0 },
        ],
        yFmt: "pct0",
        min: 0,
        max: 100,
        thresholds: [{ y: 90, label: "90% up" }, { y: 10, label: "90% down" }],
      }));
      cards.rsp.render(lineChart({
        dates: charts.rsp_spy.dates,
        series: [
          { name: "RSP / SPY", values: charts.rsp_spy.series.value, slot: 0 },
          { name: "50-day average", values: charts.rsp_spy.series.average, muted: true },
        ],
        yFmt: "num3",
      }));
    };

    const load = async () => {
      Object.values(cards).forEach((card) => card.refreshing());
      draw(await api.get(`/api/breadth${query({ range })}`));
    };

    replace(root,
      pageHead("Trend & breadth", "Is the market trending, and are most stocks taking part? Calculated from S&P 500 member prices."),
      filters(field("Period", segmented(RANGES, range, (value) => {
        range = value;
        prefs.set("breadth.range", value);
        load().catch((error) => console.warn(error));
      }, "Chart period"))),
      tilesBox,
      h("div", { class: "section-title" }, "Major indices"),
      indexBox,
      h("div", { class: "section-title" }, "Participation"),
      h("div", { class: "grid grid-2" }, cards.spx.el, cards.pct.el),
      h("div", { class: "grid grid-3 mt" }, cards.mcclellan.el, cards.netHighs.el, cards.adLine.el, cards.hlIndex.el, cards.upVolume.el, cards.rsp.el),
      h("div", { class: "section-title" }, "Sectors"),
      sectorBox,
      h("p", { class: "table-note" }, "Want individual names? The ", h("a", { href: "#/scanner" }, "S&P 500 Scanner"), " ranks every member. ", info("pct_above_50")));

    await load();
    return { refresh: load };
  },
};
