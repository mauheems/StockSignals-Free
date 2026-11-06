// Every metric in the dashboard explained, searchable and linkable.
import { h, replace } from "../lib/dom.js";
import { allCategories, allMetrics, helpBlocks } from "../lib/guide.js";
import { emptyBox, field, filters, pageHead, select } from "../lib/ui.js";

export default {
  title: (params) => (params[0] && allMetrics()[params[0]] ? allMetrics()[params[0]].name : "Metric Guide"),
  async mount(root, { params }) {
    const metrics = allMetrics();
    const categories = allCategories();
    const target = params[0] && metrics[params[0]] ? params[0] : null;
    const state = { search: "", category: "" };
    const listBox = h("div", {});
    const countBox = h("span", { class: "sub-text" });

    const draw = () => {
      const needle = state.search.trim().toLowerCase();
      const groups = categories
        .filter((category) => !state.category || category.id === state.category)
        .map((category) => ({
          category,
          entries: Object.entries(metrics).filter(([id, entry]) => entry.category === category.id && (!needle
            || id.includes(needle)
            || entry.name.toLowerCase().includes(needle)
            || entry.what.toLowerCase().includes(needle)
            || entry.read.toLowerCase().includes(needle))),
        }))
        .filter((group) => group.entries.length);
      const total = groups.reduce((sum, group) => sum + group.entries.length, 0);
      replace(countBox, `${total} of ${Object.keys(metrics).length} metrics`);
      replace(listBox, groups.length
        ? groups.map((group) => [
          h("div", { class: "section-title" }, group.category.label),
          h("div", { class: "guide-grid" }, group.entries.map(([id, entry]) => h("section", {
            class: `card guide-card ${id === target ? "is-target" : ""}`,
            id: `metric-${id}`,
          }, h("h3", {}, entry.name), helpBlocks(entry)))),
        ])
        : emptyBox("No metric matches that search."));
    };

    replace(root,
      pageHead("Metric guide", "What every number in this dashboard means, how to read it, how it's calculated and where it comes from. Thresholds described as scored are this dashboard's own rules of thumb, not official levels."),
      filters(
        field("Search", h("input", {
          class: "input",
          type: "search",
          style: { width: "260px" },
          placeholder: "For example gamma, breadth, VIX",
          "aria-label": "Search metrics",
          id: "guide-search",
          oninput: (event) => {
            state.search = event.target.value;
            draw();
          },
        })),
        field("Category", select([{ value: "", label: "All categories" }, ...categories.map((item) => ({ value: item.id, label: item.label }))], "", (value) => {
          state.category = value;
          draw();
        }, { "aria-label": "Category" })),
        h("div", { style: { alignSelf: "center" } }, countBox)),
      listBox);
    draw();

    return {
      afterAttach: () => {
        if (target) document.getElementById(`metric-${target}`)?.scrollIntoView({ block: "start" });
      },
    };
  },
};
