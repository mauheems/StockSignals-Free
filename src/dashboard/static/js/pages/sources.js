// Where the data comes from, how fresh it is, and a way to refresh each source.
import { api } from "../lib/api.js";
import { MISSING, fmtAgo, h, replace } from "../lib/dom.js";
import { dataTable, pageHead, statusBadge, tile, toast } from "../lib/ui.js";

const STATES = {
  ok: ["good", "Up to date"],
  running: ["neutral", "Updating"],
  pending: ["neutral", "Waiting for first load"],
  stale: ["warning", "Last update failed"],
  error: ["critical", "Not available"],
};

function everyText(seconds) {
  if (seconds < 3600) return `Every ${Math.round(seconds / 60)} min`;
  return `Every ${Math.round(seconds / 3600)} hours`;
}

export default {
  title: () => "Data Sources",
  async mount(root) {
    const tilesBox = h("div", { class: "grid cols-4", style: { marginBottom: "10px" } });
    const tableBox = h("section", { class: "card" });
    let table = null;

    const refreshOne = async (source, button) => {
      button.disabled = true;
      try {
        const result = await api.post("/api/refresh", { source: source.id });
        toast(result.started ? `Refreshing ${source.label}.` : `${source.label} is already updating.`);
        setTimeout(load, 700);
      } catch (error) {
        toast(error.message, "error");
      }
      setTimeout(() => { button.disabled = false; }, 3000);
    };

    const columns = [
      {
        key: "label", label: "Data", left: true, wrap: true,
        render: (row) => h("span", {}, h("div", { style: { fontWeight: 560 } }, row.label), h("div", { class: "sub-text" }, `Used by: ${row.feeds}`)),
      },
      { key: "provider", label: "Provider", left: true },
      { key: "delay", label: "Delay", left: true, wrap: true, render: (row) => h("span", { class: "sub-text" }, row.delay) },
      { key: "state", label: "Status", left: true, render: (row) => statusBadge(...(STATES[row.state] || STATES.pending)) },
      { key: "last_success", label: "Last update", render: (row) => fmtAgo(row.last_success) },
      { key: "every_seconds", label: "Schedule", render: (row) => everyText(row.every_seconds) },
      {
        key: "detail", label: "Details", left: true, wrap: true, sortable: false,
        render: (row) => h("span", { class: "sub-text", style: { color: row.last_error ? "var(--bad-text)" : undefined } }, row.last_error || row.detail || MISSING),
      },
      {
        key: "action", label: "", sortable: false,
        render: (row) => {
          const button = h("button", { class: "btn btn-xs", type: "button", disabled: row.state === "running", dataset: { source: row.id }, onclick: () => refreshOne(row, button) }, "Refresh");
          return button;
        },
      },
    ];

    const draw = (status) => {
      const ok = status.sources.filter((row) => row.last_success && !row.last_error).length;
      const failed = status.sources.filter((row) => row.last_error).length;
      replace(tilesBox,
        tile({ label: "Sources up to date", valueText: `${ok} of ${status.total}` }),
        tile({ label: "Sources with a problem", value: failed, fmtCode: "num0", foot: h("span", {}, failed ? "Panels show the last good data" : "All sources are healthy") }),
        tile({ label: "Updating now", value: status.running.length, fmtCode: "num0" }),
        tile({ label: "Local database", valueText: `${Math.round(status.db_bytes / 1e6)} MB`, foot: h("span", {}, "data/market.db") }));
      if (table) table.update(status.sources);
      else {
        table = dataTable({ columns, rows: status.sources, rowKey: (row) => row.id });
        replace(tableBox, table);
      }
    };

    const load = async () => draw(await api.get("/api/status"));
    const onStatus = (event) => draw(event.detail);
    document.addEventListener("status", onStatus);

    replace(root,
      pageHead("Data sources", "Everything comes from free public data with no accounts or API keys. If a source goes down, its panels keep the last good data and everything else carries on."),
      tilesBox,
      tableBox,
      h("p", { class: "table-note" }, "Stock and ETF prices are delayed about 15 minutes, and option chains about 15 minutes, so this is for planning, not for timing fills. These free endpoints come with no guarantee, and can change without notice. For research and personal use only."));

    await load();
    return {
      refresh: load,
      unmount: () => document.removeEventListener("status", onStatus),
    };
  },
};
