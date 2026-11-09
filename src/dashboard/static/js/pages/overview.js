// The one-screen answer to "what is the market doing, and what just changed?"
import { api } from "../lib/api.js";
import { chartCard, lineChart, rankedBars } from "../lib/charts.js";
import { MISSING, fmt, fmtDate, h, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { signalOptions } from "../lib/rules.js";
import { dataTable, deltaSpan, emptyBox, field, fillSelect, filters, pageHead, scoreBadge, segmented, sparkline, statusBadge, tile, toast } from "../lib/ui.js";
import { timeText } from "./alerts.js";

function alertsCard(data) {
  const active = data.alerts.filter((alert) => alert.enabled && alert.status.active && !alert.rule.op.startsWith("crosses")).length;
  const head = h("div", { class: "card-head" },
    h("h3", { class: "card-title" }, "Your alerts", info("alerts")),
    h("a", { class: "btn btn-xs", href: "#/alerts" }, data.alerts.length ? "Open alerts" : "Create an alert"));
  if (!data.alerts.length) {
    return h("section", { class: "card" }, head, h("p", { class: "card-sub" }, "Get a notification when a signal or one of your stocks reaches a level you choose."));
  }
  const rows = data.events.slice(0, 4).map((event) => h("div", { class: `feed-row ${event.read ? "" : "is-unread"}`, style: { gridTemplateColumns: "92px minmax(0, 1fr)" } },
    h("span", { class: "feed-date" }, timeText(event.at)),
    h("span", { class: "feed-text" }, event.read ? null : h("span", { class: "unread-dot", "aria-label": "New" }), event.message)));
  return h("section", { class: "card" }, head,
    h("p", { class: "card-sub", style: { marginBottom: "4px" } },
      `${data.alerts.length} alert${data.alerts.length === 1 ? "" : "s"}, ${active} with the condition met now${data.unread ? `, ${data.unread} new notification${data.unread === 1 ? "" : "s"}` : ""}.`),
    rows.length ? h("div", { class: "feed" }, rows) : h("p", { class: "sub-text" }, "Nothing has fired yet."));
}

function meterColor(score) {
  if (score >= 60) return "var(--accent)";
  if (score >= 45) return "color-mix(in oklab, var(--accent) 55%, var(--track))";
  if (score >= 30) return "var(--warning)";
  return "var(--critical)";
}

function meterRow({ label, metric, score, change, href }) {
  const fill = h("div", { class: "meter-fill", style: { width: score === null || score === undefined ? "0" : `${Math.max(2, score)}%`, background: meterColor(score ?? 0) } });
  return h("a", { class: "meter-row", href, title: "Open the page behind this pillar" },
    h("span", { class: "meter-label" }, label, metric ? info(metric) : null),
    h("div", { class: "meter-track", role: "meter", "aria-valuenow": score ?? 0, "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": label }, fill),
    h("span", { class: "meter-score" }, score === null || score === undefined ? MISSING : Math.round(score)),
    h("span", { class: "meter-delta" }, change === null || change === undefined ? "" : deltaSpan(`${Math.abs(change).toFixed(0)} 1w`, change, "good")));
}

function heroCard(health) {
  const change = health.change_1w;
  const summary = health.weakest && health.strongest
    ? `Weakest pillar: ${health.weakest}. Strongest: ${health.strongest}.`
    : "Waiting for enough data to compare the pillars.";
  return h("section", { class: "card" },
    h("div", { class: "card-title" }, "Market Health", info("market_health")),
    h("div", { class: "hero-row" },
      h("div", { class: "hero-figure" }, health.score === null ? MISSING : Math.round(health.score)),
      h("div", {},
        statusBadge(health.tone, health.label),
        h("div", { class: "sub-text" }, change === null || change === undefined ? "" : deltaSpan(`${Math.abs(change).toFixed(0)} points vs a week ago`, change, "good")))),
    h("p", { class: "sub-text" }, summary),
    h("p", { class: "sub-text", style: { marginTop: "4px" } }, "A summary of current conditions on a 0-100 scale, not a price forecast."));
}

function pillarsCard(data) {
  return h("section", { class: "card", style: { height: "100%", display: "flex", flexDirection: "column", justifyContent: "space-between" } },
    h("div", { class: "card-head" }, h("h3", { class: "card-title" }, "What drives the score"), h("span", { class: "sub-text" }, "Score, and change in 1 week")),
    data.pillars.map((pillar) => meterRow({ label: pillar.label, metric: pillar.metric, score: pillar.score, change: pillar.change_1w, href: `#/${pillar.page}` })),
    h("div", { style: { borderTop: "1px solid var(--grid)", marginTop: "6px", paddingTop: "6px" } },
      meterRow({ label: "Crowd fear", metric: "crowd_fear", score: data.crowd.score, change: null, href: `#/${data.crowd.page}` }),
      h("p", { class: "sub-text", style: { padding: "0 6px" } }, `${data.crowd.label}. Read as contrarian and kept outside the score.`)));
}

function quoteTile(item) {
  const isYield = item.change_kind === "points";
  const text = isYield ? `${fmt(item.change, "snum2")} pts today` : `${fmt(item.change_pct, "spct2")} today`;
  const delta = deltaSpan(text, isYield ? item.change : item.change_pct, item.higher_is);
  const foot = [];
  if (item.above_50 !== null) foot.push(`${item.above_50 ? "Above" : "Below"} 50d`);
  if (item.above_200 !== null) foot.push(`${item.above_200 ? "Above" : "Below"} 200d`);
  return tile({
    label: item.label,
    metric: item.metric,
    value: item.value,
    fmtCode: item.fmt,
    delta,
    spark: item.spark,
    foot: h("span", {}, foot.join(" · ")),
    href: `#/stock/${encodeURIComponent(item.symbol)}`,
  });
}

const FUTURES_TITLES = { pre: "Before the open", post: "After the close", closed: "While the market is closed" };

/** Index futures trade almost around the clock, so they show the mood before stocks open. */
function futuresSection(data) {
  if (data.market.state === "open" || !data.rows.length) return null;
  const tiles = data.rows.map((row) => tile({
    label: row.name,
    metric: "index_futures",
    value: row.price,
    fmtCode: "num2",
    delta: deltaSpan(`${fmt(row.change_pct, "spct2")} since the last close`, row.change_pct, "good"),
    foot: h("span", {}, `${row.symbol} · ${row.when}`),
    href: `#/chart/${encodeURIComponent(row.symbol)}`,
  }));
  const movers = (data.movers || []).map((quote) => h("a", { class: "mover", href: `#/stock/${encodeURIComponent(quote.symbol)}`, title: `${quote.label} at ${quote.when}` },
    h("b", {}, quote.symbol),
    h("span", { class: "mono" }, fmt(quote.price, "num2")),
    deltaSpan(fmt(Math.abs(quote.change_pct), "pct2"), quote.change_pct, "good"),
    h("span", { class: "ext-tag" }, quote.session === "pre" ? "Pre" : "AH")));
  return h("section", { class: "mt" },
    h("div", { class: "section-title", style: { marginTop: 0 } }, `${FUTURES_TITLES[data.market.state] || "Outside market hours"}: index futures`, info("index_futures")),
    h("p", { class: "sub-text", style: { margin: "0 0 8px" } },
      "Change against each future's price at the last 4pm close. The rest of this page uses regular-session data until the market opens."),
    h("div", { class: "grid grid-tiles" }, tiles),
    movers.length
      ? h("div", { class: "movers" },
        h("span", { class: "movers-title" }, "Your watchlist, biggest moves", info("extended_hours")),
        movers)
      : null);
}

/** A list that shows the first few rows, with a working control to reveal the rest. */
function expandable(rows, limit) {
  const box = h("div", { class: "feed" });
  const draw = (expanded) => {
    const shown = expanded ? rows : rows.slice(0, limit);
    replace(box, shown, rows.length > limit
      ? h("button", { class: "link-btn", type: "button", style: { padding: "6px 0 0", textAlign: "left" }, onclick: () => draw(!expanded) }, expanded ? "Show fewer" : `Show all ${rows.length}`)
      : null);
  };
  draw(false);
  return box;
}

const CONTRARIAN_TEXT = { 1: "fear territory", 0: "balanced territory", "-1": "greed territory" };

function flipsCard(flips) {
  const rows = flips.map((flip) => h("div", { class: "feed-row" },
    h("span", { class: "feed-date" }, fmtDate(flip.date)),
    h("span", { class: "feed-text" },
      h("a", { href: `#/${flip.page}`, style: { color: "inherit" } }, flip.label),
      info(flip.metric),
      flip.contrarian
        ? h("span", { class: "sub-text" }, `moved to ${CONTRARIAN_TEXT[flip.to]}`)
        : [h("span", { class: "sub-text" }, "turned"), scoreBadge(flip.to)],
      h("span", { class: "sub-text" }, fmt(flip.value, flip.fmt)))));
  return h("section", { class: "card" },
    h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "What changed recently"),
    h("p", { class: "card-sub", style: { marginBottom: "4px" } }, "Signals whose state flipped in the last 15 sessions."),
    rows.length ? expandable(rows, 6) : emptyBox("No signal has changed state recently."));
}

function catalystsCard(events) {
  const rows = events.map((event) => h("div", { class: "feed-row" },
    h("span", { class: "feed-date" }, fmtDate(event.date)),
    h("span", { class: "feed-text" },
      event.symbol ? h("a", { href: `#/stock/${encodeURIComponent(event.symbol)}`, style: { color: "inherit" } }, event.label) : h("span", {}, event.label),
      info(event.metric),
      h("span", { class: "sub-text" }, event.days === 0 ? "today" : event.days === 1 ? "tomorrow" : `in ${event.days} days`),
      // What prediction markets expect for this event, when they trade on it.
      event.odds ? h("span", { class: "odds-note" }, `· ${event.odds.text}`, info(event.odds.metric)) : null)));
  return h("section", { class: "card" },
    h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "Next catalysts"),
    h("p", { class: "card-sub", style: { marginBottom: "4px" } }, "Option expirations, Fed and economic releases, and your watchlist's earnings."),
    rows.length ? expandable(rows, 6) : emptyBox("The event calendar is still loading."));
}

function gammaCard(gamma) {
  if (!gamma) {
    return h("section", { class: "card" },
      h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "S&P 500 option positioning", info("gamma_regime")),
      emptyBox("Option chains are still loading."));
  }
  const positive = gamma.gamma_regime === "positive";
  const row = (label, metric, value, sub) => h("div", { class: "kv-row" },
    h("span", { class: "kv-label" }, label, info(metric)),
    h("span", { class: "kv-value" }, value, sub ? h("span", { class: "sub-text", style: { fontWeight: 400 } }, ` ${sub}`) : null));
  return h("section", { class: "card" },
    h("div", { class: "card-head" },
      h("h3", { class: "card-title" }, "S&P 500 option positioning", info("gamma_regime")),
      h("a", { class: "btn btn-xs", href: "#/options/SPX" }, "Open gamma page")),
    h("div", { style: { marginBottom: "6px" } }, statusBadge(positive ? "good" : "critical", positive ? "Positive gamma: moves tend to be damped" : "Negative gamma: moves can be amplified")),
    row("Net dealer gamma", "net_gex", fmt(gamma.net_gex, "usdcompact"), "per 1%"),
    row("Gamma flip", "gamma_flip", fmt(gamma.gamma_flip, "num0"), gamma.flip_distance_pct === null ? "" : `spot ${fmt(gamma.flip_distance_pct, "spct1")}`),
    row("Call wall", "call_wall", fmt(gamma.call_wall, "num0")),
    row("Put wall", "put_wall", fmt(gamma.put_wall, "num0")),
    row("Put/call volume", "pc_volume", fmt(gamma.pc_volume, "num2")),
    row("Implied volatility (30d)", "iv30", fmt(gamma.iv30, "pct1")),
    row("Expected move this week", "expected_move", fmt(gamma.expected_move_week, "pct1")));
}

const SIGNAL_COLUMNS = [
  { key: "label", label: "Signal", left: true, render: (row) => h("span", { style: { display: "inline-flex", alignItems: "center", gap: "4px" } }, h("a", { href: `#/${row.page}`, style: { color: "inherit" } }, row.label), info(row.metric)) },
  { key: "value", label: "Reading", render: (row) => fmt(row.value, row.fmt) },
  { key: "score", label: "State", render: (row) => scoreBadge(row.score, row.pillar === "crowd"), value: (row) => row.score },
  { key: "change_1w", label: "1-week change", render: (row) => deltaSpan(fmt(Math.abs(row.change_1w ?? 0), row.fmt.replace(/^s/, "")), row.change_1w, row.higher_is) },
  { key: "pctile_1y", label: "1-year rank", render: (row) => (row.pctile_1y === null ? MISSING : `${Math.round(row.pctile_1y)}%`) },
  { key: "date", label: "As of", render: (row) => fmtDate(row.date), value: (row) => row.date },
  { key: "spark", label: "Trend", sortable: false, render: (row) => h("div", { class: "spark-cell" }, sparkline(row.spark, { width: 80, height: 20 })) },
];

const SECTOR_PERIODS = [{ value: "chg_1d", label: "Today" }, { value: "chg_5d", label: "5 days" }, { value: "chg_1m", label: "1 month" }];

export default {
  title: () => "Overview",
  async mount(root) {
    let sectorPeriod = "chg_1d";
    const healthChart = chartCard({ title: "Market Health over the past year", metric: "market_health", height: 170 });
    const spxChart = chartCard({ title: "S&P 500 with 50- and 200-day averages", metric: "sma", height: 270 });
    const sectorChart = chartCard({ title: "Sector performance", metric: "relative_performance", height: 270 });
    const tilesBox = h("div", { class: "grid grid-tiles mt" });
    const heroBox = h("div", { class: "stack" });
    const pillarsBox = h("div", {});
    const signalsBox = h("div", {});
    const sideBox = h("div", { class: "stack" });
    const gammaBox = h("div", {});
    const alertsBox = h("div", {});
    const futuresBox = h("div", {});
    let last = null;

    // Futures move while no other data refreshes, so they poll on their own schedule.
    const loadFutures = async () => {
      try {
        replace(futuresBox, futuresSection(await api.get("/api/futures")));
      } catch {
        replace(futuresBox);
      }
    };

    const loadAlerts = async () => {
      try {
        replace(alertsBox, alertsCard(await api.get("/api/alerts")));
      } catch (error) {
        replace(alertsBox, h("section", { class: "card" }, emptyBox(error.message)));
      }
    };
    const onAlertsChanged = () => loadAlerts();
    document.addEventListener("alerts-changed", onAlertsChanged);

    // ------------------------------------------------------- Key signals, editable
    let editing = false;
    let catalog = null;
    let saving = Promise.resolve();

    const signalInfo = (id) => {
      const row = last.signals.find((item) => item.id === id);
      const entry = catalog?.groups.flatMap((group) => group.signals).find((item) => item.id === id);
      return { row, label: row?.label || entry?.label || id, metric: row?.metric || entry?.metric || id };
    };

    // Saves in the background; the list on screen has already changed.
    const saveSignals = (ids) => {
      last.signal_ids = ids;
      last.signals_custom = true;
      last.signals = ids.map((id) => last.signals.find((row) => row.id === id)).filter(Boolean);
      drawSignals();
      saving = saving
        .then(() => api.post("/api/overview/signals", { ids }))
        .then(async (result) => {
          // A newly added signal needs its reading, so fetch the page data again.
          if (result.ids.some((id) => !last.signals.some((row) => row.id === id))) draw(await api.get("/api/overview"));
        })
        .catch((error) => {
          toast(error.message, "error");
          return api.get("/api/overview").then(draw);
        });
    };

    const move = (from, to) => {
      const ids = [...last.signal_ids];
      if (to < 0 || to >= ids.length || from === to) return;
      const [item] = ids.splice(from, 1);
      ids.splice(to, 0, item);
      saveSignals(ids);
    };

    const editRow = (id, index, count) => {
      const { row, label, metric } = signalInfo(id);
      const li = h("li", { class: "edit-row", draggable: "true", dataset: { signal: id } },
        h("span", { class: "drag-handle", "aria-hidden": "true", title: "Drag to reorder" }, "⠿"),
        h("span", { class: "edit-label" }, label, info(metric)),
        h("span", { class: "sub-text edit-value" }, row ? fmt(row.value, row.fmt) : "No data yet"),
        h("span", { class: "edit-actions" },
          h("button", { class: "btn btn-ghost btn-xs", type: "button", "aria-label": `Move ${label} up`, title: "Move up", disabled: index === 0, onclick: () => move(index, index - 1) }, "↑"),
          h("button", { class: "btn btn-ghost btn-xs", type: "button", "aria-label": `Move ${label} down`, title: "Move down", disabled: index === count - 1, onclick: () => move(index, index + 1) }, "↓"),
          h("button", { class: "btn btn-ghost btn-xs", type: "button", "aria-label": `Remove ${label}`, title: "Remove from Key signals", dataset: { removeSignal: id }, onclick: () => saveSignals(last.signal_ids.filter((item) => item !== id)) }, "×")));
      li.addEventListener("dragstart", (event) => {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", String(index));
        li.classList.add("is-dragging");
      });
      li.addEventListener("dragend", () => li.classList.remove("is-dragging"));
      li.addEventListener("dragover", (event) => {
        event.preventDefault();
        const box = li.getBoundingClientRect();
        const after = event.clientY > box.top + box.height / 2;
        li.classList.toggle("drop-before", !after);
        li.classList.toggle("drop-after", after);
      });
      li.addEventListener("dragleave", () => li.classList.remove("drop-before", "drop-after"));
      li.addEventListener("drop", (event) => {
        event.preventDefault();
        const after = li.classList.contains("drop-after");
        li.classList.remove("drop-before", "drop-after");
        const from = Number(event.dataTransfer.getData("text/plain"));
        if (Number.isNaN(from)) return;
        let to = index + (after ? 1 : 0);
        if (from < to) to -= 1;
        move(from, to);
      });
      return li;
    };

    const drawSignals = () => {
      const head = (...actions) => h("div", { class: "card-head" },
        h("h3", { class: "card-title" }, "Key signals"),
        h("div", { class: "card-actions" }, actions));
      if (!editing) {
        replace(signalsBox, h("section", { class: "card" },
          head(h("button", {
            class: "btn btn-xs",
            type: "button",
            id: "edit-signals",
            title: "Choose which signals appear here, and in what order",
            onclick: async () => {
              try {
                catalog = catalog || await api.get("/api/signals");
                editing = true;
                drawSignals();
              } catch (error) {
                toast(error.message, "error");
              }
            },
          }, "Edit list")),
          dataTable({
            id: "overview-signals",
            columns: SIGNAL_COLUMNS,
            rows: last.signals,
            rowKey: (row) => row.id,
            itemName: "signal",
            reorder: (ids) => saveSignals([...ids, ...last.signal_ids.filter((item) => !ids.includes(item))]),
            empty: last.signal_ids.length ? "Signals appear here as their data loads." : "No Key signals chosen. Select Edit list to add some.",
          })));
        return;
      }
      const ids = last.signal_ids;
      const shown = new Set(ids);
      const addSelect = h("select", {
        class: "input",
        "aria-label": "Add a signal",
        id: "add-key-signal",
        onchange: (event) => {
          const id = event.target.value;
          if (id) saveSignals([...last.signal_ids, id]);
        },
      });
      const choices = signalOptions(catalog)
        .map((group) => ({ ...group, options: group.options.filter((item) => !shown.has(item.value)) }))
        .filter((group) => group.options.length);
      fillSelect(addSelect, [{ value: "", label: choices.length ? "Add a signal…" : "Every signal is already shown" }, ...choices], "");
      replace(signalsBox, h("section", { class: "card" },
        head(
          last.signals_custom ? h("button", {
            class: "btn btn-ghost btn-xs",
            type: "button",
            id: "reset-signals",
            onclick: () => {
              saving = saving
                .then(() => api.del("/api/overview/signals"))
                .then(() => api.get("/api/overview"))
                .then(draw)
                .catch((error) => toast(error.message, "error"));
            },
          }, "Reset to default") : null,
          h("button", { class: "btn btn-primary btn-xs", type: "button", id: "done-signals", onclick: () => { editing = false; drawSignals(); } }, "Done")),
        h("p", { class: "card-sub" }, "Drag rows to reorder them, or use the arrows. Removed signals can be added back below, and every signal stays on its own page."),
        ids.length
          ? h("ol", { class: "edit-list", "aria-label": "Key signals, in order" }, ids.map((id, index) => editRow(id, index, ids.length)))
          : emptyBox("No Key signals chosen. Add some below."),
        h("div", { style: { marginTop: "10px", maxWidth: "360px" } }, field("Add a signal", addSelect))));
    };

    const drawSectors = () => {
      const period = SECTOR_PERIODS.find((item) => item.value === sectorPeriod).label;
      sectorChart.setSubtitle(`Sector ETFs, ${period.toLowerCase()}`);
      sectorChart.render(rankedBars({
        items: last.sectors.map((item) => ({ label: item.name, value: item[sectorPeriod] })),
        name: period,
      }));
    };

    const draw = (data) => {
      // A server started before an update sends the older format; keep the page working.
      data.signal_ids = data.signal_ids || data.signals.map((row) => row.id);
      last = data;
      replace(heroBox, heroCard(data.health));
      replace(pillarsBox, pillarsCard(data));
      replace(tilesBox, data.tiles.map(quoteTile));
      drawSignals();
      replace(sideBox, alertsBox, flipsCard(data.flips), catalystsCard(data.catalysts));
      replace(gammaBox, gammaCard(data.gamma));
      if (data.health.history) {
        heroBox.append(healthChart.el);
        healthChart.render(lineChart({
          dates: data.health.history.dates,
          series: [{ name: "Market Health", values: data.health.history.values, area: true }],
          yFmt: "num0",
          min: 0,
          max: 100,
          thresholds: [{ y: 60, label: "Healthy" }, { y: 30, label: "Risk-off" }],
        }));
      }
      spxChart.render(lineChart({
        dates: data.spx_chart.dates,
        series: [
          { name: "S&P 500", values: data.spx_chart.series.close, slot: 0 },
          { name: "50-day", values: data.spx_chart.series.sma50, slot: 1 },
          { name: "200-day", values: data.spx_chart.series.sma200, slot: 6 },
        ],
        yFmt: "num0",
      }));
      drawSectors();
    };

    replace(root,
      pageHead("Market overview", "Where the US market stands now, what changed recently and what's coming up. Select any info button for an explanation."),
      futuresBox,
      h("div", { class: "split-1-2" }, heroBox, pillarsBox),
      tilesBox,
      h("div", { class: "split-2-1 mt", style: { alignItems: "start" } }, signalsBox, sideBox),
      h("div", { class: "section-title" }, "Market snapshot"),
      filters(field("Sector period", segmented(SECTOR_PERIODS, sectorPeriod, (value) => {
        sectorPeriod = value;
        drawSectors();
      }, "Sector performance period"))),
      h("div", { class: "grid grid-3" }, spxChart.el, sectorChart.el, gammaBox));

    loadFutures();
    draw(await api.get("/api/overview"));
    await loadAlerts();
    // Started only once the page has loaded, so a failed load can't leave a timer behind.
    const futuresTimer = setInterval(loadFutures, 3 * 60 * 1000);
    return {
      refresh: async () => {
        draw(await api.get("/api/overview"));
        await loadAlerts();
      },
      unmount: () => {
        clearInterval(futuresTimer);
        document.removeEventListener("alerts-changed", onAlertsChanged);
      },
    };
  },
};
