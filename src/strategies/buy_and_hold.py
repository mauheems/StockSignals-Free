from __future__ import annotations

import pandas as pd

from src.strategies.base import Strategy


class BuyAndHoldStrategy(Strategy):
    """Benchmark strategy that stays fully invested for the full backtest."""

    name = "Buy & Hold"

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        frame = data.copy()
        frame["signal"] = 1
        return frame[["signal"]]
