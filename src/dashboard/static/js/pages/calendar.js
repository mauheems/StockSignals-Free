// Economic calendar: the releases that move markets, what traders expect, and past surprises.
import { api } from "../lib/api.js";
import { MISSING, fmt, fmtDate, h, prefs, replace } from "../lib/dom.js";
import { info } from "../lib/guide.js";
import { dataTable, deltaSpan, emptyBox, errorBox, field, filters, pageHead, segmented } from "../lib/ui.js";

const weekday = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString("en-US", { weekday: "short" });

function expectationLine(item) {
  const range = item.low !== null && item.high !== null && item.low !== item.high
    ? ` (likely ${fmt(item.low, item.fmt)} to ${fmt(item.high, item.fmt)})`
    : "";
  return h("div", { class: "cal-expect" },
    h("span", { class: "sub-text" }, `${item.label}${item.period ? `, ${item.period}` : ""}:`),
    h("strong", {}, ` ${fmt(item.median, item.fmt)}`),
    h("span", { class: "sub-text" }, range),
    item.previous !== null ? h("span", { class: "sub-text" }, ` · last ${fmt(item.previous, item.fmt)}${item.previous_period ? ` (${item.previous_period})` : ""}`) : null,
    info(`pm_${item.key === "cpi" ? "cpi" : item.key}`));
}

function eventRow(event) {
  const details = [];
  if (event.fed) {
    details.push(h("div", { class: "cal-expect" },
      h("span", { class: "sub-text" }, "Traders expect:"),
      h("strong", {}, ` ${event.fed.likely} (${Math.round(event.fed.prob * 100)}%)`),
      h("span", { class: "sub-text" }, ` · hike ${Math.round(event.fed.hike * 100)}%, no change ${Math.round(event.fed.hold * 100)}%, cut ${Math.round(event.fed.cut * 100)}%`),
      info("fed_odds")));
  }
  event.expectations.forEach((item) => details.push(expectationLine(item)));
  return h("div", { class: "cal-row" },
    h("div", { class: "cal-date" }, h("strong", {}, `${weekday(event.date)} ${fmtDate(event.date)}`), h("span", { class: "sub-text" }, event.time ? `${event.time} ET` : "")),
    h("div", { class: "cal-body" },
      h("div", { class: "cal-title" }, event.label, event.metric ? info(event.metric) : null,
        event.kind === "expiration" ? h("span", { class: "sub-text" }, " option expiration") : null),
      details.length ? details : (event.kind === "macro" ? h("span", { class: "sub-text" }, "No prediction market for this release.") : null)));
}

const PAST_COLUMNS = [
  { key: "release", label: "Released", left: true, render: (row) => fmtDate(row.release, true), value: (row) => row.release },
  { key: "label", label: "Number", left: true, render: (row) => `${row.label}, ${row.period}` },
  { key: "expected", label: "Expected", render: (row) => fmt(row.expected, row.fmt) },
  { key: "actual", label: "Actual", render: (row) => (row.actual === null ? h("span", { class: "sub-text" }, "Not in yet") : fmt(row.actual, row.fmt)) },
  {
    key: "surprise", label: "Surprise", metric: "release_surprise",
    render: (row) => (row.surprise === null ? MISSING : deltaSpan(fmt(Math.abs(row.surprise), row.fmt.replace(/^s/, "")), row.surprise, null)),
  },
];

export default {
  title: () => "Economic Calendar",
  async mount(root) {
    const state = { expirations: "hide", ...prefs.get("calendar", {}) };
    const listBox = h("section", { class: "card" });
    const pastBox = h("section", { class: "card" });
    let data = null;

    const draw = () => {
      const events = [...data.upcoming];
      if (state.expirations === "show") {
        events.push(...data.expirations.map((item) => ({ ...item, kind: "expiration", expectations: [], fed: null })));
        events.sort((a, b) => a.date.localeCompare(b.date));
      }
      replace(listBox,
        h("div", { class: "card-head" },
          h("h3", { class: "card-title" }, "Coming up", info("econ_calendar")),
          segmented([{ value: "hide", label: "Releases only" }, { value: "show", label: "With option expirations" }], state.expirations, (value) => {
            state.expirations = value;
            prefs.set("calendar", state);
            draw();
          }, "Show option expirations")),
        h("p", { class: "card-sub" }, "Middle estimates come from Kalshi's markets, the evening before when the release is today. Big moves usually come from the surprise: the gap between this estimate and the actual number."),
        events.length ? h("div", { class: "cal-list" }, events.map(eventRow)) : emptyBox("The release calendar is still loading."));
      replace(pastBox,
        h("h3", { class: "card-title", style: { marginBottom: "4px" } }, "Past releases: expected against actual", info("release_surprise")),
        h("p", { class: "card-sub" }, "The app records what the market expected just before each release, so this list grows as releases pass."),
        dataTable({ id: "calendar-past", columns: PAST_COLUMNS, rows: data.past, rowKey: (row) => `${row.key}-${row.period}`, empty: "No releases recorded yet. After the next CPI or jobs report, its surprise shows up here." }));
    };

    const load = async () => {
      data = await api.get("/api/calendar");
      draw();
    };

    replace(root,
      pageHead("Economic calendar", "The reports and Fed meetings that move markets, with what traders expect for each. Times are US Eastern.", info("econ_calendar")),
      listBox,
      h("div", { class: "mt" }, pastBox));
    try {
      await load();
    } catch (error) {
      replace(listBox, errorBox(error.message, load));
    }
    return { refresh: load };
  },
};
