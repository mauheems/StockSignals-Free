// Small DOM and formatting helpers. Text always goes in through text nodes, never
// through innerHTML, because ticker names and labels come from outside sources.

export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key in el && !["list", "form", "role"].includes(key)) el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function replace(el, ...children) {
  el.replaceChildren();
  return append(el, children);
}

export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== null && value !== undefined) el.setAttribute(key, value);
  }
  for (const child of children) if (child) el.append(child);
  return el;
}

/** Escape text placed in the HTML strings that chart tooltips require. */
export function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const number = (value, digits) =>
  value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function compact(value, digits = 1) {
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1e12) return `${sign}${number(abs / 1e12, digits)}T`;
  if (abs >= 1e9) return `${sign}${number(abs / 1e9, digits)}B`;
  if (abs >= 1e6) return `${sign}${number(abs / 1e6, digits)}M`;
  if (abs >= 1e4) return `${sign}${number(abs / 1e3, digits)}K`;
  return `${sign}${number(abs, abs >= 100 ? 0 : digits)}`;
}

function signed(value, text) {
  return value > 0 ? `+${text}` : text;
}

export const MISSING = "–";

/** Format a value by a short code. Percent codes expect values already in percent. */
export function fmt(value, code = "num2") {
  if (value === null || value === undefined || Number.isNaN(value)) return MISSING;
  switch (code) {
    case "num0": return number(value, 0);
    case "num1": return number(value, 1);
    case "num2": return number(value, 2);
    case "num3": return number(value, 3);
    case "num4": return number(value, 4);
    case "snum0": return signed(value, number(value, 0));
    case "snum1": return signed(value, number(value, 1));
    case "snum2": return signed(value, number(value, 2));
    case "snum3": return signed(value, number(value, 3));
    case "snum4": return signed(value, number(value, 4));
    case "pct0": return `${number(value, 0)}%`;
    case "pct1": return `${number(value, 1)}%`;
    case "pct2": return `${number(value, 2)}%`;
    case "spct0": return signed(value, `${number(value, 0)}%`);
    case "spct1": return signed(value, `${number(value, 1)}%`);
    case "spct2": return signed(value, `${number(value, 2)}%`);
    case "usd0": return `$${number(value, 0)}`;
    case "usd2": return `$${number(value, 2)}`;
    case "usdbn": return `${value < 0 ? "-" : ""}$${number(Math.abs(value), 2)}B`;
    case "usdtn": return `$${number(value, 2)}T`;
    case "compact": return compact(value);
    case "usdcompact": return `${value < 0 ? "-" : ""}$${compact(Math.abs(value))}`;
    case "ratio": return number(value, 2);
    default: return String(value);
  }
}

/** Shorter tick labels for chart axes. */
export function axisLabel(value, code) {
  if (value === null || value === undefined) return "";
  const abs = Math.abs(value);
  if (code && code.includes("pct")) return `${compact(value, abs < 10 && abs % 1 !== 0 ? 1 : 0)}%`;
  if (code === "usdbn") return `${value < 0 ? "-" : ""}$${number(abs, abs < 10 && abs % 1 !== 0 ? 1 : 0)}B`;
  if (code === "usdtn") return `$${number(value, 1)}T`;
  if (code === "usdcompact") return abs === 0 ? "$0" : `${value < 0 ? "-" : ""}$${compact(abs, abs >= 1e6 ? 0 : 1)}`;
  if (code && code.startsWith("usd")) return `$${compact(value, 0)}`;
  if (abs >= 1e4) return compact(value, abs >= 1e6 ? 1 : 0);
  if (abs >= 100) return number(value, 0);
  if (abs >= 10) return number(value, abs % 1 === 0 ? 0 : 1);
  if (abs === 0) return "0";
  return number(value, abs >= 1 ? 2 : 3).replace(/0+$/, "").replace(/\.$/, "");
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function monthName(index) {
  return MONTHS[index];
}

export function fmtDate(iso, withYear = false) {
  if (!iso) return MISSING;
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  const text = `${MONTHS[month - 1]} ${day}`;
  return withYear ? `${text}, ${year}` : text;
}

export function fmtAgo(seconds) {
  if (seconds === null || seconds === undefined) return "never";
  const diff = Math.max(0, Date.now() / 1000 - seconds);
  if (diff < 90) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${(diff / 3600).toFixed(diff < 36000 ? 1 : 0)} hours ago`;
  return `${Math.round(diff / 86400)} days ago`;
}

export function daysUntil(iso) {
  const today = new Date();
  const start = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  const [year, month, day] = iso.split("-").map(Number);
  return Math.round((Date.UTC(year, month - 1, day) - start) / 86400000);
}

export function debounce(fn, ms = 200) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/** Read and write small preferences that should survive a page reload. */
export const prefs = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(`ms.${key}`);
      return raw === null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(`ms.${key}`, JSON.stringify(value));
    } catch {
      /* private browsing can refuse storage; preferences then last for the session only */
    }
  },
};
