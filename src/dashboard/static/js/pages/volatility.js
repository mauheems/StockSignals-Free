// How much movement the market expects, how much it's getting, and how stressed that is.
import { api, query } from "../lib/api.js";
import { categoryLines, chartCard, divergingBars, lineChart } from "../lib/charts.js";
import { h, prefs, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { dataTable, field, filters, heatCell, pageHead, segmented, signalTile, sparkline, symbolLink, tile } from "../lib/ui.js";

const RANGES = [
  { value: "3m", label: "3M" }, { value: "6m", label: "6M" }, { value: "1y", label: "1Y" },
  { value: "2y", label: "2Y" }, { value: "5y", label: "5Y" }, { value: "max", label: "Max" },
];

const TABLE_COLUMNS = [
  { key: "name", label: "Volatility index", left: true, render: (row) => h("span", { style: { display: "inline-flex", gap: "5px", alignItems: "center" } }, symbolLink(row.symbol), h("span", { class: "sub-text" }, row.name), info(row.metric)) },
  { key: "last", label: "Level", fmt: "num1" },
  { key: "chg_1d", label: "1 day", render: (row) => heatCell(row.chg_1d, 15) },
  { key: "chg_1m", label: "1 month", render: (row) => heatCell(row.chg_1m, 40) },
  { key: "pctile_1y", label: "1-year rank", metric: "vix_pctile", render: (row) => `${Math.round(row.pctile_1y)}%` },
  { key: "spark", label: "Trend", sortable: false, render: (row) => h("div", { class: "spark-cell" }, sparkline(row.spark, { width: 80, height: 20 })) },
];

export default {
  title: () => "Volatility",
  async mount(root) {
    let range = prefs.get("volatility.range", "1y");
    const tilesBox = h("div", { class: "grid cols-5" });
    const tableBox = h("section", { class: "card" });
    const cards = {
      term: chartCard({ title: "VIX term structure", metric: "term_structure_chart", subtitle: "Expected S&P 500 volatility by horizon", height: 240 }),
      vix: chartCard({ title: "VIX vs realized volatility", metric: "vrp", subtitle: "What options price vs what the S&P 500 actually did", height: 240 }),
      ratio: chartCard({ title: "VIX / VIX3M", metric: "vix_term", subtitle: "Above 1 means the curve is inverted: active stress", height: 210 }),
      vrp: chartCard({ title: "Volatility risk premium", metric: "vrp", subtitle: "VIX minus realized volatility. Below zero is unusual", height: 210 }),
      vvix: chartCard({ title: "VVIX", metric: "vvix", subtitle: "Volatility of the VIX itself", height: 210 }),
      skew: chartCard({ title: "SKEW", metric: "skew", subtitle: "Demand for crash protection", height: 210 }),
      move: chartCard({ title: "MOVE", metric: "move", subtitle: "Treasury market volatility", height: 210 }),
    };

    const draw = (data) => {
      const rows = data.rows;
      replace(tilesBox,
        signalTile(rows.vix),
        signalTile(rows.vix_pctile),
        signalTile(rows.vix_term),
        signalTile(rows.vix9d_ratio),
        tile({ label: "Implied daily move", metric: "implied_daily_move", value: data.implied_daily_move, fmtCode: "pct2", foot: h("span", {}, "From the VIX") }),
        signalTile(rows.rv20),
        signalTile(rows.vrp),
        signalTile(rows.vvix),
        signalTile(rows.skew),
        signalTile(rows.move));
      replace(tableBox,
        h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "Volatility across assets"),
        dataTable({ id: "volatility", columns: TABLE_COLUMNS, rows: data.table, empty: "Volatility indices are still loading." }));

      const curve = data.term_structure;
      cards.term.render(categoryLines({
        categories: curve.today.map((point) => point.label),
        series: [
          { name: "Today", values: curve.today.map((point) => point.value), slot: 0 },
          { name: "1 week ago", values: curve.week_ago.map((point) => point.value), slot: 1 },
          { name: "1 month ago", values: curve.month_ago.map((point) => point.value), muted: true },
        ],
        yFmt: "num1",
        xName: "Horizon",
      }));
      const charts = data.charts;
      cards.vix.render(lineChart({
        dates: charts.vix_rv.dates,
        series: [
          { name: "VIX", values: charts.vix_rv.series.vix, slot: 0 },
          { name: "Realized (20-day)", short: "Realized", values: charts.vix_rv.series.rv20, slot: 1 },
        ],
        yFmt: "num1",
      }));
      cards.ratio.render(lineChart({ dates: charts.term_ratio.dates, series: [{ name: "VIX / VIX3M", values: charts.term_ratio.values }], yFmt: "num2", thresholds: [{ y: 1, label: "Inverted above" }] }));
      cards.vrp.render(divergingBars({ dates: charts.vrp.dates, name: "Risk premium", values: charts.vrp.values, yFmt: "num1" }));
      cards.vvix.render(lineChart({ dates: charts.vvix.dates, series: [{ name: "VVIX", values: charts.vvix.values }], yFmt: "num0" }));
      cards.skew.render(lineChart({ dates: charts.skew.dates, series: [{ name: "SKEW", values: charts.skew.values }], yFmt: "num0" }));
      cards.move.render(lineChart({ dates: charts.move.dates, series: [{ name: "MOVE", values: charts.move.values }], yFmt: "num0" }));
    };

    const load = async () => {
      Object.values(cards).forEach((card) => card.refreshing());
      draw(await api.get(`/api/volatility${query({ range })}`));
    };

    replace(root,
      pageHead("Volatility", `How much movement the market expects vs how much it's getting. Rising short-term fear usually shows up here first. Quick rule: VIX ÷ 16 is roughly the expected daily S&P 500 move.`),
      filters(field("Period", segmented(RANGES, range, (value) => {
        range = value;
        prefs.set("volatility.range", value);
        load().catch((error) => console.warn(error));
      }, "Chart period"))),
      tilesBox,
      h("div", { class: "grid grid-2 mt" }, cards.term.el, cards.vix.el),
      h("div", { class: "grid grid-3 mt" }, cards.ratio.el, cards.vrp.el, cards.vvix.el),
      h("div", { class: "grid grid-2 mt" }, cards.skew.el, cards.move.el),
      h("div", { class: "mt" }, tableBox));

    await load();
    return { refresh: load };
  },
};
