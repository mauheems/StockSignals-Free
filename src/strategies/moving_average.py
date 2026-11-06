from __future__ import annotations

import pandas as pd

from src.strategies.base import Strategy


class MovingAverageStrategy(Strategy):
    """Long-only moving average crossover using next-day execution.

    A signal is generated from information available at the close, then executed at
    the next day's open. This prevents look-ahead bias in the trading logic. The strategy
    stays out until both averages have a full window of history behind them.
    """

    name = "Moving Average Crossover"

    def __init__(self, fast_window: int = 20, slow_window: int = 50):
        if fast_window <= 0 or slow_window <= 0:
            raise ValueError("Moving average windows must be positive integers.")
        if fast_window >= slow_window:
            raise ValueError("fast_window must be smaller than slow_window.")
        self.fast_window = fast_window
        self.slow_window = slow_window

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        frame = data.copy()
        if "Close" not in frame.columns:
            raise ValueError("Market data must include a Close column.")

        fast_ma = frame["Close"].rolling(window=self.fast_window, min_periods=self.fast_window).mean()
        slow_ma = frame["Close"].rolling(window=self.slow_window, min_periods=self.slow_window).mean()
        raw_signal = (fast_ma > slow_ma).astype(int)

        # Shift by one bar to avoid using today's close to fill a trade at the same
        # price. The signal is interpreted as the target position for the next bar.
        signal = raw_signal.shift(1).fillna(0).astype(int)

        frame["signal"] = signal
        return frame[["signal"]]
