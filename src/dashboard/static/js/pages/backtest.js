// Strategy backtests in one overview: a leaderboard against Buy & Hold, then the details.
import { api } from "../lib/api.js";
import { baseOption, chartCard, colors, dateAxis, lineChart, tooltipRows, valueAxis } from "../lib/charts.js";
import { fmt, fmtDate, h, monthName, prefs, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { conditionRow, loadCurrentValues, signalOptions, suggestedValue } from "../lib/rules.js";
import { dataTable, emptyBox, errorBox, field, filters, heatCell, pageHead, segmented, tile, toast } from "../lib/ui.js";

const today = () => new Date().toISOString().slice(0, 10);

const LEADERBOARD = [
  {
    key: "name", label: "Strategy", left: true, wrap: true, sticky: true,
    render: (row) => h("span", { style: { display: "block", minWidth: "210px" } },
      h("span", { style: { fontWeight: 560 } }, row.name),
      row.id === "buy_and_hold" ? h("span", { class: "sub-text" }, " (benchmark)") : null,
      row.note ? h("div", { class: "sub-text", style: { whiteSpace: "normal" } }, row.note) : null),
  },
  { key: "total_return", label: "Return", metric: "total_return", fmt: "spct1" },
  { key: "cagr", label: "CAGR", metric: "cagr", fmt: "spct1" },
  { key: "volatility", label: "Volatility", metric: "bt_volatility", fmt: "pct1" },
  { key: "sharpe", label: "Sharpe", metric: "sharpe", fmt: "num2" },
  { key: "sortino", label: "Sortino", metric: "sortino", fmt: "num2" },
  { key: "max_drawdown", label: "Max drawdown", metric: "max_drawdown", fmt: "pct1" },
  { key: "calmar", label: "Calmar", metric: "calmar", fmt: "num2" },
  { key: "exposure", label: "Time invested", metric: "exposure", fmt: "pct0" },
  { key: "trades", label: "Trades", metric: "trades_count", fmt: "num0" },
  { key: "win_rate", label: "Win rate", metric: "win_rate", fmt: "pct0" },
  { key: "profit_factor", label: "Profit factor", metric: "profit_factor", fmt: "num2" },
];

function equityChart(result, selectedId, showAll) {
  const details = result.details;
  const rowsById = Object.fromEntries(result.rows.map((row) => [row.id, row]));
  // The chosen strategy is the point of this chart, so it wears the accent; context stays gray.
  const series = [];
  if (showAll) {
    for (const row of result.rows) {
      if (row.id !== selectedId && row.id !== "buy_and_hold") series.push({ name: row.name, values: details[row.id].equity, muted: true, width: 1 });
    }
  }
  if (selectedId !== "buy_and_hold") series.push({ name: rowsById.buy_and_hold.name, values: details.buy_and_hold.equity, slot: 1 });
  series.push({ name: rowsById[selectedId].name, short: selectedId === "buy_and_hold" ? "Buy & Hold" : "Strategy", values: details[selectedId].equity, slot: 0, width: 2 });
  const chart = lineChart({ dates: result.dates, series, yFmt: "usd0", endLabels: !showAll });
  if (showAll && chart.option) {
    chart.option.legend.data = series.filter((item) => !item.muted).map((item) => item.name);
  }
  return chart;
}

function tradesChart(result, selectedId) {
  const c = colors();
  const trades = result.details[selectedId].trades;
  if (!trades.length) return { empty: "This strategy made no trades in this period." };
  const dates = result.dates;
  const price = result.price;
  const position = new Map(dates.map((date, index) => [date, index]));
  const marks = [];
  for (const trade of trades) {
    const entry = position.get(trade.entry_date);
    if (entry !== undefined) marks.push({ value: [trade.entry_date, price[entry]], kind: "Buy", trade });
    const exit = position.get(trade.exit_date);
    if (exit !== undefined && !trade.open) marks.push({ value: [trade.exit_date, price[exit]], kind: "Sell", trade });
  }
  const option = {
    ...baseOption(),
    grid: { left: 8, right: 14, top: 28, bottom: 4, containLabel: true },
    legend: { top: 0, left: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: c.ink2, fontSize: 11 }, data: [result.symbol, "Buy", "Sell"] },
    xAxis: dateAxis(dates, c),
    yAxis: valueAxis(c, "num2"),
    series: [
      { name: result.symbol, type: "line", data: price, showSymbol: false, lineStyle: { width: 1.5, color: c.deemph }, itemStyle: { color: c.deemph }, emphasis: { disabled: true } },
      {
        name: "Buy", type: "scatter", symbol: "triangle", symbolSize: 10, z: 4,
        itemStyle: { color: c.up, borderColor: c.surface, borderWidth: 2 },
        data: marks.filter((mark) => mark.kind === "Buy"),
      },
      {
        name: "Sell", type: "scatter", symbol: "triangle", symbolRotate: 180, symbolSize: 10, z: 4,
        itemStyle: { color: c.down, borderColor: c.surface, borderWidth: 2 },
        data: marks.filter((mark) => mark.kind === "Sell"),
      },
    ],
  };
  option.tooltip = {
    ...option.tooltip,
    trigger: "axis",
    formatter: (items) => {
      const rows = [];
      for (const item of items) {
        if (item.seriesName === result.symbol) rows.push({ color: c.deemph, value: fmt(item.value, "num2"), name: "Adjusted close" });
        else if (item.data?.trade) rows.push({ color: item.color, value: fmt(item.data.trade.return_pct, "spct1"), name: `${item.seriesName}: trade's return` });
      }
      return tooltipRows(fmtDate(items[0].axisValue, true), rows);
    },
  };
  return {
    option,
    table: {
      columns: [{ label: "Entry" }, { label: "Exit" }, { label: "Return", fmt: "spct1" }],
      rows: trades.map((trade) => [trade.entry_date, trade.open ? `${trade.exit_date} (still open)` : trade.exit_date, trade.return_pct]).reverse(),
    },
  };
}

function monthlyTable(monthly) {
  if (!monthly.length) return emptyBox("No monthly returns in this period.");
  const years = [...new Set(monthly.map((row) => row.year))].sort((a, b) => b - a);
  const lookup = new Map(monthly.map((row) => [`${row.year}-${row.month}`, row.return]));
  const months = [...Array(12).keys()];
  const yearTotal = (year) => {
    let product = 1;
    let any = false;
    for (const month of months) {
      const value = lookup.get(`${year}-${month + 1}`);
      if (value !== undefined) {
        product *= 1 + value / 100;
        any = true;
      }
    }
    return any ? (product - 1) * 100 : null;
  };
  return h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("thead", {}, h("tr", {},
      h("th", { class: "left", scope: "col" }, "Year"),
      months.map((month) => h("th", { scope: "col" }, monthName(month))),
      h("th", { scope: "col" }, "Year total"))),
    h("tbody", {}, years.map((year) => h("tr", {},
      h("td", { class: "left" }, year),
      months.map((month) => h("td", {}, heatCell(lookup.get(`${year}-${month + 1}`) ?? null, 10))),
      h("td", {}, heatCell(yearTotal(year), 40)))))));
}

export default {
  title: (params) => `Backtests${params[0] ? ` · ${params[0]}` : ""}`,
  async mount(root, { params }) {
    const options = await api.get("/api/research/options");
    await loadCurrentValues().catch(() => null);
    const saved = prefs.get("backtest", {});
    const form = {
      symbol: (params[0] || saved.symbol || "SPY").toUpperCase(),
      start: saved.start || "2018-01-01",
      end: saved.end || "",
      capital: saved.capital ?? 10000,
      commission_bps: saved.commission_bps ?? 5,
      slippage_bps: saved.slippage_bps ?? 5,
    };
    const unchecked = new Set(saved.unchecked || []);
    const paramValues = saved.params || {};
    const custom = { enabled: !!saved.customEnabled, name: "Custom rule", conditions: saved.conditions?.length ? saved.conditions : [{ signal: "self:rsi14", op: ">", value: 50 }] };

    const allOptions = signalOptions(options, options.self_signals);
    let result = null;
    let selectedId = null;
    let showAll = false;
    let running = false;

    const statusBox = h("div", {});
    const tilesBox = h("div", { class: "grid cols-7" });
    const boardBox = h("section", { class: "card" });
    const equityCard = chartCard({ title: "Equity curve", metric: "equity_curve", height: 300 });
    const drawdownCard = chartCard({ title: "Drawdown", metric: "drawdown_chart", subtitle: "Distance below the previous peak", height: 190 });
    const tradesCard = chartCard({ title: "Price and trades", metric: "trades_count", subtitle: "Dividend-adjusted price, with each buy and sell", height: 240 });
    const monthlyBox = h("section", { class: "card" });
    const tradesBox = h("section", { class: "card" });
    const resultsBox = h("div", {});

    const runButton = h("button", { class: "btn btn-primary", type: "button", id: "run-backtests", onclick: () => run() }, "Run backtests");

    const input = (key, attrs) => h("input", {
      class: "input",
      value: form[key],
      ...attrs,
      onchange: (event) => {
        form[key] = attrs.type === "number" ? Number(event.target.value) : event.target.value;
      },
      onkeydown: (event) => {
        if (event.key === "Enter") {
          form[key] = attrs.type === "number" ? Number(event.target.value) : event.target.value;
          run();
        }
      },
    });

    // ---------------------------------------------------------- strategy selector
    const strategyList = h("div", { class: "strategy-grid" });
    const drawStrategies = () => {
      replace(strategyList, options.strategies.map((template) => {
        const isBenchmark = template.id === "buy_and_hold";
        const values = (paramValues[template.id] ||= {});
        return h("div", { class: "strategy-row", dataset: { strategy: template.id } },
          h("input", {
            type: "checkbox",
            checked: isBenchmark || !unchecked.has(template.id),
            disabled: isBenchmark,
            "aria-label": `Include ${template.name}`,
            title: isBenchmark ? "Buy & Hold is always included as the benchmark" : undefined,
            onchange: (event) => {
              if (event.target.checked) unchecked.delete(template.id);
              else unchecked.add(template.id);
              updateSelectionText();
            },
          }),
          h("div", {},
            h("div", { class: "strategy-name" }, template.name, info(template.metric)),
            h("div", { class: "strategy-desc" }, template.description),
            template.params.length ? h("div", { class: "strategy-params" }, template.params.map((param) => h("label", {},
              param.label,
              h("input", {
                class: "input",
                type: "number",
                min: param.min,
                max: param.max,
                value: values[param.id] ?? param.value,
                onchange: (event) => { values[param.id] = Number(event.target.value); },
              })))) : null));
      }));
    };

    const selectionText = h("p", { class: "card-sub" });
    function updateSelectionText() {
      const count = options.strategies.filter((template) => !unchecked.has(template.id)).length + (custom.enabled ? 1 : 0);
      selectionText.textContent = `${count} strategies${custom.enabled ? ", including your own rule" : ""}. Change the selection, adjust settings or build your own rule, then run again.`;
    }
    const editorBox = h("div", { style: { marginTop: "10px" } });
    editorBox.hidden = !custom.enabled && !saved.editorOpen;
    const editButton = h("button", {
      class: "btn",
      type: "button",
      id: "edit-strategies",
      "aria-expanded": String(!editorBox.hidden),
      onclick: () => {
        editorBox.hidden = !editorBox.hidden;
        editButton.setAttribute("aria-expanded", String(!editorBox.hidden));
        editButton.textContent = editorBox.hidden ? "Edit strategies" : "Hide";
      },
    }, editorBox.hidden ? "Edit strategies" : "Hide");

    const customBox = h("div", {});
    const drawCustom = () => {
      replace(customBox,
        h("label", { class: "check-row", style: { fontWeight: 560, cursor: "pointer" } },
          h("input", { type: "checkbox", id: "custom-enabled", checked: custom.enabled, onchange: (event) => { custom.enabled = event.target.checked; updateSelectionText(); } }),
          "Include my rule", info("strategy_signal_rule")),
        h("p", { class: "strategy-desc", style: { marginBottom: "6px" } }, "Hold while every condition is true at the close. Trades happen at the next open."),
        custom.conditions.map((condition, index) => conditionRow({
          condition,
          options: allOptions,
          onRemove: custom.conditions.length > 1 ? () => {
            custom.conditions.splice(index, 1);
            drawCustom();
          } : null,
        })),
        h("button", {
          class: "btn btn-xs",
          type: "button",
          id: "add-condition",
          onclick: () => {
            custom.conditions.push({ signal: "vix", op: "<", value: suggestedValue("vix") });
            custom.enabled = true;
            drawCustom();
            updateSelectionText();
          },
        }, "Add condition"));
    };

    // -------------------------------------------------------------------- results
    const drawSelected = () => {
      const row = result.rows.find((item) => item.id === selectedId);
      const base = result.rows.find((item) => item.id === "buy_and_hold");
      const details = result.details[selectedId];
      const vs = (value, code) => (selectedId === "buy_and_hold" ? null : h("span", {}, `Buy & Hold ${fmt(value, code)}`));
      replace(tilesBox,
        tile({ label: "Total return", metric: "total_return", value: row.total_return, fmtCode: "spct1", delta: vs(base.total_return, "spct1") }),
        tile({ label: "CAGR", metric: "cagr", value: row.cagr, fmtCode: "spct1", delta: vs(base.cagr, "spct1") }),
        tile({ label: "Sharpe ratio", metric: "sharpe", value: row.sharpe, fmtCode: "num2", delta: vs(base.sharpe, "num2") }),
        tile({ label: "Sortino ratio", metric: "sortino", value: row.sortino, fmtCode: "num2", delta: vs(base.sortino, "num2") }),
        tile({ label: "Maximum drawdown", metric: "max_drawdown", value: row.max_drawdown, fmtCode: "pct1", delta: vs(base.max_drawdown, "pct1") }),
        tile({ label: "Calmar ratio", metric: "calmar", value: row.calmar, fmtCode: "num2", delta: vs(base.calmar, "num2") }),
        tile({ label: "Volatility", metric: "bt_volatility", value: row.volatility, fmtCode: "pct1", delta: vs(base.volatility, "pct1") }),
        tile({ label: "Time invested", metric: "exposure", value: row.exposure, fmtCode: "pct0" }),
        tile({ label: "Trades", metric: "trades_count", value: row.trades, fmtCode: "num0" }),
        tile({ label: "Win rate", metric: "win_rate", value: row.win_rate, fmtCode: "pct0" }),
        tile({ label: "Profit factor", metric: "profit_factor", value: row.profit_factor, fmtCode: "num2", foot: row.profit_factor === null ? h("span", {}, "No losing trades") : null }),
        tile({ label: "Average trade", metric: "avg_trade", value: row.avg_trade, fmtCode: "spct1" }),
        tile({ label: "Final value", metric: "total_return", value: row.final_value, fmtCode: "usd0" }),
        tile({ label: "Costs paid", metric: "transaction_costs", value: row.costs, fmtCode: "usd0" }));
      equityCard.setSubtitle(`${row.name} vs Buy & Hold on ${result.symbol}, starting from ${fmt(result.capital, "usd0")}`);
      equityCard.render(equityChart(result, selectedId, showAll));
      drawdownCard.render(lineChart({
        dates: result.dates,
        series: selectedId === "buy_and_hold"
          ? [{ name: "Buy & Hold", values: details.drawdown, slot: 0, area: true }]
          : [
            { name: "Buy & Hold", values: result.details.buy_and_hold.drawdown, slot: 1, width: 1 },
            { name: row.name, values: details.drawdown, slot: 0, area: true },
          ],
        yFmt: "pct0",
        endLabels: false,
      }));
      tradesCard.render(tradesChart(result, selectedId));
      replace(monthlyBox,
        h("h3", { class: "card-title", style: { marginBottom: "6px" } }, `Monthly returns: ${row.name}`, info("monthly_returns")),
        monthlyTable(details.monthly));
      replace(tradesBox,
        h("h3", { class: "card-title", style: { marginBottom: "6px" } }, `Trades: ${row.name}`, info("trades_count")),
        dataTable({
          columns: [
            { key: "entry_date", label: "Bought", left: true, render: (trade) => fmtDate(trade.entry_date, true), value: (trade) => trade.entry_date },
            { key: "entry_price", label: "Buy price", fmt: "num2" },
            { key: "exit_date", label: "Sold", left: true, render: (trade) => (trade.open ? "Still open" : fmtDate(trade.exit_date, true)), value: (trade) => trade.exit_date },
            { key: "exit_price", label: "Sell or last price", fmt: "num2" },
            { key: "bars_held", label: "Days held", fmt: "num0" },
            { key: "return_pct", label: "Return", render: (trade) => heatCell(trade.return_pct, 25) },
            { key: "profit", label: "Profit", fmt: "usd0" },
          ],
          rows: details.trades,
          sort: { key: "entry_date", dir: "desc" },
          maxHeight: 360,
          empty: "This strategy made no trades in this period.",
        }));
    };

    const drawResults = () => {
      replace(resultsBox,
        h("div", { class: "section-title", style: { marginTop: 0 } }, "Leaderboard",
          h("span", { class: "section-sub" }, `${result.name} (${result.symbol}), ${fmtDate(result.start, true)} to ${fmtDate(result.end, true)}. Select a row for details.`)),
        boardBox,
        h("div", { class: "section-title" }, "Selected strategy"),
        tilesBox,
        h("div", { class: "filters", style: { margin: "10px 0 8px" } },
          field("Equity curve", segmented([{ value: "pair", label: "vs Buy & Hold" }, { value: "all", label: "All strategies" }], showAll ? "all" : "pair", (value) => {
            showAll = value === "all";
            drawSelected();
          }, "Equity curve comparison"))),
        equityCard.el,
        h("div", { class: "grid grid-2 mt" }, drawdownCard.el, tradesCard.el),
        h("div", { class: "mt" }, monthlyBox),
        h("div", { class: "mt" }, tradesBox),
        h("p", { class: "table-note" }, "Results include dividends and the costs set above. Trades fill at the next day's open, never at a price from the signal's own close. Cash earns no interest here, which understates strategies that spend time out of the market. Rules chosen after seeing the data will usually look better than they would live."));
      replace(boardBox, dataTable({
        id: "backtest-leaderboard",
        columns: LEADERBOARD,
        rows: result.rows,
        rowKey: (row) => row.id,
        selectedKey: selectedId,
        sort: { key: "sharpe", dir: "desc" },
        onRow: (row) => {
          selectedId = row.id;
          boardBox.firstChild.update(result.rows, selectedId);
          drawSelected();
        },
      }));
      drawSelected();
    };

    async function run() {
      if (running) return;
      if (!form.symbol.trim()) {
        toast("Enter a symbol to test.", "error");
        return;
      }
      running = true;
      runButton.disabled = true;
      runButton.textContent = "Running…";
      resultsBox.classList.add("is-refreshing");
      const strategies = options.strategies
        .filter((template) => template.id === "buy_and_hold" || !unchecked.has(template.id))
        .map((template) => ({ id: template.id, params: Object.keys(paramValues[template.id] || {}).length ? paramValues[template.id] : undefined }));
      if (custom.enabled) {
        strategies.push({ id: "custom", kind: "rule", name: custom.name, description: "Your own rule.", conditions: custom.conditions });
      }
      prefs.set("backtest", { ...form, unchecked: [...unchecked], params: paramValues, customEnabled: custom.enabled, conditions: custom.conditions });
      try {
        result = await api.post("/api/backtest", { ...form, symbol: form.symbol.trim().toUpperCase(), strategies });
        if (!result.rows.some((row) => row.id === selectedId)) {
          const candidates = result.rows.filter((row) => row.id !== "buy_and_hold");
          selectedId = custom.enabled ? "custom" : (candidates.sort((a, b) => b.sharpe - a.sharpe)[0]?.id || "buy_and_hold");
        }
        replace(statusBox);
        drawResults();
      } catch (error) {
        toast(error.message, "error");
        if (!result) replace(statusBox, errorBox(error.message, run));
      } finally {
        running = false;
        runButton.disabled = false;
        runButton.textContent = "Run backtests";
        resultsBox.classList.remove("is-refreshing");
      }
    }

    drawStrategies();
    drawCustom();
    updateSelectionText();
    replace(editorBox,
      strategyList,
      h("div", { style: { marginTop: "12px", maxWidth: "620px" } },
        h("h4", { class: "card-title", style: { marginBottom: "4px" } }, "Build your own rule"),
        customBox));

    replace(root,
      pageHead("Backtests", "Test how rules would have performed on any symbol, side by side against simply buying and holding. This is for research only, and nothing here places trades."),
      filters(
        field("Symbol", input("symbol", { style: { width: "100px" }, spellcheck: false, "aria-label": "Symbol", id: "bt-symbol" })),
        field("From", input("start", { type: "date", class: "input input-date", max: today(), "aria-label": "Start date" })),
        field("To (blank = today)", input("end", { type: "date", class: "input input-date", max: today(), "aria-label": "End date" })),
        field("Starting capital", input("capital", { type: "number", class: "input input-num", min: 100, step: 1000, "aria-label": "Starting capital" })),
        field("Commission (bps)", input("commission_bps", { type: "number", class: "input input-num", min: 0, step: 1, "aria-label": "Commission in basis points" }), "transaction_costs"),
        field("Slippage (bps)", input("slippage_bps", { type: "number", class: "input input-num", min: 0, step: 1, "aria-label": "Slippage in basis points" }), "transaction_costs"),
        runButton),
      h("section", { class: "card", style: { marginBottom: "12px" } },
        h("div", { class: "card-head", style: { marginBottom: 0, alignItems: "center" } },
          h("div", {}, h("h3", { class: "card-title" }, "Strategies being tested"), selectionText),
          editButton),
        editorBox),
      statusBox,
      resultsBox);

    await run();
    return {};
  },
};
