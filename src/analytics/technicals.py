"""Standard technical indicators, written so every formula can be read and tested."""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False, min_periods=window).mean()


def wilder(series: pd.Series, window: int) -> pd.Series:
    """Wilder's smoothing, the moving average behind RSI, ATR and ADX."""
    return series.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    change = close.diff()
    average_gain = wilder(change.clip(lower=0.0), window)
    average_loss = wilder(-change.clip(upper=0.0), window)
    strength = average_gain / average_loss.replace(0.0, np.nan)
    result = 100.0 - 100.0 / (1.0 + strength)
    result = result.where(average_loss != 0.0, 100.0)
    result = result.where(~((average_loss == 0.0) & (average_gain == 0.0)), 50.0)
    return result.where(average_gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    trigger = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": trigger, "hist": line - trigger})


def bollinger(close: pd.Series, window: int = 20, width: float = 2.0) -> pd.DataFrame:
    middle = sma(close, window)
    deviation = close.rolling(window, min_periods=window).std(ddof=0)
    upper, lower = middle + width * deviation, middle - width * deviation
    span = (upper - lower).replace(0.0, np.nan)
    return pd.DataFrame({
        "upper": upper,
        "middle": middle,
        "lower": lower,
        "pct_b": (close - lower) / span,
        "bandwidth": (upper - lower) / middle * 100.0,
    })


def true_range(frame: pd.DataFrame) -> pd.Series:
    previous = frame["Close"].shift(1)
    return pd.concat(
        [frame["High"] - frame["Low"], (frame["High"] - previous).abs(), (frame["Low"] - previous).abs()],
        axis=1,
    ).max(axis=1)


def atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    return wilder(true_range(frame), window)


def adx(frame: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    up_move = frame["High"].diff()
    down_move = -frame["Low"].diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0.0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0.0), 0.0)
    range_avg = wilder(true_range(frame), window).replace(0.0, np.nan)
    plus_di = 100.0 * wilder(plus_dm, window) / range_avg
    minus_di = 100.0 * wilder(minus_dm, window) / range_avg
    total = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / total
    return pd.DataFrame({"adx": wilder(dx, window), "plus_di": plus_di, "minus_di": minus_di})


def obv(frame: pd.DataFrame) -> pd.Series:
    direction = np.sign(frame["Close"].diff()).fillna(0.0)
    return (direction * frame["Volume"].fillna(0.0)).cumsum()


def _money_flow_multiplier(frame: pd.DataFrame) -> pd.Series:
    span = (frame["High"] - frame["Low"]).replace(0.0, np.nan)
    return (((frame["Close"] - frame["Low"]) - (frame["High"] - frame["Close"])) / span).fillna(0.0)


def accumulation_distribution(frame: pd.DataFrame) -> pd.Series:
    return (_money_flow_multiplier(frame) * frame["Volume"].fillna(0.0)).cumsum()


def cmf(frame: pd.DataFrame, window: int = 20) -> pd.Series:
    money_flow = _money_flow_multiplier(frame) * frame["Volume"].fillna(0.0)
    volume = frame["Volume"].rolling(window, min_periods=window).sum().replace(0.0, np.nan)
    return money_flow.rolling(window, min_periods=window).sum() / volume


def mfi(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    typical = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
    flow = typical * frame["Volume"].fillna(0.0)
    change = typical.diff()
    positive = flow.where(change > 0.0, 0.0).rolling(window, min_periods=window).sum()
    negative = flow.where(change < 0.0, 0.0).rolling(window, min_periods=window).sum()
    ratio = positive / negative.replace(0.0, np.nan)
    result = 100.0 - 100.0 / (1.0 + ratio)
    return result.where(negative != 0.0, 100.0).where(positive.notna())


def stochastic(frame: pd.DataFrame, window: int = 14, smooth: int = 3) -> pd.DataFrame:
    highest = frame["High"].rolling(window, min_periods=window).max()
    lowest = frame["Low"].rolling(window, min_periods=window).min()
    k = 100.0 * (frame["Close"] - lowest) / (highest - lowest).replace(0.0, np.nan)
    return pd.DataFrame({"k": k, "d": k.rolling(smooth, min_periods=smooth).mean()})


def relative_volume(volume: pd.Series, window: int = 20) -> pd.Series:
    """Volume against the average of the previous days (today is not in its own average)."""
    average = volume.shift(1).rolling(window, min_periods=window).mean().replace(0.0, np.nan)
    return volume / average


def historical_volatility(close: pd.Series, window: int = 20) -> pd.Series:
    returns = np.log(close / close.shift(1))
    return returns.rolling(window, min_periods=window).std(ddof=1) * np.sqrt(TRADING_DAYS) * 100.0


def anchored_vwap(frame: pd.DataFrame, anchor) -> pd.Series:
    """Average traded price, weighted by volume, from an anchor date onward."""
    typical = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
    mask = frame.index >= pd.Timestamp(anchor)
    weighted = (typical * frame["Volume"]).where(mask).cumsum()
    volume = frame["Volume"].where(mask).cumsum().replace(0.0, np.nan)
    return weighted / volume


def session_vwap(frame: pd.DataFrame) -> pd.Series:
    """Intraday VWAP that restarts at each session."""
    typical = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
    sessions = frame.index.normalize()
    weighted = (typical * frame["Volume"]).groupby(sessions).cumsum()
    volume = frame["Volume"].groupby(sessions).cumsum().replace(0.0, np.nan)
    return weighted / volume


def cross_events(fast: pd.Series, slow: pd.Series) -> list[dict]:
    """Dates on which the fast average crossed the slow one ('golden' up, 'death' down)."""
    spread = (fast - slow).dropna()
    if spread.empty:
        return []
    state = np.sign(spread)
    state = state.where(state != 0.0).ffill().dropna()
    flips = state[state != state.shift(1)].iloc[1:]
    return [
        {"date": stamp.strftime("%Y-%m-%d"), "type": "golden" if value > 0 else "death"}
        for stamp, value in flips.items()
    ]


def trend_state(close: float, sma50: float | None, sma200: float | None) -> dict:
    """Name the setup formed by price and its 50- and 200-day averages."""
    if sma50 is None or sma200 is None or np.isnan(sma50) or np.isnan(sma200):
        return {"id": "unknown", "label": "Not enough history", "tone": "neutral"}
    if close > sma50 and sma50 > sma200:
        return {"id": "uptrend", "label": "Uptrend", "tone": "good"}
    if close > sma50 and close > sma200:
        return {"id": "recovery", "label": "Recovery", "tone": "warning"}
    if close > sma200:
        return {"id": "pullback", "label": "Pullback", "tone": "warning"}
    if close > sma50:
        return {"id": "bear_rally", "label": "Bear rally", "tone": "serious"}
    if sma50 > sma200:
        return {"id": "breakdown", "label": "Breakdown", "tone": "serious"}
    return {"id": "downtrend", "label": "Downtrend", "tone": "critical"}


def period_return(series: pd.Series, bars: int) -> float | None:
    clean = series.dropna()
    if len(clean) <= bars:
        return None
    return float(clean.iloc[-1] / clean.iloc[-1 - bars] - 1.0) * 100.0


def year_to_date_return(series: pd.Series) -> float | None:
    clean = series.dropna()
    if clean.empty:
        return None
    last = clean.index[-1]
    previous_year = clean[clean.index.year < last.year]
    if previous_year.empty:
        return None
    return float(clean.iloc[-1] / previous_year.iloc[-1] - 1.0) * 100.0


def beta(returns: pd.Series, benchmark_returns: pd.Series, window: int = TRADING_DAYS) -> float | None:
    joined = pd.concat([returns, benchmark_returns], axis=1, join="inner").dropna().tail(window)
    if len(joined) < 60:
        return None
    variance = joined.iloc[:, 1].var()
    if not variance:
        return None
    return float(joined.iloc[:, 0].cov(joined.iloc[:, 1]) / variance)


def relative_strength_score(adj_close: pd.Series) -> float | None:
    """Performance score behind the RS rating: last 3 months counted double."""
    parts = []
    for bars, weight in ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2)):
        value = period_return(adj_close, bars)
        if value is None:
            return None
        parts.append(value * weight)
    return float(sum(parts))


def indicator_frame(frame: pd.DataFrame, *, intraday: bool = False) -> pd.DataFrame:
    """Every chart overlay and pane for one symbol, computed on the full history."""
    close = frame["Close"]
    out = pd.DataFrame(index=frame.index)
    for window in (20, 50, 100, 200):
        out[f"sma{window}"] = sma(close, window)
    for window in (8, 21):
        out[f"ema{window}"] = ema(close, window)
    bands = bollinger(close)
    out["bb_upper"], out["bb_middle"], out["bb_lower"] = bands["upper"], bands["middle"], bands["lower"]
    out["bb_width"] = bands["bandwidth"]
    out["rsi"] = rsi(close)
    lines = macd(close)
    out["macd"], out["macd_signal"], out["macd_hist"] = lines["macd"], lines["signal"], lines["hist"]
    has_volume = frame["Volume"].fillna(0.0).sum() > 0
    if has_volume:
        out["obv"] = obv(frame)
        out["ad_line"] = accumulation_distribution(frame)
        out["cmf"] = cmf(frame)
        out["mfi"] = mfi(frame)
        out["rvol"] = relative_volume(frame["Volume"])
    out["atr"] = atr(frame)
    out["atr_pct"] = out["atr"] / close * 100.0
    directional = adx(frame)
    out["adx"], out["plus_di"], out["minus_di"] = directional["adx"], directional["plus_di"], directional["minus_di"]
    stoch = stochastic(frame)
    out["stoch_k"], out["stoch_d"] = stoch["k"], stoch["d"]
    out["hv20"] = historical_volatility(close, 20)
    out["high_52w"] = frame["High"].rolling(TRADING_DAYS, min_periods=20).max()
    out["low_52w"] = frame["Low"].rolling(TRADING_DAYS, min_periods=20).min()
    if has_volume:
        if intraday:
            out["vwap"] = session_vwap(frame)
        else:
            anchor = pd.Timestamp(year=frame.index[-1].year, month=1, day=1)
            out["vwap"] = anchored_vwap(frame, anchor)
    return out
