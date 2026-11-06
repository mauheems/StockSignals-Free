from __future__ import annotations

import numpy as np


def calculate_metrics(
    equity_curve: np.ndarray,
    trade_returns: np.ndarray | list | None = None,
    total_transaction_costs: float = 0.0,
    risk_free_rate: float = 0.0,
) -> dict:
    """Compute key performance and risk measures for an equity curve."""
    values = np.asarray(equity_curve, dtype=float)
    if values.size == 0:
        return {
            "total_return": 0.0,
            "cagr": 0.0,
            "volatility": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "calmar_ratio": 0.0,
            "max_drawdown": 0.0,
            "number_of_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "average_trade_return": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "total_transaction_costs": float(total_transaction_costs),
            "final_portfolio_value": 0.0,
        }

    initial_value = float(values[0])
    final_value = float(values[-1])
    total_return = ((final_value / initial_value) - 1.0) if initial_value else 0.0

    sortino = 0.0
    if len(values) > 1:
        yearly_periods = max(len(values) - 1, 1)
        cagr = ((final_value / initial_value) ** (1.0 / (yearly_periods / 252.0)) - 1.0) if initial_value and final_value > 0 else 0.0
        daily_returns = np.diff(values) / values[:-1]
        daily_returns = daily_returns[~np.isnan(daily_returns)]
        volatility = float(daily_returns.std(ddof=1) * np.sqrt(252)) if len(daily_returns) > 1 else 0.0
        annual_excess = (daily_returns.mean() * 252.0) - risk_free_rate if len(daily_returns) else 0.0
        sharpe = annual_excess / volatility if volatility > 0 else 0.0
        # Downside deviation counts only losing days, against all days in the sample.
        downside = np.minimum(daily_returns, 0.0)
        downside_deviation = float(np.sqrt(np.mean(downside ** 2)) * np.sqrt(252)) if len(daily_returns) else 0.0
        sortino = annual_excess / downside_deviation if downside_deviation > 0 else 0.0
    else:
        cagr = 0.0
        volatility = 0.0
        sharpe = 0.0

    running_max = np.maximum.accumulate(values)
    drawdown = (values - running_max) / running_max
    max_drawdown = float(drawdown.min()) if drawdown.size else 0.0
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else 0.0

    if trade_returns is None:
        trade_returns = []
    trade_returns = np.asarray(trade_returns, dtype=float)
    num_trades = int(trade_returns.size)
    win_rate = 0.0
    profit_factor = 0.0
    average_trade_return = 0.0
    best_trade = 0.0
    worst_trade = 0.0

    if num_trades > 0:
        profits = trade_returns[trade_returns > 0]
        losses = trade_returns[trade_returns < 0]
        win_rate = float(np.mean(trade_returns > 0))
        profit_factor = float(np.sum(profits) / abs(np.sum(losses))) if np.sum(losses) < 0 else float("inf")
        average_trade_return = float(np.mean(trade_returns))
        best_trade = float(np.max(trade_returns))
        worst_trade = float(np.min(trade_returns))

    return {
        "total_return": float(total_return),
        "cagr": float(cagr),
        "volatility": float(volatility),
        "sharpe_ratio": float(sharpe),
        "sortino_ratio": float(sortino),
        "calmar_ratio": float(calmar),
        "max_drawdown": float(max_drawdown),
        "number_of_trades": int(num_trades),
        "win_rate": float(win_rate),
        "profit_factor": float(profit_factor),
        "average_trade_return": float(average_trade_return),
        "best_trade": float(best_trade),
        "worst_trade": float(worst_trade),
        "total_transaction_costs": float(total_transaction_costs),
        "final_portfolio_value": float(final_value),
    }
