// Your positions: value, profit, how they would move with the market, and what's coming up.
import { openAlertDrawer } from "../lib/alert-drawer.js";
import { api } from "../lib/api.js";
import { chartCard, rankedBars } from "../lib/charts.js";
import { MISSING, daysUntil, fmt, fmtDate, h, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { dataTable, deltaSpan, emptyBox, errorBox, field, pageHead, statusBadge, symbolLink, tile, toast } from "../lib/ui.js";

const money = (value) => (value === null || value === undefined ? MISSING : fmt(value, "usd0"));
const signedMoney = (value) => (value === null || value === undefined ? MISSING : `${value >= 0 ? "+" : "-"}$${fmt(Math.abs(value), "num0")}`);

function distance(value, label) {
  if (value === null || value === undefined) return h("span", { class: "sub-text" }, MISSING);
  return h("span", { title: `${label} is ${fmt(Math.abs(value), "pct1")} ${value < 0 ? "below" : "above"} the last price` }, fmt(value, "spct1"));
}

export default {
  title: () => "Portfolio",
  async mount(root) {
    let data = null;
    let editing = null; // the position being edited, or null when adding
    const tilesBox = h("div", { class: "grid cols-6" });
    const tableBox = h("section", { class: "card" });
    const eventsBox = h("section", { class: "card" });
    const sectorCard = chartCard({ title: "Where your money is", metric: "sector_weights", subtitle: "Share of the portfolio's value by sector", height: 220 });

    // ------------------------------------------------------------------ the form
    const inputs = {
      symbol: h("input", { class: "input", id: "position-symbol", placeholder: "e.g. NVDA", spellcheck: false, style: { width: "100px" }, "aria-label": "Ticker" }),
      shares: h("input", { class: "input input-num", id: "position-shares", type: "number", step: "any", min: "0", "aria-label": "Shares" }),
      cost: h("input", { class: "input input-num", id: "position-cost", type: "number", step: "any", min: "0", "aria-label": "Average price paid per share" }),
      opened: h("input", { class: "input", id: "position-opened", type: "date", "aria-label": "Date bought" }),
      stop: h("input", { class: "input input-num", id: "position-stop", type: "number", step: "any", min: "0", placeholder: "optional", "aria-label": "Stop price" }),
      target: h("input", { class: "input input-num", id: "position-target", type: "number", step: "any", min: "0", placeholder: "optional", "aria-label": "Target price" }),
      note: h("input", { class: "input", id: "position-note", placeholder: "optional", style: { width: "200px" }, "aria-label": "Note" }),
    };
    const submit = h("button", { class: "btn btn-primary", type: "submit", id: "position-save" }, "Add position");
    const cancel = h("button", { class: "btn btn-ghost", type: "button", hidden: true, onclick: () => resetForm() }, "Cancel");
    const formTitle = h("h3", { class: "card-title", style: { marginBottom: "8px" } }, "Add a position");

    function resetForm() {
      editing = null;
      Object.values(inputs).forEach((input) => { input.value = ""; });
      inputs.symbol.disabled = false;
      submit.textContent = "Add position";
      cancel.hidden = true;
      replace(formTitle, "Add a position");
    }

    function startEdit(row) {
      editing = row;
      inputs.symbol.value = row.symbol;
      inputs.symbol.disabled = true;
      inputs.shares.value = row.shares;
      inputs.cost.value = row.cost;
      inputs.opened.value = row.opened || "";
      inputs.stop.value = row.stop ?? "";
      inputs.target.value = row.target ?? "";
      inputs.note.value = row.note || "";
      submit.textContent = "Save changes";
      cancel.hidden = false;
      replace(formTitle, `Edit your ${row.symbol} position`);
      inputs.shares.focus();
    }

    const form = h("form", {
      class: "filters",
      onsubmit: async (event) => {
        event.preventDefault();
        const body = {
          symbol: inputs.symbol.value.trim().toUpperCase(),
          shares: inputs.shares.value,
          cost: inputs.cost.value,
          opened: inputs.opened.value,
          stop: inputs.stop.value,
          target: inputs.target.value,
          note: inputs.note.value,
        };
        if (!editing && !body.symbol) {
          toast("Type the ticker of what you own.", "error");
          inputs.symbol.focus();
          return;
        }
        if (!body.shares || !body.cost) {
          toast("Enter how many shares you own and the average price you paid.", "error");
          (body.shares ? inputs.cost : inputs.shares).focus();
          return;
        }
        submit.disabled = true;
        try {
          if (editing) {
            await api.patch(`/api/positions/${editing.id}`, body);
            toast(`Saved your ${editing.symbol} position.`);
          } else {
            const added = await api.post("/api/positions", body);
            toast(`Added ${added.shares} ${added.symbol} shares.`);
          }
          resetForm();
          await load();
        } catch (error) {
          toast(error.message, "error");
        }
        submit.disabled = false;
      },
    },
    field("Ticker", inputs.symbol), field("Shares", inputs.shares), field("Average price paid", inputs.cost, "position_cost"),
    field("Bought on", inputs.opened), field("Stop", inputs.stop, "stop_target"), field("Target", inputs.target, "stop_target"),
    field("Note", inputs.note), h("div", { style: { alignSelf: "flex-end", display: "flex", gap: "6px" } }, submit, cancel));

    // ------------------------------------------------------------------- the table
    const remove = async (row) => {
      if (!window.confirm(`Remove your ${row.symbol} position? Your journal keeps its entries.`)) return;
      try {
        await api.del(`/api/positions/${row.id}`);
        toast(`Removed ${row.symbol}.`);
        if (editing?.id === row.id) resetForm();
        await load();
      } catch (error) {
        toast(error.message, "error");
      }
    };

    const columns = [
      { key: "symbol", label: "Position", left: true, sticky: true, render: (row) => h("span", { style: { display: "inline-flex", gap: "6px", alignItems: "baseline" } }, symbolLink(row.symbol), h("span", { class: "sub-text" }, (row.name || "").slice(0, 22))) },
      { key: "shares", label: "Shares", render: (row) => fmt(row.shares, row.shares % 1 ? "num2" : "num0") },
      { key: "cost", label: "Avg cost", metric: "position_cost", fmt: "usd2" },
      { key: "last", label: "Last", fmt: "usd2" },
      { key: "value", label: "Value", metric: "portfolio_value", render: (row) => money(row.value) },
      { key: "pnl", label: "Profit", metric: "portfolio_pnl", render: (row) => deltaSpan(signedMoney(row.pnl).replace(/^[+-]/, ""), row.pnl, "good") },
      { key: "pnl_pct", label: "Profit %", render: (row) => deltaSpan(fmt(Math.abs(row.pnl_pct ?? 0), "pct1"), row.pnl_pct, "good") },
      { key: "day_pct", label: "Today", render: (row) => deltaSpan(fmt(Math.abs(row.day_pct ?? 0), "pct2"), row.day_pct, "good") },
      { key: "weight", label: "Weight", metric: "position_weight", fmt: "pct1" },
      { key: "beta", label: "Beta", metric: "beta", fmt: "num2" },
      { key: "trend", label: "Trend", metric: "trend_state", value: (row) => row.trend?.label, render: (row) => (row.trend ? statusBadge(row.trend.tone, row.trend.label) : MISSING) },
      { key: "stop_distance", label: "To stop", metric: "stop_target", render: (row) => distance(row.stop_distance, "Your stop") },
      { key: "target_distance", label: "To target", render: (row) => distance(row.target_distance, "Your target") },
      {
        key: "next_earnings", label: "Earnings", metric: "next_earnings", value: (row) => row.next_earnings,
        render: (row) => (row.next_earnings ? `${fmtDate(row.next_earnings)} (${daysUntil(row.next_earnings)}d)` : MISSING),
      },
      {
        key: "actions", label: "", sortable: false,
        render: (row) => h("span", { class: "row-actions" },
          h("button", { class: "btn btn-ghost btn-xs", type: "button", dataset: { edit: String(row.id) }, onclick: () => startEdit(row) }, "Edit"),
          h("button", {
            class: "btn btn-ghost btn-xs",
            type: "button",
            title: row.stop ? `Alert when ${row.symbol} closes below your stop` : "Set a stop first (Edit)",
            disabled: !row.stop,
            onclick: () => openAlertDrawer({ kind: "stock", symbol: row.symbol, op: "crosses_below", value: String(row.stop), note: `Stop for my ${row.symbol} position`, label: row.symbol }),
          }, "Stop alert"),
          h("a", { class: "btn btn-ghost btn-xs", href: `#/journal/new/${encodeURIComponent(row.symbol)}` }, "Log trade"),
          h("button", { class: "btn btn-ghost btn-xs", type: "button", dataset: { danger: "true", position: String(row.id) }, "aria-label": `Remove the ${row.symbol} position`, onclick: () => remove(row) }, "Remove")),
      },
    ];

    const draw = () => {
      const t = data.totals;
      const has = data.rows.length > 0;
      replace(tilesBox,
        tile({ label: "Value", metric: "portfolio_value", valueText: has ? money(t.value) : MISSING, foot: h("span", {}, `${t.count} position${t.count === 1 ? "" : "s"}`) }),
        tile({ label: "Profit", metric: "portfolio_pnl", valueText: has ? signedMoney(t.pnl) : MISSING, delta: has ? deltaSpan(fmt(Math.abs(t.pnl_pct ?? 0), "pct1"), t.pnl_pct, "good") : null, foot: h("span", {}, has ? `Paid ${money(t.basis)}` : "") }),
        tile({ label: data.market.state === "open" ? "Today" : "Last session", metric: "portfolio_pnl", valueText: has ? signedMoney(t.day_change) : MISSING, delta: has && t.day_pct !== null ? deltaSpan(fmt(Math.abs(t.day_pct), "pct2"), t.day_pct, "good") : null }),
        tile({ label: "Portfolio beta", metric: "portfolio_beta", value: t.beta, fmtCode: "num2", foot: h("span", {}, t.beta === null ? "" : t.beta > 1.1 ? "Moves more than the market" : t.beta < 0.9 ? "Moves less than the market" : "Moves about with the market") }),
        tile({ label: `If the S&P 500 falls ${Math.abs(t.stress_move)}%`, metric: "stress_test", valueText: t.stress_change === null || t.stress_change === undefined ? MISSING : signedMoney(t.stress_change), foot: h("span", {}, "Rough estimate from each stock's beta") }),
        tile({ label: "Largest position", metric: "position_weight", valueText: t.largest ? t.largest.symbol : MISSING, foot: h("span", {}, t.largest ? `${fmt(t.largest.weight, "pct0")} of the portfolio` : "") }));
      replace(tableBox,
        h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "Your positions"),
        dataTable({
          id: "portfolio",
          columns,
          rows: data.rows,
          rowKey: (row) => row.id,
          itemName: "position",
          reorder: (ids) => api.post("/api/positions/order", { ids }).catch((error) => toast(error.message, "error")),
          empty: "No positions yet. Add what you own above: the ticker, how many shares and the average price you paid.",
        }));
      sectorCard.render(rankedBars({ items: data.sectors.map((item) => ({ label: item.sector, value: item.weight })), yFmt: "pct1", name: "Weight", emptyText: "Add positions to see how your money is spread." }));
      const events = data.catalysts.map((event) => h("div", { class: "feed-row" },
        h("span", { class: "feed-date" }, fmtDate(event.date)),
        h("span", { class: "feed-text" }, event.label, info(event.metric),
          h("span", { class: "sub-text" }, event.days === 0 ? "today" : event.days === 1 ? "tomorrow" : `in ${event.days} days`),
          event.odds ? h("span", { class: "odds-note" }, `· ${event.odds.text}`) : null)));
      replace(eventsBox,
        h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "Coming up for your positions"),
        events.length ? h("div", { class: "feed" }, events) : emptyBox("No earnings dates known for your positions yet."));
    };

    const load = async () => {
      data = await api.get("/api/portfolio");
      draw();
    };

    replace(root,
      pageHead("Portfolio", "What you own, valued at the latest prices, and how it would likely move with the market. Everything stays on this computer.", info("portfolio_value")),
      h("section", { class: "card", style: { marginBottom: "10px" } }, formTitle, form),
      tilesBox,
      h("div", { class: "split-2-1 mt", style: { alignItems: "start" } }, tableBox, h("div", { class: "stack" }, sectorCard.el, eventsBox)));
    try {
      await load();
    } catch (error) {
      replace(tableBox, errorBox(error.message, load));
    }
    return { refresh: load };
  },
};
