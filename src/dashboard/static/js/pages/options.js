// Dealer gamma, key strikes, put/call activity and implied volatility for any underlying.
import { api, query } from "../lib/api.js";
import { baseOption, categoryLines, chartCard, colors, lineChart, tooltipRows, valueAxis } from "../lib/charts.js";
import { MISSING, compact, fmt, fmtAgo, fmtDate, h, prefs, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { dataTable, emptyBox, errorBox, field, fillSelect, filters, loadingBox, pageHead, segmented, select, statusBadge, symbolLink, tile, toast } from "../lib/ui.js";

const STATIC_SCOPES = [
  { value: "all", label: "All expiries" },
  { value: "0dte", label: "Expiring today" },
  { value: "7d", label: "Next 7 days" },
  { value: "30d", label: "Next 30 days" },
  { value: "90d", label: "Next 90 days" },
  { value: "monthly", label: "Next monthly" },
];

const MEASURES = {
  gex: { label: "Net gamma", metric: "net_gex", title: "Net dealer gamma by strike", sub: "Estimated dollars of hedging per 1% move. Blue is positive gamma, red is negative" },
  split: { label: "Call & put gamma", metric: "call_put_gex", title: "Call and put gamma by strike", sub: "Estimated dollars of hedging per 1% move" },
  oi: { label: "Open interest", metric: "open_interest", title: "Open interest by strike", sub: "Contracts open as of the prior close" },
  volume: { label: "Volume", metric: "option_volume", title: "Volume by strike", sub: "Contracts traded in the latest session" },
  vanna: { label: "Vanna", metric: "vanna", title: "Vanna exposure by strike", sub: "Estimated change in dealers' delta, in dollars, per 1 point rise in implied volatility" },
  charm: { label: "Charm", metric: "charm", title: "Charm exposure by strike", sub: "Estimated change in dealers' delta, in dollars, per day that passes. Negative suggests buying, positive suggests selling" },
};

function strikeChart(data, measureKey) {
  const strikes = data.strikes;
  if (!strikes.strike.length) return { empty: "No strikes within this range. Try widening the strike range." };
  const c = colors();
  const k = data.kpis;
  const labels = strikes.strike.map((value) => (value % 1 === 0 ? String(value) : value.toFixed(1)));
  const nearestLabel = (value) => {
    if (value === null || value === undefined) return null;
    let best = 0;
    strikes.strike.forEach((strike, index) => {
      if (Math.abs(strike - value) < Math.abs(strikes.strike[best] - value)) best = index;
    });
    return Math.abs(strikes.strike[best] - value) / value < 0.05 ? labels[best] : null;
  };
  const levelLines = [
    { value: k.spot, label: `Spot ${fmt(k.spot, "num0")}`, color: c.ink },
    { value: k.gamma_flip, label: `Flip ${fmt(k.gamma_flip, "num0")}`, color: c.serious },
    { value: k.call_wall, label: `Call wall ${fmt(k.call_wall, "num0")}`, color: c.muted },
    { value: k.put_wall, label: `Put wall ${fmt(k.put_wall, "num0")}`, color: c.muted },
  ].map((level) => ({ ...level, at: nearestLabel(level.value) })).filter((level) => level.at !== null);
  const markLine = {
    silent: true,
    symbol: "none",
    label: { show: true, position: "end", fontSize: 10.5, color: c.ink2, formatter: (item) => item.name, rotate: 0 },
    data: levelLines.map((level, index) => ({
      xAxis: level.at,
      name: level.label,
      lineStyle: { color: level.color, width: 1, type: "solid" },
      label: { distance: 2 + (index % 2) * 12 },
    })),
  };
  const bar = (name, values, color) => ({
    name,
    type: "bar",
    stack: "strikes",
    barMaxWidth: 24,
    barCategoryGap: strikes.strike.length > 80 ? "10%" : "25%",
    itemStyle: { color: typeof color === "function" ? c.up : color },
    emphasis: { disabled: true },
    data: values.map((value) => ({
      value,
      itemStyle: {
        color: typeof color === "function" ? color(value) : color,
        borderRadius: strikes.strike.length > 80 ? 1 : value >= 0 ? [3, 3, 0, 0] : [0, 0, 3, 3],
      },
    })),
  });
  const polarity = (value) => (value >= 0 ? c.up : c.down);
  const mirrored = measureKey === "oi" || measureKey === "volume";
  let series;
  let code = "usdcompact";
  if (measureKey === "gex") series = [bar("Net gamma", strikes.net_gex, polarity)];
  else if (measureKey === "split") series = [bar("Call gamma", strikes.call_gex, c.up), bar("Put gamma", strikes.put_gex, c.down)];
  else if (measureKey === "oi") {
    code = "compact";
    series = [bar("Call open interest", strikes.call_oi, c.up), bar("Put open interest", strikes.put_oi.map((value) => -value), c.down)];
  } else if (measureKey === "volume") {
    code = "compact";
    series = [bar("Call volume", strikes.call_volume, c.up), bar("Put volume", strikes.put_volume.map((value) => -value), c.down)];
  } else if (measureKey === "vanna") series = [bar("Vanna exposure", strikes.vanna, polarity)];
  else series = [bar("Charm exposure", strikes.charm, polarity)];
  series[0].markLine = markLine;
  const option = {
    ...baseOption(),
    grid: { left: 8, right: 14, top: series.length > 1 ? 44 : 30, bottom: 4, containLabel: true },
    legend: series.length > 1 ? { top: 0, left: 0, icon: "roundRect", itemWidth: 10, itemHeight: 10, textStyle: { color: c.ink2, fontSize: 11 } } : undefined,
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: c.axis } },
      axisTick: { show: false },
      axisLabel: { color: c.muted, hideOverlap: true, fontSize: 11 },
    },
    yAxis: valueAxis(c, code, { scale: false }),
    series,
  };
  option.tooltip = {
    ...option.tooltip,
    trigger: "axis",
    axisPointer: { type: "shadow", shadowStyle: { color: "rgba(127,127,127,0.12)" } },
    formatter: (items) => tooltipRows(`Strike ${items[0].axisValue}`, items.map((item) => ({
      color: item.color,
      // Puts are drawn below zero for contrast, but counts of contracts are never negative.
      value: fmt(mirrored ? Math.abs(item.value) : item.value, code),
      name: item.seriesName,
    }))),
  };
  const columns = [{ label: "Strike" }, ...series.map((item) => ({ label: item.name, fmt: code }))];
  const rows = strikes.strike.map((strike, index) => [strike, ...series.map((item) => {
    const value = item.data[index].value;
    return mirrored ? Math.abs(value) : value;
  })]).reverse();
  return { option, table: { columns, rows } };
}

function profileChart(data) {
  const profile = data.profile;
  if (!profile.spot.length) return { empty: "No gamma profile for this scope." };
  const c = colors();
  const k = data.kpis;
  const values = profile.net_gex.map((value) => value / 1e9);
  const option = {
    ...baseOption(),
    grid: { left: 8, right: 14, top: 20, bottom: 18, containLabel: true },
    xAxis: {
      type: "value",
      min: profile.spot[0],
      max: profile.spot[profile.spot.length - 1],
      name: "If the underlying were at this price",
      nameLocation: "middle",
      nameGap: 24,
      nameTextStyle: { color: c.muted },
      splitLine: { show: false },
      axisLine: { lineStyle: { color: c.axis } },
      axisTick: { show: false },
      axisLabel: { color: c.muted, formatter: (value) => fmt(value, "num0"), hideOverlap: true },
      axisPointer: { show: true, type: "line", lineStyle: { color: c.muted, width: 1, type: "solid" }, label: { show: false } },
    },
    yAxis: valueAxis(c, "usdbn"),
    series: [{
      name: "Net gamma",
      type: "line",
      data: profile.spot.map((spot, index) => [spot, values[index]]),
      showSymbol: false,
      lineStyle: { width: 2, color: c.accent },
      itemStyle: { color: c.accent },
      emphasis: { disabled: true },
      areaStyle: { color: c.accent, opacity: 0.1 },
      markLine: {
        silent: true,
        symbol: "none",
        label: { color: c.ink2, fontSize: 10.5, formatter: (item) => item.name, position: "end" },
        data: [
          { xAxis: k.spot, name: "Spot", lineStyle: { color: c.ink, width: 1, type: "solid" } },
          k.gamma_flip ? { xAxis: k.gamma_flip, name: "Flip", lineStyle: { color: c.serious, width: 1, type: "solid" } } : null,
          { yAxis: 0, name: "", lineStyle: { color: c.axis, width: 1, type: "solid" }, label: { show: false } },
        ].filter(Boolean),
      },
    }],
  };
  option.tooltip = {
    ...option.tooltip,
    trigger: "axis",
    formatter: (items) => tooltipRows(`At ${fmt(items[0].value[0], "num0")}`, [{ color: c.accent, value: fmt(items[0].value[1], "usdbn"), name: "Net gamma per 1% move" }]),
  };
  return {
    option,
    table: { columns: [{ label: "Price", fmt: "num0" }, { label: "Net gamma ($B per 1%)", fmt: "num2" }], rows: profile.spot.map((spot, index) => [spot, values[index]]) },
  };
}

function expiryChart(data) {
  const rows = data.expiries.slice(0, 40);
  if (!rows.length) return { empty: "No expiries available." };
  const c = colors();
  const values = rows.map((row) => row.net_gex / 1e9);
  const option = {
    ...baseOption(),
    grid: { left: 8, right: 14, top: 10, bottom: 4, containLabel: true },
    xAxis: {
      type: "category",
      data: rows.map((row) => row.expiry),
      axisLine: { lineStyle: { color: c.axis } },
      axisTick: { show: false },
      axisLabel: { color: c.muted, hideOverlap: true, formatter: (value) => fmtDate(value) },
    },
    yAxis: valueAxis(c, "usdbn", { scale: false }),
    series: [{
      name: "Net gamma",
      type: "bar",
      barMaxWidth: 24,
      emphasis: { disabled: true },
      data: values.map((value) => ({
        value,
        itemStyle: { color: value >= 0 ? c.up : c.down, borderRadius: value >= 0 ? [3, 3, 0, 0] : [0, 0, 3, 3] },
      })),
    }],
  };
  option.tooltip = {
    ...option.tooltip,
    trigger: "axis",
    axisPointer: { type: "shadow", shadowStyle: { color: "rgba(127,127,127,0.12)" } },
    formatter: (items) => {
      const row = rows[items[0].dataIndex];
      return tooltipRows(`${fmtDate(row.expiry, true)}${row.monthly ? " (monthly)" : ""}, ${row.dte} days`, [
        { color: items[0].color, value: fmt(items[0].value, "usdbn"), name: "Net gamma per 1%" },
        { color: c.deemph, value: fmt(row.expected_move_pct, "pct2"), name: "Expected move" },
        { color: c.deemph, value: fmt(row.max_pain, "num0"), name: "Max pain" },
      ]);
    },
  };
  return {
    option,
    table: {
      columns: [{ label: "Expiry" }, { label: "Net gamma ($B)", fmt: "num2" }, { label: "ATM IV", fmt: "pct1" }, { label: "Expected move", fmt: "pct2" }, { label: "Max pain", fmt: "num0" }],
      rows: rows.map((row) => [row.expiry, row.net_gex / 1e9, row.atm_iv, row.expected_move_pct, row.max_pain]),
    },
  };
}

function termChart(data) {
  const rows = data.expiries.filter((row) => row.atm_iv !== null && row.dte > 0).slice(0, 24);
  return categoryLines({
    categories: rows.map((row) => fmtDate(row.expiry)),
    series: [{ name: "At-the-money IV", values: rows.map((row) => row.atm_iv) }],
    yFmt: "pct1",
    emptyText: "No implied volatility for these expiries.",
  });
}

function smileChart(data) {
  const smile = data.smile;
  if (!smile) return { empty: "No implied volatility across strikes right now." };
  return categoryLines({
    categories: smile.strikes.map((value) => String(value)),
    series: [{ name: `IV, ${fmtDate(smile.expiry)} expiry`, values: smile.iv, symbols: false }],
    yFmt: "pct1",
    xName: "Strike",
  });
}

const unusualColumns = (symbol) => [
  { key: "underlying", label: "Underlying", left: true, sortable: false, render: () => symbolLink(symbol) },
  { key: "type", label: "Side", left: true, render: (row) => statusBadge("neutral", row.type) },
  { key: "strike", label: "Strike", render: (row) => fmt(row.strike, row.strike % 1 ? "num1" : "num0") },
  { key: "moneyness_pct", label: "From spot", metric: "moneyness", fmt: "spct1" },
  { key: "expiry", label: "Expiry", value: (row) => row.expiry, render: (row) => `${fmtDate(row.expiry)} (${row.dte}d)` },
  { key: "volume", label: "Volume", metric: "option_volume", render: (row) => compact(row.volume) },
  { key: "open_interest", label: "Open int.", metric: "open_interest", render: (row) => compact(row.open_interest) },
  { key: "vol_oi", label: "Vol / OI", metric: "vol_oi", render: (row) => `${row.vol_oi.toFixed(1)}x` },
  { key: "premium", label: "Premium", metric: "option_premium", render: (row) => fmt(row.premium, "usdcompact") },
  { key: "mid", label: "Mid", metric: "option_mid", fmt: "num2" },
  { key: "iv", label: "IV", metric: "iv30", fmt: "pct1" },
  { key: "delta", label: "Delta", metric: "option_delta", fmt: "num2" },
];

/** Rank within the saved history, which grows by one reading per trading day. */
function ivRank(history) {
  const values = (history.series.iv30 || []).filter((value) => value !== null).slice(-252);
  if (values.length < 20) return { rank: null, count: values.length };
  const low = Math.min(...values);
  const high = Math.max(...values);
  return { rank: high > low ? ((values[values.length - 1] - low) / (high - low)) * 100 : null, count: values.length };
}

function kpiTiles(data) {
  const k = data.kpis;
  const rank = ivRank(data.history);
  const positive = k.gamma_regime === "positive";
  const money = (value) => (value === null || value === undefined ? MISSING : fmt(value, "usdcompact"));
  const expiry = (iso) => (iso ? `Expiry ${fmtDate(iso)}` : "");
  return [
    tile({ label: "Spot", metric: "spot", value: k.spot, fmtCode: "num2", foot: h("span", {}, data.source_time ? `Last update ${fmtDate(data.source_time)} ${data.source_time.slice(11, 16)} ET` : "") }),
    tile({ label: "Net dealer gamma", metric: "net_gex", valueText: money(k.net_gex), foot: h("span", {}, "per 1% move") }),
    tile({ label: "Gamma regime", metric: "gamma_regime", valueText: positive ? "Positive" : "Negative", foot: statusBadge(positive ? "good" : "critical", positive ? "Moves tend to be damped" : "Moves can be amplified") }),
    tile({ label: "Gamma flip", metric: "gamma_flip", value: k.gamma_flip, fmtCode: "num0", foot: h("span", {}, k.gamma_flip ? `Spot ${fmt(k.flip_distance_pct, "spct1")} vs flip` : "No crossing within 20%") }),
    tile({ label: "Call wall", metric: "call_wall", value: k.call_wall, fmtCode: "num0", foot: h("span", {}, k.call_wall ? `${fmt(k.call_wall_distance_pct, "spct1")} from spot` : "") }),
    tile({ label: "Put wall", metric: "put_wall", value: k.put_wall, fmtCode: "num0", foot: h("span", {}, k.put_wall ? `${fmt(k.put_wall_distance_pct, "spct1")} from spot` : "") }),
    tile({ label: "Max pain", metric: "max_pain", value: k.max_pain, fmtCode: "num0", foot: h("span", {}, expiry(k.max_pain_expiry)) }),
    tile({ label: "Max pain (monthly)", metric: "max_pain", value: k.max_pain_monthly, fmtCode: "num0", foot: h("span", {}, expiry(k.max_pain_monthly_expiry)) }),
    tile({ label: "Put/call volume", metric: "pc_volume", value: k.pc_volume, fmtCode: "num2", foot: h("span", {}, `${compact(k.put_volume)} puts, ${compact(k.call_volume)} calls`) }),
    tile({ label: "Put/call open interest", metric: "pc_oi", value: k.pc_oi, fmtCode: "num2", foot: h("span", {}, `${compact(k.put_oi)} puts, ${compact(k.call_oi)} calls`) }),
    tile({ label: "Put/call premium", metric: "pc_premium", value: k.pc_premium, fmtCode: "num2", foot: h("span", {}, `${fmt(k.put_premium, "usdcompact")} vs ${fmt(k.call_premium, "usdcompact")}`) }),
    tile({ label: "Implied volatility (30d)", metric: "iv30", value: k.iv30, fmtCode: "pct1", foot: h("span", {}, `7d ${fmt(k.iv7, "pct1")} · 90d ${fmt(k.iv90, "pct1")}`) }),
    tile({
      label: "IV rank",
      metric: "iv_rank",
      valueText: rank.rank === null ? "Collecting" : fmt(rank.rank, "num0"),
      foot: h("span", {}, rank.rank === null ? `${rank.count} of 20 days saved` : `Over ${rank.count} days saved`),
    }),
    tile({ label: "IV term slope", metric: "term_slope", value: k.term_slope, fmtCode: "snum1", foot: h("span", {}, "30d minus 90d, points") }),
    tile({ label: "25-delta skew", metric: "skew_25d", value: k.skew_25d, fmtCode: "snum1", foot: h("span", {}, k.skew_dte ? `Puts over calls, ${k.skew_dte}-day expiry` : "") }),
    tile({ label: "Expected move, next expiry", metric: "expected_move", value: k.expected_move_next, fmtCode: "pct2", foot: h("span", {}, expiry(k.expected_move_next_expiry)) }),
    tile({ label: "Expected move, this week", metric: "expected_move", value: k.expected_move_week, fmtCode: "pct2", foot: h("span", {}, expiry(k.expected_move_week_expiry)) }),
    tile({ label: "Expected move, monthly", metric: "expected_move", value: k.expected_move_month, fmtCode: "pct2", foot: h("span", {}, expiry(k.expected_move_month_expiry)) }),
    tile({ label: "Same-day volume share", metric: "zero_dte_share", value: k.zero_dte_volume_share, fmtCode: "pct0", foot: h("span", {}, "Latest session") }),
    tile({ label: "Gamma expiring today", metric: "zero_dte_gamma", value: k.zero_dte_gamma_share, fmtCode: "pct0", foot: h("span", {}, "Share of gross gamma") }),
    tile({ label: "Vanna exposure", metric: "vanna", value: k.vanna_total, fmtCode: "usdcompact", foot: h("span", {}, "per 1 point of IV") }),
    tile({ label: "Charm exposure", metric: "charm", value: k.charm_total, fmtCode: "usdcompact", foot: h("span", {}, "per day") }),
    tile({ label: "Total option volume", metric: "option_volume", value: k.total_volume, fmtCode: "compact", foot: h("span", {}, "Contracts, latest session") }),
    tile({ label: "Total open interest", metric: "open_interest", value: (k.call_oi ?? 0) + (k.put_oi ?? 0), fmtCode: "compact", foot: h("span", {}, "Contracts open") }),
  ];
}

export default {
  title: (params) => `Options & Gamma${params[0] ? ` · ${params[0]}` : ""}`,
  async mount(root, { params }) {
    const saved = prefs.get("options", {});
    const state = {
      symbol: (params[0] || saved.symbol || "SPX").toUpperCase(),
      scope: "all",
      measure: saved.measure || "gex",
      window: saved.window || 15,
    };

    const body = h("div", {});
    const tilesBox = h("div", { class: "grid cols-6" });
    const headBox = h("div", { style: { marginBottom: "8px", display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" } });
    const strikeCard = chartCard({ title: MEASURES[state.measure].title, metric: MEASURES[state.measure].metric, subtitle: MEASURES[state.measure].sub, height: 340 });
    const profileCard = chartCard({ title: "Gamma profile", metric: "gamma_profile", subtitle: "Where the curve crosses zero is the gamma flip", height: 240 });
    const expiryCard = chartCard({ title: "Gamma by expiry", metric: "gex_by_expiry", subtitle: "Always shows all expiries, for what rolls off when", height: 240 });
    const termCard = chartCard({ title: "IV term structure", metric: "iv_term", subtitle: "At-the-money implied volatility per expiry", height: 220 });
    const smileCard = chartCard({ title: "Volatility smile", metric: "iv_smile", subtitle: "Implied volatility across strikes", height: 220 });
    const historyCard = chartCard({ title: "Net gamma history", metric: "net_gex", subtitle: "Saved daily while this dashboard runs", height: 220 });
    const unusualBox = h("section", { class: "card" });

    const underlyingSelect = h("select", { class: "input", "aria-label": "Underlying", onchange: (event) => setSymbol(event.target.value) });
    const customInput = h("input", {
      class: "input",
      style: { width: "90px" },
      placeholder: "Other…",
      "aria-label": "Type any optionable ticker",
      spellcheck: false,
      onkeydown: (event) => {
        if (event.key === "Enter" && event.target.value.trim()) {
          setSymbol(event.target.value.trim().toUpperCase());
          event.target.value = "";
        }
      },
    });
    const scopeSelect = select(STATIC_SCOPES, state.scope, (value) => {
      state.scope = value;
      load();
    }, { "aria-label": "Expiry scope" });

    const strikeWindow = segmented([{ value: 5, label: "±5%" }, { value: 10, label: "±10%" }, { value: 15, label: "±15%" }, { value: 30, label: "±30%" }], state.window, (value) => {
      state.window = value;
      prefs.set("options", { symbol: state.symbol, measure: state.measure, window: value });
      load();
    }, "Strike range");

    let data = null;
    let loadId = 0;

    const fillUnderlyings = async () => {
      const { underlyings } = await api.get("/api/options/underlyings");
      const groups = {};
      for (const item of underlyings) (groups[item.group] ||= []).push({ value: item.symbol, label: item.label });
      const options = Object.entries(groups).map(([group, items]) => ({ group, options: items }));
      if (!underlyings.some((item) => item.symbol === state.symbol)) options.unshift({ group: "Selected", options: [{ value: state.symbol, label: state.symbol }] });
      fillSelect(underlyingSelect, options, state.symbol);
    };

    function setSymbol(symbol) {
      state.symbol = symbol;
      state.scope = "all";
      prefs.set("options", { symbol, measure: state.measure, window: state.window });
      history.replaceState(null, "", `#/options/${encodeURIComponent(symbol)}`);
      document.title = `Options & Gamma · ${symbol} · Market Signals`;
      fillUnderlyings().then(() => load());
    }

    const drawStrike = () => {
      const measure = MEASURES[state.measure];
      strikeCard.el.querySelector(".card-title").firstChild.textContent = measure.title;
      strikeCard.el.querySelector(".card-title .info-btn")?.replaceWith(info(measure.metric));
      strikeCard.setSubtitle(measure.sub);
      strikeCard.render(strikeChart(data, state.measure));
    };

    const draw = () => {
      if (!data.available) {
        replace(body, h("div", { class: "card" }, errorBox(data.reason || `No options found for ${state.symbol}.`)));
        return;
      }
      if (data.empty_scope) {
        replace(headBox, h("span", { class: "sub-text" }, data.reason));
        replace(body, h("div", { class: "card" }, emptyBox(`${data.reason} Choose another expiry scope above.`)));
        fillScopeOptions();
        return;
      }
      replace(body,
        headBox,
        tilesBox,
        h("div", { class: "section-title" }, "Positioning by strike"),
        h("div", { class: "filters", style: { marginBottom: "8px" } },
          field("Measure", segmented(Object.entries(MEASURES).map(([value, item]) => ({ value, label: item.label })), state.measure, (value) => {
            state.measure = value;
            prefs.set("options", { symbol: state.symbol, measure: value, window: state.window });
            drawStrike();
          }, "Measure")),
          field("Strike range", strikeWindow)),
        strikeCard.el,
        h("div", { class: "grid grid-2 mt" }, profileCard.el, expiryCard.el),
        h("div", { class: "section-title" }, "Implied volatility"),
        h("div", { class: "grid grid-3" }, termCard.el, smileCard.el, historyCard.el),
        h("div", { class: "section-title" }, "Unusual activity"),
        unusualBox);
      replace(headBox,
        symbolLink(data.symbol),
        h("span", { class: "sub-text" }, data.name),
        h("span", { class: "sub-text" }, `Chain fetched ${fmtAgo(data.fetched_at)}`),
        h("a", { class: "btn btn-xs", href: `#/chart/${encodeURIComponent(data.symbol === "SPX" ? "^GSPC" : data.symbol)}` }, "Open in Chart Lab"));
      replace(tilesBox, kpiTiles(data));
      fillScopeOptions();
      drawStrike();
      profileCard.render(profileChart(data));
      expiryCard.render(expiryChart(data));
      termCard.render(termChart(data));
      smileCard.render(smileChart(data));
      const history = data.history;
      historyCard.render(lineChart({
        dates: history.dates,
        series: [{ name: "Net gamma ($B per 1%)", values: history.series.net_gex || [], area: true }],
        yFmt: "usdbn",
        emptyText: "Starts with today's reading and grows by one point per trading day.",
      }));
      historyCard.setFoot(history.dates.length ? `${history.dates.length} day${history.dates.length === 1 ? "" : "s"} collected so far. Free sources have no history for this, so it builds up while the dashboard runs.` : "");
      replace(unusualBox,
        h("h3", { class: "card-title", style: { marginBottom: "4px" } }, `${data.symbol} options trading far above their open interest`, info("unusual_activity")),
        h("p", { class: "card-sub", style: { marginBottom: "6px" } }, `${data.name}. Sorted by dollars traded. The data doesn't reveal whether these were bought or sold. Pick another underlying above to see its contracts.`),
        dataTable({ columns: unusualColumns(data.symbol), rows: data.unusual, maxHeight: 380, empty: "No unusual activity in this scope." }));
    };

    function fillScopeOptions() {
      const dated = (data.expiries || []).slice(0, 30).map((row) => ({
        value: row.expiry,
        label: `${fmtDate(row.expiry)}${row.monthly ? " (monthly)" : ""} · ${row.dte}d`,
      }));
      fillSelect(scopeSelect, [{ group: "Groups", options: STATIC_SCOPES }, { group: "Single expiry", options: dated }], state.scope);
    }

    async function load() {
      const id = ++loadId;
      [strikeCard, profileCard, expiryCard, termCard, smileCard, historyCard].forEach((card) => card.refreshing());
      try {
        const next = await api.get(`/api/options/${encodeURIComponent(state.symbol)}${query({ scope: state.scope, window: state.window })}`);
        if (id !== loadId) return;
        data = next;
        draw();
      } catch (error) {
        if (id !== loadId) return;
        toast(error.message, "error");
        replace(body, h("div", { class: "card" }, errorBox(error.message, load)));
      }
    }

    replace(body, loadingBox());
    replace(root,
      pageHead("Options & gamma", "Where option positions sit and how dealers' hedging may affect price. Data comes from Cboe's delayed chains. Gamma figures are estimates: select the info buttons to see why."),
      filters(
        field("Underlying", underlyingSelect),
        field("Other ticker", customInput),
        field("Expiries", scopeSelect)),
      body);

    await fillUnderlyings();
    await load();
    // The alert drawer starts from the underlying on screen (SPX watches the S&P 500 index).
    return { refresh: load, alertContext: () => ({ kind: "stock", symbol: state.symbol, label: state.symbol }) };
  },
};
