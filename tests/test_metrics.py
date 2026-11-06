import numpy as np

from src.backtest.metrics import calculate_metrics


def test_calculate_metrics_return_and_drawdown():
    equity_curve = np.array([1000, 1100, 900, 1200, 1050], dtype=float)

    metrics = calculate_metrics(equity_curve)

    assert metrics["total_return"] > 0
    assert metrics["max_drawdown"] < 0
    assert "sharpe_ratio" in metrics
    assert metrics["final_portfolio_value"] == 1050.0
