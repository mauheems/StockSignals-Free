import pandas as pd

from src.strategies.moving_average import MovingAverageStrategy
from src.strategies.momentum import MomentumStrategy


def test_moving_average_generates_expected_signal():
    data = pd.DataFrame({
        "Close": [10, 11, 12, 11, 13, 12, 14, 15, 14],
    })

    signals = MovingAverageStrategy(fast_window=2, slow_window=3).generate_signals(data)

    assert "signal" in signals.columns
    assert signals["signal"].isin([0, 1]).all()
    assert signals["signal"].tolist() == [0, 0, 0, 1, 1, 0, 1, 0, 1]


def test_momentum_generates_expected_signal():
    data = pd.DataFrame({
        "Close": [100, 97, 94, 91, 100, 105, 110],
    })

    signals = MomentumStrategy(lookback=3).generate_signals(data)

    assert "signal" in signals.columns
    assert signals["signal"].tolist() == [0, 0, 0, 0, 0, 1, 1]


def test_strategy_does_not_use_future_data():
    data = pd.DataFrame({
        "Close": [10, 11, 12, 13, 12, 11, 10],
    })

    signals = MovingAverageStrategy(fast_window=2, slow_window=3).generate_signals(data)

    assert signals["signal"].tolist() == [0, 0, 0, 1, 1, 1, 0]
    assert signals["signal"].iloc[1] == 0
    assert signals["signal"].iloc[3] == 1
