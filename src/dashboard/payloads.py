"""Builds the data behind each dashboard page from stored market data."""

from __future__ import annotations

import datetime as dt
import math
from typing import Iterable

import numpy as np
import pandas as pd

from src.analytics import options as option_analytics
from src.analytics import rotation as rotation_analytics
from src.analytics import signals as sig
from src.analytics import stocks as stock_analytics
from src.analytics import technicals as ta
from src.analytics.signals import Ctx
from src.analytics.studies import Condition, HORIZONS, condition_coverage, condition_mask, forward_return_study
from src.backtest.engine import BacktestEngine
from src.data import cboe, fundamentals
from src.data.catalog import (
    CORE_NAMES,
    INDICES,
    OPTION_CORE,
    RISK_RATIOS,
    ROTATION_UNIVERSES,
    SECTOR_TO_GICS,
    SECTORS,
    normalize_symbol,
)
from src.data.extended import CLOSE as REGULAR_CLOSE, OPEN as REGULAR_OPEN
from src.data.market_calendar import last_completed_session, market_state, now_et, upcoming_expirations
from src.data.market_data import SymbolNotFound, ensure_prices, intraday_prices
from src.data.refresh import save_option_snapshot
from src.data.store import Store
from src.data.universe import load_sp500
from src.strategies.rules import STRATEGY_TEMPLATES, build_strategy

RANGES = {"1m": 31, "3m": 92, "6m": 183, "1y": 366, "2y": 731, "3y": 1096, "5y": 1827, "10y": 3653, "max": None}

PRICE_ALIASES = {"SPX": "^GSPC", "NDX": "^NDX", "RUT": "^RUT", "VIX": "^VIX", "DJI": "^DJI", "DJX": "^DJI", "XSP": "^GSPC"}


def price_symbol(symbol: str) -> str:
    """Option underlyings and common index names mapped to their Yahoo price symbol."""
    return PRICE_ALIASES.get(symbol, symbol)


def sanitize(value):
    """Make any nested result JSON-safe: NaN and infinity become null."""
    if isinstance(value, dict):
        return {str(key): sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, np.ndarray):
        return [sanitize(item) for item in value.tolist()]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return value.isoformat()
    return value


def _start_of_range(range_key: str, last: pd.Timestamp | None = None) -> pd.Timestamp | None:
    days = RANGES.get(range_key, 366)
    if days is None:
        return None
    return (last if last is not None else pd.Timestamp(now_et().date())) - pd.Timedelta(days=days)


def _round(values: Iterable[float], digits: int = 4) -> list:
    return [None if value is None or (isinstance(value, float) and math.isnan(value)) else round(float(value), digits) for value in values]


def series_payload(series: pd.Series, range_key: str = "max", digits: int = 4) -> dict:
    clean = series.dropna()
    if clean.empty:
        return {"dates": [], "values": []}
    start = _start_of_range(range_key)
    if start is not None:
        clean = clean[clean.index >= start]
    return {"dates": [stamp.strftime("%Y-%m-%d") for stamp in clean.index], "values": _round(clean.values, digits)}


def table_payload(columns: dict[str, pd.Series], range_key: str = "max", digits: int = 4, calendar: pd.DatetimeIndex | None = None) -> dict:
    """Several series on one shared date axis, ready for a multi-line chart."""
    present = {name: series for name, series in columns.items() if series is not None and not series.dropna().empty}
    if not present:
        return {"dates": [], "series": {name: [] for name in columns}}
    frame = pd.concat(present, axis=1).sort_index()
    if calendar is not None:
        frame = frame.reindex(frame.index.union(calendar)).ffill(limit=5).reindex(calendar)
    start = _start_of_range(range_key)
    if start is not None:
        frame = frame[frame.index >= start]
    frame = frame.dropna(how="all")
    return {
        "dates": [stamp.strftime("%Y-%m-%d") for stamp in frame.index],
        "series": {
            name: _round(frame[name].values, digits) if name in frame else []
            for name in columns
        },
    }


def _rows(ctx: Ctx, ids: Iterable[str]) -> dict[str, dict | None]:
    return {signal_id: sig.signal_row(ctx, signal_id) for signal_id in ids}


def _quote_tile(ctx: Ctx, symbol: str, label: str, metric: str, fmt: str, higher_is: str | None, *, change: str = "pct") -> dict | None:
    close = ctx.close(symbol)
    if close.empty:
        return None
    last = float(close.iloc[-1])
    previous = float(close.iloc[-2]) if len(close) > 1 else None
    sma50, sma200 = ta.sma(close, 50).iloc[-1], ta.sma(close, 200).iloc[-1]
    return {
        "symbol": symbol,
        "label": label,
        "metric": metric,
        "fmt": fmt,
        "higher_is": higher_is,
        "value": last,
        "date": close.index[-1].strftime("%Y-%m-%d"),
        "change": last - previous if previous is not None else None,
        "change_pct": (last / previous - 1.0) * 100.0 if previous else None,
        "change_kind": change,
        "above_50": None if pd.isna(sma50) else bool(last > sma50),
        "above_200": None if pd.isna(sma200) else bool(last > sma200),
        "spark": _round(close.tail(60).values, 3),
    }


def _catalysts(store: Store, limit: int = 12) -> list[dict]:
    today = now_et().date()
    events = list(store.kv_get("events:macro", []))
    events.extend(upcoming_expirations(today))
    for symbol in store.watchlist():
        details = store.kv_get(f"fund:{symbol}")
        if details and details.get("next_earnings"):
            events.append({"date": details["next_earnings"], "label": f"{symbol} earnings", "kind": "earnings", "metric": "next_earnings", "symbol": symbol})
    upcoming = [event for event in events if event["date"] >= today.isoformat()]
    upcoming.sort(key=lambda item: item["date"])
    for event in upcoming:
        event["days"] = (dt.date.fromisoformat(event["date"]) - today).days
    return upcoming[:limit]


OVERVIEW_TILES = [
    ("^GSPC", "S&P 500", "spx", "num0", "good", "pct"),
    ("^NDX", "Nasdaq 100", "ndx", "num0", "good", "pct"),
    ("^RUT", "Russell 2000", "rut", "num0", "good", "pct"),
    ("^VIX", "VIX", "vix", "num1", "bad", "pct"),
    ("^TNX", "10-year yield", "dgs10", "pct2", None, "points"),
    ("DX-Y.NYB", "US dollar", "dxy", "num1", None, "pct"),
    ("CL=F", "WTI crude", "oil", "usd2", None, "pct"),
    ("GC=F", "Gold", "gold", "usd0", None, "pct"),
    ("BTC-USD", "Bitcoin", "bitcoin", "usd0", None, "pct"),
]

OVERVIEW_SIGNALS = [
    "spx_vs_200", "spx_vs_50", "pct_above_200", "pct_above_50", "mcclellan", "net_highs_10d",
    "vix", "vix_term", "vrp", "spx_net_gex", "sm_gex", "dix", "pc_equity_10d",
    "hy_oas", "t10y3m", "nfci", "xly_xlp", "iwm_spy", "smh_spy", "soxx", "fear_greed", "aaii_spread",
]


def overview(store: Store, ctx: Ctx) -> dict:
    history = sig.pillar_history(ctx, lagged=False)
    latest = history.dropna(how="all").iloc[-1] if not history.dropna(how="all").empty else pd.Series(dtype=float)

    def week_change(column: str) -> float | None:
        if column not in history:
            return None
        values = history[column].dropna()
        return float(values.iloc[-1] - values.iloc[-6]) if len(values) > 5 else None

    pillars = []
    for pillar_id, info in sig.PILLARS.items():
        members = [item for item in sig.SIGNALS if item.pillar == pillar_id and item.score is not None]
        readings = []
        for member in members:
            row = sig.signal_row(ctx, member.id, spark_points=2)
            if row and row["score"] is not None:
                row.pop("spark", None)
                readings.append(row)
        score = latest.get(pillar_id)
        pillars.append({
            "id": pillar_id,
            "label": info["label"],
            "page": info["page"],
            "metric": f"pillar_{pillar_id}",
            "score": None if score is None or pd.isna(score) else float(score),
            "change_1w": week_change(pillar_id),
            "signals": readings,
        })

    health_score = latest.get("health")
    health = {
        "score": None if health_score is None or pd.isna(health_score) else float(health_score),
        **sig.health_label(health_score),
        "change_1w": week_change("health"),
    }
    ranked = sorted((item for item in pillars if item["score"] is not None), key=lambda item: item["score"])
    health["weakest"] = ranked[0]["label"] if ranked else None
    health["strongest"] = ranked[-1]["label"] if ranked else None
    if "health" in history:
        chart = history["health"].dropna()
        chart = chart[chart.index >= chart.index[-1] - pd.Timedelta(days=366)] if not chart.empty else chart
        health["history"] = {"dates": [stamp.strftime("%Y-%m-%d") for stamp in chart.index], "values": _round(chart.values, 1)}

    crowd_score = latest.get("crowd")
    crowd_members = []
    for member in sig.SIGNALS:
        if member.pillar == sig.CROWD_PILLAR["id"]:
            row = sig.signal_row(ctx, member.id, spark_points=2)
            if row:
                row.pop("spark", None)
                crowd_members.append(row)
    crowd = {
        "score": None if crowd_score is None or pd.isna(crowd_score) else float(crowd_score),
        **sig.crowd_label(crowd_score),
        "change_1w": week_change("crowd"),
        "signals": crowd_members,
        "page": sig.CROWD_PILLAR["page"],
    }

    tiles = [_quote_tile(ctx, *item[:5], change=item[5]) for item in OVERVIEW_TILES]

    sectors = []
    for symbol, name in SECTORS.items():
        adj = ctx.adj(symbol)
        if adj.empty:
            continue
        sectors.append({
            "symbol": symbol,
            "name": name,
            "chg_1d": ta.period_return(adj, 1),
            "chg_5d": ta.period_return(adj, 5),
            "chg_1m": ta.period_return(adj, 21),
        })

    close = ctx.close("^GSPC")
    spx_chart = table_payload(
        {"close": close, "sma50": ta.sma(close, 50), "sma200": ta.sma(close, 200)},
        "1y",
        digits=2,
    )
    gamma = store.kv_get("opt:summary:SPX")
    return {
        "market": market_state(),
        "health": health,
        "pillars": pillars,
        "crowd": crowd,
        "tiles": [tile for tile in tiles if tile],
        "signals": [row for row in _rows(ctx, OVERVIEW_SIGNALS).values() if row],
        "flips": sig.recent_flips(ctx),
        "catalysts": _catalysts(store),
        "sectors": sectors,
        "spx_chart": spx_chart,
        "gamma": gamma,
    }


def _trend_row(ctx: Ctx, symbol: str, name: str) -> dict | None:
    frame = ctx.prices(symbol)
    if frame.empty or frame["Close"].dropna().empty:
        return None
    snapshot = stock_analytics.build_snapshot(frame)
    return {
        "symbol": symbol,
        "name": name,
        "last": snapshot["last"],
        "change_pct": snapshot["change_pct"],
        "ret_1m": snapshot["ret_1m"],
        "ret_ytd": snapshot["ret_ytd"],
        "sma50": snapshot["sma50"],
        "sma200": snapshot["sma200"],
        "vs_sma50": snapshot["vs_sma50"],
        "vs_sma200": snapshot["vs_sma200"],
        "trend": snapshot["trend"],
        "last_cross": snapshot["last_cross"],
        "rsi": snapshot["rsi"],
        "off_high": snapshot["off_high"],
    }


def breadth(store: Store, ctx: Ctx, range_key: str) -> dict:
    ids = ["pct_above_20", "pct_above_50", "pct_above_200", "mcclellan", "net_new_highs", "net_highs_10d", "hl_index", "up_vol_share", "rsp_spy"]
    close = ctx.close("^GSPC")
    adv, dec = ctx.series("breadth:adv"), ctx.series("breadth:dec")
    new_highs, new_lows = ctx.series("breadth:new_highs"), ctx.series("breadth:new_lows")
    rows = _rows(ctx, ids)
    thrust = ctx.series("breadth:thrust").dropna()
    return {
        "market": market_state(),
        "rows": rows,
        "advancing": float(adv.iloc[-1]) if not adv.empty else None,
        "declining": float(dec.iloc[-1]) if not dec.empty else None,
        "new_highs": float(new_highs.iloc[-1]) if not new_highs.empty else None,
        "new_lows": float(new_lows.iloc[-1]) if not new_lows.empty else None,
        "thrust": {
            "value": float(thrust.iloc[-1]) if not thrust.empty else None,
            "last_signal": store.kv_get("breadth:last_thrust"),
        },
        "indices": [row for row in (_trend_row(ctx, symbol, name) for symbol, name in INDICES.items()) if row],
        "charts": {
            "spx": table_payload({"close": close, "sma50": ta.sma(close, 50), "sma200": ta.sma(close, 200)}, range_key, digits=2),
            "pct": table_payload({
                "above_20": ctx.series("breadth:pct20"),
                "above_50": ctx.series("breadth:pct50"),
                "above_200": ctx.series("breadth:pct200"),
            }, range_key, digits=1),
            "mcclellan": series_payload(ctx.series("breadth:mcclellan"), range_key, 1),
            "ad_line": series_payload(ctx.series("breadth:ad_line"), range_key, 0),
            "net_highs": series_payload(ctx.series("breadth:net_highs"), range_key, 0),
            "hl_index": series_payload(ctx.series("breadth:hl_index"), range_key, 1),
            "up_volume": table_payload({
                "daily": ctx.series("breadth:up_vol_share"),
                "avg_10d": ctx.series("breadth:up_vol_10d"),
            }, range_key, digits=1),
            "rsp_spy": _with_average(ctx.signal("rsp_spy"), range_key, digits=4),
        },
        "sectors": store.kv_get("breadth:sectors", []),
    }


def _with_average(series: pd.Series, range_key: str, window: int = 50, digits: int = 4) -> dict:
    return table_payload({"value": series, "average": ta.sma(series, window)}, range_key, digits=digits)


def rotation(store: Store, ctx: Ctx, universe: str, benchmark: str, lens: str, tail: int) -> dict:
    if universe == "watchlist":
        symbols = {symbol: _symbol_name(store, symbol) for symbol in store.watchlist()}
        label = "Watchlist"
    else:
        config = ROTATION_UNIVERSES.get(universe) or ROTATION_UNIVERSES["sectors"]
        universe = universe if universe in ROTATION_UNIVERSES else "sectors"
        symbols, label = config["symbols"], config["label"]
    universes = [{"id": key, "label": value["label"]} for key, value in ROTATION_UNIVERSES.items()] + [{"id": "watchlist", "label": "Watchlist"}]
    base = ctx.adj(benchmark)
    if base.empty:
        # Nothing to measure against yet, which is normal while the first download runs.
        return {
            "universe": universe, "universe_label": label, "benchmark": benchmark, "lens": lens, "tail": tail,
            "points": [], "table": [], "ratios": [], "risk_on": None,
            "risk_on_history": {"dates": [], "values": []}, "universes": universes,
        }
    prices = {symbol: ctx.adj(symbol) for symbol in symbols if symbol != benchmark}
    prices = {symbol: series for symbol, series in prices.items() if not series.empty}
    points = rotation_analytics.rotation_map(prices, base, lens=lens, tail=tail)
    names = dict(symbols)
    for point in points:
        point["name"] = names.get(point["symbol"], point["symbol"])

    table = []
    sector_breadth = {row["sector"]: row for row in store.kv_get("breadth:sectors", [])}
    positions = {point["symbol"]: point for point in points}
    for symbol, series in prices.items():
        close = ctx.close(symbol)
        volume = ctx.prices(symbol)["Volume"]
        rvol = ta.relative_volume(volume).dropna()
        sma50, sma200 = ta.sma(close, 50).iloc[-1], ta.sma(close, 200).iloc[-1]
        row = {
            "symbol": symbol,
            "name": names.get(symbol, symbol),
            **rotation_analytics.performance_row(series, base),
            "quadrant": positions.get(symbol, {}).get("quadrant"),
            "rs_ratio": positions.get(symbol, {}).get("rs_ratio"),
            "rs_momentum": positions.get(symbol, {}).get("rs_momentum"),
            "above_50": None if pd.isna(sma50) else bool(close.iloc[-1] > sma50),
            "above_200": None if pd.isna(sma200) else bool(close.iloc[-1] > sma200),
            "rvol": float(rvol.iloc[-1]) if not rvol.empty else None,
        }
        gics = SECTOR_TO_GICS.get(symbol)
        if gics and gics in sector_breadth:
            row["breadth_pct50"] = sector_breadth[gics].get("pct50")
        table.append(row)

    ratios = []
    for ratio in RISK_RATIOS:
        state = rotation_analytics.ratio_state(sig.price_ratio(ctx, ratio["a"], ratio["b"]))
        if state:
            ratios.append({**ratio, **state, "metric": ratio["id"]})
    share = ctx.signal("risk_on_share")
    return {
        "universe": universe,
        "universe_label": label,
        "benchmark": benchmark,
        "lens": lens,
        "tail": tail,
        "points": points,
        "table": table,
        "ratios": ratios,
        "risk_on": sig.signal_row(ctx, "risk_on_share"),
        "risk_on_history": series_payload(share, "1y", 1),
        "universes": universes,
    }


TERM_POINTS = [("^VIX9D", "9 days", 9), ("^VIX", "30 days", 30), ("^VIX3M", "3 months", 93), ("^VIX6M", "6 months", 183), ("^VIX1Y", "1 year", 365)]
VOL_TABLE = [
    ("^VIX", "S&P 500 (VIX)", "vix"),
    ("^VXN", "Nasdaq 100 (VXN)", "vxn"),
    ("^OVX", "Crude oil (OVX)", "ovx"),
    ("^GVZ", "Gold (GVZ)", "gvz"),
    ("^MOVE", "Treasuries (MOVE)", "move"),
    ("^VVIX", "VIX itself (VVIX)", "vvix"),
]


def volatility(ctx: Ctx, range_key: str) -> dict:
    ids = ["vix", "vix_pctile", "vix_term", "vix9d_ratio", "vvix", "skew", "move", "rv20", "vrp"]
    rows = _rows(ctx, ids)
    vix = ctx.close("^VIX")

    def curve(days_back: int) -> list:
        output = []
        for symbol, label, days in TERM_POINTS:
            series = ctx.close(symbol)
            value = None
            if not series.empty:
                earlier = series[series.index <= series.index[-1] - pd.Timedelta(days=days_back)]
                value = float(earlier.iloc[-1]) if not earlier.empty else None
            output.append({"label": label, "days": days, "value": value})
        return output

    table = []
    for symbol, name, metric in VOL_TABLE:
        close = ctx.close(symbol)
        if close.empty:
            continue
        window = close.tail(ta.TRADING_DAYS)
        table.append({
            "symbol": symbol,
            "name": name,
            "metric": metric,
            "last": float(close.iloc[-1]),
            "chg_1d": ta.period_return(close, 1),
            "chg_1m": ta.period_return(close, 21),
            "pctile_1y": float((window <= close.iloc[-1]).mean() * 100.0),
            "spark": _round(close.tail(60).values, 2),
        })
    return {
        "market": market_state(),
        "rows": rows,
        "implied_daily_move": float(vix.iloc[-1]) / math.sqrt(252) if not vix.empty else None,
        "term_structure": {"today": curve(0), "week_ago": curve(7), "month_ago": curve(30)},
        "charts": {
            "vix_rv": table_payload({"vix": vix, "rv20": ctx.signal("rv20")}, range_key, digits=2),
            "term_ratio": series_payload(ctx.signal("vix_term"), range_key, 3),
            "vrp": series_payload(ctx.signal("vrp"), range_key, 2),
            "vvix": series_payload(ctx.signal("vvix"), range_key, 1),
            "skew": series_payload(ctx.signal("skew"), range_key, 1),
            "move": series_payload(ctx.signal("move"), range_key, 1),
        },
        "table": table,
    }


def flows(store: Store, ctx: Ctx, range_key: str) -> dict:
    ids = [
        "pc_total", "pc_equity", "pc_index", "pc_equity_10d", "dix", "sm_gex", "spx_net_gex",
        "fear_greed", "aaii_spread", "aaii_bull", "aaii_bear", "cot_am_net", "cot_lev_net", "crowd_fear",
    ]

    def pc_pair(series_id: str) -> dict:
        series = ctx.series(series_id)
        return table_payload({"daily": series, "avg_10d": ta.sma(series, 10)}, range_key, digits=3)

    dix = ctx.signal("dix")
    return {
        "market": market_state(),
        "rows": _rows(ctx, ids),
        "cnn": store.kv_get("cnn:current"),
        "charts": {
            "pc_equity": pc_pair("cboe:pc_equity"),
            "pc_total": pc_pair("cboe:pc_total"),
            "pc_index": pc_pair("cboe:pc_index"),
            "dix": table_payload({"dix": dix, "avg_20d": ta.sma(dix, 20)}, range_key, digits=2),
            "gex": series_payload(ctx.signal("sm_gex"), range_key, 2),
            "fear_greed": series_payload(ctx.signal("fear_greed"), range_key, 1),
            "aaii": table_payload({"bull": ctx.signal("aaii_bull"), "bear": ctx.signal("aaii_bear")}, range_key, digits=1),
            "cot": table_payload({
                "asset_managers": ctx.signal("cot_am_net"),
                "leveraged_funds": ctx.signal("cot_lev_net"),
            }, range_key, digits=0),
        },
    }


UST_TENORS = [("1M", 1), ("2M", 2), ("3M", 3), ("4M", 4), ("6M", 6), ("1Y", 12), ("2Y", 24), ("3Y", 36), ("5Y", 60), ("7Y", 84), ("10Y", 120), ("20Y", 240), ("30Y", 360)]


def _curve_on(ctx: Ctx, days_back: int) -> dict | None:
    points, stamp = [], None
    for tenor, months in UST_TENORS:
        series = ctx.series(f"ust:{tenor}")
        if series.empty:
            continue
        target = series.index[-1] - pd.Timedelta(days=days_back)
        earlier = series[series.index <= target]
        if earlier.empty:
            continue
        stamp = earlier.index[-1]
        points.append({"tenor": tenor, "months": months, "value": float(earlier.iloc[-1])})
    return {"date": stamp.strftime("%Y-%m-%d"), "points": points} if points else None


def macro(ctx: Ctx, range_key: str) -> dict:
    ids = [
        "dgs10", "dgs2", "t10y2y", "t10y3m", "hy_oas", "hy_oas_chg", "ig_oas", "ccc_oas", "nfci", "stlfsi", "real10y",
        "breakeven10y", "fed_funds", "dxy", "oil", "gold", "copper", "copper_gold", "net_liquidity", "claims", "unrate",
        "sahm", "cpi_yoy", "core_cpi_yoy", "core_pce_yoy", "hyg_ief",
    ]
    return {
        "market": market_state(),
        "rows": _rows(ctx, ids),
        "curve": {"today": _curve_on(ctx, 0), "month_ago": _curve_on(ctx, 30), "year_ago": _curve_on(ctx, 365)},
        "charts": {
            "yields": table_payload({"y10": ctx.signal("dgs10"), "y2": ctx.signal("dgs2"), "ff": ctx.signal("fed_funds")}, range_key, digits=2),
            "spreads_curve": table_payload({"t10y2y": ctx.signal("t10y2y"), "t10y3m": ctx.signal("t10y3m")}, range_key, digits=2),
            "credit": table_payload({"hy": ctx.signal("hy_oas"), "ig": ctx.signal("ig_oas")}, range_key, digits=2),
            "hyg_ief": _with_average(ctx.signal("hyg_ief"), range_key),
            "nfci": series_payload(ctx.signal("nfci"), range_key, 3),
            "liquidity": series_payload(ctx.signal("net_liquidity"), range_key, 3),
            "real_breakeven": table_payload({"real": ctx.signal("real10y"), "breakeven": ctx.signal("breakeven10y")}, range_key, digits=2),
            "dxy": _with_average(ctx.signal("dxy"), range_key, digits=2),
            "copper_gold": _with_average(ctx.signal("copper_gold") * 1000.0, range_key, digits=3),
            "claims": series_payload(ctx.signal("claims"), range_key, 0),
            "inflation": table_payload({"cpi": ctx.signal("cpi_yoy"), "core_cpi": ctx.signal("core_cpi_yoy"), "core_pce": ctx.signal("core_pce_yoy")}, range_key, digits=2),
            "unemployment": table_payload({"unrate": ctx.signal("unrate"), "sahm": ctx.signal("sahm")}, range_key, digits=2),
        },
    }


# --------------------------------------------------------------------------- options

def options_page(store: Store, symbol: str, scope: str, window: float) -> dict:
    underlying = cboe.option_symbol(symbol)
    try:
        chain = cboe.get_chain(underlying)
    except cboe.NoOptions as exc:
        return {"available": False, "symbol": underlying, "reason": str(exc)}
    if not chain.get("spot"):
        return {"available": False, "symbol": underlying, "reason": f"Cboe has no current price for {underlying}."}
    source_time = chain.get("source_time")
    session = source_time[:10] if isinstance(source_time, str) and len(source_time) >= 10 else last_completed_session().isoformat()
    result = option_analytics.analyze_chain(chain["frame"], chain["spot"], scope=scope, window_pct=window, session=session)
    result.update({
        "symbol": underlying,
        "source_time": source_time,
        "fetched_at": chain.get("fetched_at"),
        "market": market_state(),
        "name": CORE_NAMES.get(price_symbol(underlying)) or _symbol_name(store, underlying),
        "history": table_payload({
            "net_gex": store.get_series(f"opt:{underlying}:net_gex") / 1e9,
            "flip_distance_pct": store.get_series(f"opt:{underlying}:flip_distance_pct"),
            "iv30": store.get_series(f"opt:{underlying}:iv30"),
            "pc_volume": store.get_series(f"opt:{underlying}:pc_volume"),
        }, "max", digits=3),
    })
    return result


def option_underlyings(store: Store) -> list[dict]:
    fixed = [{"symbol": symbol, "label": symbol, "group": "Market"} for symbol in OPTION_CORE]
    extras = [{"symbol": symbol, "label": symbol, "group": "Market"} for symbol in ("NDX", "RUT", "VIX") if symbol not in OPTION_CORE]
    seen = {item["symbol"] for item in fixed + extras}
    watch = [{"symbol": symbol, "label": symbol, "group": "Watchlist"} for symbol in store.watchlist() if symbol not in seen]
    return fixed + extras + watch


def gamma_levels(symbol: str) -> dict:
    """Key option levels for drawing on a price chart; empty when there are no options."""
    underlying = cboe.option_symbol(symbol)
    try:
        chain = cboe.get_chain(underlying)
    except cboe.NoOptions as exc:
        return {"available": False, "reason": str(exc)}
    if not chain.get("spot"):
        return {"available": False, "reason": "No current option data."}
    source_time = chain.get("source_time")
    session = source_time[:10] if isinstance(source_time, str) and len(source_time) >= 10 else None
    result = option_analytics.analyze_chain(chain["frame"], chain["spot"], session=session)
    if not result.get("available") or result.get("empty_scope"):
        return {"available": False, "reason": result.get("reason")}
    kpis = result["kpis"]
    return {
        "available": True,
        "underlying": underlying,
        "gamma_flip": kpis["gamma_flip"],
        "call_wall": kpis["call_wall"],
        "put_wall": kpis["put_wall"],
        "max_pain": kpis["max_pain"],
        "max_pain_expiry": kpis["max_pain_expiry"],
    }


# ---------------------------------------------------------------------------- stocks

def _symbol_name(store: Store, symbol: str) -> str:
    if symbol in CORE_NAMES:
        return CORE_NAMES[symbol]
    members = load_sp500(store)
    match = members[members["symbol"] == symbol]
    if not match.empty:
        return str(match["name"].iloc[0])
    details = store.kv_get(f"fund:{symbol}")
    if details:
        info = details.get("info") or {}
        return info.get("shortName") or info.get("longName") or symbol
    return symbol


def _benchmark(ctx: Ctx) -> pd.Series:
    return ctx.adj("SPY")


def stock_page(store: Store, ctx: Ctx, symbol: str) -> dict:
    target = price_symbol(symbol)
    frame = ensure_prices(store, target, start=store.history_from(target) or "2000-01-01")
    snapshot = stock_analytics.build_snapshot(frame, _benchmark(ctx), store)
    details = store.kv_get(f"fund:{target}")
    info = (details or {}).get("info") or {}
    chips = stock_analytics.signal_chips(
        snapshot,
        next_earnings=(details or {}).get("next_earnings"),
        short_pct=info.get("shortPercentOfFloat"),
    )
    underlying = cboe.option_symbol(target)
    return {
        "symbol": target,
        "name": _symbol_name(store, target),
        "in_watchlist": target in store.watchlist(),
        "market": market_state(),
        "snapshot": snapshot,
        "chips": chips,
        "options": store.kv_get(f"opt:summary:{underlying}"),
        "option_underlying": underlying,
        "dark_pool": stock_analytics.dark_pool_summary(store.get_short_volume(target)),
    }


def stock_fundamentals(store: Store, symbol: str) -> dict:
    target = price_symbol(symbol)
    details = fundamentals.get_fundamentals(store, target)
    return details or {"symbol": target, "info": {}, "next_earnings": None, "earnings_history": [], "insiders": None, "analysts": None}


def stock_options_summary(store: Store, symbol: str) -> dict:
    """Option summary for a stock page, loading the chain on first visit."""
    underlying = cboe.option_symbol(price_symbol(symbol))
    cached = store.kv_get(f"opt:summary:{underlying}", max_age=6 * 3600)
    if cached:
        return {"available": True, "underlying": underlying, "summary": cached}
    try:
        chain = cboe.get_chain(underlying)
    except cboe.NoOptions as exc:
        return {"available": False, "underlying": underlying, "reason": str(exc)}
    result = save_option_snapshot(store, underlying, chain)
    if result is None:
        return {"available": False, "underlying": underlying, "reason": "No usable option data right now."}
    return {"available": True, "underlying": underlying, "summary": store.kv_get(f"opt:summary:{underlying}")}


def watchlist_rows(store: Store, ctx: Ctx) -> list[dict]:
    rows = []
    benchmark = _benchmark(ctx)
    for symbol in store.watchlist():
        frame = store.get_prices(symbol)
        if frame.empty:
            try:
                frame = ensure_prices(store, symbol)
            except SymbolNotFound:
                rows.append({"symbol": symbol, "name": symbol, "error": "No price data found."})
                continue
        snapshot = stock_analytics.build_snapshot(frame, benchmark, store)
        details = store.kv_get(f"fund:{symbol}") or {}
        info = details.get("info") or {}
        option = store.kv_get(f"opt:summary:{cboe.option_symbol(symbol)}") or {}
        dark_pool = stock_analytics.dark_pool_summary(store.get_short_volume(symbol))
        rows.append({
            "symbol": symbol,
            "name": _symbol_name(store, symbol),
            "last": snapshot.get("last"),
            "change_pct": snapshot.get("change_pct"),
            "ret_5d": snapshot.get("ret_5d"),
            "ret_1m": snapshot.get("ret_1m"),
            "ret_ytd": snapshot.get("ret_ytd"),
            "rvol": snapshot.get("rvol"),
            "rsi": snapshot.get("rsi"),
            "vs_sma50": snapshot.get("vs_sma50"),
            "vs_sma200": snapshot.get("vs_sma200"),
            "trend": snapshot.get("trend"),
            "rs_rating": snapshot.get("rs_rating"),
            "off_high": snapshot.get("off_high"),
            "iv30": option.get("iv30"),
            "hv20": snapshot.get("hv20"),
            "pc_volume": option.get("pc_volume"),
            "net_gex": option.get("net_gex"),
            "flip_distance_pct": option.get("flip_distance_pct"),
            "short_pct": info.get("shortPercentOfFloat"),
            "dark_pool": dark_pool["value"] if dark_pool else None,
            "next_earnings": details.get("next_earnings"),
            "spark": snapshot.get("spark"),
            "chips": stock_analytics.signal_chips(
                snapshot,
                next_earnings=details.get("next_earnings"),
                short_pct=info.get("shortPercentOfFloat"),
            ),
        })
    return rows


def scanner(store: Store) -> dict:
    rows = store.kv_get("breadth:members", [])
    sectors = sorted({row["sector"] for row in rows if row.get("sector")})
    return {"rows": rows, "sectors": sectors, "watchlist": store.watchlist()}


def search(store: Store, query: str, limit: int = 10) -> list[dict]:
    needle = (query or "").strip().upper()
    if not needle:
        return []
    results: list[dict] = []
    seen: set[str] = set()

    def add(symbol: str, name: str) -> None:
        if symbol not in seen:
            seen.add(symbol)
            results.append({"symbol": symbol, "name": name})

    members = load_sp500(store)
    candidates = [(symbol, _symbol_name(store, symbol)) for symbol in store.watchlist()]
    candidates += list(CORE_NAMES.items())
    candidates += list(zip(members["symbol"], members["name"]))
    for symbol, name in candidates:
        if symbol == needle:
            add(symbol, str(name))
    for symbol, name in candidates:
        if symbol.startswith(needle) or symbol.lstrip("^").startswith(needle):
            add(symbol, str(name))
    for symbol, name in candidates:
        if needle in str(name).upper():
            add(symbol, str(name))
    return results[:limit]


# ----------------------------------------------------------------------------- chart

INTERVAL_RANGES = {
    "15m": {"5d": 5, "1m": 31, "60d": 60},
    "1h": {"1m": 31, "3m": 92, "6m": 183, "1y": 366, "2y": 730},
    "1d": {"3m": 92, "6m": 183, "1y": 366, "2y": 731, "5y": 1827, "10y": 3653, "max": None},
    "1wk": {"1y": 366, "2y": 731, "5y": 1827, "10y": 3653, "max": None},
}


def _weekly_frame(frame: pd.DataFrame) -> pd.DataFrame:
    periods = frame.index.to_period("W-FRI")
    grouped = frame.groupby(periods)
    weekly = pd.DataFrame({
        "Open": grouped["Open"].first(),
        "High": grouped["High"].max(),
        "Low": grouped["Low"].min(),
        "Close": grouped["Close"].last(),
        "AdjClose": grouped["AdjClose"].last(),
        "Volume": grouped["Volume"].sum(),
    })
    weekly.index = pd.DatetimeIndex(frame.index.to_series().groupby(periods).last().values)
    return weekly


def _chart_frame(store: Store, symbol: str, interval: str, extended: bool = False) -> pd.DataFrame:
    if interval in ("15m", "1h"):
        return intraday_prices(symbol, interval, prepost=extended)
    frame = ensure_prices(store, symbol).dropna(subset=["Open", "High", "Low", "Close"])
    return _weekly_frame(frame) if interval == "1wk" else frame


def _crop(frame: pd.DataFrame, interval: str, range_key: str) -> tuple[pd.DataFrame, str]:
    ranges = INTERVAL_RANGES[interval]
    if range_key not in ranges:
        range_key = "1y" if "1y" in ranges else next(iter(ranges))
    days = ranges[range_key]
    if days is None or frame.empty:
        return frame, range_key
    return frame[frame.index >= frame.index[-1] - pd.Timedelta(days=days)], range_key


def bar_sessions(index: pd.DatetimeIndex, symbol: str) -> list[str]:
    """'pre', 'regular' or 'post' per intraday bar (times in ET).

    Futures trade nearly around the clock with no pre-market of their own, so all their
    bars count as regular.
    """
    if symbol.endswith("=F"):
        return ["regular"] * len(index)
    sessions = []
    for stamp in index:
        clock = stamp.time()
        sessions.append("pre" if clock < REGULAR_OPEN else "post" if clock >= REGULAR_CLOSE else "regular")
    return sessions


def chart(store: Store, ctx: Ctx, symbol: str, interval: str, range_key: str, extended: bool = False) -> dict:
    target = price_symbol(symbol)
    if interval not in INTERVAL_RANGES:
        raise ValueError(f"Unknown chart interval '{interval}'.")
    intraday = interval in ("15m", "1h")
    extended = extended and intraday
    frame = _chart_frame(store, target, interval, extended)
    sessions = bar_sessions(frame.index, target) if intraday else []
    regular = pd.Series([item == "regular" for item in sessions], index=frame.index) if extended else None
    indicators = ta.indicator_frame(frame, intraday=intraday, regular=regular)
    if not intraday and target != "SPY":
        benchmark = ctx.adj("SPY") if interval == "1d" else _weekly_frame(ctx.prices("SPY").dropna(subset=["Open"]))["AdjClose"]
        joined = pd.concat([frame["AdjClose"], benchmark], axis=1, join="inner").dropna()
        if not joined.empty:
            relative = joined.iloc[:, 0] / joined.iloc[:, 1]
            indicators["rs_spy"] = relative.reindex(indicators.index)
    cropped, range_key = _crop(frame, interval, range_key)
    if cropped.empty:
        raise SymbolNotFound(f"No chart data for {target} in this range.")
    shown = indicators.loc[cropped.index]
    date_format = "%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d"
    crosses = []
    if not intraday:
        start_label = cropped.index[0].strftime("%Y-%m-%d")
        crosses = [item for item in ta.cross_events(indicators["sma50"], indicators["sma200"]) if item["date"] >= start_label]
    return {
        "symbol": target,
        "name": _symbol_name(store, target),
        "interval": interval,
        "range": range_key,
        "ranges": list(INTERVAL_RANGES[interval]),
        "dates": [stamp.strftime(date_format) for stamp in cropped.index],
        "open": _round(cropped["Open"].values, 4),
        "high": _round(cropped["High"].values, 4),
        "low": _round(cropped["Low"].values, 4),
        "close": _round(cropped["Close"].values, 4),
        "volume": _round(cropped["Volume"].fillna(0.0).values, 0),
        "indicators": {column: _round(shown[column].values, 4) for column in shown.columns},
        "crosses": crosses,
        "extended": extended,
        "sessions": sessions[len(frame) - len(cropped):] if intraday else [],
        "market": market_state(),
    }


def signal_catalog() -> dict:
    groups = []
    for group in sig.SIGNAL_GROUPS:
        items = [
            {"id": item.id, "label": item.label, "fmt": item.fmt, "metric": item.metric, "positive_only": item.positive_only, "page": item.page}
            for item in sig.SIGNALS
            if item.group == group
        ]
        if items:
            groups.append({"group": group, "signals": items})
    return {"groups": groups}


def signal_current(ctx: Ctx) -> dict:
    """Latest reading and 1-year range per signal, to help pick sensible thresholds."""
    output: dict[str, dict] = {}
    for item in sig.SIGNALS:
        values = ctx.signal(item.id)
        if values.empty:
            continue
        window = values[values.index >= values.index[-1] - pd.Timedelta(days=365)]
        output[item.id] = {
            "value": float(values.iloc[-1]),
            "low_1y": float(window.min()),
            "high_1y": float(window.max()),
            "fmt": item.fmt,
        }
    return output


def aligned_signals(store: Store, ctx: Ctx, symbol: str, interval: str, range_key: str, ids: list[str]) -> dict:
    """Signals and comparison tickers lined up with a chart's dates for overlaying."""
    if interval not in ("1d", "1wk"):
        raise ValueError("Signals are daily, so they can only be overlaid on daily or weekly charts.")
    target = price_symbol(symbol)
    frame = _chart_frame(store, target, interval)
    cropped, _ = _crop(frame, interval, range_key)
    dates = cropped.index
    labels = [stamp.strftime("%Y-%m-%d") for stamp in dates]
    output: dict[str, dict] = {}
    for item in ids:
        if item.startswith("px:"):
            ticker = price_symbol(item.removeprefix("px:"))
            try:
                prices = ensure_prices(store, ticker)
            except SymbolNotFound as exc:
                output[item] = {"error": str(exc)}
                continue
            aligned = prices["AdjClose"].dropna()
            aligned = aligned.reindex(aligned.index.union(dates)).ffill(limit=7).reindex(dates)
            output[item] = {
                "label": ticker,
                "fmt": "num2",
                "metric": "compare_ticker",
                "positive_only": True,
                "values": _round(aligned.values, 4),
            }
            continue
        definition = sig.REGISTRY.get(item)
        if definition is None:
            output[item] = {"error": f"Unknown signal '{item}'."}
            continue
        values = ctx.signal(item)
        if values.empty:
            output[item] = {"error": f"No data available yet for {definition.label}.", "label": definition.label}
            continue
        limit = definition.ffill if interval == "1d" else max(definition.ffill, 10)
        aligned = values.reindex(values.index.union(dates)).ffill(limit=limit).reindex(dates)
        output[item] = {
            "label": definition.label,
            "fmt": definition.fmt,
            "metric": definition.metric,
            "positive_only": definition.positive_only,
            "first_date": values.index[0].strftime("%Y-%m-%d"),
            "values": _round(aligned.values, 4),
        }
    return {"dates": labels, "signals": output}


# -------------------------------------------------------------------------- research

STUDY_PRESETS = [
    {"id": "vix_30", "label": "VIX closes above 30", "conditions": [{"signal": "vix", "op": ">", "value": 30}]},
    {"id": "vix_low", "label": "VIX closes below 13 (complacency)", "conditions": [{"signal": "vix", "op": "<", "value": 13}]},
    {"id": "curve_inverted", "label": "VIX curve inverts (VIX above VIX3M)", "conditions": [{"signal": "vix_term", "op": ">", "value": 1}]},
    {"id": "washed_out", "label": "Under 20% of stocks above their 50-day average", "conditions": [{"signal": "pct_above_50", "op": "<", "value": 20}]},
    {"id": "mcclellan_low", "label": "McClellan Oscillator below -50", "conditions": [{"signal": "mcclellan", "op": "<", "value": -50}]},
    {"id": "negative_gamma", "label": "Market gamma turns negative", "conditions": [{"signal": "sm_gex", "op": "<", "value": 0}]},
    {"id": "dix_high", "label": "DIX above 47% (heavy dark-pool buying)", "conditions": [{"signal": "dix", "op": ">", "value": 47}]},
    {"id": "put_buying", "label": "Equity put/call 10-day average above 0.75", "conditions": [{"signal": "pc_equity_10d", "op": ">", "value": 0.75}]},
    {"id": "bears", "label": "AAII bears outnumber bulls by 20 points", "conditions": [{"signal": "aaii_spread", "op": "<", "value": -20}]},
    {"id": "extreme_fear", "label": "CNN Fear & Greed below 25", "conditions": [{"signal": "fear_greed", "op": "<", "value": 25}]},
    {"id": "correction", "label": "S&P 500 down 10% from its 52-week high", "conditions": [{"signal": "spx_drawdown", "op": "<", "value": -10}]},
    {"id": "credit_stress", "label": "High-yield spread widens 1 point in 3 months", "conditions": [{"signal": "hy_oas_chg", "op": ">", "value": 1}]},
    {"id": "health_low", "label": "Market Health score below 30", "conditions": [{"signal": "market_health", "op": "<", "value": 30}]},
]


def research_options() -> dict:
    return {
        **signal_catalog(),
        "self_signals": [
            {"id": f"self:{key}", "label": value["label"], "metric": value["metric"]}
            for key, value in sig.SELF_SIGNALS.items()
        ],
        "strategies": STRATEGY_TEMPLATES,
        "study_presets": STUDY_PRESETS,
        "horizons": list(HORIZONS),
    }


def _monthly_returns(dates: pd.DatetimeIndex, equity: np.ndarray) -> list[dict]:
    series = pd.Series(equity, index=dates)
    month_end = series.resample("ME").last()
    returns = month_end.pct_change(fill_method=None)
    if len(month_end) and series.iloc[0]:
        returns.iloc[0] = month_end.iloc[0] / series.iloc[0] - 1.0
    return [
        {"year": int(stamp.year), "month": int(stamp.month), "return": float(value) * 100.0}
        for stamp, value in returns.dropna().items()
    ]


def _drawdown_series(equity: np.ndarray) -> np.ndarray:
    peak = np.maximum.accumulate(equity)
    return (equity - peak) / peak * 100.0


def run_backtests(store: Store, ctx: Ctx, request: dict) -> dict:
    symbol = price_symbol(_required_symbol(request))
    start = str(request.get("start") or "2018-01-01")
    end = request.get("end") or None
    capital = float(request.get("capital") or 10000.0)
    commission = float(request.get("commission_bps") if request.get("commission_bps") is not None else 5.0) / 10000.0
    slippage = float(request.get("slippage_bps") if request.get("slippage_bps") is not None else 5.0) / 10000.0
    if capital <= 0:
        raise ValueError("Starting capital must be positive.")
    if commission < 0 or slippage < 0:
        raise ValueError("Costs cannot be negative.")
    specs = request.get("strategies") or [{"id": item["id"]} for item in STRATEGY_TEMPLATES]
    if not any(spec.get("id") == "buy_and_hold" for spec in specs):
        specs = [{"id": "buy_and_hold"}, *specs]

    _validate_date(start, "Start date")
    if end:
        _validate_date(str(end), "End date")
    full = ensure_prices(store, symbol, start=start).dropna(subset=["Open", "Close"])
    frame = full.loc[start:str(end)] if end else full.loc[start:]
    if len(frame) < 30:
        raise ValueError(f"{symbol} has too little history in this period to backtest (only {len(frame)} days).")
    factor = (frame["AdjClose"] / frame["Close"]).fillna(1.0)
    data = frame[["Open", "High", "Low", "Close", "Volume"]].copy()
    for column in ("Open", "High", "Low", "Close"):
        data[column] = data[column] * factor

    engine = BacktestEngine(capital, commission, slippage)
    rows, details = [], {}
    for spec in specs:
        strategy = build_strategy(spec, ctx)
        result = engine.run(data, strategy)
        run_id = str(spec.get("id") or spec.get("name") or "custom")
        equity = result["equity_curve"]
        note = None
        conditions = getattr(strategy, "conditions", None)
        if conditions:
            first = condition_coverage(ctx, conditions, data.index, data)
            if first is None:
                note = "This rule's signals have no data in the selected period, so it never invested."
            elif first > data.index[0].strftime("%Y-%m-%d"):
                note = f"Signal history starts {first}. The rule holds cash before then."
        rows.append({
            "id": run_id,
            "name": strategy.name,
            "description": spec.get("description") or next((item["description"] for item in STRATEGY_TEMPLATES if item["id"] == spec.get("id")), None),
            "note": note,
            "total_return": result["total_return"] * 100.0,
            "cagr": result["cagr"] * 100.0,
            "volatility": result["volatility"] * 100.0,
            "sharpe": result["sharpe_ratio"],
            "sortino": result["sortino_ratio"],
            "calmar": result["calmar_ratio"],
            "max_drawdown": result["max_drawdown"] * 100.0,
            "exposure": result["exposure"] * 100.0,
            "trades": result["number_of_trades"],
            "win_rate": result["win_rate"] * 100.0,
            "profit_factor": result["profit_factor"],
            "avg_trade": result["average_trade_return"] * 100.0,
            "best_trade": result["best_trade"] * 100.0,
            "worst_trade": result["worst_trade"] * 100.0,
            "costs": result["total_transaction_costs"],
            "final_value": result["final_portfolio_value"],
        })
        details[run_id] = {
            "equity": _round(equity, 2),
            "drawdown": _round(_drawdown_series(equity), 2),
            "trades": result["trades"],
            "monthly": _monthly_returns(data.index, equity),
        }
    return {
        "symbol": symbol,
        "name": _symbol_name(store, symbol),
        "start": data.index[0].strftime("%Y-%m-%d"),
        "end": data.index[-1].strftime("%Y-%m-%d"),
        "capital": capital,
        "commission_bps": commission * 10000.0,
        "slippage_bps": slippage * 10000.0,
        "dates": [stamp.strftime("%Y-%m-%d") for stamp in data.index],
        "price": _round(data["Close"].values, 2),
        "rows": rows,
        "details": details,
    }


def run_study(store: Store, ctx: Ctx, request: dict) -> dict:
    symbol = price_symbol(_required_symbol(request))
    raw = request.get("conditions") or []
    if not raw:
        raise ValueError("Add at least one condition to study.")
    conditions = [Condition.parse(item) for item in raw]
    start = request.get("start") or None
    if start:
        _validate_date(str(start), "Start date")
    cooldown = int(request.get("cooldown") if request.get("cooldown") is not None else 10)
    if not 0 <= cooldown <= 252:
        raise ValueError("Cooldown must be between 0 and 252 trading days.")
    frame = ensure_prices(store, symbol, start=str(start) if start else "2000-01-01").dropna(subset=["Close"])
    if start:
        frame = frame.loc[str(start):]
    if frame.empty:
        raise ValueError(f"{symbol} has no prices in this period.")
    mask = condition_mask(ctx, conditions, frame.index, frame)
    coverage = condition_coverage(ctx, conditions, frame.index, frame)
    if coverage is None:
        raise ValueError("These signals have no data in the selected period yet.")
    prices = frame["AdjClose"].dropna()
    # Compare with all days on which the signal could have been measured, so the baseline
    # covers the same market history as the events.
    prices = prices[prices.index >= pd.Timestamp(coverage)]
    study = forward_return_study(prices, mask, cooldown=cooldown)
    events = study["events"]
    close = frame["Close"]
    close = close[close.index >= pd.Timestamp(coverage)]
    return {
        "symbol": symbol,
        "name": _symbol_name(store, symbol),
        "conditions": [condition.label() for condition in conditions],
        "coverage_from": coverage,
        "last_date": frame.index[-1].strftime("%Y-%m-%d"),
        "cooldown": cooldown,
        **study,
        "active_now": bool(mask.iloc[-1]),
        "price": {"dates": [stamp.strftime("%Y-%m-%d") for stamp in close.index], "values": _round(close.values, 2)},
        "event_prices": [
            {"date": day, "price": float(close.loc[day])}
            for day in events
            if pd.Timestamp(day) in close.index
        ],
    }


def _required_symbol(request: dict) -> str:
    return normalize_symbol(str(request.get("symbol") or ""))


def _validate_date(value: str, label: str) -> None:
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must look like YYYY-MM-DD.") from exc
