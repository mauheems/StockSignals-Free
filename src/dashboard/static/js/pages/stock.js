// Everything about one stock: trend, momentum, volume, options, short interest, fundamentals.
import { openAlertDrawer } from "../lib/alert-drawer.js";
import { api } from "../lib/api.js";
import { chartCard, lineChart } from "../lib/charts.js";
import { MISSING, compact, daysUntil, debounce, fmt, fmtDate, h, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { dataTable, deltaSpan, emptyBox, heatCell, kvRow, pageHead, statusBadge, toast } from "../lib/ui.js";

const CHECK_ICONS = { good: "▲", warn: "◆", bad: "▼", info: "●" };
const CHECK_WORDS = { good: "In favour", warn: "Watch out", bad: "Against", info: "To know" };

/** "Before you buy": the stock's situation in a few plain checks. */
function checklistCard(checks, summary) {
  if (!checks?.length) return null;
  return h("section", { class: "card", id: "checklist" },
    h("div", { class: "card-head" },
      h("h3", { class: "card-title" }, "Before you buy", info("checklist")),
      h("span", { class: "sub-text" }, `${summary.good} in favour · ${summary.warn} to watch · ${summary.bad} against`)),
    h("div", { class: "checklist" }, checks.map((check) => h("div", { class: `check check-${check.state}` },
      h("span", { class: "check-icon", "aria-label": CHECK_WORDS[check.state], title: CHECK_WORDS[check.state] }, CHECK_ICONS[check.state]),
      h("div", {},
        h("div", { class: "check-label" }, check.label, info(check.metric)),
        h("div", { class: "check-text" }, check.text))))),
    h("p", { class: "sub-text", style: { marginTop: "6px" } }, "Rules of thumb to put the numbers in context, not advice. A red check can be exactly why a trade is interesting, if you know it."));
}

/** Your own lists and notes for this stock. */
function notesCard(data) {
  const tagsBox = h("div", { class: "chips" });
  const saved = h("span", { class: "sub-text", "aria-live": "polite" });
  let tags = data.tags || [];
  const all = data.all_tags || [];
  const drawTags = () => replace(tagsBox,
    tags.length ? tags.map((tag) => h("span", { class: "chip" }, tag,
      h("button", {
        type: "button",
        "aria-label": `Remove from ${tag}`,
        title: `Remove from ${tag}`,
        onclick: async () => {
          try {
            tags = (await api.post("/api/tags/remove", { symbol: data.symbol, tag })).tags;
            drawTags();
          } catch (error) {
            toast(error.message, "error");
          }
        },
      }, "×"))) : h("span", { class: "sub-text" }, "Not in any of your lists yet."));
  drawTags();
  const tagInput = h("input", { class: "input", list: "tag-names", placeholder: "List name, e.g. Semis", "aria-label": "Add to a list", id: "tag-input", maxlength: 30, style: { width: "170px" } });
  const addTag = async (event) => {
    event.preventDefault();
    const tag = tagInput.value.trim();
    if (!tag) {
      toast("Type a list name, such as Semis or Ideas.");
      tagInput.focus();
      return;
    }
    try {
      tags = (await api.post("/api/tags", { symbol: data.symbol, tag })).tags;
      tagInput.value = "";
      drawTags();
    } catch (error) {
      toast(error.message, "error");
    }
  };
  const note = h("textarea", { class: "input stock-note", id: "stock-note", rows: 3, maxlength: 5000, placeholder: "Your thoughts on this stock: thesis, levels to watch, what would change your mind.", "aria-label": "Your note" }, data.note?.text || "");
  const store = debounce(async () => {
    try {
      await api.post(`/api/notes/${encodeURIComponent(data.symbol)}`, { text: note.value });
      replace(saved, note.value.trim() ? "Saved" : "Note removed");
    } catch (error) {
      replace(saved, error.message);
    }
  }, 700);
  note.addEventListener("input", () => {
    replace(saved, "Saving…");
    store();
  });
  return h("section", { class: "card" },
    h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "Your lists and notes", info("notes_tags")),
    tagsBox,
    h("form", { class: "filters", style: { margin: "8px 0" }, onsubmit: addTag }, tagInput, h("button", { class: "btn btn-xs", type: "submit" }, "Add to list"),
      h("datalist", { id: "tag-names" }, all.map((name) => h("option", { value: name })))),
    note, saved);
}

/** Your own position in this stock, if you have one. */
function positionLine(rows) {
  if (!rows?.length) return null;
  const shares = rows.reduce((sum, row) => sum + row.shares, 0);
  const pnl = rows.reduce((sum, row) => sum + (row.pnl || 0), 0);
  const basis = rows.reduce((sum, row) => sum + row.basis, 0);
  const pct = basis ? (pnl / basis) * 100 : null;
  return h("p", { class: "earnings-odds" },
    h("span", { class: "ext-tag" }, "Your position"),
    h("span", {}, `${fmt(shares, shares % 1 ? "num2" : "num0")} shares, ${pnl >= 0 ? "up" : "down"} $${fmt(Math.abs(pnl), "num0")}`),
    deltaSpan(fmt(Math.abs(pct ?? 0), "pct1"), pct, "good"),
    h("a", { href: "#/portfolio" }, "Portfolio"));
}

const KIND_WORDS = { buy: "Bought", sell: "Sold", award: "Award", gift: "Gift", "option exercise": "Option exercise", other: "Other" };

/** SEC filings and insider trades, loaded on demand. */
function filingsSection(result) {
  if (!result) return h("section", { class: "card" }, h("h3", { class: "card-title" }, "Filings and insider trades", info("filings")), emptyBox("Loading filings…"));
  const summary = result.summary;
  const tradeRows = result.trades.filter((trade) => trade.kind === "buy" || trade.kind === "sell").slice(0, 12);
  const summaryText = summary.buys || summary.sells
    ? `Last 6 months: ${summary.buys} open-market buy${summary.buys === 1 ? "" : "s"} ($${compact(summary.bought_value)}) and ${summary.sells} sale${summary.sells === 1 ? "" : "s"} ($${compact(summary.sold_value)}) by insiders.`
    : "No open-market insider buys or sales in the last 6 months.";
  return h("div", { class: "grid grid-2", style: { alignItems: "start" } },
    h("section", { class: "card" },
      h("h3", { class: "card-title", style: { marginBottom: "4px" } }, "Insider trades", info("insider_trades")),
      h("p", { class: "card-sub" }, summaryText),
      dataTable({
        id: "insider-trades",
        columns: [
          { key: "date", label: "Date", left: true, render: (row) => fmtDate(row.date, true), value: (row) => row.date },
          { key: "insider", label: "Insider", left: true, render: (row) => h("span", {}, row.insider, h("span", { class: "sub-text" }, row.position ? ` · ${row.position}` : "")) },
          { key: "kind", label: "Trade", left: true, render: (row) => statusBadge(row.kind === "buy" ? "good" : "critical", KIND_WORDS[row.kind]) },
          { key: "shares", label: "Shares", render: (row) => compact(row.shares) },
          { key: "value", label: "Value", render: (row) => (row.value ? `$${compact(row.value)}` : MISSING) },
        ],
        rows: tradeRows,
        rowKey: (row) => `${row.date}-${row.insider}-${row.shares}`,
        empty: "No open-market insider buys or sales listed.",
      })),
    h("section", { class: "card" },
      h("h3", { class: "card-title", style: { marginBottom: "4px" } }, "SEC filings", info("filings")),
      h("p", { class: "card-sub" }, "Newest first. Important events (8-K), quarterly and annual reports are marked."),
      h("div", { class: "feed" }, result.filings.slice(0, 15).map((filing) => h("div", { class: "feed-row", style: { gridTemplateColumns: "84px minmax(0, 1fr)" } },
        h("span", { class: "feed-date" }, fmtDate(filing.date, true)),
        h("span", { class: "feed-text" },
          h("strong", { style: { fontWeight: filing.notable ? 650 : 450 } }, filing.form), filing.kind ? h("span", { class: "sub-text" }, filing.kind) : null,
          h("span", {}, filing.title),
          filing.url ? h("a", { href: filing.url, target: "_blank", rel: "noopener noreferrer", class: "sub-text" }, "Read ↗") : null))),
        result.filings.length ? null : emptyBox("No filings listed for this symbol."))));
}

const pct = (value, code = "pct1") => (value === null || value === undefined ? MISSING : fmt(value * 100, code));

/** Polymarket's odds that the company beats its earnings estimate, when such a market exists. */
function earningsOddsLine(odds, symbol) {
  if (!odds) return null;
  const estimate = odds.consensus_eps !== null && odds.consensus_eps !== undefined ? `the $${odds.consensus_eps.toFixed(2)} EPS estimate` : "its EPS estimate";
  return h("p", { class: "earnings-odds" },
    h("span", { class: "ext-tag" }, "Earnings odds"),
    h("span", {}, `Chance that ${symbol} beats ${estimate} on ${fmtDate(odds.date)}: `),
    h("strong", {}, `${Math.round(odds.prob * 100)}%`),
    h("span", { class: "sub-text" }, `(${fmt(odds.volume, "usdcompact")} traded)`),
    info("earnings_odds"),
    h("a", { href: odds.url, target: "_blank", rel: "noopener noreferrer" }, "Polymarket ↗"));
}

function section(title, ...rows) {
  return h("section", { class: "card" }, h("h3", { class: "card-title", style: { marginBottom: "6px" } }, title), h("div", { class: "kv" }, rows));
}

function periodCells(snapshot) {
  const items = [["1 day", snapshot.change_pct, 5], ["1 week", snapshot.ret_5d, 10], ["1 month", snapshot.ret_1m, 20], ["3 months", snapshot.ret_3m, 35], ["6 months", snapshot.ret_6m, 50], ["YTD", snapshot.ret_ytd, 50], ["1 year", snapshot.ret_1y, 80]];
  return h("div", { class: "card", style: { marginBottom: "10px" } },
    h("div", { style: { display: "flex", flexWrap: "wrap", gap: "6px 26px", alignItems: "center" } },
      h("h3", { class: "card-title" }, "Returns", info("period_returns")),
      items.map(([label, value, scale]) => h("div", { style: { display: "inline-flex", alignItems: "center", gap: "6px" } },
        h("span", { class: "sub-text" }, label),
        heatCell(value, scale, label === "1 day" ? "spct2" : "spct1")))));
}

function trendSection(s) {
  return section("Trend",
      kvRow("Trend state", "trend_state", statusBadge(s.trend.tone, s.trend.label)),
      kvRow("50-day average", "sma", fmt(s.sma50, "num2")),
      kvRow("Price vs 50-day", "vs_sma", fmt(s.vs_sma50, "spct1")),
      kvRow("200-day average", "sma", fmt(s.sma200, "num2")),
      kvRow("Price vs 200-day", "vs_sma", fmt(s.vs_sma200, "spct1")),
      kvRow("20-day average", "sma", fmt(s.sma20, "num2")),
      kvRow("21-day EMA", "ema", fmt(s.ema21, "num2")),
      kvRow("Last 50/200 cross", "golden_cross", s.last_cross ? `${s.last_cross.type === "golden" ? "Golden" : "Death"}, ${fmtDate(s.last_cross.date, true)}` : MISSING),
      kvRow("52-week high", "off_high", fmt(s.high_52w, "num2")),
      kvRow("From 52-week high", "off_high", fmt(s.off_high, "spct1")),
      kvRow("52-week low", "off_high", fmt(s.low_52w, "num2")),
      kvRow("Position in range", "range_position", fmt(s.range_position, "pct0")),
      kvRow("ADX (trend strength)", "adx", fmt(s.adx, "num0")));
}

function technicalSections(s) {
  return [
    section("Momentum and relative strength",
      kvRow("RSI (14)", "rsi", fmt(s.rsi, "num0")),
      kvRow("MACD histogram", "macd", fmt(s.macd_hist, "num2")),
      kvRow("Last MACD cross", "macd", s.macd_cross ? `${s.macd_cross.direction === "bullish" ? "Bullish" : "Bearish"}, ${s.macd_cross.bars_ago} days ago` : MISSING),
      kvRow("Stochastic %K", "stochastic", fmt(s.stoch_k, "num0")),
      kvRow("RS rating vs S&P 500", "rs_rating", fmt(s.rs_rating, "num0")),
      kvRow("vs SPY, 1 month", "rs_vs_spy", fmt(s.rs_1m, "spct1")),
      kvRow("vs SPY, 3 months", "rs_vs_spy", fmt(s.rs_3m, "spct1")),
      kvRow("Beta vs SPY", "beta", fmt(s.beta, "num2"))),
    section("Volume",
      kvRow("Volume", "volume_bars", s.volume === null ? MISSING : compact(s.volume)),
      kvRow("20-day average volume", "volume_bars", s.avg_volume_20 === null ? MISSING : compact(s.avg_volume_20)),
      kvRow("Relative volume", "rvol", s.rvol === null ? MISSING : `${s.rvol.toFixed(2)}x`),
      kvRow("Up/down volume (50 days)", "up_down_volume", fmt(s.up_down_volume, "num2")),
      kvRow("On-balance volume", "obv", s.obv_trend ? (s.obv_trend === "rising" ? "Rising" : "Falling") : MISSING),
      kvRow("Chaikin Money Flow", "cmf", fmt(s.cmf, "snum2")),
      kvRow("Money Flow Index", "mfi", fmt(s.mfi, "num0"))),
    section("Volatility",
      kvRow("ATR (14)", "atr", fmt(s.atr, "num2")),
      kvRow("ATR as % of price", "atr", fmt(s.atr_pct, "pct1")),
      kvRow("Realized, 20 days", "hv", fmt(s.hv20, "pct0")),
      kvRow("Realized, 60 days", "hv", fmt(s.hv60, "pct0")),
      kvRow("Bollinger band width", "bollinger", fmt(s.bb_width, "pct1")),
      kvRow("Band width, 1-year rank", "bollinger", s.bb_squeeze_rank === null ? MISSING : `${Math.round(s.bb_squeeze_rank)}%`)),
  ];
}

function optionsSection(result, s) {
  if (!result) return h("section", { class: "card" }, h("h3", { class: "card-title" }, "Options"), emptyBox("Loading option data…"));
  if (!result.available) return h("section", { class: "card" }, h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "Options"), emptyBox(result.reason || "No listed options for this symbol."));
  const k = result.summary;
  const dealerTone = k.gamma_regime === "positive" ? "good" : "critical";
  return h("section", { class: "card" },
    h("div", { class: "card-head" },
      h("h3", { class: "card-title" }, "Options"),
      h("a", { class: "btn btn-xs", href: `#/options/${encodeURIComponent(result.underlying)}` }, "Full gamma view")),
    h("div", { class: "kv" },
      kvRow("Dealer gamma regime", "gamma_regime", statusBadge(dealerTone, k.gamma_regime === "positive" ? "Positive" : "Negative")),
      kvRow("Net dealer gamma (per 1%)", "net_gex", fmt(k.net_gex, "usdcompact")),
      kvRow("Gamma flip", "gamma_flip", fmt(k.gamma_flip, "num2")),
      kvRow("Call wall", "call_wall", fmt(k.call_wall, "num2")),
      kvRow("Put wall", "put_wall", fmt(k.put_wall, "num2")),
      kvRow("Max pain (next expiry)", "max_pain", fmt(k.max_pain, "num2")),
      kvRow("Put/call volume", "pc_volume", fmt(k.pc_volume, "num2")),
      kvRow("Put/call open interest", "pc_oi", fmt(k.pc_oi, "num2")),
      kvRow("Implied volatility (30d)", "iv30", fmt(k.iv30, "pct1")),
      kvRow("Implied vs realized", "iv_vs_hv", k.iv30 !== null && s.hv20 !== null ? fmt(k.iv30 - s.hv20, "snum1") : MISSING),
      kvRow("25-delta skew", "skew_25d", fmt(k.skew_25d, "snum1")),
      kvRow("Expected move this week", "expected_move", fmt(k.expected_move_week, "pct1")),
      kvRow("Expected move, monthly", "expected_move", fmt(k.expected_move_month, "pct1"))));
}

function fundamentalsSections(data, s, darkPool) {
  const i = data.info || {};
  const blocks = [];
  if (i.longBusinessSummary || i.sector || i.quoteType) {
    blocks.push(h("section", { class: "card" },
      h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "About"),
      h("div", { class: "kv", style: { marginBottom: "6px" } },
        i.sector ? kvRow("Sector", null, i.sector) : null,
        i.industry ? kvRow("Industry", null, i.industry) : null,
        i.exchange ? kvRow("Exchange", null, i.exchange) : null),
      i.longBusinessSummary
        ? h("p", { class: "sub-text", style: { maxHeight: "150px", overflowY: "auto" } }, i.longBusinessSummary)
        : null));
  }
  const shortChange = i.sharesShort && i.sharesShortPriorMonth ? (i.sharesShort / i.sharesShortPriorMonth - 1) * 100 : null;
  blocks.push(section("Short interest and dark pools",
    kvRow("Short % of float", "short_interest", pct(i.shortPercentOfFloat)),
    kvRow("Days to cover", "short_ratio", fmt(i.shortRatio, "num1")),
    kvRow("Short shares, 1-month change", "short_interest", fmt(shortChange, "spct1")),
    kvRow("Short interest reported", "short_interest", i.dateShortInterest ? fmtDate(i.dateShortInterest, true) : MISSING),
    kvRow("Off-exchange short volume", "dark_pool_short", darkPool ? fmt(darkPool.value, "pct1") : MISSING),
    kvRow("20-day average", "dark_pool_short", darkPool ? fmt(darkPool.avg_20d, "pct1") : MISSING),
    kvRow("vs its 60-day range (z-score)", "dark_pool_short", darkPool ? fmt(darkPool.z_score, "snum1") : MISSING)));
  if (data.next_earnings || i.quoteType === "EQUITY") {
    const days = data.next_earnings ? daysUntil(data.next_earnings) : null;
    const upside = i.targetMeanPrice && s.last ? (i.targetMeanPrice / s.last - 1) * 100 : null;
    const analysts = data.analysts;
    blocks.push(section("Earnings and analysts",
      kvRow("Next earnings", "next_earnings", data.next_earnings ? `${fmtDate(data.next_earnings, true)}${days !== null && days >= 0 ? ` (${days}d)` : ""}` : MISSING),
      kvRow("Average price target", "analyst_target", fmt(i.targetMeanPrice, "num2")),
      kvRow("Target vs price", "analyst_target", fmt(upside, "spct1")),
      kvRow("Target range", "analyst_target", i.targetLowPrice ? `${fmt(i.targetLowPrice, "num0")} to ${fmt(i.targetHighPrice, "num0")}` : MISSING),
      kvRow("Average rating (1 = strong buy)", "analyst_rating", i.recommendationMean ? `${fmt(i.recommendationMean, "num2")} ${i.recommendationKey ? `(${String(i.recommendationKey).replace("_", " ")})` : ""}` : MISSING),
      kvRow("Analysts covering", "analyst_rating", fmt(i.numberOfAnalystOpinions, "num0")),
      kvRow("Buy / hold / sell", "analyst_rating", analysts ? `${(analysts.strongBuy || 0) + (analysts.buy || 0)} / ${analysts.hold ?? 0} / ${(analysts.sell || 0) + (analysts.strongSell || 0)}` : MISSING)));
    blocks.push(section("Valuation",
      kvRow("Market cap", "market_cap", i.marketCap ? `$${compact(i.marketCap)}` : MISSING),
      kvRow("P/E, trailing", "pe_trailing", fmt(i.trailingPE, "num1")),
      kvRow("P/E, forward", "pe_forward", fmt(i.forwardPE, "num1")),
      kvRow("PEG", "peg", fmt(i.trailingPegRatio ?? i.pegRatio, "num2")),
      kvRow("Price/sales", "price_sales", fmt(i.priceToSalesTrailing12Months, "num1")),
      kvRow("Price/book", "price_book", fmt(i.priceToBook, "num1")),
      kvRow("EV/EBITDA", "ev_ebitda", fmt(i.enterpriseToEbitda, "num1")),
      kvRow("Dividend yield", "dividend_yield", pct(i.trailingAnnualDividendYield, "pct2"))));
    blocks.push(section("Growth and quality",
      kvRow("Revenue growth", "revenue_growth", pct(i.revenueGrowth, "spct1")),
      kvRow("Earnings growth", "earnings_growth", pct(i.earningsGrowth, "spct1")),
      kvRow("Gross margin", "gross_margin", pct(i.grossMargins)),
      kvRow("Operating margin", "operating_margin", pct(i.operatingMargins)),
      kvRow("Net margin", "profit_margin", pct(i.profitMargins)),
      kvRow("Return on equity", "roe", pct(i.returnOnEquity)),
      kvRow("Free cash flow", "fcf", i.freeCashflow ? `$${compact(i.freeCashflow)}` : MISSING),
      kvRow("Debt/equity", "debt_equity", fmt(i.debtToEquity, "pct0"))));
    const insiders = data.insiders;
    blocks.push(section("Ownership and insiders",
      kvRow("Institutions own", "inst_ownership", pct(i.heldPercentInstitutions)),
      kvRow("Insiders own", "insider_ownership", pct(i.heldPercentInsiders)),
      kvRow("Insider shares bought, 6m", "insider_activity", insiders?.bought_shares ? compact(insiders.bought_shares) : MISSING),
      kvRow("Insider shares sold, 6m", "insider_activity", insiders?.sold_shares ? compact(insiders.sold_shares) : MISSING),
      kvRow("Net insider shares, 6m", "insider_activity", insiders?.net_shares ? compact(insiders.net_shares) : MISSING)));
  }
  return blocks;
}

function earningsTable(history) {
  if (!history?.length) return null;
  return h("section", { class: "card" },
    h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "Earnings history", info("earnings_surprise")),
    dataTable({
      id: "earnings",
      columns: [
        { key: "date", label: "Report date", left: true, render: (row) => fmtDate(row.date, true), value: (row) => row.date },
        { key: "estimate", label: "EPS estimate", fmt: "num2" },
        { key: "reported", label: "EPS reported", fmt: "num2" },
        { key: "surprise_pct", label: "Surprise", render: (row) => heatCell(row.surprise_pct, 20) },
      ],
      rows: history,
    }));
}

export default {
  title: (params) => params[0] || "Stock",
  async mount(root, { params }) {
    const symbol = (params[0] || "SPY").toUpperCase();
    const data = await api.get(`/api/stock/${encodeURIComponent(symbol)}`);
    const s = data.snapshot;
    const alertContext = () => ({ kind: "stock", symbol: data.symbol, label: data.symbol });
    let inWatchlist = data.in_watchlist;

    const watchButton = h("button", { class: "btn", type: "button", id: "watch-toggle" });
    const syncWatch = () => {
      watchButton.textContent = inWatchlist ? "Remove from watchlist" : "Add to watchlist";
      watchButton.classList.toggle("btn-primary", !inWatchlist);
    };
    syncWatch();
    watchButton.addEventListener("click", async () => {
      watchButton.disabled = true;
      try {
        if (inWatchlist) await api.del(`/api/watchlist/${encodeURIComponent(data.symbol)}`);
        else await api.post("/api/watchlist", { symbol: data.symbol });
        inWatchlist = !inWatchlist;
        syncWatch();
        toast(inWatchlist ? `Added ${data.symbol} to the watchlist.` : `Removed ${data.symbol} from the watchlist.`);
      } catch (error) {
        toast(error.message, "error");
      }
      watchButton.disabled = false;
    });

    const priceChart = chartCard({ title: "Price with 50- and 200-day averages", metric: "sma", height: 300 });
    const optionsBox = h("div", {}, optionsSection(null, s));
    const fundamentalBox = h("div", { class: "grid grid-3" }, h("section", { class: "card" }, emptyBox("Loading company data…")));
    const earningsBox = h("div", {});
    const filingsBox = h("div", {}, filingsSection(null));
    const extendedBox = h("span", { class: "ext-line" });
    const loadExtended = async () => {
      try {
        const quote = (await api.get(`/api/extended?symbols=${encodeURIComponent(data.symbol)}`)).quotes[data.symbol];
        replace(extendedBox, quote ? [
          h("span", { class: "ext-tag" }, quote.label),
          h("strong", {}, fmt(quote.price, "num2")),
          deltaSpan(`${fmt(Math.abs(quote.change), "num2")} (${fmt(Math.abs(quote.change_pct), "pct2")})`, quote.change_pct, "good"),
          h("span", { class: "sub-text" }, quote.when),
          info("extended_hours"),
        ] : null);
      } catch {
        replace(extendedBox);
      }
    };
    const chipsBox = h("div", { class: "chips", style: { marginBottom: "10px" } },
      data.chips.map((chip) => h("span", { class: `chip tone-${chip.tone}` }, chip.text, info(chip.metric))));

    replace(root,
      pageHead(`${data.symbol}`, null, h("span", { class: "sub-text", style: { fontWeight: 400, fontSize: "14px" } }, data.name)),
      h("div", { style: { display: "flex", alignItems: "baseline", gap: "12px", flexWrap: "wrap", marginBottom: "8px" } },
        h("span", { class: "hero-figure", style: { fontSize: "34px" } }, fmt(s.last, "num2")),
        deltaSpan(`${fmt(Math.abs(s.change ?? 0), "num2")} (${fmt(Math.abs(s.change_pct ?? 0), "pct2")}) today`, s.change_pct, "good"),
        h("span", { class: "sub-text" }, `As of ${fmtDate(s.date, true)}`),
        extendedBox,
        h("div", { style: { display: "flex", gap: "6px", marginLeft: "auto", flexWrap: "wrap" } },
          watchButton,
          h("a", { class: "btn", href: `#/chart/${encodeURIComponent(data.symbol)}` }, "Open in Chart Lab"),
          h("button", { class: "btn", type: "button", id: "stock-add-alert", onclick: () => openAlertDrawer(alertContext()) }, "Add alert"),
          h("a", { class: "btn", href: `#/journal/new/${encodeURIComponent(data.symbol)}` }, "Log a trade"),
          h("a", { class: "btn", href: `#/backtest/${encodeURIComponent(data.symbol)}` }, "Backtest this"))),
      data.chips.length ? chipsBox : null,
      earningsOddsLine(data.earnings_odds, data.symbol),
      positionLine(data.positions),
      periodCells(s),
      h("div", { class: "split-2-1", style: { alignItems: "start", marginBottom: "10px" } }, checklistCard(data.checklist, data.checklist_summary) || h("div", {}), notesCard(data)),
      h("div", { class: "split-2-1" }, priceChart.el, trendSection(s)),
      h("div", { class: "grid grid-3 mt" }, technicalSections(s)),
      h("div", { class: "mt" }, optionsBox),
      h("div", { class: "mt" }, fundamentalBox),
      h("div", { class: "mt" }, earningsBox),
      h("div", { class: "section-title" }, "Filings and insider trades"),
      filingsBox);

    loadExtended();
    const chartData = await api.get(`/api/chart/${encodeURIComponent(data.symbol)}?interval=1d&range=1y`);
    priceChart.render(lineChart({
      dates: chartData.dates,
      series: [
        { name: data.symbol, values: chartData.close, slot: 0 },
        { name: "50-day", values: chartData.indicators.sma50, slot: 1 },
        { name: "200-day", values: chartData.indicators.sma200, slot: 6 },
      ],
      yFmt: "num2",
    }));

    let active = true;
    api.get(`/api/stock/${encodeURIComponent(data.symbol)}/options`)
      .then((result) => { if (active) replace(optionsBox, optionsSection(result, s)); })
      .catch((error) => { if (active) replace(optionsBox, optionsSection({ available: false, reason: error.message }, s)); });
    api.get(`/api/stock/${encodeURIComponent(data.symbol)}/fundamentals`)
      .then((result) => {
        if (!active) return;
        replace(fundamentalBox, fundamentalsSections(result, s, data.dark_pool));
        replace(earningsBox, earningsTable(result.earnings_history));
      })
      .catch((error) => { if (active) replace(fundamentalBox, h("section", { class: "card" }, emptyBox(error.message))); });
    api.get(`/api/stock/${encodeURIComponent(data.symbol)}/filings`)
      .then((result) => { if (active) replace(filingsBox, filingsSection(result)); })
      .catch((error) => { if (active) replace(filingsBox, h("section", { class: "card" }, emptyBox(error.message))); });

    const extendedTimer = setInterval(loadExtended, 3 * 60 * 1000);
    return {
      alertContext,
      unmount: () => {
        active = false;
        clearInterval(extendedTimer);
      },
    };
  },
};
