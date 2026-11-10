// What traders with money at stake expect: the Fed, the economy and company earnings.
import { api } from "../lib/api.js";
import { baseOption, chartCard, colors, lineChart, tooltipRows } from "../lib/charts.js";
import { MISSING, fmt, fmtAgo, fmtDate, h, prefs, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { dataTable, emptyBox, field, pageHead, segmented, signalTile, statusBadge, symbolLink, tile } from "../lib/ui.js";

const pct = (value) => (value === null || value === undefined ? MISSING : `${Math.round(value * 100)}%`);
const traded = (volume) => `${fmt(volume, "usdcompact")} traded`;
const outside = (href, text) => h("a", { href, target: "_blank", rel: "noopener noreferrer", class: "sub-text" }, text);

/** Odds for each outcome of the next Fed meeting, as a small bar list. */
function fedCard(fed) {
  const title = (text) => h("h3", { class: "card-title" }, text, info("fed_odds"));
  if (!fed) return h("section", { class: "card" }, title("Next Fed meeting"), emptyBox("Fed odds appear once Kalshi has been read."));
  return h("section", { class: "card" },
    h("div", { class: "card-head" }, title(`Next Fed meeting, ${fmtDate(fed.date, true)}`), outside(fed.url, `${fed.source} ↗`)),
    h("div", { class: "odds-list" }, fed.outcomes.map((item) => h("div", { class: `odds-row ${item.label === fed.likely ? "is-likely" : ""}` },
      h("span", { class: "odds-label" }, item.label),
      h("div", { class: "odds-track", role: "meter", "aria-valuenow": Math.round(item.prob * 100), "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": item.label },
        h("div", { class: "odds-fill", style: { width: `${Math.max(0.5, item.prob * 100)}%` } })),
      h("span", { class: "odds-value" }, pct(item.prob))))),
    h("p", { class: "sub-text", style: { marginTop: "6px" } }, `${traded(fed.volume)} on this meeting. Odds are scaled to add up to 100%.`));
}

const PATH_COLUMNS = [
  { key: "date", label: "Meeting", left: true, render: (row) => fmtDate(row.date, true), value: (row) => row.date },
  { key: "likely_rate", label: "Most likely rate after", metric: "fed_path", render: (row) => h("span", {}, fmt(row.likely_rate, "pct2"), h("span", { class: "sub-text" }, ` (${pct(row.likely_prob)})`)) },
  { key: "expected_rate", label: "Average expectation", fmt: "pct2" },
  { key: "cut", label: "Cut", render: (row) => pct(row.cut) },
  { key: "hold", label: "No change", render: (row) => pct(row.hold) },
  { key: "hike", label: "Hike", render: (row) => pct(row.hike) },
  { key: "volume", label: "Traded", render: (row) => fmt(row.volume, "usdcompact") },
];

/** "0.3%", "0.1% or less", "above 4.0%", "2.5% to 3.0%": names for each outcome bar. */
function bucketLabel(bucket, reading, code) {
  if (reading.discrete) {
    if (bucket.lo === null) return `${fmt(bucket.hi, code)} or less`;
    if (bucket.hi === null) return `${fmt(bucket.value, code)} or more`;
    return fmt(bucket.value, code);
  }
  if (bucket.lo === null) return `below ${fmt(bucket.hi, code)}`;
  if (bucket.hi === null) return `above ${fmt(bucket.lo, code)}`;
  return `${fmt(bucket.lo, code)} to ${fmt(bucket.hi, code)}`;
}

/** Bars of the odds for each possible result of one upcoming number. */
function outcomeChart(indicator) {
  const reading = indicator.reading;
  // Older stored readings (before outcome odds were kept) have no buckets yet.
  if (!reading?.buckets?.length) return { empty: "This number has too little trading right now to show odds." };
  const c = colors();
  // Trim the long tails of near-zero odds, so the bars that matter get the room.
  let rows = reading.buckets;
  while (rows.length > 3 && rows[0].prob < 0.005) rows = rows.slice(1);
  while (rows.length > 3 && rows[rows.length - 1].prob < 0.005) rows = rows.slice(0, -1);
  const code = indicator.fmt === "compact" ? "compact" : indicator.fmt.replace(/^s/, "");
  const labels = rows.map((row) => bucketLabel(row, reading, code));
  const likely = Math.max(...rows.map((row) => row.prob));
  const option = {
    ...baseOption(),
    grid: { left: 40, right: 12, top: 18, bottom: 46 },
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: c.axis } },
      axisTick: { show: false },
      axisLabel: { color: c.muted, interval: 0, rotate: labels.length > 9 ? 35 : 0, fontSize: 10.5 },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: (value) => Math.min(100, Math.ceil(value.max / 10) * 10 + 10),
      splitNumber: 4,
      axisLabel: { color: c.muted, formatter: (value) => `${value}%` },
      splitLine: { lineStyle: { color: c.grid } },
    },
    series: [{
      type: "bar",
      name: "Odds",
      barMaxWidth: 34,
      data: rows.map((row) => ({
        value: Math.round(row.prob * 1000) / 10,
        itemStyle: { color: c.accent, opacity: row.prob === likely ? 1 : 0.55, borderRadius: [4, 4, 0, 0] },
      })),
      label: { show: true, position: "top", color: c.ink2, fontSize: 10.5, formatter: (item) => (item.value >= 2 ? `${Math.round(item.value)}%` : "") },
    }],
  };
  option.tooltip = {
    ...option.tooltip,
    trigger: "item",
    formatter: (item) => tooltipRows(`${indicator.label}: ${labels[item.dataIndex]}`, [{ color: c.accent, value: `${item.value}%`, name: "Odds" }]),
  };
  return {
    option,
    table: { columns: [{ label: "Outcome" }, { label: "Odds", fmt: "pct1" }], rows: rows.map((row, index) => [labels[index], row.prob * 100]) },
  };
}

function indicatorTile(indicator) {
  const reading = indicator.reading;
  if (!reading) return tile({ label: indicator.label, metric: indicator.metric, valueText: MISSING, foot: h("span", {}, "Too little trading right now") });
  const range = reading.low !== null && reading.high !== null && reading.low !== reading.high
    ? `likely ${fmt(reading.low, indicator.fmt)} to ${fmt(reading.high, indicator.fmt)}`
    : null;
  return tile({
    label: `${indicator.label}${reading.period ? `, ${reading.period}` : ""}`,
    metric: indicator.metric,
    value: reading.median,
    fmtCode: indicator.fmt,
    delta: range ? h("span", { class: "sub-text" }, range) : null,
    foot: h("span", {}, `Out ${fmtDate(reading.release)} · ${traded(reading.volume)}`),
  });
}

const EARNINGS_COLUMNS = [
  {
    key: "symbol", label: "Company", left: true,
    render: (row) => h("span", { style: { display: "inline-flex", gap: "6px", alignItems: "baseline" } }, symbolLink(row.symbol), h("span", { class: "sub-text" }, row.name),
      row.in_watchlist ? statusBadge("neutral", "Watchlist") : null),
  },
  { key: "date", label: "Report", left: true, render: (row) => fmtDate(row.date), value: (row) => row.date },
  { key: "prob", label: "Beat odds", metric: "earnings_odds", render: (row) => h("strong", {}, pct(row.prob)) },
  { key: "consensus_eps", label: "EPS estimate", fmt: "usd2" },
  { key: "volume", label: "Traded", render: (row) => fmt(row.volume, "usdcompact") },
  { key: "url", label: "Market", sortable: false, render: (row) => outside(row.url, "Polymarket ↗") },
];

export default {
  title: () => "Prediction Markets",
  async mount(root) {
    const state = { indicator: "cpi_mom", earnings: "all", ...prefs.get("prediction", {}) };
    const save = () => prefs.set("prediction", state);
    const fedBox = h("div", {});
    const pathBox = h("section", { class: "card" });
    const tilesBox = h("div", { class: "grid cols-4" });
    const pickerBox = h("div", {});
    const earningsBox = h("section", { class: "card" });
    const freshness = h("span", { class: "sub-text" });
    const outcomeCard = chartCard({ title: "Odds for each outcome", metric: "outcome_odds", height: 260 });
    const historyCard = chartCard({ title: "Fed and recession odds over the past year", metric: "prediction_markets", subtitle: "Kalshi, in percent. Fed odds follow whichever meeting is next", height: 220 });
    let data = null;

    const drawOutcomes = () => {
      const indicator = data.indicators.find((item) => item.key === state.indicator) || data.indicators[0];
      state.indicator = indicator.key;
      const reading = indicator.reading;
      outcomeCard.setSubtitle(reading
        ? `${indicator.label}, ${reading.period || "next report"}, out ${fmtDate(reading.release, true)}. The darkest bar is the most likely result.`
        : indicator.label);
      replace(pickerBox, segmented(
        data.indicators.map((item) => ({ value: item.key, label: item.label.replace(", year over year", " (yearly)").replace(", monthly change", " (monthly)") })),
        state.indicator,
        (value) => { state.indicator = value; save(); drawOutcomes(); },
        "Number to show",
      ));
      outcomeCard.render(outcomeChart(indicator));
    };

    const drawEarnings = () => {
      const rows = state.earnings === "watchlist" ? data.earnings.filter((row) => row.in_watchlist) : data.earnings;
      replace(earningsBox,
        h("div", { class: "card-head" },
          h("h3", { class: "card-title" }, "Will they beat earnings?", info("earnings_odds")),
          segmented([{ value: "all", label: "All companies" }, { value: "watchlist", label: "Your watchlist" }], state.earnings, (value) => {
            state.earnings = value;
            save();
            drawEarnings();
          }, "Which companies")),
        h("p", { class: "card-sub" }, "Polymarket's odds that a company's earnings per share beat the analysts' estimate in its next report. Most companies beat, so 70 to 80% is ordinary. These markets are small: check the amount traded."),
        dataTable({
          id: "prediction-earnings",
          columns: EARNINGS_COLUMNS,
          rows,
          rowKey: (row) => row.symbol,
          empty: state.earnings === "watchlist" ? "None of your watchlist stocks has an earnings market right now." : "No earnings markets right now.",
        }));
    };

    const draw = () => {
      replace(freshness, data.updated ? `Kalshi and Polymarket, read every hour. Updated ${fmtAgo(data.updated)}.` : "Waiting for the first reading of Kalshi and Polymarket.");
      replace(fedBox, fedCard(data.fed));
      replace(pathBox,
        h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "Fed meetings ahead", info("fed_path")),
        h("p", { class: "card-sub" }, "Where traders expect the Fed's rate after each meeting. Later meetings appear once their markets trade enough to be read."),
        dataTable({ id: "prediction-fed-path", columns: PATH_COLUMNS, rows: data.fed_path, empty: "No Fed rate markets with live quotes right now." }));
      const recession = data.recession.map((row) => tile({
        label: `Recession starting in ${row.year}`,
        metric: "pm_recession",
        value: row.prob * 100,
        fmtCode: "pct0",
        foot: h("span", {}, traded(row.volume)),
      }));
      replace(tilesBox, data.indicators.map(indicatorTile), recession.length ? recession : signalTile(null, { label: "Recession odds", metric: "pm_recession" }));
      drawOutcomes();
      historyCard.render(lineChart({
        dates: data.history.dates,
        series: [
          { name: "Fed hike odds", short: "Hike", values: data.history.series.hike, slot: 0 },
          { name: "Fed cut odds", short: "Cut", values: data.history.series.cut, slot: 1 },
          { name: "Recession odds", short: "Recession", values: data.history.series.recession, slot: 2 },
        ],
        yFmt: "pct0",
        min: 0,
        max: 100,
        emptyText: "Odds history builds up as the app reads the markets.",
      }));
      drawEarnings();
    };

    const load = async () => {
      [outcomeCard, historyCard].forEach((card) => card.refreshing());
      data = await api.get("/api/prediction");
      draw();
    };

    replace(root,
      pageHead("Prediction markets", "What people betting real money expect for the Fed, the economy and company earnings. Read the prices as the crowd's odds, not as forecasts: where little is traded, a few bets can move them.",
        info("prediction_markets")),
      h("p", { style: { margin: "-6px 0 12px" } }, freshness),
      h("div", { class: "section-title", style: { marginTop: 0 } }, "The Fed"),
      h("div", { class: "split-1-2", style: { alignItems: "start" } }, fedBox, pathBox),
      h("div", { class: "section-title" }, "The economy: coming reports", h("span", { class: "section-sub" }, "The market's middle estimate for each number before it is published")),
      tilesBox,
      h("div", { class: "filters mt", style: { marginBottom: "8px" } }, field("Show odds for", pickerBox, "outcome_odds")),
      outcomeCard.el,
      h("div", { class: "section-title" }, "Company earnings"),
      earningsBox,
      h("div", { class: "section-title" }, "Over time"),
      historyCard.el);

    await load();
    return { refresh: load };
  },
};
