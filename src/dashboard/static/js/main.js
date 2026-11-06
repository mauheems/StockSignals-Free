// App shell: navigation, routing, global ticker search, data status and theme.
import { api, query } from "./lib/api.js";
import { disposeCharts } from "./lib/charts.js";
import { debounce, fmtAgo, h, replace } from "./lib/dom.js";
import { setCatalog } from "./lib/guide.js";
import { errorBox, loadingBox, toast } from "./lib/ui.js";

import backtest from "./pages/backtest.js";
import breadth from "./pages/breadth.js";
import chart from "./pages/chart.js";
import flows from "./pages/flows.js";
import guide from "./pages/guide.js";
import macro from "./pages/macro.js";
import options from "./pages/options.js";
import overview from "./pages/overview.js";
import rotation from "./pages/rotation.js";
import scanner from "./pages/scanner.js";
import sources from "./pages/sources.js";
import stock from "./pages/stock.js";
import study from "./pages/study.js";
import volatility from "./pages/volatility.js";
import watchlist from "./pages/watchlist.js";

const PAGES = { overview, breadth, rotation, volatility, options, flows, macro, watchlist, scanner, chart, stock, backtest, study, guide, sources };

const NAV = [
  { label: "Market", items: [
    ["overview", "Overview"], ["breadth", "Trend & Breadth"], ["rotation", "Rotation"], ["volatility", "Volatility"],
    ["options", "Options & Gamma"], ["flows", "Flows & Sentiment"], ["macro", "Macro & Credit"],
  ] },
  { label: "Stocks", items: [["watchlist", "Watchlist"], ["scanner", "S&P 500 Scanner"], ["chart", "Chart Lab"]] },
  { label: "Research", items: [["backtest", "Backtests"], ["study", "Signal Study"]] },
  { label: "Reference", items: [["guide", "Metric Guide"], ["sources", "Data Sources"]] },
];

let currentPage = null;
let lastVersion = null;
let refreshTimer = null;
let pageFailed = false;

const contentEl = h("main", { class: "content", id: "content" });
const bannerEl = h("div", { class: "banner", role: "status" });
bannerEl.hidden = true;
const statusDot = h("span", { class: "status-dot" });
const statusText = h("span", {}, "Connecting…");
const freshnessText = h("span", { class: "sub-text" });
const navLinks = new Map();

function currentRoute() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [page, ...rest] = raw.split("/");
  return { page: PAGES[page] ? page : "overview", params: rest.map(decodeURIComponent) };
}

function highlightNav(page) {
  // A stock's own page belongs to the Stocks group: keep Watchlist lit while on it.
  const active = page === "stock" ? "watchlist" : page;
  for (const [id, link] of navLinks) {
    const on = id === active;
    link.classList.toggle("is-active", on);
    if (on) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

async function route() {
  const { page, params } = currentRoute();
  highlightNav(page);
  disposeCharts();
  if (currentPage?.unmount) currentPage.unmount();
  currentPage = null;
  pageFailed = false;
  // The page is on screen while it loads, so charts measure real sizes as they draw.
  const root = h("div", {}, loadingBox());
  replace(contentEl, root);
  try {
    const instance = await PAGES[page].mount(root, { params });
    // The user may have navigated away while this page was loading.
    if (currentRoute().page !== page || currentRoute().params.join("/") !== params.join("/")) return;
    currentPage = instance || {};
    window.scrollTo(0, 0);
    document.title = `${PAGES[page].title(params)} · Market Signals`;
    if (currentPage.afterAttach) currentPage.afterAttach();
  } catch (error) {
    console.warn(`Page ${page} failed to load:`, error);
    if (currentRoute().page === page) {
      pageFailed = true;
      replace(contentEl, errorBox(error.message || "This page could not load.", route));
    }
  }
}

function applyStatus(status) {
  const running = status.running.length;
  if (status.first_run || (running && status.loaded < status.total)) {
    bannerEl.hidden = false;
    const share = Math.round((status.loaded / status.total) * 100);
    replace(
      bannerEl,
      h("div", { class: "banner-progress" }, h("div", { style: { width: `${share}%` } })),
      h("span", {}, `Loading market history: ${status.loaded} of ${status.total} sources ready. The first run takes a few minutes, and panels fill in as data arrives.`),
    );
  } else {
    bannerEl.hidden = true;
  }
  const latest = Math.max(0, ...status.sources.map((source) => source.last_success || 0));
  replace(freshnessText, running ? `Updating ${running} source${running > 1 ? "s" : ""}…` : `Updated ${fmtAgo(latest || null)}`);
  if (lastVersion !== null && status.version !== lastVersion) {
    clearTimeout(refreshTimer);
    if (pageFailed) {
      // Most likely the data this page needs hadn't loaded yet, and now it may have.
      refreshTimer = setTimeout(route, 400);
    } else if (currentPage?.refresh) {
      // Pull the newer data into the page without losing the user's place.
      refreshTimer = setTimeout(() => currentPage?.refresh?.().catch((error) => console.warn("Page refresh failed:", error)), 400);
    }
  }
  lastVersion = status.version;
  document.dispatchEvent(new CustomEvent("status", { detail: status }));
}

async function pollStatus() {
  try {
    const [status, overviewMarket] = await Promise.all([api.get("/api/status"), api.get("/api/market")]);
    applyStatus(status);
    statusDot.className = `status-dot ${overviewMarket.state}`;
    replace(statusText, `${overviewMarket.label} · ${overviewMarket.time_et}`);
    setTimeout(pollStatus, status.running.length ? 8000 : 30000);
  } catch {
    statusDot.className = "status-dot";
    replace(statusText, "Server not responding");
    setTimeout(pollStatus, 8000);
  }
}

function buildSearch() {
  const input = h("input", {
    class: "input",
    type: "search",
    placeholder: "Search a ticker or company…",
    "aria-label": "Search for a ticker or company",
    autocomplete: "off",
    spellcheck: false,
  });
  const results = h("div", { class: "search-results", role: "listbox" });
  results.hidden = true;
  let items = [];
  let cursor = -1;

  const choose = (symbol) => {
    input.value = "";
    results.hidden = true;
    location.hash = `#/stock/${encodeURIComponent(symbol)}`;
  };

  const draw = () => {
    replace(results, items.map((item, index) => h("button", {
      class: `search-item ${index === cursor ? "is-active" : ""}`,
      type: "button",
      role: "option",
      "aria-selected": String(index === cursor),
      onmousedown: (event) => {
        event.preventDefault();
        choose(item.symbol);
      },
    }, h("b", {}, item.symbol), h("span", {}, item.name))));
    results.hidden = !items.length;
  };

  const lookup = debounce(async () => {
    const text = input.value.trim();
    if (!text) {
      items = [];
      draw();
      return;
    }
    try {
      const data = await api.get(`/api/search${query({ q: text })}`);
      items = data.results;
      cursor = items.length ? 0 : -1;
      draw();
    } catch {
      items = [];
      draw();
    }
  }, 130);

  input.addEventListener("input", lookup);
  input.addEventListener("blur", () => setTimeout(() => { results.hidden = true; }, 120));
  input.addEventListener("focus", () => { if (items.length) results.hidden = false; });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" && items.length) {
      cursor = Math.min(items.length - 1, cursor + 1);
      draw();
      event.preventDefault();
    } else if (event.key === "ArrowUp" && items.length) {
      cursor = Math.max(0, cursor - 1);
      draw();
      event.preventDefault();
    } else if (event.key === "Enter") {
      const typed = input.value.trim().toUpperCase();
      // An exact ticker wins; otherwise take the highlighted match or try what was typed.
      const exact = items.find((item) => item.symbol === typed);
      const target = exact?.symbol || items[cursor]?.symbol || typed;
      if (target) choose(target);
    } else if (event.key === "Escape") {
      results.hidden = true;
    }
  });
  return h("div", { class: "search" }, input, results);
}

function buildTheme() {
  const button = h("button", { class: "btn btn-ghost btn-xs", type: "button", id: "theme-toggle" });
  const label = () => {
    const dark = document.documentElement.dataset.theme === "dark";
    button.textContent = dark ? "Light mode" : "Dark mode";
  };
  label();
  button.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("ms.theme", next);
    label();
    // Charts draw on canvas with resolved colours, so they are redrawn in the new theme.
    route();
  });
  return button;
}

function buildRefresh() {
  const button = h("button", {
    class: "btn btn-xs",
    type: "button",
    id: "refresh-all",
    title: "Download the latest data from every source now",
    onclick: async () => {
      button.disabled = true;
      try {
        const result = await api.post("/api/refresh");
        toast(result.started ? `Refreshing ${result.started} data sources.` : "Everything was refreshed moments ago.");
        setTimeout(pollStatusOnce, 800);
      } catch (error) {
        toast(error.message, "error");
      }
      setTimeout(() => { button.disabled = false; }, 4000);
    },
  }, "Refresh data");
  return button;
}

async function pollStatusOnce() {
  try {
    applyStatus(await api.get("/api/status"));
  } catch {
    /* the regular status poll reports connection problems */
  }
}

function buildShell() {
  const nav = h("nav", { "aria-label": "Main" }, NAV.map((group) => h("div", { class: "nav-group", style: { marginBottom: "12px" } },
    h("div", { class: "nav-group-label" }, group.label),
    group.items.map(([id, label]) => {
      const link = h("a", { class: "nav-link", href: `#/${id}` }, label);
      navLinks.set(id, link);
      return link;
    }))));
  const sidebar = h("aside", { class: "sidebar" },
    h("a", { class: "brand", href: "#/overview", style: { color: "inherit", textDecoration: "none" } },
      h("span", { class: "brand-name" }, "Market Signals"),
      h("span", { class: "brand-sub" }, "US market dashboard")),
    nav,
    h("div", { class: "sidebar-foot" }, "For research only, not investment advice."));
  const topbar = h("header", { class: "topbar" },
    buildSearch(),
    h("div", { class: "topbar-spacer" }),
    h("span", { class: "status-pill", id: "market-status" }, statusDot, statusText),
    freshnessText,
    buildRefresh(),
    buildTheme());
  replace(document.getElementById("app"), h("div", { class: "shell" }, sidebar, h("div", { class: "main" }, topbar, bannerEl, contentEl)));
}

async function boot() {
  buildShell();
  try {
    setCatalog(await api.get("/api/metrics"));
  } catch (error) {
    replace(contentEl, errorBox(error.message, () => location.reload()));
    return;
  }
  window.addEventListener("hashchange", route);
  await route();
  pollStatus();
}

boot();
