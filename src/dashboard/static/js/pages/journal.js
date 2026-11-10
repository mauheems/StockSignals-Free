// Trade journal: why you bought or sold, what the signals said then, and how it turned out.
import { api } from "../lib/api.js";
import { MISSING, fmt, fmtDate, h, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { deltaSpan, emptyBox, errorBox, field, pageHead, segmented, statusBadge, symbolLink, tile, toast } from "../lib/ui.js";

const ACTIONS = [{ value: "buy", label: "Bought" }, { value: "sell", label: "Sold" }, { value: "note", label: "Just a note" }];
const ACTION_TONE = { buy: "good", sell: "critical", note: "neutral" };
const ACTION_WORD = { buy: "Bought", sell: "Sold", note: "Note" };
const PROMPTS = "Why now? What would prove you wrong? Which signals or news did you look at? How much are you willing to lose?";

const pct = (value, code = "pct1") => (value === null || value === undefined ? MISSING : fmt(value, code));
const today = () => new Date().toLocaleDateString("en-CA");

/** What the dashboard said at the moment of the entry, in a few lines. */
function snapshotView(snapshot) {
  if (!snapshot) return h("p", { class: "sub-text" }, "No signal snapshot saved with this entry.");
  const market = snapshot.market || {};
  const stock = snapshot.stock || {};
  const items = [
    ["Market Health", market.health === null || market.health === undefined ? MISSING : `${Math.round(market.health)} (${market.health_label})`, "market_health"],
    ["VIX", pct(market.vix, "num1"), "vix"],
    ["S&P 500 vs 200-day", pct(market.spx_vs_200, "spct1"), "spx_vs_200"],
    ["Trend", stock.trend || MISSING, "trend_state"],
    ["RSI", pct(stock.rsi, "num0"), "rsi"],
    ["vs 200-day", pct(stock.vs_sma200, "spct1"), "vs_sma"],
    ["RS rating", pct(stock.rs_rating, "num0"), "rs_rating"],
    ["Rel. volume", stock.rvol === null || stock.rvol === undefined ? MISSING : `${stock.rvol.toFixed(1)}x`, "rvol"],
    ["IV 30d", pct(stock.iv30, "pct0"), "iv30"],
    ["Next earnings", stock.next_earnings ? fmtDate(stock.next_earnings) : MISSING, "next_earnings"],
    ["Beat odds", stock.beat_odds === null || stock.beat_odds === undefined ? MISSING : `${Math.round(stock.beat_odds * 100)}%`, "earnings_odds"],
  ];
  return h("div", { class: "snapshot-grid" }, items.map(([label, value, metric]) => h("div", { class: "kv-row" },
    h("span", { class: "kv-label" }, label, info(metric)), h("span", { class: "kv-value" }, value))));
}

export default {
  title: () => "Journal",
  async mount(root, { params }) {
    const draft = { action: "buy", symbol: params[0] === "new" && params[1] ? params[1].toUpperCase() : "", update: true };
    let data = null;
    let filter = "all";
    const reviewBox = h("div", { class: "grid cols-5" });
    const listBox = h("div", { class: "stack" });

    const symbolList = h("datalist", { id: "journal-symbols" });
    const inputs = {
      date: h("input", { class: "input", id: "journal-date", type: "date", value: today(), max: today(), "aria-label": "Date" }),
      symbol: h("input", { class: "input", id: "journal-symbol", list: "journal-symbols", value: draft.symbol, placeholder: "e.g. NVDA", spellcheck: false, style: { width: "110px" }, "aria-label": "Ticker" }),
      shares: h("input", { class: "input input-num", id: "journal-shares", type: "number", step: "any", min: "0", "aria-label": "Shares" }),
      price: h("input", { class: "input input-num", id: "journal-price", type: "number", step: "any", min: "0", "aria-label": "Price per share" }),
      reason: h("textarea", { class: "input journal-reason", id: "journal-reason", rows: 4, placeholder: PROMPTS, maxlength: 4000, "aria-label": "Why" }),
      update: h("input", { type: "checkbox", id: "journal-update", checked: true }),
    };
    const tradeFields = h("div", { class: "filters", style: { margin: 0 } }, field("Shares", inputs.shares), field("Price per share", inputs.price),
      h("label", { class: "check-inline", for: "journal-update" }, inputs.update, "Also update my positions", info("journal_positions")));
    const actionBox = h("div", {});
    const drawAction = () => {
      replace(actionBox, segmented(ACTIONS, draft.action, (value) => {
        draft.action = value;
        tradeFields.hidden = value === "note";
        drawAction();
      }, "What you did"));
      tradeFields.hidden = draft.action === "note";
    };
    drawAction();
    const save = h("button", { class: "btn btn-primary", type: "submit", id: "journal-save" }, "Save entry");
    const form = h("form", {
      class: "stack",
      onsubmit: async (event) => {
        event.preventDefault();
        // Say what's missing right away, without a round trip to the server.
        const missing = !inputs.symbol.value.trim() ? [inputs.symbol, "Type the ticker this entry is about."]
          : draft.action !== "note" && !inputs.shares.value ? [inputs.shares, "Enter how many shares you traded."]
            : draft.action !== "note" && !inputs.price.value ? [inputs.price, "Enter the price per share."]
              : !inputs.reason.value.trim() ? [inputs.reason, "Write down why: that is what makes the journal useful later."]
                : null;
        if (missing) {
          toast(missing[1], "error");
          missing[0].focus();
          return;
        }
        save.disabled = true;
        try {
          const result = await api.post("/api/journal", {
            date: inputs.date.value,
            symbol: inputs.symbol.value.trim().toUpperCase(),
            action: draft.action,
            shares: inputs.shares.value,
            price: inputs.price.value,
            reason: inputs.reason.value,
            update_positions: inputs.update.checked,
          });
          toast(result.positions ? `Saved. ${result.positions}` : "Saved to your journal, with what the signals said right now.");
          inputs.reason.value = "";
          inputs.shares.value = "";
          inputs.price.value = "";
          await load();
        } catch (error) {
          toast(error.message, "error");
        }
        save.disabled = false;
      },
    },
    h("div", { class: "filters", style: { margin: 0 } }, field("What you did", actionBox), field("Date", inputs.date), field("Ticker", inputs.symbol)),
    tradeFields,
    field("Why", inputs.reason, "journal"),
    h("div", {}, save), symbolList);

    const entryCard = (row) => {
      const details = h("div", { class: "entry-details", hidden: true }, snapshotView(row.snapshot));
      const toggle = h("button", {
        class: "link-btn",
        type: "button",
        "aria-expanded": "false",
        onclick: () => {
          details.hidden = !details.hidden;
          toggle.setAttribute("aria-expanded", String(!details.hidden));
          toggle.textContent = details.hidden ? "What the signals said then" : "Hide the signals";
        },
      }, "What the signals said then");
      const outcome = row.action === "note" || row.move === null || row.move === undefined
        ? null
        : h("span", { class: "entry-outcome" },
          `${row.symbol} since: `, deltaSpan(fmt(Math.abs(row.move), "pct1"), row.move, "good"),
          row.spy_move !== null && row.spy_move !== undefined ? h("span", { class: "sub-text" }, ` · S&P 500 ${fmt(row.spy_move, "spct1")}`) : null,
          row.result !== null && row.result !== undefined && row.days >= 5
            ? statusBadge(row.result > 0 ? "good" : "critical", row.result > 0 ? "Worked out so far" : "Went against you so far")
            : h("span", { class: "sub-text" }, row.days < 5 ? " · too early to judge" : ""),
          info("journal_result"));
      const editReason = async () => {
        const text = window.prompt("Edit your reasoning:", row.reason);
        if (text === null) return;
        try {
          await api.patch(`/api/journal/${row.id}`, { reason: text });
          await load();
        } catch (error) {
          toast(error.message, "error");
        }
      };
      const remove = async () => {
        if (!window.confirm("Delete this journal entry? Your positions are not changed.")) return;
        try {
          await api.del(`/api/journal/${row.id}`);
          await load();
        } catch (error) {
          toast(error.message, "error");
        }
      };
      return h("article", { class: "card journal-entry", dataset: { entry: String(row.id) } },
        h("div", { class: "card-head" },
          h("div", { class: "entry-title" },
            statusBadge(ACTION_TONE[row.action], ACTION_WORD[row.action]), symbolLink(row.symbol),
            row.shares ? h("span", {}, `${fmt(row.shares, row.shares % 1 ? "num2" : "num0")} @ ${fmt(row.price, "usd2")}`) : null,
            h("span", { class: "sub-text" }, fmtDate(row.date, true))),
          h("div", { class: "card-actions" },
            h("button", { class: "btn btn-ghost btn-xs", type: "button", onclick: editReason }, "Edit"),
            h("button", { class: "btn btn-ghost btn-xs", type: "button", dataset: { danger: "true" }, onclick: remove }, "Delete"))),
        h("p", { class: "entry-reason" }, row.reason),
        outcome, h("div", {}, toggle), details);
    };

    const draw = () => {
      const r = data.review;
      replace(symbolList, data.symbols.map((symbol) => h("option", { value: symbol })));
      replace(reviewBox,
        tile({ label: "Entries", metric: "journal", value: r.entries, fmtCode: "num0" }),
        tile({ label: "Trades a week or older", metric: "journal_result", value: r.decisions, fmtCode: "num0" }),
        tile({ label: "Worked out so far", metric: "journal_result", valueText: r.decisions ? `${r.worked} of ${r.decisions}` : MISSING, foot: h("span", {}, r.decisions ? fmt((r.worked / r.decisions) * 100, "pct0") : "") }),
        tile({ label: "Average since buying", metric: "journal_result", value: r.avg_buy, fmtCode: "spct1" }),
        tile({ label: "Buys vs the S&P 500", metric: "journal_result", value: r.avg_buy_vs_market, fmtCode: "spct1", foot: h("span", {}, "Average difference since each buy") }));
      const rows = data.rows.filter((row) => filter === "all" || row.action === filter);
      replace(listBox,
        segmented([{ value: "all", label: "All" }, { value: "buy", label: "Buys" }, { value: "sell", label: "Sales" }, { value: "note", label: "Notes" }], filter, (value) => {
          filter = value;
          draw();
        }, "Show entries"),
        rows.length ? rows.map(entryCard) : emptyBox(data.rows.length ? "No entries of this kind." : "No entries yet. Log your next trade above, while the reasons are fresh."));
    };

    const load = async () => {
      data = await api.get("/api/journal");
      draw();
    };

    replace(root,
      pageHead("Journal", "Write down why you buy or sell. The app saves what its signals said at that moment, and shows later how it turned out, so you can see which reasons hold up.", info("journal")),
      h("section", { class: "card", style: { marginBottom: "10px" } }, h("h3", { class: "card-title", style: { marginBottom: "8px" } }, "New entry"), form),
      h("div", { class: "section-title" }, "Your review", h("span", { class: "section-sub" }, "Results so far, not final: an open trade can still turn")),
      reviewBox,
      h("div", { class: "section-title" }, "Entries"),
      listBox);
    try {
      await load();
    } catch (error) {
      replace(listBox, errorBox(error.message, load));
    }
    if (draft.symbol) inputs.reason.focus();
    return { refresh: load };
  },
};
