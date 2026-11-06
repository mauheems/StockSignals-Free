"""Testing a signal honestly: what happened to prices after it fired, versus any other day."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from src.analytics.signals import REGISTRY, SELF_SIGNALS, Ctx, research_series

OPERATORS = {
    ">": lambda series, value: series > value,
    "<": lambda series, value: series < value,
    ">=": lambda series, value: series >= value,
    "<=": lambda series, value: series <= value,
}
HORIZONS = {"1 week": 5, "2 weeks": 10, "1 month": 21, "3 months": 63, "6 months": 126, "1 year": 252}


@dataclass(frozen=True)
class Condition:
    signal: str
    op: str
    value: float

    @staticmethod
    def parse(raw: dict) -> "Condition":
        signal, op = str(raw.get("signal", "")), str(raw.get("op", ""))
        if op not in OPERATORS:
            raise ValueError(f"Unknown operator '{op}'.")
        if not (signal in REGISTRY or (signal.startswith("self:") and signal.removeprefix("self:") in SELF_SIGNALS)):
            raise ValueError(f"Unknown signal '{signal}'.")
        try:
            value = float(raw.get("value"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"The value for '{signal}' must be a number.") from exc
        if not np.isfinite(value):
            raise ValueError(f"The value for '{signal}' must be a number.")
        return Condition(signal, op, value)

    def label(self) -> str:
        if self.signal.startswith("self:"):
            name = SELF_SIGNALS[self.signal.removeprefix("self:")]["label"]
        else:
            name = REGISTRY[self.signal].label
        return f"{name} {self.op} {self.value:g}"


def condition_mask(ctx: Ctx, conditions: Iterable[Condition], index: pd.DatetimeIndex, frame: pd.DataFrame | None = None) -> pd.Series:
    """True on dates when every condition held, based only on what was known on that date."""
    mask = pd.Series(True, index=index)
    for condition in conditions:
        values = research_series(ctx, condition.signal, index, frame)
        # A missing reading can never satisfy a rule: comparisons with NaN are False.
        mask &= OPERATORS[condition.op](values, condition.value).fillna(False)
    return mask


def condition_coverage(ctx: Ctx, conditions: Iterable[Condition], index: pd.DatetimeIndex, frame: pd.DataFrame | None = None) -> str | None:
    """First date on which all signals in a rule have data."""
    start = None
    for condition in conditions:
        values = research_series(ctx, condition.signal, index, frame).dropna()
        if values.empty:
            return None
        first = values.index[0]
        start = first if start is None or first > start else start
    return start.strftime("%Y-%m-%d") if start is not None else None


def select_events(mask: pd.Series, cooldown: int = 0) -> pd.DatetimeIndex:
    """Days a condition held; with a cooldown, keep only the first day of each cluster."""
    days = np.flatnonzero(mask.values)
    if cooldown <= 0:
        return mask.index[days]
    kept: list[int] = []
    next_allowed = -1
    for day in days:
        if day >= next_allowed:
            kept.append(int(day))
        # Each day the condition still holds extends the cluster, so one long episode
        # counts as a single event instead of dozens of nearly identical ones.
        next_allowed = int(day) + cooldown
    return mask.index[kept]


def _stats(returns: pd.Series) -> dict:
    clean = returns.dropna()
    if clean.empty:
        return {"count": 0, "mean": None, "median": None, "win_rate": None, "worst": None, "best": None}
    return {
        "count": int(clean.size),
        "mean": float(clean.mean()),
        "median": float(clean.median()),
        "win_rate": float((clean > 0).mean() * 100.0),
        "worst": float(clean.min()),
        "best": float(clean.max()),
    }


def forward_return_study(
    prices: pd.Series,
    mask: pd.Series,
    *,
    cooldown: int = 10,
    horizons: dict[str, int] = HORIZONS,
) -> dict:
    """Compare returns after a condition with returns after all days in the same period."""
    prices = prices.dropna()
    mask = mask.reindex(prices.index).fillna(False).astype(bool)
    events = select_events(mask, cooldown)
    rows = []
    for label, bars in horizons.items():
        forward = (prices.shift(-bars) / prices - 1.0) * 100.0
        signal_stats = _stats(forward.reindex(events))
        baseline_stats = _stats(forward)
        edge = None
        if signal_stats["mean"] is not None and baseline_stats["mean"] is not None:
            edge = signal_stats["mean"] - baseline_stats["mean"]
        rows.append({"horizon": label, "bars": bars, "signal": signal_stats, "baseline": baseline_stats, "edge": edge})
    return {
        "events": [stamp.strftime("%Y-%m-%d") for stamp in events],
        "event_count": int(len(events)),
        "days_true": int(mask.sum()),
        "days_total": int(len(mask)),
        "pct_time_true": float(mask.mean() * 100.0) if len(mask) else 0.0,
        "horizons": rows,
    }


def spans_from_mask(mask: pd.Series) -> list[dict]:
    """Turn a True/False series into start and end dates, for shading on a chart."""
    spans: list[dict] = []
    start = None
    previous = None
    for stamp, value in mask.items():
        if value and start is None:
            start = stamp
        if not value and start is not None:
            spans.append({"start": start.strftime("%Y-%m-%d"), "end": previous.strftime("%Y-%m-%d")})
            start = None
        previous = stamp
    if start is not None and previous is not None:
        spans.append({"start": start.strftime("%Y-%m-%d"), "end": previous.strftime("%Y-%m-%d")})
    return spans
