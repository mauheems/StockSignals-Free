"""Alerts: watch a market signal or a stock, and record a notification when a level is hit.

Alerts are checked every time new data arrives. Notifications are stored in the database
and shown in the dashboard. Sending them elsewhere (email, text) would only need to read
the same notification rows.
"""

from __future__ import annotations

import math
import threading
from typing import Callable

import pandas as pd

from src.analytics.signals import REGISTRY, SELF_SIGNALS, Ctx, self_signal
from src.data.catalog import normalize_symbol
from src.data.store import Store

OPERATORS = {
    "above": "is above",
    "below": "is below",
    "crosses_above": "crosses above",
    "crosses_below": "crosses below",
}
PAST_TENSE = {
    "above": "is above",
    "below": "is below",
    "crosses_above": "crossed above",
    "crosses_below": "crossed below",
}

# How each stock measure is displayed. Keys are the shared technical readings.
STOCK_FORMATS = {
    "close": "usd2",
    "ret_1": "spct2",
    "close_vs_sma20": "spct1",
    "close_vs_sma50": "spct1",
    "close_vs_sma200": "spct1",
    "sma50_vs_sma200": "spct1",
    "rsi14": "num0",
    "macd_hist": "snum2",
    "adx14": "num0",
    "rvol20": "num2",
    "cmf20": "snum2",
    "atr_pct": "pct1",
    "hv20": "pct1",
    "ret_21": "spct1",
    "ret_63": "spct1",
    "ret_126": "spct1",
    "drawdown_52w": "spct1",
}
MAX_ALERTS = 200

_check_lock = threading.Lock()


def format_value(value: float | None, code: str) -> str:
    """Plain-text version of the front end's number formats, for notification messages."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    signed = code.startswith("s")
    base = code[1:] if signed else code
    sign = "+" if signed and value > 0 else ""
    if base.startswith("pct"):
        return f"{sign}{value:,.{int(base[3:] or 1)}f}%"
    if base.startswith("num"):
        return f"{sign}{value:,.{int(base[3:] or 2)}f}"
    if base == "usdbn":
        return f"{'-' if value < 0 else ''}${abs(value):,.2f}B"
    if base == "usdtn":
        return f"${value:,.2f}T"
    if base.startswith("usd"):
        return f"{'-' if value < 0 else ''}${abs(value):,.{int(base[3:] or 2)}f}"
    return f"{value:,.2f}"


def stock_measure_label(metric: str) -> str:
    label = SELF_SIGNALS[metric]["label"].replace(" (%)", "")
    # Lower-case the first word to read as a sentence, but keep acronyms such as RSI and ATR.
    if len(label) > 1 and not label[1].isupper():
        label = label[0].lower() + label[1:]
    return label


def parse_rule(raw: dict, store: Store, ensure: Callable[[Store, str], object]) -> dict:
    """Validate an alert request. Raises ValueError with a readable message."""
    kind = str(raw.get("kind", ""))
    op = str(raw.get("op", ""))
    if op not in OPERATORS:
        raise ValueError("Choose when the alert should fire: is above, is below, crosses above or crosses below.")
    try:
        value = float(raw.get("value"))
    except (TypeError, ValueError) as exc:
        raise ValueError("The level must be a number.") from exc
    if not math.isfinite(value):
        raise ValueError("The level must be a number.")
    note = str(raw.get("note") or "").strip()[:200]
    if kind == "signal":
        signal = str(raw.get("signal", ""))
        if signal not in REGISTRY:
            raise ValueError(f"Unknown signal '{signal}'.")
        return {"kind": "signal", "signal": signal, "op": op, "value": value, "note": note}
    if kind == "stock":
        symbol = normalize_symbol(str(raw.get("symbol", "")))
        metric = str(raw.get("metric", ""))
        if metric not in STOCK_FORMATS:
            raise ValueError(f"Unknown stock measure '{metric}'.")
        # Downloads the history if needed, and raises SymbolNotFound for unknown tickers.
        ensure(store, symbol)
        return {"kind": "stock", "symbol": symbol, "metric": metric, "op": op, "value": value, "note": note}
    raise ValueError("An alert watches either a stock or a market signal.")


def describe(rule: dict) -> dict:
    """Human label, number format and the page to open for an alert's subject."""
    if rule["kind"] == "signal":
        definition = REGISTRY[rule["signal"]]
        return {"subject": definition.label, "fmt": definition.fmt, "link": f"#/{definition.page}", "metric": definition.metric}
    metric = rule["metric"]
    return {
        "subject": f"{rule['symbol']} {stock_measure_label(metric)}",
        "fmt": STOCK_FORMATS[metric],
        "link": f"#/stock/{rule['symbol']}",
        "metric": SELF_SIGNALS[metric]["metric"],
    }


def _series(rule: dict, store: Store, ctx: Ctx) -> pd.Series:
    if rule["kind"] == "signal":
        return ctx.signal(rule["signal"]).dropna()
    frame = store.get_prices(rule["symbol"]).dropna(subset=["Close"])
    if frame.empty:
        return pd.Series(dtype=float)
    return self_signal(rule["metric"], frame).dropna()


def check(rule: dict, store: Store, ctx: Ctx) -> dict:
    """Where the watched value stands now, against the alert's level."""
    values = _series(rule, store, ctx)
    if values.empty:
        return {"available": False}
    latest = float(values.iloc[-1])
    previous = float(values.iloc[-2]) if len(values) > 1 else None
    level = rule["value"]
    upward = rule["op"] in ("above", "crosses_above")
    beyond = latest > level if upward else latest < level
    crossed = False
    if previous is not None and rule["op"].startswith("crosses"):
        crossed = previous <= level < latest if upward else previous >= level > latest
    return {
        "available": True,
        "value": latest,
        "previous": previous,
        "date": values.index[-1].strftime("%Y-%m-%d"),
        "active": beyond,
        "crossed": crossed,
    }


def message(rule: dict, status: dict) -> str:
    info = describe(rule)
    level = format_value(rule["value"], info["fmt"])
    now = format_value(status["value"], info["fmt"])
    return f"{info['subject']} {PAST_TENSE[rule['op']]} {level} (now {now})"


def evaluate_one(store: Store, ctx: Ctx, alert: dict) -> dict | None:
    """Check one alert and record a notification if it fired. Returns the notification."""
    rule = alert["rule"]
    status = check(rule, store, ctx)
    if not status["available"]:
        store.alert_update(alert["id"], last_checked_only=True)
        return None
    fired = False
    event_key = alert["last_event_key"]
    if rule["op"] in ("above", "below"):
        # Fires when the condition becomes true, then waits until it has been false again.
        fired = status["active"] and alert["last_state"] != 1
    elif status["crossed"] and alert["last_event_key"] != status["date"]:
        # A crossing on a given day is reported once, even while that day's bar is still moving.
        fired = True
        event_key = status["date"]
    store.alert_update(
        alert["id"],
        last_state=int(status["active"]),
        last_value=status["value"],
        last_date=status["date"],
        last_event_key=event_key,
        fired=fired,
    )
    if not fired:
        return None
    event = {"alert_id": alert["id"], "date": status["date"], "message": message(rule, status), "value": status["value"]}
    store.alert_event_add(**event)
    return event


def evaluate_all(store: Store, ctx: Ctx) -> int:
    """Check every enabled alert. Returns how many notifications were created."""
    with _check_lock:
        fired = 0
        for alert in store.alerts():
            if alert["enabled"] and evaluate_one(store, ctx, alert):
                fired += 1
        return fired


def evaluate_new(store: Store, ctx: Ctx, alert_id: int) -> None:
    with _check_lock:
        alert = store.alert(alert_id)
        if alert:
            evaluate_one(store, ctx, alert)


def alert_view(alert: dict, store: Store, ctx: Ctx) -> dict:
    rule = alert["rule"]
    info = describe(rule)
    status = check(rule, store, ctx)
    return {
        "id": alert["id"],
        "rule": rule,
        "label": f"{info['subject']} {OPERATORS[rule['op']]} {format_value(rule['value'], info['fmt'])}",
        "subject": info["subject"],
        "condition": OPERATORS[rule["op"]],
        "fmt": info["fmt"],
        "link": info["link"],
        "metric": info["metric"],
        "enabled": bool(alert["enabled"]),
        "created_at": alert["created_at"],
        "last_triggered": alert["last_triggered"],
        "status": status,
    }
