"""One stock at a time: its trend, momentum, volume and volatility in plain numbers."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from src.analytics import technicals as ta
from src.analytics.breadth import rs_rating_for
from src.data.store import Store


def _last(series: pd.Series) -> float | None:
    clean = series.dropna()
    return float(clean.iloc[-1]) if not clean.empty else None


def build_snapshot(frame: pd.DataFrame, benchmark: pd.Series | None = None, store: Store | None = None) -> dict:
    """All per-stock technical readings for the latest bar."""
    close = frame["Close"].dropna()
    adjusted = frame["AdjClose"].dropna() if "AdjClose" in frame else close
    if close.empty:
        return {}
    last = float(close.iloc[-1])
    previous = float(close.iloc[-2]) if len(close) > 1 else None

    sma20, sma50, sma100, sma200 = ta.sma(close, 20), ta.sma(close, 50), ta.sma(close, 100), ta.sma(close, 200)
    ema21 = ta.ema(close, 21)
    last50, last200 = _last(sma50), _last(sma200)
    crosses = ta.cross_events(sma50, sma200)
    latest_cross = crosses[-1] if crosses else None
    if latest_cross:
        latest_cross = {**latest_cross, "bars_ago": int(len(close.loc[latest_cross["date"]:]) - 1)}

    window = close.tail(ta.TRADING_DAYS)
    high_window = frame["High"].dropna().tail(ta.TRADING_DAYS)
    low_window = frame["Low"].dropna().tail(ta.TRADING_DAYS)
    high_52w = float(high_window.max()) if not high_window.empty else None
    low_52w = float(low_window.min()) if not low_window.empty else None
    high_date = high_window.idxmax().strftime("%Y-%m-%d") if not high_window.empty else None

    macd_lines = ta.macd(close)
    hist = macd_lines["hist"].dropna()
    macd_cross = None
    if len(hist) > 1:
        flips = np.sign(hist)[np.sign(hist) != np.sign(hist).shift(1)].iloc[1:]
        if not flips.empty:
            macd_cross = {
                "direction": "bullish" if flips.iloc[-1] > 0 else "bearish",
                "bars_ago": int(len(close.loc[flips.index[-1]:]) - 1),
            }
    directional = ta.adx(frame)
    bands = ta.bollinger(close)
    stoch = ta.stochastic(frame)
    width = bands["bandwidth"].dropna()
    squeeze_rank = float((width.tail(ta.TRADING_DAYS) <= width.iloc[-1]).mean() * 100.0) if not width.empty else None

    volume = frame["Volume"].dropna()
    has_volume = not volume.empty and volume.sum() > 0
    rvol_series = ta.relative_volume(frame["Volume"]) if has_volume else pd.Series(dtype=float)
    change = close.diff()
    up_volume = frame["Volume"].where(change > 0).tail(50).sum() if has_volume else 0.0
    down_volume = frame["Volume"].where(change < 0).tail(50).sum() if has_volume else 0.0
    obv_series = ta.obv(frame) if has_volume else pd.Series(dtype=float)

    snapshot = {
        "last": last,
        "prev_close": previous,
        "change": last - previous if previous else None,
        "change_pct": (last / previous - 1.0) * 100.0 if previous else None,
        "date": close.index[-1].strftime("%Y-%m-%d"),
        "open": _last(frame["Open"]),
        "high": _last(frame["High"]),
        "low": _last(frame["Low"]),
        "volume": _last(frame["Volume"]) if has_volume else None,
        "ret_5d": ta.period_return(adjusted, 5),
        "ret_1m": ta.period_return(adjusted, 21),
        "ret_3m": ta.period_return(adjusted, 63),
        "ret_6m": ta.period_return(adjusted, 126),
        "ret_1y": ta.period_return(adjusted, 252),
        "ret_ytd": ta.year_to_date_return(adjusted),
        "sma20": _last(sma20),
        "sma50": last50,
        "sma100": _last(sma100),
        "sma200": last200,
        "ema21": _last(ema21),
        "vs_sma20": (last / _last(sma20) - 1.0) * 100.0 if _last(sma20) else None,
        "vs_sma50": (last / last50 - 1.0) * 100.0 if last50 else None,
        "vs_sma200": (last / last200 - 1.0) * 100.0 if last200 else None,
        "sma50_slope": ta.period_return(sma50, 20),
        "sma200_slope": ta.period_return(sma200, 20),
        "trend": ta.trend_state(last, last50, last200),
        "last_cross": latest_cross,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "high_52w_date": high_date,
        "off_high": (last / high_52w - 1.0) * 100.0 if high_52w else None,
        "above_low": (last / low_52w - 1.0) * 100.0 if low_52w else None,
        "range_position": (last - low_52w) / (high_52w - low_52w) * 100.0 if high_52w and low_52w and high_52w > low_52w else None,
        "rsi": _last(ta.rsi(close)),
        "macd": _last(macd_lines["macd"]),
        "macd_signal": _last(macd_lines["signal"]),
        "macd_hist": _last(macd_lines["hist"]),
        "macd_cross": macd_cross,
        "adx": _last(directional["adx"]),
        "plus_di": _last(directional["plus_di"]),
        "minus_di": _last(directional["minus_di"]),
        "stoch_k": _last(stoch["k"]),
        "avg_volume_20": _last(ta.sma(frame["Volume"], 20)) if has_volume else None,
        "rvol": _last(rvol_series) if has_volume else None,
        "up_down_volume": float(up_volume / down_volume) if down_volume else None,
        "obv_trend": ("rising" if obv_series.iloc[-1] > ta.sma(obv_series, 20).iloc[-1] else "falling") if len(obv_series.dropna()) > 20 else None,
        "cmf": _last(ta.cmf(frame)) if has_volume else None,
        "mfi": _last(ta.mfi(frame)) if has_volume else None,
        "atr": _last(ta.atr(frame)),
        "atr_pct": (_last(ta.atr(frame)) / last * 100.0) if _last(ta.atr(frame)) else None,
        "hv20": _last(ta.historical_volatility(close, 20)),
        "hv60": _last(ta.historical_volatility(close, 60)),
        "bb_pct_b": _last(bands["pct_b"]),
        "bb_width": _last(bands["bandwidth"]),
        "bb_squeeze_rank": squeeze_rank,
        "spark": [float(value) for value in close.tail(30)],
        "spark_1y": [float(value) for value in window.iloc[::5]],
    }

    if benchmark is not None and not benchmark.empty:
        joined = pd.concat([adjusted, benchmark], axis=1, join="inner").dropna()
        if len(joined) > 30:
            relative = joined.iloc[:, 0] / joined.iloc[:, 1]
            average = ta.sma(relative, 50)
            snapshot["rs_1m"] = ta.period_return(relative, 21)
            snapshot["rs_3m"] = ta.period_return(relative, 63)
            snapshot["rs_above_50"] = bool(relative.iloc[-1] > average.iloc[-1]) if not pd.isna(average.iloc[-1]) else None
            returns = joined.pct_change(fill_method=None).dropna()
            snapshot["beta"] = ta.beta(returns.iloc[:, 0], returns.iloc[:, 1])
    score = ta.relative_strength_score(adjusted)
    snapshot["rs_rating"] = rs_rating_for(store, score) if store is not None else None
    return snapshot


def signal_chips(snapshot: dict, *, next_earnings: str | None = None, short_pct: float | None = None, today: dt.date | None = None) -> list[dict]:
    """Short, plain-language flags for what stands out about a stock right now."""
    chips: list[dict] = []
    cross = snapshot.get("last_cross")
    if cross and cross.get("bars_ago", 999) <= 20:
        golden = cross["type"] == "golden"
        chips.append({
            "text": f"{'Golden' if golden else 'Death'} cross {cross['bars_ago']}d ago",
            "tone": "good" if golden else "critical",
            "metric": "golden_cross",
        })
    off_high = snapshot.get("off_high")
    if off_high is not None and off_high >= -1.0:
        chips.append({"text": "At 52-week high", "tone": "good", "metric": "off_high"})
    above_low = snapshot.get("above_low")
    if above_low is not None and above_low <= 3.0:
        chips.append({"text": "Near 52-week low", "tone": "critical", "metric": "off_high"})
    rsi = snapshot.get("rsi")
    if rsi is not None and rsi >= 70:
        chips.append({"text": f"RSI overbought ({rsi:.0f})", "tone": "warning", "metric": "rsi"})
    elif rsi is not None and rsi <= 30:
        chips.append({"text": f"RSI oversold ({rsi:.0f})", "tone": "warning", "metric": "rsi"})
    rvol = snapshot.get("rvol")
    if rvol is not None and rvol >= 2.0:
        chips.append({"text": f"Volume {rvol:.1f}x normal", "tone": "neutral", "metric": "rvol"})
    macd_cross = snapshot.get("macd_cross")
    if macd_cross and macd_cross.get("bars_ago", 999) <= 3:
        bullish = macd_cross["direction"] == "bullish"
        chips.append({"text": f"MACD {'bullish' if bullish else 'bearish'} cross", "tone": "good" if bullish else "critical", "metric": "macd"})
    squeeze = snapshot.get("bb_squeeze_rank")
    if squeeze is not None and squeeze <= 10:
        chips.append({"text": "Bollinger squeeze", "tone": "neutral", "metric": "bollinger"})
    if next_earnings:
        today = today or dt.date.today()
        days = (dt.date.fromisoformat(next_earnings) - today).days
        if 0 <= days <= 14:
            chips.append({"text": "Earnings today" if days == 0 else f"Earnings in {days}d", "tone": "warning", "metric": "next_earnings"})
    if short_pct is not None and short_pct >= 0.15:
        chips.append({"text": f"Short interest {short_pct * 100:.0f}% of float", "tone": "warning", "metric": "short_interest"})
    return chips


def dark_pool_summary(frame: pd.DataFrame) -> dict | None:
    """Off-exchange short-sale share for one stock, against its own recent history."""
    if frame is None or frame.empty:
        return None
    ratio = (frame["short_volume"] / frame["total_volume"].replace(0.0, np.nan)).dropna() * 100.0
    if ratio.empty:
        return None
    average = float(ratio.tail(20).mean())
    deviation = float(ratio.tail(60).std(ddof=0)) if len(ratio) >= 20 else None
    return {
        "value": float(ratio.iloc[-1]),
        "date": ratio.index[-1].strftime("%Y-%m-%d"),
        "avg_20d": average,
        "z_score": (float(ratio.iloc[-1]) - float(ratio.tail(60).mean())) / deviation if deviation else None,
        "spark": [float(value) for value in ratio.tail(40)],
    }
