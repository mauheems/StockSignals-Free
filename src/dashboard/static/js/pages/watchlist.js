// Your own list of stocks with every signal side by side.
import { api } from "../lib/api.js";
import { MISSING, daysUntil, fmt, fmtDate, h, replace } from "../lib/dom.js";
import { dataTable, emptyBox, errorBox, heatCell, pageHead, sparkline, statusBadge, symbolLink, toast } from "../lib/ui.js";

export function chipList(chips) {
  if (!chips?.length) return h("span", { class: "sub-text" }, MISSING);
  return h("div", { class: "chips", style: { flexWrap: "nowrap" } }, chips.map((chip) => h("span", { class: `chip tone-${chip.tone}`, title: chip.text }, chip.text)));
}

export default {
  title: () => "Watchlist",
  async mount(root) {
    const tableBox = h("section", { class: "card" });
    let tableEl = null;

    const remove = async (symbol) => {
      try {
        await api.del(`/api/watchlist/${encodeURIComponent(symbol)}`);
        toast(`Removed ${symbol}.`);
        await load();
      } catch (error) {
        toast(error.message, "error");
      }
    };

    const columns = [
      {
        key: "symbol", label: "Stock", left: true, sticky: true,
        render: (row) => h("span", { style: { display: "inline-flex", alignItems: "baseline", gap: "6px" } },
          h("button", {
            class: "btn btn-ghost btn-xs",
            type: "button",
            style: { height: "20px", padding: "0 4px" },
            title: `Remove ${row.symbol} from the watchlist`,
            "aria-label": `Remove ${row.symbol}`,
            dataset: { remove: row.symbol },
            onclick: () => remove(row.symbol),
          }, "×"),
          symbolLink(row.symbol),
          h("span", { class: "sub-text" }, (row.name || "").slice(0, 22))),
      },
      { key: "last", label: "Last", fmt: "num2" },
      { key: "change_pct", label: "Today", metric: "period_returns", render: (row) => heatCell(row.change_pct, 5, "spct2") },
      { key: "trend", label: "Trend", metric: "trend_state", value: (row) => row.trend?.label, render: (row) => (row.trend ? statusBadge(row.trend.tone, row.trend.label) : MISSING) },
      { key: "chips", label: "Flags", left: true, sortable: false, render: (row) => chipList(row.chips) },
      { key: "ret_5d", label: "1 week", render: (row) => heatCell(row.ret_5d, 10) },
      { key: "ret_1m", label: "1 month", render: (row) => heatCell(row.ret_1m, 20) },
      { key: "ret_ytd", label: "YTD", render: (row) => heatCell(row.ret_ytd, 50) },
      { key: "vs_sma50", label: "vs 50-day", metric: "vs_sma", fmt: "spct1" },
      { key: "vs_sma200", label: "vs 200-day", metric: "vs_sma", fmt: "spct1" },
      { key: "rs_rating", label: "RS rating", metric: "rs_rating", fmt: "num0" },
      { key: "rsi", label: "RSI", metric: "rsi", fmt: "num0" },
      { key: "rvol", label: "Rel. volume", metric: "rvol", render: (row) => (row.rvol === null || row.rvol === undefined ? MISSING : `${row.rvol.toFixed(1)}x`) },
      { key: "off_high", label: "From 52w high", metric: "off_high", fmt: "spct1" },
      { key: "iv30", label: "IV 30d", metric: "iv30", fmt: "pct0" },
      { key: "hv20", label: "Realized 20d", metric: "hv", fmt: "pct0" },
      { key: "pc_volume", label: "Put/call", metric: "pc_volume", fmt: "num2" },
      {
        key: "net_gex", label: "Dealer gamma", metric: "gamma_regime",
        render: (row) => (row.net_gex === null || row.net_gex === undefined ? MISSING : statusBadge(row.net_gex > 0 ? "good" : "critical", row.net_gex > 0 ? "Positive" : "Negative")),
      },
      { key: "short_pct", label: "Short % float", metric: "short_interest", value: (row) => (row.short_pct === null || row.short_pct === undefined ? null : row.short_pct * 100), fmt: "pct1" },
      { key: "dark_pool", label: "Off-exch. short", metric: "dark_pool_short", fmt: "pct0" },
      {
        key: "next_earnings", label: "Next earnings", metric: "next_earnings", value: (row) => row.next_earnings,
        render: (row) => {
          if (!row.next_earnings) return MISSING;
          const days = daysUntil(row.next_earnings);
          return `${fmtDate(row.next_earnings)}${days >= 0 && days < 100 ? ` (${days}d)` : ""}`;
        },
      },
      { key: "spark", label: "1 month", sortable: false, render: (row) => h("div", { class: "spark-cell" }, sparkline(row.spark, { width: 80, height: 20 })) },
    ];

    const draw = (data) => {
      if (!data.rows.length) {
        replace(tableBox, emptyBox("Your watchlist is empty. Add a ticker above to start."));
        tableEl = null;
        return;
      }
      if (tableEl) {
        tableEl.update(data.rows);
      } else {
        tableEl = dataTable({ columns, rows: data.rows, onRow: (row) => { location.hash = `#/stock/${encodeURIComponent(row.symbol)}`; }, rowKey: (row) => row.symbol });
        replace(tableBox, tableEl);
      }
    };

    const load = async () => draw(await api.get("/api/watchlist"));

    const input = h("input", { class: "input", placeholder: "Ticker, for example AMD", "aria-label": "Ticker to add", spellcheck: false, style: { width: "220px" } });
    const addButton = h("button", { class: "btn btn-primary", type: "submit" }, "Add to watchlist");
    const form = h("form", {
      style: { display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap", marginBottom: "12px" },
      onsubmit: async (event) => {
        event.preventDefault();
        const symbol = input.value.trim().toUpperCase();
        if (!symbol) {
          toast("Type a ticker to add, for example AMD.");
          input.focus();
          return;
        }
        addButton.disabled = true;
        addButton.textContent = "Adding…";
        try {
          const result = await api.post("/api/watchlist", { symbol });
          toast(`Added ${result.symbol}. Options and company data will fill in shortly.`);
          input.value = "";
          await load();
        } catch (error) {
          toast(error.message, "error");
        } finally {
          addButton.disabled = false;
          addButton.textContent = "Add to watchlist";
        }
      },
    }, input, addButton, h("span", { class: "sub-text" }, "Any US ticker Yahoo Finance knows, including ETFs."));

    replace(root,
      pageHead("Watchlist", "Your stocks with trend, momentum, options and short interest side by side. Select a row for the full picture, or find names in the S&P 500 Scanner."),
      form,
      tableBox,
      h("p", { class: "table-note" }, "These stocks also appear on the Rotation page (choose the Watchlist universe) and in Next catalysts on the Overview."));

    try {
      await load();
    } catch (error) {
      replace(tableBox, errorBox(error.message, load));
    }
    return { refresh: load };
  },
};
