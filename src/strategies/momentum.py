from __future__ import annotations

import pandas as pd

from src.strategies.base import Strategy


class MomentumStrategy(Strategy):
    """Simple long-only momentum benchmark using a lookback period."""

    name = "Momentum"

    def __init__(self, lookback: int = 20):
        if lookback <= 0:
            raise ValueError("lookback must be positive.")
        self.lookback = lookback

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        frame = data.copy()
        if "Close" not in frame.columns:
            raise ValueError("Market data must include a Close column.")

        momentum = frame["Close"] / frame["Close"].shift(self.lookback) - 1
        signal = (momentum > 0).astype(int).shift(1).fillna(0).astype(int)

        frame["signal"] = signal
        return frame[["signal"]]
