"""The morning brief: what changed and what matters today, in a few plain sentences."""

from __future__ import annotations

import datetime as dt
import time

import pandas as pd

from src.analytics import signals as sig
from src.analytics.signals import Ctx
from src.data.store import Store

SCORE_WORDS = {1: "supportive", 0: "neutral", -1: "a risk"}


def _line(text: str, tone: str = "neutral", link: str | None = None, metric: str | None = None) -> dict:
    return {"text": text, "tone": tone, "link": link, "metric": metric}


def _pct(value: float) -> str:
    return f"{value:+.1f}%"


def build(store: Store, ctx: Ctx, market: dict, futures: list[dict], movers: list[dict], catalysts: list[dict],
          portfolio_totals: dict | None) -> dict:
    state = market["state"]
    title = {"pre": "Before the open", "open": "Today so far", "post": "After the close", "closed": "While the market is closed"}.get(state, "Today")
    lines: list[dict] = []

    # 1. Where the market is heading or standing.
    es = next((row for row in futures if row["symbol"] == "ES=F"), None)
    if state != "open" and es:
        lines.append(_line(f"S&P 500 futures are {_pct(es['change_pct'])} since the last close.", "good" if es["change_pct"] > 0 else "bad", "#/overview", "index_futures"))
    else:
        spx = ctx.close("^GSPC").dropna()
        if len(spx) > 1:
            change = (spx.iloc[-1] / spx.iloc[-2] - 1.0) * 100.0
            when = "today" if state == "open" else f"on {spx.index[-1].strftime('%b %-d')}"
            lines.append(_line(f"The S&P 500 is {_pct(change)} {when}, at {spx.iloc[-1]:,.0f}.", "good" if change > 0 else "bad", "#/breadth", "spx"))

    # 2. Market Health against the previous session.
    history = sig.pillar_history(ctx, lagged=False)
    health = history["health"].dropna() if "health" in history else pd.Series(dtype=float)
    if len(health) > 1:
        now, before = float(health.iloc[-1]), float(health.iloc[-2])
        label = sig.health_label(now)["label"]
        latest = history.dropna(how="all").iloc[-1]
        pillars = {info["label"]: latest.get(key) for key, info in sig.PILLARS.items() if pd.notna(latest.get(key))}
        weakest = min(pillars, key=pillars.get) if pillars else None
        change = now - before
        moved = "unchanged" if abs(change) < 0.5 else f"{'up' if change > 0 else 'down'} {abs(change):.0f} from the session before"
        text = f"Market Health is {now:.0f} ({label}), {moved}."
        if weakest:
            text += f" Weakest part: {weakest}."
        lines.append(_line(text, "good" if now >= 60 else "bad" if now < 45 else "neutral", "#/overview", "market_health"))

    # 3. Signals that changed state in the latest session.
    calendar = ctx.calendar()
    if len(calendar):
        latest_day = calendar[-1].strftime("%Y-%m-%d")
        flips = [flip for flip in sig.recent_flips(ctx, sessions=3, limit=20) if flip["date"] == latest_day and flip["pillar"] != sig.CROWD_PILLAR["id"]]
        if flips:
            parts = [f"{flip['label']} turned {SCORE_WORDS.get(int(flip['to']), 'neutral')}" for flip in flips[:3]]
            more = f", and {len(flips) - 3} more" if len(flips) > 3 else ""
            better = sum(1 for flip in flips if flip["to"] > flip["from"])
            tone = "good" if better > len(flips) / 2 else "bad" if better < len(flips) / 2 else "neutral"
            lines.append(_line("; ".join(parts) + more + ".", tone, "#/overview", flips[0]["metric"]))

    # 4. Today's and tomorrow's events, with what the markets expect.
    soon = [event for event in catalysts if event.get("days") in (0, 1) and event.get("kind") != "earnings"]
    for event in soon[:3]:
        when = "Today" if event["days"] == 0 else "Tomorrow"
        odds = f" ({event['odds']['text']})" if event.get("odds") else ""
        lines.append(_line(f"{when}: {event['label']}{odds}.", "neutral", "#/calendar", event.get("metric")))

    # 5. Your stocks reporting earnings soon.
    reports = store.kv_get("earnings:calendar", [])
    own = set(store.watchlist()) | set(store.position_symbols())
    today = dt.date.today()
    due = [row for row in reports if row["symbol"] in own and 0 <= (dt.date.fromisoformat(row["date"]) - today).days <= 1]
    if due:
        parts = []
        for row in due[:4]:
            day = "today" if row["date"] == today.isoformat() else "tomorrow"
            timing = {"before": " before the open", "after": " after the close"}.get(row.get("timing") or "", "")
            parts.append(f"{row['symbol']} {day}{timing}")
        lines.append(_line("Earnings from your stocks: " + ", ".join(parts) + ".", "neutral", "#/earnings", "next_earnings"))

    # 6. Your portfolio.
    if portfolio_totals and portfolio_totals.get("count") and portfolio_totals.get("day_pct") is not None:
        day = portfolio_totals["day_pct"]
        lines.append(_line(f"Your positions are {_pct(day)} {'today' if state == 'open' else 'over the last session'} (${portfolio_totals['day_change']:+,.0f}).",
                           "good" if day > 0 else "bad", "#/portfolio", "portfolio_value"))

    # 7. Watchlist movers (outside regular hours, or today).
    if movers:
        parts = [f"{row['symbol']} {_pct(row['change_pct'])}" for row in movers[:4]]
        label = "Biggest moves in your watchlist before the open" if state == "pre" else "Biggest moves in your watchlist after the close" if state == "post" else "Biggest moves in your watchlist"
        lines.append(_line(f"{label}: {', '.join(parts)}.", "neutral", "#/watchlist", "extended_hours" if state in ("pre", "post", "closed") else "period_returns"))

    # 8. Alerts that fired in the last day.
    recent = [event for event in store.alert_events(limit=50) if event["at"] >= time.time() - 24 * 3600]
    if recent:
        extra = f" and {len(recent) - 1} more" if len(recent) > 1 else ""
        lines.append(_line(f"Alert: {recent[0]['message']}{extra}.", "neutral", "#/alerts", "alerts"))

    # 9. A big shift in Fed odds over the past week.
    hikes = ctx.series("pm:fed_hike").dropna()
    if len(hikes) > 5:
        week = hikes[hikes.index <= hikes.index[-1] - pd.Timedelta(days=7)]
        if not week.empty and abs(hikes.iloc[-1] - week.iloc[-1]) >= 10:
            change = hikes.iloc[-1] - week.iloc[-1]
            lines.append(_line(f"Odds of a Fed hike at the next meeting moved {'up' if change > 0 else 'down'} {abs(change):.0f} points this week, to {hikes.iloc[-1]:.0f}%.",
                               "bad" if change > 0 else "good", "#/prediction", "pm_fed_hike"))
    return {"title": title, "lines": lines, "state": state}
