// Timeline: what the dashboard would have said on any past date, and what happened next.
import { api, query } from "../lib/api.js";
import { chartCard, colors, lineChart } from "../lib/charts.js";
import { MISSING, fmt, fmtDate, h, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { dataTable, emptyBox, errorBox, field, filters, pageHead, scoreBadge, segmented, statusBadge, tile, toast } from "../lib/ui.js";

const QUICK = [
  { value: "7", label: "1 week ago" },
  { value: "30", label: "1 month ago" },
  { value: "91", label: "3 months ago" },
  { value: "365", label: "1 year ago" },
];

const daysAgo = (days) => {
  const day = new Date();
  day.setDate(day.getDate() - Number(days));
  return day.toLocaleDateString("en-CA");
};

function scoreText(value) {
  return value === null || value === undefined ? MISSING : Math.round(value);
}

/** Puts a vertical line at the chosen date on a line chart. */
function withMarker(result, date, c) {
  if (!result.option) return result;
  const series = result.option.series[0];
  const marker = { xAxis: date, lineStyle: { color: c.ink2, width: 1.5, type: "dashed" }, label: { formatter: fmtDate(date, true), color: c.ink2, position: "insideEndTop", fontSize: 10.5 } };
  series.markLine = { symbol: "none", silent: true, animation: false, data: [...(series.markLine?.data || []), marker], label: { show: true } };
  return result;
}

const SIGNAL_COLUMNS = [
  { key: "label", label: "Signal", left: true, render: (row) => h("span", { style: { display: "inline-flex", gap: "4px", alignItems: "center" } }, row.label, info(row.metric)) },
  { key: "then", label: "Reading then", render: (row) => fmt(row.then, row.fmt) },
  { key: "score_then", label: "State then", value: (row) => row.score_then, render: (row) => scoreBadge(row.score_then, row.crowd) },
  { key: "now", label: "Reading now", render: (row) => fmt(row.now, row.fmt) },
  { key: "score_now", label: "State now", value: (row) => row.score_now, render: (row) => scoreBadge(row.score_now, row.crowd) },
];

export default {
  title: () => "Timeline",
  async mount(root) {
    let data = null;
    let loadId = 0;
    const dateInput = h("input", { class: "input", type: "date", id: "timeline-date", "aria-label": "Date to look back at" });
    const quickBox = h("div", {});
    const notableSelect = h("select", { class: "input", "aria-label": "Notable dates", id: "timeline-notable" });
    const healthCard = chartCard({ title: "Market Health over time", metric: "timeline", subtitle: "Select a point on the line to look back at that day", height: 220 });
    const spxCard = chartCard({ title: "S&P 500", metric: "spx", height: 200 });
    const thenBox = h("div", { class: "grid cols-4" });
    const pillarsBox = h("section", { class: "card" });
    const nextBox = h("section", { class: "card" });
    const signalsBox = h("section", { class: "card" });
    let clicks = false;

    const draw = () => {
      const c = colors();
      dateInput.value = data.date;
      dateInput.min = data.first || "";
      dateInput.max = data.latest;
      // Jumps, not a setting: every button works every time.
      replace(quickBox, h("div", { class: "segmented", role: "group", "aria-label": "Quick picks" },
        QUICK.map((item) => h("button", { type: "button", onclick: () => go(daysAgo(item.value)) }, item.label))));
      replace(notableSelect, h("option", { value: "" }, "Market peaks and lows…"),
        data.notable.map((item) => h("option", { value: item.date }, `${fmtDate(item.date, true)}: ${item.label}`)));
      healthCard.render(withMarker(lineChart({
        dates: data.chart.dates,
        series: [{ name: "Market Health", values: data.chart.health, area: true }],
        yFmt: "num0", min: 0, max: 100,
        thresholds: [{ y: 60, label: "Healthy" }, { y: 30, label: "Risk-off" }],
      }), data.date, c));
      spxCard.render(withMarker(lineChart({ dates: data.spx_chart.dates, series: [{ name: "S&P 500", values: data.spx_chart.values }], yFmt: "num0" }), data.date, c));
      if (!clicks && healthCard.chart) {
        clicks = true;
        healthCard.chart.getZr().on("click", (event) => {
          const point = healthCard.chart.convertFromPixel({ seriesIndex: 0 }, [event.offsetX, event.offsetY]);
          const index = Math.round(point?.[0]);
          if (Number.isFinite(index) && data.chart.dates[index]) go(data.chart.dates[index]);
        });
      }
      const health = data.health;
      replace(thenBox,
        tile({ label: `Market Health on ${fmtDate(data.date, true)}`, metric: "market_health", valueText: scoreText(health.then), foot: h("span", {}, health.label_then) }),
        tile({ label: "Market Health now", metric: "market_health", valueText: scoreText(health.now), foot: h("span", {}, health.label_now) }),
        tile({ label: "Crowd fear then", metric: "crowd_fear", valueText: scoreText(data.crowd.then), foot: h("span", {}, "0 greedy, 100 fearful") }),
        tile({ label: "S&P 500 then", metric: "spx", value: data.spx_then, fmtCode: "num0" }));
      replace(pillarsBox,
        h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "What drove the score then"),
        data.pillars.map((pillar) => h("div", { class: "kv-row" },
          h("span", { class: "kv-label" }, pillar.label, info(pillar.metric)),
          h("span", { class: "kv-value" }, `${scoreText(pillar.then)}`, h("span", { class: "sub-text", style: { fontWeight: 400 } }, ` now ${scoreText(pillar.now)}`)))));
      replace(nextBox,
        h("h3", { class: "card-title", style: { marginBottom: "6px" } }, "What the S&P 500 did next", info("forward_returns")),
        data.forward.map((item) => h("div", { class: "kv-row" },
          h("span", { class: "kv-label" }, `After ${item.label}`),
          item.change === null
            ? h("span", { class: "kv-value sub-text" }, "Not yet known")
            : h("span", { class: "kv-value" }, fmt(item.change, "spct1"), h("span", { class: "sub-text", style: { fontWeight: 400 } }, ` · worst point ${fmt(item.worst, "spct1")}`)))),
        h("p", { class: "sub-text", style: { marginTop: "6px" } }, "One date says little on its own. Use Signal Study to test a reading across many dates."));
      replace(signalsBox,
        h("h3", { class: "card-title", style: { marginBottom: "6px" } }, `Every scored signal on ${fmtDate(data.date, true)} and now`),
        dataTable({ id: "timeline-signals", columns: SIGNAL_COLUMNS, rows: data.signals, rowKey: (row) => row.id, maxHeight: 520 }));
    };

    async function go(date) {
      const id = ++loadId;
      [healthCard, spxCard].forEach((card) => card.refreshing());
      try {
        const next = await api.get(`/api/timeline${query({ date })}`);
        if (id !== loadId) return;
        if (!next.available) {
          replace(signalsBox, emptyBox("Market history is still loading."));
          return;
        }
        data = next;
        draw();
      } catch (error) {
        toast(error.message, "error");
      }
    }

    dateInput.addEventListener("change", () => { if (dateInput.value) go(dateInput.value); });
    notableSelect.addEventListener("change", () => { if (notableSelect.value) go(notableSelect.value); });

    replace(root,
      pageHead("Timeline", "Look back at any day: what this dashboard would have shown then, using only data that was published by that day, and what the market did next.", info("timeline")),
      filters(field("Date", dateInput), field("Jump to", quickBox), field("Notable dates", notableSelect)),
      healthCard.el,
      h("div", { class: "mt" }, thenBox),
      h("div", { class: "grid grid-2 mt", style: { alignItems: "start" } }, pillarsBox, nextBox),
      h("div", { class: "mt" }, spxCard.el),
      h("div", { class: "mt" }, signalsBox));
    try {
      await go(daysAgo(30));
    } catch (error) {
      replace(signalsBox, errorBox(error.message, () => go(daysAgo(30))));
    }
    return { refresh: () => go(data?.date || daysAgo(30)) };
  },
};
