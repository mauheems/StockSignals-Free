// The backdrop: interest rates, credit stress, liquidity, growth and inflation.
import { api, query } from "../lib/api.js";
import { categoryLines, chartCard, lineChart } from "../lib/charts.js";
import { fmt, fmtDate, h, prefs, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { emptyBox, field, filters, pageHead, segmented, signalTile, tile } from "../lib/ui.js";

const tradedText = (volume) => `${fmt(volume, "usdcompact")} traded`;

/** Odds for each outcome of the next Fed meeting, as a small bar list. */
function fedCard(fed) {
  if (!fed) return h("section", { class: "card" }, h("h3", { class: "card-title" }, "Next Fed meeting", info("fed_odds")), emptyBox("Fed odds appear once Kalshi has been read."));
  return h("section", { class: "card" },
    h("div", { class: "card-head" },
      h("h3", { class: "card-title" }, `Next Fed meeting, ${fmtDate(fed.date, true)}`, info("fed_odds")),
      h("a", { class: "sub-text", href: fed.url, target: "_blank", rel: "noopener noreferrer" }, `${fed.source} ↗`)),
    h("div", { class: "odds-list" }, fed.outcomes.map((item) => h("div", { class: `odds-row ${item.label === fed.likely ? "is-likely" : ""}` },
      h("span", { class: "odds-label" }, item.label),
      h("div", { class: "odds-track", role: "meter", "aria-valuenow": Math.round(item.prob * 100), "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": item.label },
        h("div", { class: "odds-fill", style: { width: `${Math.max(0.5, item.prob * 100)}%` } })),
      h("span", { class: "odds-value" }, `${Math.round(item.prob * 100)}%`)))),
    h("p", { class: "sub-text", style: { marginTop: "6px" } }, `${tradedText(fed.volume)} on this meeting. Odds are scaled to add up to 100%.`));
}

function expectationTile(reading, metric, label) {
  if (!reading) return tile({ label, metric, valueText: "–", foot: h("span", {}, "No market right now") });
  const range = reading.low !== null && reading.high !== null ? `likely ${fmt(reading.low, reading.fmt)} to ${fmt(reading.high, reading.fmt)}` : null;
  return tile({
    label: `${label}${reading.period ? `, ${reading.period}` : ""}`,
    metric,
    value: reading.median,
    fmtCode: reading.fmt,
    delta: range ? h("span", { class: "sub-text" }, range) : null,
    foot: h("span", {}, `Out ${fmtDate(reading.release)} · ${tradedText(reading.volume)}`),
  });
}

const RANGES = [
  { value: "1y", label: "1Y" }, { value: "2y", label: "2Y" }, { value: "5y", label: "5Y" },
  { value: "10y", label: "10Y" }, { value: "max", label: "Max" },
];

export default {
  title: () => "Macro & Credit",
  async mount(root) {
    let range = prefs.get("macro.range", "2y");
    const ratesBox = h("div", { class: "grid cols-4" });
    const creditBox = h("div", { class: "grid cols-4" });
    const economyBox = h("div", { class: "grid cols-5" });
    const predictionBox = h("div", { class: "split-1-2" });
    const cards = {
      curve: chartCard({ title: "Treasury yield curve", metric: "yield_curve", subtitle: "Yields by maturity", height: 250 }),
      yields: chartCard({ title: "Key interest rates", metric: "dgs10", subtitle: "10-year, 2-year and the Fed's policy rate", height: 250 }),
      spreads: chartCard({ title: "Yield curve spreads", metric: "t10y3m", subtitle: "Below zero means the curve is inverted", height: 220 }),
      realBreak: chartCard({ title: "Real yield and breakeven inflation", metric: "real10y", subtitle: "The 10-year yield split into two parts", height: 220 }),
      credit: chartCard({ title: "Credit spreads", metric: "hy_oas", subtitle: "Extra yield over Treasuries. FRED offers about 3 years", height: 220 }),
      hyg: chartCard({ title: "High yield vs Treasuries (HYG / IEF)", metric: "hyg_ief", subtitle: "Falling means credit is weakening", height: 220 }),
      nfci: chartCard({ title: "Financial conditions (NFCI)", metric: "nfci", subtitle: "Above zero is tighter than average", height: 220 }),
      liquidity: chartCard({ title: "Net Fed liquidity", metric: "net_liquidity", subtitle: "Fed assets minus Treasury cash and reverse repo", height: 220 }),
      dxy: chartCard({ title: "US dollar", metric: "dxy", subtitle: "With its 50-day average", height: 220 }),
      copperGold: chartCard({ title: "Copper / gold", metric: "copper_gold", subtitle: "Growth vs fear, with its 50-day average (x1,000)", height: 220 }),
      inflation: chartCard({ title: "Inflation", metric: "cpi_yoy", subtitle: "Percent change over 12 months", height: 220 }),
      claims: chartCard({ title: "Initial jobless claims", metric: "claims", subtitle: "Weekly", height: 220 }),
      odds: chartCard({ title: "Fed and recession odds over the past year", metric: "prediction_markets", subtitle: "Kalshi, in percent. Fed odds follow whichever meeting is next", height: 200 }),
      labour: chartCard({ title: "Unemployment and Sahm rule", metric: "sahm", subtitle: "A Sahm reading of 0.5 has marked early recessions", height: 220 }),
    };

    const draw = (data) => {
      const rows = data.rows;
      const prediction = data.prediction;
      const board = prediction.board || {};
      const recession = board.recession_next;
      replace(predictionBox,
        fedCard(board.fed),
        h("div", { class: "stack" },
          h("div", { class: "grid cols-4" },
            signalTile(prediction.rows.pm_recession, { label: recession ? `Recession odds, ${recession.year}` : "Recession odds, next year", metric: "pm_recession" }),
            expectationTile(board.cpi, "pm_cpi", "Expected CPI inflation"),
            expectationTile(board.unemployment, "pm_unemployment", "Expected unemployment"),
            expectationTile(board.payrolls, "pm_payrolls", "Expected payrolls")),
          cards.odds.el));
      cards.odds.render(lineChart({
        dates: prediction.history.dates,
        series: [
          { name: "Fed hike odds", short: "Hike", values: prediction.history.series.hike, slot: 0 },
          { name: "Fed cut odds", short: "Cut", values: prediction.history.series.cut, slot: 1 },
          { name: "Recession odds", short: "Recession", values: prediction.history.series.recession, slot: 2 },
        ],
        yFmt: "pct0",
        min: 0,
        max: 100,
        emptyText: "Odds history builds up as the app reads the markets.",
      }));
      replace(ratesBox,
        signalTile(rows.dgs10), signalTile(rows.dgs2), signalTile(rows.t10y2y), signalTile(rows.t10y3m),
        signalTile(rows.fed_funds), signalTile(rows.real10y), signalTile(rows.breakeven10y), signalTile(rows.dxy));
      replace(creditBox,
        signalTile(rows.hy_oas), signalTile(rows.hy_oas_chg), signalTile(rows.ig_oas), signalTile(rows.ccc_oas),
        signalTile(rows.hyg_ief, { deltaFmt: "snum3" }), signalTile(rows.nfci), signalTile(rows.stlfsi), signalTile(rows.net_liquidity, { deltaFmt: "snum2" }));
      replace(economyBox,
        signalTile(rows.cpi_yoy), signalTile(rows.core_cpi_yoy), signalTile(rows.core_pce_yoy), signalTile(rows.unrate),
        signalTile(rows.sahm), signalTile(rows.claims), signalTile(rows.oil, { deltaFmt: "snum2" }), signalTile(rows.gold, { deltaFmt: "snum0" }),
        signalTile(rows.copper, { deltaFmt: "snum2" }), signalTile(rows.copper_gold, { deltaFmt: "snum4" }));

      const curve = data.curve;
      const tenors = curve.today ? curve.today.points.map((point) => point.tenor) : [];
      const onTenors = (snapshot) => tenors.map((tenor) => snapshot?.points.find((point) => point.tenor === tenor)?.value ?? null);
      cards.curve.render(categoryLines({
        categories: tenors,
        series: [
          { name: curve.today ? `Latest (${curve.today.date})` : "Latest", values: onTenors(curve.today), slot: 0 },
          { name: "1 month ago", values: onTenors(curve.month_ago), slot: 1 },
          { name: "1 year ago", values: onTenors(curve.year_ago), muted: true },
        ],
        yFmt: "pct2",
        emptyText: "The Treasury curve is still loading.",
      }));
      const charts = data.charts;
      cards.yields.render(lineChart({
        dates: charts.yields.dates,
        series: [
          { name: "10-year", values: charts.yields.series.y10, slot: 0 },
          { name: "2-year", values: charts.yields.series.y2, slot: 1 },
          { name: "Fed funds", values: charts.yields.series.ff, muted: true },
        ],
        yFmt: "pct2",
      }));
      cards.spreads.render(lineChart({
        dates: charts.spreads_curve.dates,
        series: [
          { name: "10y - 2y", values: charts.spreads_curve.series.t10y2y, slot: 0 },
          { name: "10y - 3m", values: charts.spreads_curve.series.t10y3m, slot: 1 },
        ],
        yFmt: "pct2",
        thresholds: [{ y: 0 }],
      }));
      cards.realBreak.render(lineChart({
        dates: charts.real_breakeven.dates,
        series: [
          { name: "Real yield", values: charts.real_breakeven.series.real, slot: 0 },
          { name: "Breakeven inflation", short: "Breakeven", values: charts.real_breakeven.series.breakeven, slot: 1 },
        ],
        yFmt: "pct2",
      }));
      cards.credit.render(lineChart({
        dates: charts.credit.dates,
        series: [
          { name: "High yield", values: charts.credit.series.hy, slot: 0 },
          { name: "Investment grade", short: "Inv. grade", values: charts.credit.series.ig, slot: 1 },
        ],
        yFmt: "pct2",
      }));
      const withAverage = (chart, name, yFmt) => lineChart({
        dates: chart.dates,
        series: [
          { name, values: chart.series.value, slot: 0 },
          { name: "50-day average", values: chart.series.average, muted: true },
        ],
        yFmt,
      });
      cards.hyg.render(withAverage(charts.hyg_ief, "HYG / IEF", "num3"));
      cards.nfci.render(lineChart({ dates: charts.nfci.dates, series: [{ name: "NFCI", values: charts.nfci.values }], yFmt: "num2", thresholds: [{ y: 0, label: "Average" }] }));
      cards.liquidity.render(lineChart({ dates: charts.liquidity.dates, series: [{ name: "Net liquidity", values: charts.liquidity.values, area: true }], yFmt: "usdtn" }));
      cards.dxy.render(withAverage(charts.dxy, "Dollar index", "num1"));
      cards.copperGold.render(withAverage(charts.copper_gold, "Copper / gold", "num2"));
      cards.inflation.render(lineChart({
        dates: charts.inflation.dates,
        series: [
          { name: "CPI", values: charts.inflation.series.cpi, slot: 0 },
          { name: "Core CPI", values: charts.inflation.series.core_cpi, slot: 1 },
          { name: "Core PCE", values: charts.inflation.series.core_pce, slot: 2 },
        ],
        yFmt: "pct1",
        thresholds: [{ y: 2, label: "Fed goal" }],
      }));
      cards.claims.render(lineChart({ dates: charts.claims.dates, series: [{ name: "Initial claims", values: charts.claims.values }], yFmt: "num0" }));
      cards.labour.render(lineChart({
        dates: charts.unemployment.dates,
        series: [
          { name: "Unemployment rate", short: "Unemployment", values: charts.unemployment.series.unrate, slot: 0 },
          { name: "Sahm indicator", short: "Sahm", values: charts.unemployment.series.sahm, slot: 1 },
        ],
        yFmt: "num1",
        thresholds: [{ y: 0.5, label: "Sahm trigger" }],
      }));
    };

    const load = async () => {
      Object.values(cards).forEach((card) => card.refreshing());
      draw(await api.get(`/api/macro${query({ range })}`));
    };

    replace(root,
      pageHead("Macro & credit", "Interest rates, credit stress, liquidity and the economy. Credit often turns before stocks do, so a calm stock market with widening spreads deserves attention."),
      filters(field("Period", segmented(RANGES, range, (value) => {
        range = value;
        prefs.set("macro.range", value);
        load().catch((error) => console.warn(error));
      }, "Chart period"))),
      h("div", { class: "section-title", style: { marginTop: 0 } }, "What prediction markets expect", info("prediction_markets"),
        h("span", { class: "section-sub" }, "Odds from people betting real money on what comes next")),
      predictionBox,
      h("div", { class: "section-title" }, "Rates and the dollar"),
      ratesBox,
      h("div", { class: "grid grid-2 mt" }, cards.curve.el, cards.yields.el),
      h("div", { class: "grid grid-3 mt" }, cards.spreads.el, cards.realBreak.el, cards.dxy.el),
      h("div", { class: "section-title" }, "Credit and liquidity"),
      creditBox,
      h("div", { class: "grid grid-3 mt" }, cards.credit.el, cards.hyg.el, cards.nfci.el),
      h("div", { class: "grid grid-2 mt" }, cards.liquidity.el, cards.copperGold.el),
      h("div", { class: "section-title" }, "Economy and commodities"),
      economyBox,
      h("div", { class: "grid grid-3 mt" }, cards.inflation.el, cards.claims.el, cards.labour.el));

    await load();
    return { refresh: load };
  },
};
