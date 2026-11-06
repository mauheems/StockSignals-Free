import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine
from src.strategies.base import Strategy
from src.strategies.buy_and_hold import BuyAndHoldStrategy
from src.strategies.moving_average import MovingAverageStrategy
from src.strategies.rules import RsiReversionStrategy, TrendFilterStrategy


class FixedSignal(Strategy):
    """Uses exactly the positions given, so timing can be checked by hand."""

    name = "Fixed"

    def __init__(self, signals):
        self.signals = signals

    def generate_signals(self, data):
        return pd.DataFrame({"signal": self.signals}, index=data.index)


def market(opens, closes=None):
    closes = closes if closes is not None else opens
    return pd.DataFrame(
        {"Open": opens, "High": opens, "Low": opens, "Close": closes, "Volume": [1000] * len(opens)},
        index=pd.date_range("2024-01-01", periods=len(opens), freq="B"),
        dtype=float,
    )


def test_backtest_increases_value_with_rising_prices():
    data = pd.DataFrame(
        {
            "Open": [100, 100, 100, 100],
            "High": [100, 100, 100, 100],
            "Low": [100, 100, 100, 100],
            "Close": [100, 110, 120, 130],
            "Volume": [1000, 1000, 1000, 1000],
        },
        index=pd.date_range("2024-01-01", periods=4, freq="D"),
    )

    engine = BacktestEngine(initial_capital=1000, commission_rate=0.0, slippage_rate=0.0)
    result = engine.run(data, BuyAndHoldStrategy())

    assert result["final_portfolio_value"] > 1000
    assert result["total_return"] > 0
    assert result["number_of_trades"] >= 1


def test_a_signal_trades_at_the_open_of_its_own_bar_with_no_extra_delay():
    # A strategy says "hold during bar 2". The fill must be bar 2's open: 110, not bar 3's 120.
    data = market([100, 105, 110, 120, 130])
    engine = BacktestEngine(1100, commission_rate=0.0, slippage_rate=0.0)
    result = engine.run(data, FixedSignal([0, 0, 1, 1, 1]))
    assert result["orders"][0]["price"] == 110
    assert result["orders"][0]["timestamp"] == data.index[2]


def test_moving_average_strategy_buys_the_open_after_the_signal_close():
    closes = [10.0, 10.0, 10.0, 10.0, 20.0, 20.0, 20.0, 20.0]
    data = market(closes)
    strategy = MovingAverageStrategy(fast_window=2, slow_window=3)
    result = BacktestEngine(1000, 0.0, 0.0).run(data, strategy)
    # Fast average first exceeds the slow one at the close of bar 4, so the buy is at bar 5's open.
    assert result["orders"][0]["timestamp"] == data.index[5]


def test_a_held_position_is_not_resized_every_day():
    data = market(list(np.linspace(100, 200, 50)))
    result = BacktestEngine(10000, 0.0005, 0.0005).run(data, BuyAndHoldStrategy())
    assert len(result["orders"]) == 1
    assert result["number_of_trades"] == 1


def test_round_trip_accounting_by_hand():
    data = market([100, 100, 100, 110, 110], closes=[100, 100, 105, 110, 110])
    engine = BacktestEngine(1000, commission_rate=0.0, slippage_rate=0.0)
    result = engine.run(data, FixedSignal([0, 1, 1, 0, 0]))
    trade = result["trades"][0]
    assert trade["entry_price"] == 100
    assert trade["exit_price"] == 110
    assert trade["quantity"] == 10
    assert trade["profit"] == pytest.approx(100.0)
    assert trade["return_pct"] == pytest.approx(10.0)
    assert trade["bars_held"] == 2
    assert trade["open"] is False
    assert result["final_portfolio_value"] == pytest.approx(1100.0)
    assert result["win_rate"] == pytest.approx(1.0)


def test_costs_reduce_results_by_the_expected_amount():
    data = market([100, 100, 100, 100])
    engine = BacktestEngine(1000, commission_rate=0.001, slippage_rate=0.001)
    result = engine.run(data, FixedSignal([0, 1, 0, 0]))
    # 9 shares fit after costs. Each side pays 0.2% of $900 in costs.
    assert result["orders"][0]["quantity"] == 9
    assert result["total_transaction_costs"] == pytest.approx(3.6)
    assert result["final_portfolio_value"] == pytest.approx(1000 - 3.6)


def test_a_position_still_open_at_the_end_is_marked_to_market():
    data = market([100, 100, 100], closes=[100, 100, 150])
    result = BacktestEngine(1000, 0.0, 0.0).run(data, FixedSignal([0, 1, 1]))
    trade = result["trades"][-1]
    assert trade["open"] is True
    assert trade["return_pct"] == pytest.approx(50.0)
    assert result["number_of_trades"] == 1


def test_exposure_counts_days_invested():
    data = market([100] * 5)
    result = BacktestEngine(1000, 0.0, 0.0).run(data, FixedSignal([0, 1, 1, 0, 0]))
    assert result["exposure"] == pytest.approx(0.5)


def test_never_invested_strategy_keeps_its_cash():
    data = market([100, 120, 90, 150])
    result = BacktestEngine(1000, 0.001, 0.001).run(data, FixedSignal([0, 0, 0, 0]))
    assert result["final_portfolio_value"] == 1000
    assert result["number_of_trades"] == 0
    assert result["total_transaction_costs"] == 0


def test_trend_filter_holds_only_above_its_average():
    data = market(list(np.arange(1.0, 11.0)) + list(np.arange(10.0, 0.0, -1.0)))
    signals = TrendFilterStrategy(window=3).generate_signals(data)["signal"]
    assert signals.iloc[5] == 1
    assert signals.iloc[-1] == 0


def test_rsi_reversion_stays_in_until_the_exit_level():
    closes = [100.0] + [100.0 - i * 2 for i in range(1, 20)] + [62.0 + i * 3 for i in range(1, 25)]
    data = market(closes)
    signals = RsiReversionStrategy(entry=30, exit=55).generate_signals(data)["signal"]
    assert signals.max() == 1
    assert signals.iloc[0] == 0
    assert signals.iloc[-1] == 0


def test_rsi_reversion_rejects_impossible_levels():
    with pytest.raises(ValueError):
        RsiReversionStrategy(entry=60, exit=50)
