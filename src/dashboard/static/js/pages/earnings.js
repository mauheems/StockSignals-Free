// Earnings season: who reports when, how big a move the options market expects, and how
// that compares with the stock's usual reaction.
import { api } from "../lib/api.js";
import { MISSING, fmt, fmtAgo, fmtDate, h, prefs, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { dataTable, emptyBox, errorBox, field, filters, pageHead, segmented, statusBadge, symbolLink, tile } from "../lib/ui.js";

const SCOPES = [
  { value: "mine", label: "Your stocks" },
  { value: "sp500", label: "S&P 500" },
  { value: "all", label: "All over $2B" },
];
const PERIODS = [
  { value: "7", label: "Next 7 days" },
  { value: "14", label: "Next 14 days" },
  { value: "28", label: "Next 4 weeks" },
];
const TIMING = { before: "Before the open", after: "After the close", during: "During the day" };

const weekday = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString("en-US", { weekday: "short" });
const outside = (href, text) => h("a", { href, target: "_blank", rel: "noopener noreferrer" }, text);

function moveCell(row) {
  if (row.implied_move === null || row.implied_move === undefined) return h("span", { class: "sub-text" }, MISSING);
  const bigger = row.typical_move && row.implied_move > row.typical_move * 1.25;
  return h("span", { title: `Options expiring ${row.implied_expiry} price a move of about ±${fmt(row.implied_move, "pct1")}` },
    `±${fmt(row.implied_move, "pct1")}`, bigger ? h("span", { class: "sub-text" }, " · above usual") : null);
}

const columns = [
  { key: "date", label: "Date", left: true, render: (row) => `${weekday(row.date)} ${fmtDate(row.date)}`, value: (row) => row.date },
  { key: "timing", label: "When", left: true, metric: "earnings_timing", value: (row) => row.timing || "", render: (row) => TIMING[row.timing] || h("span", { class: "sub-text" }, "Not announced") },
  {
    key: "symbol", label: "Company", left: true, sticky: true,
    render: (row) => h("span", { style: { display: "inline-flex", gap: "6px", alignItems: "baseline", flexWrap: "wrap" } },
      symbolLink(row.symbol), h("span", { class: "sub-text" }, (row.company || "").slice(0, 28)),
      row.in_portfolio ? statusBadge("good", "Yours") : row.in_watchlist ? statusBadge("neutral", "Watchlist") : null),
  },
  { key: "eps_estimate", label: "EPS estimate", metric: "eps_estimate", fmt: "usd2" },
  { key: "implied_move", label: "Options expect", metric: "implied_move_earnings", render: moveCell },
  { key: "typical_move", label: "Usual move", metric: "typical_move", render: (row) => (row.typical_move === null || row.typical_move === undefined ? MISSING : h("span", { title: `Average of the last ${row.past_reports} reports; largest ${fmt(row.largest_move, "pct1")}` }, `±${fmt(row.typical_move, "pct1")}`)) },
  { key: "beat_odds", label: "Beat odds", metric: "earnings_odds", render: (row) => (row.beat_odds === null || row.beat_odds === undefined ? MISSING : outside(row.beat_url, `${Math.round(row.beat_odds * 100)}% ↗`)) },
  { key: "market_cap", label: "Market value", fmt: "usdcompact" },
];

export default {
  title: () => "Earnings Season",
  async mount(root) {
    const state = { scope: "mine", period: "14", ...prefs.get("earnings", {}) };
    const save = () => prefs.set("earnings", state);
    const tilesBox = h("div", { class: "grid cols-4" });
    const tableBox = h("section", { class: "card" });
    const freshness = h("p", { class: "sub-text", style: { margin: "-6px 0 10px" } });
    const search = h("input", { class: "input", type: "search", placeholder: "Filter by ticker or name", "aria-label": "Filter companies", style: { width: "200px" }, oninput: () => draw() });
    let data = null;

    const visible = () => {
      const until = new Date();
      until.setDate(until.getDate() + Number(state.period));
      const end = until.toLocaleDateString("en-CA");
      const text = search.value.trim().toUpperCase();
      return data.rows.filter((row) => row.date <= end
        && (state.scope === "all" || (state.scope === "sp500" ? row.in_sp500 : row.in_watchlist || row.in_portfolio))
        && (!text || row.symbol.startsWith(text) || (row.company || "").toUpperCase().includes(text)));
    };

    const draw = () => {
      const rows = visible();
      const mine = data.rows.filter((row) => row.in_watchlist || row.in_portfolio);
      const nextMine = mine[0];
      const week = new Date();
      week.setDate(week.getDate() + 7);
      const thisWeek = data.rows.filter((row) => row.date <= week.toLocaleDateString("en-CA"));
      const biggest = [...thisWeek].sort((a, b) => (b.market_cap || 0) - (a.market_cap || 0)).slice(0, 4).map((row) => row.symbol);
      replace(tilesBox,
        tile({ label: "Companies reporting in 7 days", metric: "earnings_timing", value: thisWeek.length, fmtCode: "num0", foot: h("span", {}, "Worth over $2 billion") }),
        tile({ label: "Biggest names this week", metric: "earnings_timing", valueText: biggest.join(", ") || MISSING }),
        tile({ label: "Your stocks reporting", metric: "next_earnings", value: mine.length, fmtCode: "num0", foot: h("span", {}, "In the next 4 weeks") }),
        tile({ label: "Your next report", metric: "next_earnings", valueText: nextMine ? nextMine.symbol : MISSING, foot: h("span", {}, nextMine ? `${weekday(nextMine.date)} ${fmtDate(nextMine.date)}${nextMine.timing ? `, ${TIMING[nextMine.timing].toLowerCase()}` : ""}` : "") }));
      replace(tableBox,
        h("div", { class: "card-head" }, h("h3", { class: "card-title" }, `${rows.length} report${rows.length === 1 ? "" : "s"}`), search),
        h("p", { class: "card-sub" }, "\"Options expect\" is the move priced into options expiring just after the report; \"Usual move\" is the stock's average reaction to its recent reports. When options expect much more than usual, the market sees this report as unusually risky."),
        dataTable({
          id: "earnings-season",
          columns,
          rows,
          rowKey: (row) => row.symbol,
          maxHeight: 760,
          empty: state.scope === "mine" ? "None of your watchlist or portfolio stocks reports in this period." : "No reports in this period.",
        }));
    };

    const load = async () => {
      data = await api.get("/api/earnings");
      replace(freshness, data.updated ? `Yahoo Finance earnings calendar, updated ${fmtAgo(data.updated)}. Implied moves need the stock's option chain, so they show for your watchlist, positions and the main ETFs.` : "The earnings calendar is still loading.");
      draw();
    };

    replace(root,
      pageHead("Earnings season", "Who reports when, and how big a move the market expects. A report can move a stock more in one day than it usually moves in a month.", info("implied_move_earnings")),
      freshness,
      filters(
        field("Show", segmented(SCOPES, state.scope, (value) => { state.scope = value; save(); draw(); }, "Which companies")),
        field("Period", segmented(PERIODS, state.period, (value) => { state.period = value; save(); draw(); }, "Period"))),
      tilesBox,
      h("div", { class: "mt" }, tableBox));
    try {
      await load();
    } catch (error) {
      replace(tableBox, errorBox(error.message, load));
    }
    return { refresh: load };
  },
};
