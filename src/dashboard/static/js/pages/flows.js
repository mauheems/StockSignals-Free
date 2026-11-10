// How investors are positioned: puts and calls, dark pools, surveys and futures.
import { api, query } from "../lib/api.js";
import { chartCard, divergingBars, lineChart } from "../lib/charts.js";
import { MISSING, fmt, h, prefs, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { dataTable, field, filters, heatCell, pageHead, segmented, signalTile, tile } from "../lib/ui.js";

const RANGES = [
  { value: "3m", label: "3M" }, { value: "6m", label: "6M" }, { value: "1y", label: "1Y" },
  { value: "2y", label: "2Y" }, { value: "5y", label: "5Y" }, { value: "10y", label: "10Y" },
];

function pairChart(chart, name) {
  return lineChart({
    dates: chart.dates,
    series: [
      { name: "Daily", values: chart.series.daily, muted: true, width: 1 },
      { name: "10-day average", values: chart.series.avg_10d, slot: 0 },
    ],
    yFmt: "num2",
    emptyText: `${name} history is still loading.`,
  });
}

export default {
  title: () => "Flows & Sentiment",
  async mount(root) {
    let range = prefs.get("flows.range", "1y");
    const tilesBox = h("div", { class: "grid cols-6" });
    const cnnBox = h("section", { class: "card" });
    const cards = {
      equity: chartCard({ title: "Equity put/call ratio", metric: "pc_equity", subtitle: "High means fear, low means call-buying confidence", height: 220 }),
      total: chartCard({ title: "Total put/call ratio", metric: "pc_total", subtitle: "All Cboe option volume", height: 220 }),
      index: chartCard({ title: "Index put/call ratio", metric: "pc_index", subtitle: "Mostly institutional hedging", height: 220 }),
      dix: chartCard({ title: "Dark Index (DIX)", metric: "dix", subtitle: "Apparent dark-pool buying pressure in S&P 500 stocks", height: 230 }),
      gex: chartCard({ title: "Market gamma exposure (GEX)", metric: "sm_gex", subtitle: "SqueezeMetrics, dollars per 1-point S&P 500 move", height: 230 }),
      fear: chartCard({ title: "CNN Fear & Greed Index", metric: "fear_greed", height: 230 }),
      aaii: chartCard({ title: "AAII sentiment survey", metric: "aaii_bull", subtitle: "Share of individual investors who are bullish vs bearish", height: 230 }),
      cot: chartCard({ title: "S&P futures positioning (COT)", metric: "cot_am_net", subtitle: "Net contracts, weekly. Asset managers vs leveraged funds", height: 230 }),
    };

    const draw = (data) => {
      const rows = data.rows;
      replace(tilesBox,
        signalTile(rows.crowd_fear),
        signalTile(rows.pc_equity),
        signalTile(rows.pc_equity_10d),
        signalTile(rows.pc_total),
        signalTile(rows.pc_index),
        signalTile(rows.dix),
        signalTile(rows.sm_gex),
        signalTile(rows.spx_net_gex),
        signalTile(rows.fear_greed),
        signalTile(rows.aaii_spread),
        signalTile(rows.cot_am_net, { fmtCode: "compact", deltaFmt: "compact" }),
        signalTile(rows.cot_lev_net, { fmtCode: "compact", deltaFmt: "compact" }));

      const cnn = data.cnn;
      if (cnn) {
        const previous = [["Previous close", cnn.previous_close], ["1 week ago", cnn.previous_1_week], ["1 month ago", cnn.previous_1_month], ["1 year ago", cnn.previous_1_year]];
        replace(cnnBox,
          h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "Fear & Greed components", info("fear_greed")),
          h("div", { class: "grid grid-2" },
            h("div", {},
              h("div", { class: "tile-value", style: { marginBottom: "2px" } }, `${fmt(cnn.score, "num0")} · ${cnn.rating}`),
              previous.map(([label, value]) => h("div", { class: "kv-row" }, h("span", { class: "kv-label" }, label), h("span", { class: "kv-value" }, fmt(value, "num0"))))),
            dataTable({
              columns: [
                { key: "label", label: "Component", left: true },
                { key: "score", label: "Score", render: (row) => {
                  const cell = heatCell(row.score - 50, 50, "num0");
                  cell.textContent = fmt(row.score, "num0");
                  return cell;
                } },
                { key: "rating", label: "Reading", render: (row) => row.rating ?? MISSING },
              ],
              rows: cnn.components,
              empty: "No component scores available.",
            })));
      } else {
        replace(cnnBox, tile({ label: "Fear & Greed components", metric: "fear_greed", valueText: MISSING, foot: h("span", {}, "Still loading") }));
      }

      const charts = data.charts;
      cards.equity.render(pairChart(charts.pc_equity, "Equity put/call"));
      cards.total.render(pairChart(charts.pc_total, "Total put/call"));
      cards.index.render(pairChart(charts.pc_index, "Index put/call"));
      cards.dix.render(lineChart({
        dates: charts.dix.dates,
        series: [
          { name: "DIX", values: charts.dix.series.dix, muted: true, width: 1 },
          { name: "20-day average", values: charts.dix.series.avg_20d, slot: 0 },
        ],
        yFmt: "pct1",
      }));
      cards.gex.render(divergingBars({ dates: charts.gex.dates, name: "GEX", values: charts.gex.values, yFmt: "usdbn" }));
      cards.fear.render(lineChart({
        dates: charts.fear_greed.dates,
        series: [{ name: "Fear & Greed", values: charts.fear_greed.values, area: true }],
        yFmt: "num0",
        min: 0,
        max: 100,
        thresholds: [{ y: 75, label: "Extreme greed above" }, { y: 25, label: "Extreme fear below" }],
      }));
      cards.aaii.render(lineChart({
        dates: charts.aaii.dates,
        series: [
          { name: "Bullish", values: charts.aaii.series.bull, slot: 0 },
          { name: "Bearish", values: charts.aaii.series.bear, slot: 7 },
        ],
        yFmt: "pct0",
        cadence: "weekly",
      }));
      cards.cot.render(lineChart({
        dates: charts.cot.dates,
        series: [
          { name: "Asset managers", short: "Managers", values: charts.cot.series.asset_managers, slot: 0 },
          { name: "Leveraged funds", short: "Lev. funds", values: charts.cot.series.leveraged_funds, slot: 1 },
        ],
        yFmt: "compact",
        cadence: "weekly",
      }));
    };

    const load = async () => {
      Object.values(cards).forEach((card) => card.refreshing());
      draw(await api.get(`/api/flows${query({ range })}`));
    };

    replace(root,
      pageHead("Flows & sentiment", "How investors are positioned. Extreme fear or greed tends to read the opposite way, and most of these work better as context than as timing signals."),
      filters(field("Period", segmented(RANGES, range, (value) => {
        range = value;
        prefs.set("flows.range", value);
        load().catch((error) => console.warn(error));
      }, "Chart period"))),
      tilesBox,
      h("div", { class: "section-title" }, "Puts and calls"),
      h("div", { class: "grid grid-3" }, cards.equity.el, cards.total.el, cards.index.el),
      h("div", { class: "section-title" }, "Dark pools and dealer gamma"),
      h("div", { class: "grid grid-2" }, cards.dix.el, cards.gex.el),
      h("div", { class: "section-title" }, "Surveys and positioning"),
      h("div", { class: "grid grid-3" }, cards.fear.el, cards.aaii.el, cards.cot.el),
      h("div", { class: "mt" }, cnnBox));

    await load();
    return { refresh: load };
  },
};
