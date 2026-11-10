"""Earnings season and the economic calendar: what is coming, what is expected, what happened."""

from __future__ import annotations

import datetime as dt
import re

import pandas as pd

from src.analytics.checklist import implied_move
from src.data.calendars import CALENDAR_KEY
from src.data.store import Store

# --------------------------------------------------------------------------- earnings


def typical_move(prices: pd.DataFrame, history: list[dict], count: int = 8) -> dict | None:
    """Average size of the stock's move over past earnings reports.

    Measured from the close before the report day to the close after it, which covers both
    reports before the open and after the close.
    """
    closes = prices["Close"].dropna() if not prices.empty else pd.Series(dtype=float)
    if closes.empty:
        return None
    moves = []
    for item in history[:count]:
        day = pd.Timestamp(item["date"])
        before = closes[closes.index < day]
        after = closes[closes.index > day]
        if before.empty or after.empty:
            continue
        moves.append(abs(float(after.iloc[0] / before.iloc[-1] - 1.0)) * 100.0)
    if not moves:
        return None
    return {"average": sum(moves) / len(moves), "largest": max(moves), "reports": len(moves)}


def earnings_season(store: Store, members: set[str]) -> dict:
    watchlist = set(store.watchlist())
    holdings = set(store.position_symbols())
    odds = store.kv_get("pm:earnings") or {}
    today = dt.date.today().isoformat()
    rows = {row["symbol"]: dict(row) for row in store.kv_get(CALENDAR_KEY, []) if row["date"] >= today}
    # Your own stocks always appear, also when they are below the calendar's size cut-off.
    for symbol in watchlist | holdings:
        details = store.kv_get(f"fund:{symbol}") or {}
        if symbol not in rows and details.get("next_earnings") and details["next_earnings"] >= today:
            info = details.get("info") or {}
            rows[symbol] = {"symbol": symbol, "company": info.get("shortName") or symbol, "date": details["next_earnings"],
                            "timing": None, "eps_estimate": None, "market_cap": info.get("marketCap"), "event": ""}
    result = []
    for symbol, row in rows.items():
        details = store.kv_get(f"fund:{symbol}") or {}
        option = store.kv_get(f"opt:summary:{symbol}")
        move, expiry = implied_move(option, row["date"])
        typical = typical_move(store.get_prices(symbol), details.get("earnings_history") or []) if details else None
        beat = odds.get(symbol)
        result.append({
            **row,
            "in_watchlist": symbol in watchlist,
            "in_portfolio": symbol in holdings,
            "in_sp500": symbol in members,
            "implied_move": move,
            "implied_expiry": expiry,
            "typical_move": typical["average"] if typical else None,
            "largest_move": typical["largest"] if typical else None,
            "past_reports": typical["reports"] if typical else 0,
            "beat_odds": beat["prob"] if beat else None,
            "beat_url": beat["url"] if beat else None,
        })
    result.sort(key=lambda item: (item["date"], -(item["market_cap"] or 0)))
    return {"rows": result, "updated": store.kv_time(CALENDAR_KEY)}


# ------------------------------------------------------------------- economic calendar
# Usual release times (ET) by event, and the Kalshi numbers that belong to each.
RELEASES = {
    "CPI inflation report": ("08:30", ["cpi_mom", "core_cpi_mom", "cpi"]),
    "Jobs report (nonfarm payrolls)": ("08:30", ["payrolls", "unemployment"]),
    "GDP report": ("08:30", ["gdp"]),
    "PPI producer prices": ("08:30", []),
    "Retail sales": ("08:30", []),
    "PCE inflation and spending": ("08:30", []),
    "FOMC rate decision": ("14:00", []),
    "FOMC rate decision and projections": ("14:00", []),
}
LABELS = {
    "cpi_mom": ("CPI, monthly change", "spct1"),
    "core_cpi_mom": ("Core CPI, monthly change", "spct1"),
    "cpi": ("CPI inflation, year over year", "pct1"),
    "unemployment": ("Unemployment rate", "pct1"),
    "payrolls": ("Payrolls change", "compact"),
    "gdp": ("Real GDP growth, annualized", "pct1"),
}


def period_start(period: str | None) -> pd.Timestamp | None:
    """'October 2026' or 'Q4 2026' -> the first day of that period."""
    if not period:
        return None
    quarter = re.match(r"Q([1-4]) (\d{4})$", period)
    if quarter:
        return pd.Timestamp(int(quarter.group(2)), 3 * int(quarter.group(1)) - 2, 1)
    try:
        return pd.Timestamp(dt.datetime.strptime(period, "%B %Y"))
    except ValueError:
        return None


def actuals(store: Store) -> dict[str, pd.Series]:
    """The published numbers, on the same basis as the Kalshi markets, by period start."""
    cpi = store.get_series("fred:CPIAUCSL")
    core = store.get_series("fred:CPILFESL")
    payrolls = store.get_series("fred:PAYEMS")
    gdp = store.get_series("fred:A191RL1Q225SBEA")
    return {
        # Kalshi settles CPI changes on the figures rounded to one decimal.
        "cpi_mom": (cpi.pct_change(fill_method=None) * 100.0).round(1),
        "core_cpi_mom": (core.pct_change(fill_method=None) * 100.0).round(1),
        "cpi": (cpi.pct_change(12, fill_method=None) * 100.0).round(1),
        "unemployment": store.get_series("fred:UNRATE"),
        "payrolls": payrolls.diff() * 1000.0,
        "gdp": gdp,
    }


def economic_calendar(store: Store, days: int = 75) -> dict:
    board = store.kv_get("pm:board") or {}
    log = store.kv_get("pm:expectations") or {}
    published = actuals(store)
    today = dt.date.today()
    end = (today + dt.timedelta(days=days)).isoformat()
    upcoming = []
    for event in store.kv_get("events:macro", []):
        if not today.isoformat() <= event["date"] <= end:
            continue
        time_et, keys = RELEASES.get(event["label"], (None, []))
        expectations = []
        for key in keys:
            reading = board.get(key)
            if not reading or abs((dt.date.fromisoformat(reading["release"]) - dt.date.fromisoformat(event["date"])).days) > 3:
                continue
            label, fmt = LABELS[key]
            values = published[key].dropna()
            expectations.append({
                "key": key, "label": label, "fmt": fmt, "period": reading.get("period"),
                "median": reading.get("median"), "low": reading.get("low"), "high": reading.get("high"),
                "volume": reading.get("volume"),
                "previous": float(values.iloc[-1]) if not values.empty else None,
                "previous_period": values.index[-1].strftime("%B %Y") if not values.empty else None,
            })
        fed = board.get("fed") if event["label"].startswith("FOMC") else None
        if fed and abs((dt.date.fromisoformat(fed["date"]) - dt.date.fromisoformat(event["date"])).days) > 2:
            fed = None
        upcoming.append({**event, "time": time_et, "expectations": expectations,
                         "fed": {"likely": fed["likely"], "prob": fed["likely_prob"], "hike": fed["hike"], "cut": fed["cut"], "hold": fed["hold"]} if fed else None})
    # Past releases: what the market expected just before, against what came out.
    past = []
    for key, periods in log.items():
        if key not in LABELS:
            continue
        label, fmt = LABELS[key]
        values = published[key].dropna()
        for period, expected in periods.items():
            start = period_start(period)
            if start is None or expected["release"] > today.isoformat():
                continue
            actual = float(values[start]) if start in values.index else None
            past.append({
                "key": key, "label": label, "fmt": fmt, "period": period, "release": expected["release"],
                "expected": expected["median"], "actual": actual,
                "surprise": actual - expected["median"] if actual is not None and expected["median"] is not None else None,
            })
    past.sort(key=lambda item: item["release"], reverse=True)
    return {"upcoming": upcoming, "past": past[:40]}


def log_expectations(store: Store, board: dict) -> None:
    """Keep each number's last expectation before its release, to compare with the result later."""
    log = store.kv_get("pm:expectations") or {}
    for key in LABELS:
        reading = board.get(key)
        if not reading or not reading.get("period") or reading.get("median") is None:
            continue
        periods = log.setdefault(key, {})
        periods[reading["period"]] = {"median": reading["median"], "release": reading["release"]}
        # Keep the latest two years.
        for old in sorted(periods, key=lambda name: periods[name]["release"])[:-24]:
            periods.pop(old)
    store.kv_set("pm:expectations", log)
