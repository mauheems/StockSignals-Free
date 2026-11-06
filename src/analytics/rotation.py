"""Where money is rotating: relative performance, a rotation map and risk-appetite ratios.

The rotation map follows the idea behind Relative Rotation Graphs (relative strength
against relative momentum). It is an open approximation; the exact commercial RRG
formula is proprietary, so values will differ slightly from that product.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.analytics import technicals as ta

PERIODS = {"1d": 1, "5d": 5, "1m": 21, "3m": 63, "6m": 126, "1y": 252}


def _zscore(series: pd.Series, window: int) -> pd.Series:
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std(ddof=0).replace(0.0, np.nan)
    return (series - mean) / std


def rotation_lines(price: pd.Series, benchmark: pd.Series, window: int = 14) -> pd.DataFrame:
    """Relative strength level and its momentum, both centred on 100."""
    joined = pd.concat([price, benchmark], axis=1, join="inner").dropna()
    relative = joined.iloc[:, 0] / joined.iloc[:, 1] * 100.0
    strength = 100.0 + _zscore(relative, window)
    momentum = 100.0 + _zscore(strength.diff(), window)
    return pd.DataFrame({"rs_ratio": strength, "rs_momentum": momentum}).dropna()


def quadrant(rs_ratio: float, rs_momentum: float) -> str:
    if rs_ratio >= 100.0:
        return "Leading" if rs_momentum >= 100.0 else "Weakening"
    return "Improving" if rs_momentum >= 100.0 else "Lagging"


def weekly(series: pd.Series) -> pd.Series:
    return series.resample("W-FRI").last().dropna()


def rotation_map(
    prices: dict[str, pd.Series],
    benchmark: pd.Series,
    *,
    lens: str = "weekly",
    tail: int = 8,
) -> list[dict]:
    """Current position and recent trail on the rotation map for each symbol."""
    window = 14 if lens == "weekly" else 20
    base = weekly(benchmark) if lens == "weekly" else benchmark
    output: list[dict] = []
    for symbol, series in prices.items():
        sampled = weekly(series) if lens == "weekly" else series
        lines = rotation_lines(sampled, base, window)
        if lines.empty:
            continue
        trail = lines.tail(tail + 1)
        latest = trail.iloc[-1]
        output.append({
            "symbol": symbol,
            "rs_ratio": float(latest["rs_ratio"]),
            "rs_momentum": float(latest["rs_momentum"]),
            "quadrant": quadrant(float(latest["rs_ratio"]), float(latest["rs_momentum"])),
            "trail": [
                {"date": stamp.strftime("%Y-%m-%d"), "x": float(row.rs_ratio), "y": float(row.rs_momentum)}
                for stamp, row in zip(trail.index, trail.itertuples())
            ],
        })
    return output


def performance_row(series: pd.Series, benchmark: pd.Series | None = None) -> dict:
    row: dict[str, float | None] = {}
    for key, bars in PERIODS.items():
        value = ta.period_return(series, bars)
        row[f"ret_{key}"] = value
        if benchmark is not None:
            base = ta.period_return(benchmark, bars)
            row[f"rel_{key}"] = value - base if value is not None and base is not None else None
    ytd = ta.year_to_date_return(series)
    row["ret_ytd"] = ytd
    if benchmark is not None:
        base = ta.year_to_date_return(benchmark)
        row["rel_ytd"] = ytd - base if ytd is not None and base is not None else None
    return row


def ratio_state(ratio: pd.Series, spark_points: int = 60) -> dict | None:
    """Summarise a risk-appetite ratio: is it above its own 50-day average, and rising?"""
    clean = ratio.dropna()
    if len(clean) < 60:
        return None
    average = ta.sma(clean, 50)
    last, last_average = float(clean.iloc[-1]), float(average.iloc[-1])
    above = last > last_average
    tail = clean.tail(ta.TRADING_DAYS)
    rank = float((tail <= last).mean() * 100.0)
    return {
        "value": last,
        "sma50": last_average,
        "vs_sma50": (last / last_average - 1.0) * 100.0,
        "state": "risk_on" if above else "risk_off",
        "chg_1w": ta.period_return(clean, 5),
        "chg_1m": ta.period_return(clean, 21),
        "chg_3m": ta.period_return(clean, 63),
        "pctile_1y": rank,
        "spark": [float(value) for value in clean.tail(spark_points)],
    }
