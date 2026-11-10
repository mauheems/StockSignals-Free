"""'Before you buy': the stock's situation in a few plain checks, green, amber or red.

These are rules of thumb that put the numbers on the stock page in context. They are not a
recommendation: a red check can be the reason a trade is attractive, if you know it.
"""

from __future__ import annotations

import datetime as dt
import math


def _ok(value) -> bool:
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def _check(check_id: str, label: str, state: str, text: str, metric: str) -> dict:
    return {"id": check_id, "label": label, "state": state, "text": text, "metric": metric}


def implied_move(option: dict | None, until: str | None) -> tuple[float | None, str | None]:
    """The options market's expected move up to the first expiry on or after a date."""
    if not option or not until:
        return None, None
    choices = []
    for key in ("week", "next", "month"):
        move, expiry = option.get(f"expected_move_{key}"), option.get(f"expected_move_{key}_expiry")
        if _ok(move) and expiry and expiry >= until:
            choices.append((expiry, move))
    if not choices:
        return None, None
    expiry, move = min(choices)
    return float(move), expiry


def build(snapshot: dict, *, next_earnings: str | None, option: dict | None, short_pct: float | None,
          health: float | None, beat_odds: dict | None, today: dt.date) -> list[dict]:
    checks = []

    trend = snapshot.get("trend") or {}
    tone = trend.get("tone")
    state = "good" if tone == "good" else "bad" if tone in ("serious", "critical") else "warn"
    checks.append(_check("trend", "Trend", state, f"{trend.get('label', 'Unknown')}: price is {snapshot.get('vs_sma50', 0):+.1f}% from its 50-day and {snapshot.get('vs_sma200', 0):+.1f}% from its 200-day average."
                         if _ok(snapshot.get("vs_sma50")) and _ok(snapshot.get("vs_sma200")) else "Not enough history for the 50- and 200-day averages.", "trend_state"))

    rsi = snapshot.get("rsi")
    if _ok(rsi):
        if rsi >= 70:
            checks.append(_check("rsi", "Momentum", "warn", f"RSI {rsi:.0f}: stretched after a strong run. Buying here is chasing; pullbacks are common.", "rsi"))
        elif rsi <= 30:
            checks.append(_check("rsi", "Momentum", "warn", f"RSI {rsi:.0f}: oversold. Can mean a bounce is near, or that selling is still strong. Wait for the price to turn up?", "rsi"))
        else:
            checks.append(_check("rsi", "Momentum", "good", f"RSI {rsi:.0f}: neither stretched nor washed out.", "rsi"))

    rs = snapshot.get("rs_rating")
    if _ok(rs):
        state = "good" if rs >= 70 else "bad" if rs <= 30 else "warn"
        checks.append(_check("rs", "Strength vs the market", state, f"RS rating {rs:.0f} of 99: {'outperforming most stocks' if rs >= 70 else 'lagging most stocks' if rs <= 30 else 'middle of the pack'} over the past year.", "rs_rating"))

    rvol, change = snapshot.get("rvol"), snapshot.get("change_pct")
    if _ok(rvol) and _ok(change) and rvol >= 1.5:
        up = change >= 0
        checks.append(_check("volume", "Volume", "good" if up else "bad",
                             f"{rvol:.1f}x normal volume on a {change:+.1f}% day: heavy {'buying' if up else 'selling'} interest." , "rvol"))

    if next_earnings:
        days = (dt.date.fromisoformat(next_earnings) - today).days
        if 0 <= days <= 14:
            move, expiry = implied_move(option, next_earnings)
            text = f"Earnings in {days} day{'s' if days != 1 else ''} ({next_earnings})."
            if move is not None:
                text += f" Options price a move of about ±{move:.1f}% by {expiry}."
            if beat_odds:
                text += f" Polymarket: {round(beat_odds['prob'] * 100)}% chance of beating the estimate."
            checks.append(_check("earnings", "Earnings", "warn", text + " A position held through the report takes that gamble.", "next_earnings"))
        elif days > 14:
            checks.append(_check("earnings", "Earnings", "good", f"Next report in {days} days ({next_earnings}): no earnings gap risk soon.", "next_earnings"))

    if _ok(health):
        state = "good" if health >= 60 else "bad" if health < 45 else "warn"
        checks.append(_check("market", "Market backdrop", state, f"Market Health {health:.0f}: {'supportive' if health >= 60 else 'risk-off' if health < 30 else 'cautious' if health < 45 else 'mixed'}. Most stocks follow the market.", "market_health"))

    if option and _ok(option.get("net_gex")):
        positive = option["net_gex"] > 0
        checks.append(_check("gamma", "Options positioning", "good" if positive else "warn",
                             "Dealer gamma is positive: their hedging tends to damp moves." if positive else "Dealer gamma is negative: their hedging can make moves bigger in both directions.", "gamma_regime"))

    if _ok(short_pct):
        share = short_pct * 100
        if share >= 15:
            checks.append(_check("short", "Short interest", "warn", f"{share:.0f}% of the float is sold short: crowded bets against it. Squeezes and sharp drops both happen.", "short_interest"))

    atr = snapshot.get("atr")
    last = snapshot.get("last")
    if _ok(atr) and _ok(last) and last:
        checks.append(_check("risk", "Position size", "info",
                             f"Typical daily range (ATR) is ${atr:.2f}, {atr / last * 100:.1f}% of the price. A stop two ATRs away sits near ${last - 2 * atr:,.2f}: size the position so that loss is one you accept.", "atr"))
    return checks


def summary(checks: list[dict]) -> dict:
    counts = {state: sum(1 for check in checks if check["state"] == state) for state in ("good", "warn", "bad")}
    return counts
