// Every S&P 500 member, ranked and filtered, for finding stocks to look at.
import { api } from "../lib/api.js";
import { MISSING, h, prefs, replace } from "../lib/dom.js";
import { dataTable, emptyBox, field, filters, heatCell, pageHead, select, statusBadge, symbolLink, toast } from "../lib/ui.js";

const SECTOR_SHORT = {
  "Communication Services": "Comm. Services",
  "Consumer Discretionary": "Discretionary",
  "Consumer Staples": "Staples",
  "Information Technology": "Technology",
};

const PRESETS = {
  all: { label: "All members", filter: () => true, sort: { key: "symbol", dir: "asc" } },
  gainers: { label: "Biggest gainers today", filter: (row) => row.chg_1d > 0, sort: { key: "chg_1d", dir: "desc" } },
  losers: { label: "Biggest losers today", filter: (row) => row.chg_1d < 0, sort: { key: "chg_1d", dir: "asc" } },
  rvol: { label: "Unusual volume", filter: (row) => row.rvol >= 1.5, sort: { key: "rvol", dir: "desc" } },
  highs: { label: "At 52-week highs", filter: (row) => row.at_high, sort: { key: "ret_1m", dir: "desc" } },
  lows: { label: "At 52-week lows", filter: (row) => row.at_low, sort: { key: "ret_1m", dir: "asc" } },
  rs: { label: "Relative strength leaders (RS 90+)", filter: (row) => row.rs_rating >= 90, sort: { key: "rs_rating", dir: "desc" } },
  uptrend: { label: "Uptrend, within 5% of high", filter: (row) => row.trend?.id === "uptrend" && row.off_high >= -5, sort: { key: "rs_rating", dir: "desc" } },
  oversold: { label: "Oversold (RSI under 30)", filter: (row) => row.rsi !== null && row.rsi < 30, sort: { key: "rsi", dir: "asc" } },
  overbought: { label: "Overbought (RSI over 70)", filter: (row) => row.rsi !== null && row.rsi > 70, sort: { key: "rsi", dir: "desc" } },
  golden: { label: "Golden cross this week", filter: (row) => row.golden_cross, sort: { key: "symbol", dir: "asc" } },
  death: { label: "Death cross this week", filter: (row) => row.death_cross, sort: { key: "symbol", dir: "asc" } },
};

export default {
  title: () => "S&P 500 Scanner",
  async mount(root) {
    const state = { preset: "gainers", sector: "", search: "", ...prefs.get("scanner", {}) };
    if (!PRESETS[state.preset]) state.preset = "gainers";
    let data = null;
    const tableBox = h("section", { class: "card" });
    const countBox = h("span", { class: "sub-text" });
    const watch = new Set();

    const toggleWatch = async (symbol, button) => {
      button.disabled = true;
      try {
        if (watch.has(symbol)) {
          await api.del(`/api/watchlist/${encodeURIComponent(symbol)}`);
          watch.delete(symbol);
          toast(`Removed ${symbol} from the watchlist.`);
        } else {
          await api.post("/api/watchlist", { symbol });
          watch.add(symbol);
          toast(`Added ${symbol} to the watchlist.`);
        }
        button.textContent = watch.has(symbol) ? "Watching" : "Watch";
      } catch (error) {
        toast(error.message, "error");
      }
      button.disabled = false;
    };

    const columns = [
      {
        key: "symbol", label: "Stock", left: true, sticky: true,
        render: (row) => {
          const button = h("button", {
            class: "btn btn-xs",
            type: "button",
            style: { height: "20px", padding: "0 5px", marginRight: "6px", minWidth: "62px", justifyContent: "center" },
            "aria-label": `Toggle ${row.symbol} on the watchlist`,
            dataset: { watch: row.symbol },
            onclick: () => toggleWatch(row.symbol, button),
          }, watch.has(row.symbol) ? "Watching" : "Watch");
          return h("span", {}, button, symbolLink(row.symbol), " ", h("span", { class: "sub-text" }, row.name.slice(0, 18)));
        },
      },
      { key: "sector", label: "Sector", left: true, render: (row) => h("span", { class: "sub-text" }, SECTOR_SHORT[row.sector] || row.sector) },
      { key: "last", label: "Last", fmt: "num2" },
      { key: "chg_1d", label: "Today", metric: "period_returns", render: (row) => heatCell(row.chg_1d, 5, "spct2") },
      { key: "ret_5d", label: "1 week", render: (row) => heatCell(row.ret_5d, 10) },
      { key: "ret_1m", label: "1 month", render: (row) => heatCell(row.ret_1m, 20) },
      { key: "ret_3m", label: "3 months", render: (row) => heatCell(row.ret_3m, 35) },
      { key: "ret_ytd", label: "YTD", render: (row) => heatCell(row.ret_ytd, 50) },
      { key: "trend", label: "Trend", metric: "trend_state", value: (row) => row.trend?.label, render: (row) => (row.trend ? statusBadge(row.trend.tone, row.trend.label) : MISSING) },
      { key: "rs_rating", label: "RS rating", metric: "rs_rating", fmt: "num0" },
      { key: "vs_sma50", label: "vs 50-day", metric: "vs_sma", fmt: "spct1" },
      { key: "vs_sma200", label: "vs 200-day", metric: "vs_sma", fmt: "spct1" },
      { key: "off_high", label: "From 52w high", metric: "off_high", fmt: "spct1" },
      { key: "rsi", label: "RSI", metric: "rsi", fmt: "num0" },
      { key: "rvol", label: "Rel. volume", metric: "rvol", render: (row) => (row.rvol === null ? MISSING : `${row.rvol.toFixed(1)}x`) },
    ];

    const draw = () => {
      prefs.set("scanner", state);
      if (!data.rows.length) {
        replace(tableBox, emptyBox("The scanner fills in after S&P 500 member prices finish loading."));
        replace(countBox, "");
        return;
      }
      const preset = PRESETS[state.preset];
      const search = state.search.trim().toUpperCase();
      const rows = data.rows.filter((row) => preset.filter(row)
        && (!state.sector || row.sector === state.sector)
        && (!search || row.symbol.startsWith(search) || row.name.toUpperCase().includes(search)));
      replace(countBox, `${rows.length} of ${data.rows.length} members`);
      replace(tableBox, dataTable({
        columns,
        rows,
        sort: preset.sort,
        maxHeight: 720,
        empty: "No stocks match these filters right now.",
        rowKey: (row) => row.symbol,
        onRow: (row) => { location.hash = `#/stock/${encodeURIComponent(row.symbol)}`; },
      }));
    };

    const load = async () => {
      data = await api.get("/api/scanner");
      watch.clear();
      data.watchlist.forEach((symbol) => watch.add(symbol));
      fillSectors();
      draw();
    };

    const sectorSelect = h("select", { class: "input", "aria-label": "Sector", onchange: (event) => { state.sector = event.target.value; draw(); } });
    function fillSectors() {
      replace(sectorSelect, h("option", { value: "" }, "All sectors"), data.sectors.map((sector) => h("option", { value: sector }, sector)));
      sectorSelect.value = state.sector;
    }

    replace(root,
      pageHead("S&P 500 scanner", "Rank and filter every S&P 500 member by trend, momentum and volume. Select a row for full details."),
      filters(
        field("Show", select(Object.entries(PRESETS).map(([value, item]) => ({ value, label: item.label })), state.preset, (value) => {
          state.preset = value;
          draw();
        }, { "aria-label": "Scan" })),
        field("Sector", sectorSelect),
        field("Search", h("input", {
          class: "input",
          placeholder: "Ticker or name",
          value: state.search,
          "aria-label": "Search members",
          spellcheck: false,
          oninput: (event) => {
            state.search = event.target.value;
            draw();
          },
        })),
        h("div", { style: { alignSelf: "center" } }, countBox)),
      tableBox);

    await load();
    return { refresh: load };
  },
};
