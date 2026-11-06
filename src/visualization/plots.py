from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def _drawdown_series(equity_curve: np.ndarray) -> np.ndarray:
    values = np.asarray(equity_curve, dtype=float)
    if values.size == 0:
        return np.array([], dtype=float)
    running_max = np.maximum.accumulate(values)
    return (values - running_max) / running_max


def generate_backtest_plots(
    data: pd.DataFrame,
    strategy_equity: np.ndarray,
    benchmark_equity: np.ndarray,
    orders: list[dict] | None,
    output_dir: str | Path,
) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    equity_path = output_dir / "equity_curve.png"
    drawdown_path = output_dir / "drawdown.png"
    trades_path = output_dir / "trades.png"

    plt.figure(figsize=(10, 6))
    plt.plot(data.index, strategy_equity, label="Strategy")
    plt.plot(data.index, benchmark_equity, label="Buy & Hold", linestyle="--")
    plt.title("Equity Curve")
    plt.xlabel("Date")
    plt.ylabel("Portfolio Value")
    plt.legend()
    plt.tight_layout()
    plt.savefig(equity_path)
    plt.close()

    drawdown = _drawdown_series(strategy_equity)
    plt.figure(figsize=(10, 4))
    plt.plot(data.index, drawdown * 100.0, color="tab:red")
    plt.axhline(0, color="black", linewidth=0.8)
    plt.title("Drawdown (%)")
    plt.xlabel("Date")
    plt.ylabel("Drawdown")
    plt.tight_layout()
    plt.savefig(drawdown_path)
    plt.close()

    plt.figure(figsize=(12, 5))
    plt.plot(data.index, data["Close"], label="Close")
    if orders:
        buys = [order for order in orders if order.get("side") == "BUY"]
        sells = [order for order in orders if order.get("side") == "SELL"]
        for order in buys:
            plt.scatter(order["timestamp"], order.get("execution_price", order["price"]), marker="^", color="green", s=40, label="Buy" if not buys.index(order) else "")
        for order in sells:
            plt.scatter(order["timestamp"], order.get("execution_price", order["price"]), marker="v", color="red", s=40, label="Sell" if not sells.index(order) else "")
    plt.title("Price and Trade Execution")
    plt.xlabel("Date")
    plt.ylabel("Price")
    plt.legend()
    plt.tight_layout()
    plt.savefig(trades_path)
    plt.close()

    return {
        "equity_curve": equity_path,
        "drawdown": drawdown_path,
        "trades": trades_path,
    }
