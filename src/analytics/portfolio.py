"""Your positions and trade journal: value, profit, exposure, and what the signals said."""

from __future__ import annotations

import datetime as dt
import math

import pandas as pd

from src.analytics import signals as sig
from src.analytics import stocks as stock_analytics
from src.analytics.signals import Ctx
from src.data.store import Store

STRESS_MOVE = -5.0  # "what if the S&P 500 falls 5%"


def _clean(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _sector(store: Store, symbol: str) -> str:
    info = (store.kv_get(f"fund:{symbol}") or {}).get("info") or {}
    kind = str(info.get("quoteType") or "").upper()
    if kind in ("ETF", "MUTUALFUND"):
        return "Funds (ETFs)"
    return info.get("sector") or "Other"


def valuation(store: Store, ctx: Ctx) -> dict:
    """Every position valued at the latest price, plus totals, sector weights and a stress test."""
    benchmark = ctx.adj("SPY")
    rows = []
    for position in store.positions():
        symbol = position["symbol"]
        frame = store.get_prices(symbol)
        snapshot = stock_analytics.build_snapshot(frame, benchmark, store) if not frame.empty else {}
        last = _clean(snapshot.get("last"))
        previous = _clean(snapshot.get("prev_close"))
        shares, cost = position["shares"], position["cost"]
        value = shares * last if last is not None else None
        basis = shares * cost
        details = store.kv_get(f"fund:{symbol}") or {}
        beta = _clean(snapshot.get("beta"))
        rows.append({
            **position,
            "name": (details.get("info") or {}).get("shortName") or symbol,
            "last": last,
            "date": snapshot.get("date"),
            "value": value,
            "basis": basis,
            "pnl": value - basis if value is not None else None,
            "pnl_pct": (last / cost - 1.0) * 100.0 if last is not None and cost else None,
            "day_change": shares * (last - previous) if last is not None and previous is not None else None,
            "day_pct": _clean(snapshot.get("change_pct")),
            "beta": beta,
            "sector": _sector(store, symbol),
            "trend": snapshot.get("trend"),
            "next_earnings": details.get("next_earnings"),
            "stop_distance": (position["stop"] / last - 1.0) * 100.0 if position.get("stop") and last else None,
            "target_distance": (position["target"] / last - 1.0) * 100.0 if position.get("target") and last else None,
        })
    total = sum(row["value"] for row in rows if row["value"] is not None)
    basis = sum(row["basis"] for row in rows)
    day = sum(row["day_change"] for row in rows if row["day_change"] is not None)
    for row in rows:
        row["weight"] = row["value"] / total * 100.0 if total and row["value"] is not None else None
    # Beta is how much a stock has tended to move per 1% move in the S&P 500 (1 when unknown).
    weighted_beta = sum((row["beta"] if row["beta"] is not None else 1.0) * row["value"] for row in rows if row["value"]) / total if total else None
    sectors: dict[str, float] = {}
    for row in rows:
        if row["value"]:
            sectors[row["sector"]] = sectors.get(row["sector"], 0.0) + row["value"] / total * 100.0
    largest = max(rows, key=lambda row: row["weight"] or 0) if rows else None
    return {
        "rows": rows,
        "totals": {
            "value": total,
            "basis": basis,
            "pnl": total - basis if rows else None,
            "pnl_pct": (total / basis - 1.0) * 100.0 if basis else None,
            "day_change": day,
            "day_pct": day / (total - day) * 100.0 if total and total != day else None,
            "beta": weighted_beta,
            "stress_move": STRESS_MOVE,
            "stress_change": total * weighted_beta * STRESS_MOVE / 100.0 if total and weighted_beta is not None else None,
            "largest": {"symbol": largest["symbol"], "weight": largest["weight"]} if largest and largest["weight"] else None,
            "count": len(rows),
        },
        "sectors": sorted(({"sector": key, "weight": value} for key, value in sectors.items()), key=lambda item: -item["weight"]),
    }


def apply_trade(store: Store, symbol: str, action: str, shares: float, price: float) -> str:
    """Update positions after a journal trade. Returns what changed, in words."""
    held = [row for row in store.positions() if row["symbol"] == symbol]
    if action == "buy":
        if held:
            position = held[0]
            total = position["shares"] + shares
            cost = (position["shares"] * position["cost"] + shares * price) / total
            store.position_update(position["id"], shares=total, cost=cost)
            return f"{symbol} position is now {total:g} shares at an average ${cost:,.2f}."
        store.position_add(symbol, shares, price, opened=dt.date.today().isoformat())
        return f"Added a {symbol} position of {shares:g} shares."
    if action == "sell":
        owned = sum(row["shares"] for row in held)
        if shares > owned + 1e-9:
            raise ValueError(f"You have {owned:g} {symbol} shares in your positions, so {shares:g} can't be sold from them.")
        remaining = shares
        for position in held:
            used = min(position["shares"], remaining)
            left = position["shares"] - used
            if left <= 1e-9:
                store.position_delete(position["id"])
            else:
                store.position_update(position["id"], shares=left)
            remaining -= used
            if remaining <= 1e-9:
                break
        return f"Sold {shares:g} {symbol} shares from your positions."
    return "Positions unchanged."


def signal_snapshot(store: Store, ctx: Ctx, symbol: str) -> dict:
    """What the dashboard said about the market and the stock at this moment, for the journal."""
    history = sig.pillar_history(ctx, lagged=False).dropna(how="all")
    market: dict = {}
    if not history.empty:
        latest = history.iloc[-1]
        market = {
            "health": _clean(latest.get("health")),
            "health_label": sig.health_label(latest.get("health"))["label"],
            "pillars": {sig.PILLARS[key]["label"]: _clean(latest.get(key)) for key in sig.PILLARS},
            "crowd": _clean(latest.get("crowd")),
        }
    for signal_id in ("vix", "spx_vs_200"):
        row = sig.signal_row(ctx, signal_id, spark_points=2)
        if row:
            market[signal_id] = row["value"]
    stock: dict = {}
    frame = store.get_prices(symbol)
    if not frame.empty:
        snapshot = stock_analytics.build_snapshot(frame, ctx.adj("SPY"), store)
        stock = {key: _clean(snapshot.get(key)) for key in ("last", "rsi", "vs_sma50", "vs_sma200", "rvol", "rs_rating", "atr_pct", "off_high", "ret_1m")}
        stock["trend"] = (snapshot.get("trend") or {}).get("label")
    details = store.kv_get(f"fund:{symbol}") or {}
    option = store.kv_get(f"opt:summary:{symbol}") or {}
    stock["iv30"] = _clean(option.get("iv30"))
    stock["next_earnings"] = details.get("next_earnings")
    odds = (store.kv_get("pm:earnings") or {}).get(symbol)
    stock["beat_odds"] = odds["prob"] if odds else None
    return {"market": market, "stock": stock, "taken_at": dt.datetime.now().isoformat(timespec="minutes")}


def _price_on(frame: pd.DataFrame, day: str) -> float | None:
    closes = frame["Close"].dropna()
    closes = closes[closes.index <= pd.Timestamp(day)]
    return float(closes.iloc[-1]) if not closes.empty else None


def journal_view(store: Store, ctx: Ctx) -> dict:
    """Journal entries with what happened since, and a short review."""
    spy = store.get_prices("SPY")
    spy_now = float(spy["Close"].dropna().iloc[-1]) if not spy.empty else None
    rows = []
    for entry in store.journal():
        frame = store.get_prices(entry["symbol"])
        now = float(frame["Close"].dropna().iloc[-1]) if not frame.empty else None
        then = entry["price"] or (_price_on(frame, entry["date"]) if not frame.empty else None)
        move = (now / then - 1.0) * 100.0 if now and then else None
        spy_then = _price_on(spy, entry["date"]) if not spy.empty else None
        spy_move = (spy_now / spy_then - 1.0) * 100.0 if spy_now and spy_then else None
        if entry["action"] == "buy":
            result = move
        elif entry["action"] == "sell":
            result = -move if move is not None else None  # a sale paid off if the stock fell afterwards
        else:
            result = None
        rows.append({
            **entry,
            "price_now": now,
            "move": move,
            "spy_move": spy_move,
            "result": result,
            "vs_market": result - spy_move if result is not None and spy_move is not None and entry["action"] == "buy" else None,
            "days": (dt.date.today() - dt.date.fromisoformat(entry["date"])).days,
        })
    decided = [row for row in rows if row["result"] is not None and row["days"] >= 5]
    buys = [row for row in decided if row["action"] == "buy"]
    review = {
        "entries": len(rows),
        "decisions": len(decided),
        "worked": sum(1 for row in decided if row["result"] > 0),
        "avg_buy": sum(row["result"] for row in buys) / len(buys) if buys else None,
        "avg_buy_vs_market": (sum(row["vs_market"] for row in buys if row["vs_market"] is not None) / len(buys)) if buys else None,
    }
    return {"rows": rows, "review": review}
