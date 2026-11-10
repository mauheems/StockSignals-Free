"""The dashboard on any past date: what it would have said then, and what happened next.

Readings use only data that had been published by that date (the same publication delays
as the backtests), so the picture is what you could actually have seen at the time.
"""

from __future__ import annotations

import pandas as pd

from src.analytics import signals as sig
from src.analytics.signals import Ctx

FORWARD = {"1 week": 5, "1 month": 21, "3 months": 63, "6 months": 126}


def _value(series: pd.Series, day: pd.Timestamp) -> float | None:
    known = series.dropna()
    known = known[known.index <= day]
    return float(known.iloc[-1]) if not known.empty else None


def notable_dates(spx: pd.Series, since: pd.Timestamp) -> list[dict]:
    """Market peaks before falls of 8% or more, and the lows that followed: dates worth revisiting."""
    window = spx[spx.index >= since].dropna()
    if window.empty:
        return []
    found = []
    peak_day, peak = window.index[0], window.iloc[0]
    trough_day, trough = peak_day, peak
    for day, price in window.items():
        if price > peak:
            if (trough / peak - 1.0) <= -0.08:
                found.append({"date": peak_day.strftime("%Y-%m-%d"), "label": f"Peak before a {abs(trough / peak - 1) * 100:.0f}% fall"})
                found.append({"date": trough_day.strftime("%Y-%m-%d"), "label": "Low of that fall"})
            peak_day, peak, trough_day, trough = day, price, day, price
        elif price < trough:
            trough_day, trough = day, price
    if (trough / peak - 1.0) <= -0.08:
        found.append({"date": peak_day.strftime("%Y-%m-%d"), "label": f"Peak before a {abs(trough / peak - 1) * 100:.0f}% fall"})
        found.append({"date": trough_day.strftime("%Y-%m-%d"), "label": "Low so far"})
    return found[-12:]


def at(ctx: Ctx, date: str | None, years: int = 3) -> dict:
    calendar = ctx.calendar()
    if not len(calendar):
        return {"available": False}
    history = sig.pillar_history(ctx, lagged=True)
    health = history["health"].dropna() if "health" in history else pd.Series(dtype=float)
    start = calendar[-1] - pd.DateOffset(years=years)
    target = pd.Timestamp(date) if date else calendar[-1] - pd.Timedelta(days=30)
    past = calendar[calendar <= target]
    day = past[-1] if len(past) else calendar[0]
    latest = calendar[-1]

    def pillar_rows(when: pd.Timestamp) -> dict:
        row = history.reindex([when]).iloc[0] if when in history.index else pd.Series(dtype=float)
        return {key: (None if pd.isna(row.get(key)) else float(row.get(key))) for key in [*sig.PILLARS, "health", "crowd"]}

    then, now = pillar_rows(day), pillar_rows(latest)
    pillars = [{"id": key, "label": info["label"], "then": then.get(key), "now": now.get(key), "metric": f"pillar_{key}"} for key, info in sig.PILLARS.items()]
    signals = []
    for definition in sig.SIGNALS:
        if definition.score is None or definition.pillar is None:
            continue
        values = sig.research_series(ctx, definition.id, calendar)
        scores = ctx.score(definition.id)
        score_then = _value(scores.reindex(scores.index.union(calendar)).ffill(limit=definition.ffill).shift(definition.lag), day) if not scores.empty else None
        value_then = _value(values, day)
        if value_then is None:
            continue
        signals.append({
            "id": definition.id, "label": definition.label, "fmt": definition.fmt, "metric": definition.metric,
            "pillar": definition.pillar, "crowd": definition.pillar == sig.CROWD_PILLAR["id"],
            "then": value_then, "score_then": score_then,
            "now": _value(values, latest), "score_now": _value(scores, latest) if not scores.empty else None,
        })
    spx = ctx.close("^GSPC")
    spx_then = _value(spx, day)
    forward = []
    position = spx.index.searchsorted(day, side="right") - 1 if not spx.empty else -1
    for label, bars in FORWARD.items():
        if position < 0 or position + bars >= len(spx):
            forward.append({"label": label, "change": None})
            continue
        window = spx.iloc[position: position + bars + 1]
        forward.append({
            "label": label,
            "change": float(window.iloc[-1] / window.iloc[0] - 1.0) * 100.0,
            "worst": float(window.min() / window.iloc[0] - 1.0) * 100.0,
        })
    chart = health[health.index >= start]
    spx_chart = spx[spx.index >= start].dropna()
    return {
        "available": True,
        "date": day.strftime("%Y-%m-%d"),
        "latest": latest.strftime("%Y-%m-%d"),
        "first": chart.index[0].strftime("%Y-%m-%d") if not chart.empty else None,
        "health": {"then": then.get("health"), "now": now.get("health"), "label_then": sig.health_label(then.get("health"))["label"], "label_now": sig.health_label(now.get("health"))["label"]},
        "crowd": {"then": then.get("crowd"), "now": now.get("crowd")},
        "pillars": pillars,
        "signals": signals,
        "spx_then": spx_then,
        "forward": forward,
        "chart": {"dates": [stamp.strftime("%Y-%m-%d") for stamp in chart.index], "health": [round(float(value), 1) for value in chart.values]},
        "spx_chart": {"dates": [stamp.strftime("%Y-%m-%d") for stamp in spx_chart.index], "values": [round(float(value), 2) for value in spx_chart.values]},
        "notable": notable_dates(spx, start),
    }
