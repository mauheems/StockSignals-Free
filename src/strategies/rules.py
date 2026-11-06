"""Strategies built from the dashboard's signals, plus simple technical templates."""

from __future__ import annotations

import pandas as pd

from src.analytics import technicals as ta
from src.analytics.signals import Ctx
from src.analytics.studies import Condition, condition_mask
from src.strategies.base import Strategy
from src.strategies.buy_and_hold import BuyAndHoldStrategy
from src.strategies.momentum import MomentumStrategy
from src.strategies.moving_average import MovingAverageStrategy


class TrendFilterStrategy(Strategy):
    """Hold while the close is above its long moving average, otherwise stay in cash."""

    name = "Trend Filter"

    def __init__(self, window: int = 200):
        if window <= 1:
            raise ValueError("window must be greater than 1.")
        self.window = window

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        average = ta.sma(data["Close"], self.window)
        signal = (data["Close"] > average).astype(int).shift(1).fillna(0).astype(int)
        return pd.DataFrame({"signal": signal})


class RsiReversionStrategy(Strategy):
    """Buy after RSI falls below an oversold level and sell once it recovers."""

    name = "RSI Mean Reversion"

    def __init__(self, entry: float = 30.0, exit: float = 55.0, window: int = 14):
        if entry >= exit:
            raise ValueError("The RSI entry level must be below the exit level.")
        self.entry, self.exit, self.window = entry, exit, window

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        values = ta.rsi(data["Close"], self.window)
        holding = False
        decisions = []
        for value in values:
            if not holding and value < self.entry:
                holding = True
            elif holding and value > self.exit:
                holding = False
            decisions.append(1 if holding else 0)
        signal = pd.Series(decisions, index=data.index).shift(1).fillna(0).astype(int)
        return pd.DataFrame({"signal": signal})


class RuleStrategy(Strategy):
    """Be long whenever every condition held at the previous close."""

    name = "Custom Rule"

    def __init__(self, ctx: Ctx, conditions: list[Condition], name: str | None = None):
        if not conditions:
            raise ValueError("A rule needs at least one condition.")
        self.ctx = ctx
        self.conditions = conditions
        if name:
            self.name = name

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        mask = condition_mask(self.ctx, self.conditions, data.index, data)
        return pd.DataFrame({"signal": mask.astype(int).shift(1).fillna(0).astype(int)})


# Everything selectable in the Backtests page. `kind` decides how the strategy is built.
STRATEGY_TEMPLATES: list[dict] = [
    {
        "id": "buy_and_hold", "kind": "buy_and_hold", "name": "Buy & Hold", "metric": "strategy_buy_hold",
        "description": "Invest on the first day and never sell. The benchmark for everything else.",
        "params": [],
    },
    {
        "id": "ma_cross_20_50", "kind": "ma_cross", "name": "20/50 MA Crossover", "metric": "strategy_ma_cross",
        "description": "Hold while the 20-day average is above the 50-day average.",
        "params": [
            {"id": "fast_window", "label": "Fast average (days)", "value": 20, "min": 2, "max": 400},
            {"id": "slow_window", "label": "Slow average (days)", "value": 50, "min": 3, "max": 500},
        ],
    },
    {
        "id": "golden_cross", "kind": "ma_cross", "name": "Golden Cross (50/200)", "metric": "strategy_ma_cross",
        "description": "Hold while the 50-day average is above the 200-day average.",
        "params": [
            {"id": "fast_window", "label": "Fast average (days)", "value": 50, "min": 2, "max": 400},
            {"id": "slow_window", "label": "Slow average (days)", "value": 200, "min": 3, "max": 500},
        ],
    },
    {
        "id": "trend_filter_200", "kind": "trend_filter", "name": "Above 200-day average", "metric": "strategy_trend_filter",
        "description": "Hold while the close is above its 200-day average.",
        "params": [{"id": "window", "label": "Average (days)", "value": 200, "min": 2, "max": 500}],
    },
    {
        "id": "momentum_126", "kind": "momentum", "name": "6-Month Momentum", "metric": "strategy_momentum",
        "description": "Hold while the price is higher than it was six months ago.",
        "params": [{"id": "lookback", "label": "Lookback (days)", "value": 126, "min": 2, "max": 500}],
    },
    {
        "id": "rsi_reversion", "kind": "rsi_reversion", "name": "RSI Mean Reversion", "metric": "strategy_rsi",
        "description": "Buy when RSI drops below the entry level, sell when it recovers above the exit level.",
        "params": [
            {"id": "entry", "label": "Buy when RSI below", "value": 30, "min": 5, "max": 60},
            {"id": "exit", "label": "Sell when RSI above", "value": 55, "min": 20, "max": 95},
        ],
    },
    {
        "id": "vix_regime", "kind": "rule", "name": "VIX under 25", "metric": "strategy_signal_rule",
        "description": "Hold only while the VIX closes below 25.",
        "params": [], "conditions": [{"signal": "vix", "op": "<", "value": 25}],
    },
    {
        "id": "term_structure", "kind": "rule", "name": "VIX curve in contango", "metric": "strategy_signal_rule",
        "description": "Hold only while the VIX is below VIX3M, meaning no near-term panic is priced.",
        "params": [], "conditions": [{"signal": "vix_term", "op": "<", "value": 1}],
    },
    {
        "id": "breadth_filter", "kind": "rule", "name": "Breadth above 50%", "metric": "strategy_signal_rule",
        "description": "Hold while more than half of S&P 500 members are above their 200-day average.",
        "params": [], "conditions": [{"signal": "pct_above_200", "op": ">", "value": 50}],
    },
    {
        "id": "positive_gamma", "kind": "rule", "name": "Positive market gamma", "metric": "strategy_signal_rule",
        "description": "Hold while market-wide dealer gamma (SqueezeMetrics GEX) is positive.",
        "params": [], "conditions": [{"signal": "sm_gex", "op": ">", "value": 0}],
    },
    {
        "id": "market_health", "kind": "rule", "name": "Market Health above 50", "metric": "strategy_signal_rule",
        "description": "Hold while this dashboard's own Market Health score is above 50.",
        "params": [], "conditions": [{"signal": "market_health", "op": ">", "value": 50}],
    },
]


def build_strategy(spec: dict, ctx: Ctx) -> Strategy:
    """Create a strategy from an API request: a template id plus optional overrides."""
    template = next((item for item in STRATEGY_TEMPLATES if item["id"] == spec.get("id")), None)
    kind = spec.get("kind") or (template["kind"] if template else None)
    name = spec.get("name") or (template["name"] if template else None)
    params = {item["id"]: item["value"] for item in (template or {}).get("params", [])}
    for key, value in (spec.get("params") or {}).items():
        if key not in params:
            raise ValueError(f"Unknown parameter '{key}'.")
        params[key] = float(value)

    if kind == "buy_and_hold":
        strategy: Strategy = BuyAndHoldStrategy()
    elif kind == "ma_cross":
        strategy = MovingAverageStrategy(int(params["fast_window"]), int(params["slow_window"]))
    elif kind == "trend_filter":
        strategy = TrendFilterStrategy(int(params["window"]))
    elif kind == "momentum":
        strategy = MomentumStrategy(int(params["lookback"]))
    elif kind == "rsi_reversion":
        strategy = RsiReversionStrategy(float(params["entry"]), float(params["exit"]))
    elif kind == "rule":
        raw = spec.get("conditions") if spec.get("conditions") is not None else (template or {}).get("conditions", [])
        strategy = RuleStrategy(ctx, [Condition.parse(item) for item in raw], name)
    else:
        raise ValueError(f"Unknown strategy '{spec.get('id') or kind}'.")
    if name:
        strategy.name = name
    return strategy
