// Does a signal actually help? Compare what happened after it fired with any other day.
import { api } from "../lib/api.js";
import { baseOption, chartCard, colors, dateAxis, tooltipRows, valueAxis } from "../lib/charts.js";
import { MISSING, fmt, fmtDate, h, prefs, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { conditionRow, loadCurrentValues, signalOptions, suggestedValue } from "../lib/rules.js";
import { errorBox, field, filters, heatCell, pageHead, select, statusBadge, tile, toast } from "../lib/ui.js";

function comparisonChart(result) {
  const rows = result.horizons.filter((row) => row.signal.count > 0);
  if (!rows.length) return { empty: "The condition never fired with enough price history after it." };
  const c = colors();
  const seriesFor = (name, key, color) => ({
    name,
    type: "bar",
    barMaxWidth: 24,
    barGap: "10%",
    itemStyle: { color },
    emphasis: { disabled: true },
    data: rows.map((row) => ({
      value: row[key].mean,
      itemStyle: { color, borderRadius: row[key].mean >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4] },
    })),
  });
  const option = {
    ...baseOption(),
    grid: { left: 8, right: 14, top: 30, bottom: 4, containLabel: true },
    legend: { top: 0, left: 0, itemWidth: 10, itemHeight: 10, icon: "roundRect", textStyle: { color: c.ink2, fontSize: 11 } },
    xAxis: { type: "category", data: rows.map((row) => row.horizon), axisLine: { lineStyle: { color: c.axis } }, axisTick: { show: false }, axisLabel: { color: c.muted } },
    yAxis: valueAxis(c, "pct1", { scale: false }),
    series: [seriesFor("After the signal", "signal", c.accent), seriesFor("Any day (baseline)", "baseline", c.deemph)],
  };
  option.tooltip = {
    ...option.tooltip,
    trigger: "axis",
    axisPointer: { type: "shadow", shadowStyle: { color: "rgba(127,127,127,0.12)" } },
    formatter: (items) => tooltipRows(`${items[0].axisValue} later`, items.map((item) => ({ color: item.color, value: fmt(item.value, "spct2"), name: `Average, ${item.seriesName}` }))),
  };
  return {
    option,
    table: {
      columns: [{ label: "Horizon" }, { label: "After the signal", fmt: "spct2" }, { label: "Any day", fmt: "spct2" }],
      rows: rows.map((row) => [row.horizon, row.signal.mean, row.baseline.mean]),
    },
  };
}

function eventsChart(result) {
  const { price, event_prices: events } = result;
  if (!price.dates.length) return { empty: "No prices in this period." };
  const c = colors();
  const option = {
    ...baseOption(),
    grid: { left: 8, right: 14, top: 28, bottom: 4, containLabel: true },
    legend: { top: 0, left: 0, itemWidth: 12, itemHeight: 8, textStyle: { color: c.ink2, fontSize: 11 }, data: [result.symbol, "Signal fired"] },
    xAxis: dateAxis(price.dates, c),
    yAxis: valueAxis(c, "num0"),
    series: [
      { name: result.symbol, type: "line", data: price.values, showSymbol: false, lineStyle: { width: 1.5, color: c.deemph }, itemStyle: { color: c.deemph }, emphasis: { disabled: true } },
      {
        name: "Signal fired",
        type: "scatter",
        data: events.map((event) => [event.date, event.price]),
        symbolSize: 9,
        z: 4,
        itemStyle: { color: c.accent, borderColor: c.surface, borderWidth: 2 },
      },
    ],
  };
  option.tooltip = {
    ...option.tooltip,
    trigger: "axis",
    formatter: (items) => tooltipRows(fmtDate(items[0].axisValue, true), items.map((item) => ({
      color: item.seriesName === "Signal fired" ? c.accent : c.deemph,
      value: fmt(Array.isArray(item.value) ? item.value[1] : item.value, "num2"),
      name: item.seriesName,
    }))),
  };
  return {
    option,
    table: { columns: [{ label: "Signal date" }, { label: "Close", fmt: "num2" }], rows: [...events].reverse().map((event) => [event.date, event.price]) },
  };
}

function resultTable(result) {
  const headers = ["Later by", "Events", "Average", "Median", "Win rate", "Worst", "Best", "Any-day average", "Any-day win rate", "Edge"];
  const metrics = [null, "event_count", "forward_returns", "forward_returns", "forward_returns", "forward_returns", "forward_returns", "baseline", "baseline", "edge"];
  return h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("thead", {}, h("tr", {}, headers.map((label, index) => h("th", { class: index === 0 ? "left" : "", scope: "col" }, h("span", { class: "th-inner" }, label, metrics[index] ? info(metrics[index]) : null))))),
    h("tbody", {}, result.horizons.map((row) => h("tr", {},
      h("td", { class: "left" }, row.horizon),
      h("td", {}, fmt(row.signal.count, "num0")),
      h("td", {}, heatCell(row.signal.mean, 15, "spct2")),
      h("td", {}, fmt(row.signal.median, "spct2")),
      h("td", {}, fmt(row.signal.win_rate, "pct0")),
      h("td", {}, fmt(row.signal.worst, "spct1")),
      h("td", {}, fmt(row.signal.best, "spct1")),
      h("td", {}, fmt(row.baseline.mean, "spct2")),
      h("td", {}, fmt(row.baseline.win_rate, "pct0")),
      h("td", {}, row.edge === null ? MISSING : heatCell(row.edge, 8, "spct2")))))));
}

export default {
  title: () => "Signal Study",
  async mount(root) {
    const options = await api.get("/api/research/options");
    await loadCurrentValues().catch(() => null);
    const allOptions = signalOptions(options, options.self_signals);
    const saved = prefs.get("study", {});
    const state = {
      symbol: saved.symbol || "SPY",
      start: saved.start || "",
      cooldown: saved.cooldown ?? 10,
      preset: saved.preset ?? "vix_30",
      conditions: saved.conditions?.length ? saved.conditions : options.study_presets[0].conditions.map((item) => ({ ...item })),
    };

    let result = null;
    let running = false;
    const conditionsBox = h("div", {});
    const statusBox = h("div", {});
    const tilesBox = h("div", { class: "grid grid-kpi" });
    const tableBox = h("section", { class: "card" });
    const compareCard = chartCard({ title: "Average return after the signal", metric: "forward_returns", subtitle: "Against the average for any day in the same period", height: 260 });
    const eventsCard = chartCard({ title: "When the signal fired", metric: "event_count", height: 260 });
    const resultsBox = h("div", {});
    const runButton = h("button", { class: "btn btn-primary", type: "button", id: "run-study", onclick: () => run() }, "Run study");

    const presetSelect = select(
      [{ value: "", label: "My own conditions" }, ...options.study_presets.map((item) => ({ value: item.id, label: item.label }))],
      state.preset,
      (value) => {
        state.preset = value;
        const preset = options.study_presets.find((item) => item.id === value);
        if (preset) {
          state.conditions = preset.conditions.map((item) => ({ ...item }));
          drawConditions();
          run();
        }
      },
      { "aria-label": "Preset signal", id: "study-preset" },
    );

    const markCustom = () => {
      state.preset = "";
      presetSelect.value = "";
    };

    function drawConditions() {
      replace(conditionsBox,
        state.conditions.map((condition, index) => conditionRow({
          condition,
          options: allOptions,
          onChange: markCustom,
          onRemove: state.conditions.length > 1 ? () => {
            state.conditions.splice(index, 1);
            markCustom();
            drawConditions();
          } : null,
        })),
        h("button", {
          class: "btn btn-xs",
          type: "button",
          id: "study-add-condition",
          onclick: () => {
            state.conditions.push({ signal: "pct_above_50", op: "<", value: suggestedValue("pct_above_50") });
            markCustom();
            drawConditions();
          },
        }, "Add condition"));
    }

    const draw = () => {
      tilesBox.className = "grid cols-4";
      replace(tilesBox,
        tile({ label: "Events", metric: "event_count", value: result.event_count, fmtCode: "num0", foot: h("span", {}, result.event_count < 10 ? "Too few to rely on" : `Cooldown ${result.cooldown} days`) }),
        tile({ label: "Share of days condition held", metric: "condition_share", value: result.pct_time_true, fmtCode: "pct1", foot: h("span", {}, `${fmt(result.days_true, "num0")} of ${fmt(result.days_total, "num0")} days`) }),
        tile({ label: "True right now?", metric: "condition_active", valueText: result.active_now ? "Yes" : "No", foot: result.active_now ? statusBadge("warning", "Condition is active") : h("span", {}, `As of ${fmtDate(result.last_date, true)}`) }),
        tile({ label: "Signal history from", metric: "study_coverage", valueText: fmtDate(result.coverage_from, true), foot: h("span", {}, "Baseline uses the same period") }));
      replace(tableBox,
        h("h3", { class: "card-title", style: { marginBottom: "4px" } }, `${result.symbol} after: ${result.conditions.join(" and ")}`),
        resultTable(result),
        h("p", { class: "table-note" }, "Returns are dividend-adjusted and measured from the close on the event date. Events can overlap with each other, and small samples can show large edges by chance. Data with publishing delays is lagged, so this only uses what was known at the time."));
      compareCard.render(comparisonChart(result));
      eventsCard.setSubtitle(`${result.event_count} events on ${result.symbol}`);
      eventsCard.render(eventsChart(result));
    };

    async function run() {
      if (running) return;
      running = true;
      runButton.disabled = true;
      runButton.textContent = "Running…";
      resultsBox.classList.add("is-refreshing");
      prefs.set("study", state);
      try {
        result = await api.post("/api/study", {
          symbol: state.symbol.trim().toUpperCase(),
          start: state.start || null,
          cooldown: state.cooldown,
          conditions: state.conditions,
        });
        replace(statusBox);
        replace(resultsBox, tilesBox, h("div", { class: "mt" }, tableBox), h("div", { class: "grid grid-2 mt" }, compareCard.el, eventsCard.el));
        draw();
      } catch (error) {
        toast(error.message, "error");
        if (!result) replace(statusBox, errorBox(error.message, run));
      } finally {
        running = false;
        runButton.disabled = false;
        runButton.textContent = "Run study";
        resultsBox.classList.remove("is-refreshing");
      }
    }

    drawConditions();
    replace(root,
      pageHead("Signal study", "Pick any condition and see what the market did next, compared with an ordinary day. Use this to check whether a signal has helped historically before you lean on it."),
      filters(
        field("Start from a preset", presetSelect),
        field("Measure on", h("input", { class: "input", style: { width: "100px" }, value: state.symbol, spellcheck: false, "aria-label": "Symbol to measure", id: "study-symbol", onchange: (event) => { state.symbol = event.target.value; } })),
        field("From (blank = all history)", h("input", { class: "input input-date", type: "date", value: state.start, "aria-label": "Start date", onchange: (event) => { state.start = event.target.value; } })),
        field("Cooldown (days)", h("input", { class: "input input-num", type: "number", min: 0, max: 252, value: state.cooldown, "aria-label": "Cooldown in trading days", onchange: (event) => { state.cooldown = Number(event.target.value); } }), "cooldown"),
        runButton),
      h("section", { class: "card", style: { marginBottom: "10px", maxWidth: "720px" } },
        h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "Conditions (all must be true)"),
        conditionsBox),
      statusBox,
      resultsBox);

    await run();
    return {};
  },
};
