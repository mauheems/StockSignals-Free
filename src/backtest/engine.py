from __future__ import annotations

import numpy as np
import pandas as pd

from src.backtest.metrics import calculate_metrics
from src.backtest.portfolio import Portfolio
from src.risk.position_sizing import calculate_position_size
from src.strategies.base import Strategy


class BacktestEngine:
    """Simulates a long-only strategy that is either fully in or out of one symbol.

    Each strategy's `signal` at bar t is the position to hold during bar t, decided from
    information available at the previous close. The engine trades at bar t's open, only
    when the wanted position changes, so a held position is never resized day to day.
    """

    def __init__(self, initial_capital: float = 10000.0, commission_rate: float = 0.0005, slippage_rate: float = 0.0005):
        self.initial_capital = float(initial_capital)
        self.commission_rate = float(commission_rate)
        self.slippage_rate = float(slippage_rate)

    def _entry_shares(self, portfolio: Portfolio, price: float, max_position_fraction: float) -> int:
        if price <= 0:
            return 0
        budget = calculate_position_size(portfolio.portfolio_value, price, max_position_fraction)
        budget = min(budget, portfolio.cash)
        unit_cost = price * (1.0 + self.slippage_rate + self.commission_rate)
        return int(budget // unit_cost)

    def run(self, data: pd.DataFrame, strategy: Strategy, max_position_fraction: float = 1.0) -> dict:
        if not isinstance(strategy, Strategy):
            raise TypeError("strategy must be an instance of Strategy.")

        if "Open" not in data.columns or "Close" not in data.columns:
            raise ValueError("Data must contain Open and Close columns.")
        if data.empty:
            raise ValueError("No market data to backtest.")

        signal_frame = strategy.generate_signals(data)
        aligned = data.copy().join(signal_frame[["signal"]], how="left")
        aligned["signal"] = aligned["signal"].fillna(0).astype(int)

        portfolio = Portfolio(cash=self.initial_capital, last_price=float(aligned["Close"].iloc[0]))
        equity_curve = [portfolio.portfolio_value]
        order_history: list[dict] = []
        round_trips: list[dict] = []
        open_trade: dict | None = None
        bars_invested = 0

        opens = aligned["Open"].to_numpy(dtype=float)
        closes = aligned["Close"].to_numpy(dtype=float)
        signals = aligned["signal"].to_numpy(dtype=int)
        dates = aligned.index

        for idx in range(1, len(aligned)):
            wants_position = signals[idx] == 1
            open_price = float(opens[idx])

            if wants_position and portfolio.shares == 0:
                quantity = self._entry_shares(portfolio, open_price, max_position_fraction)
                if quantity > 0:
                    order = portfolio.buy(quantity, open_price, self.commission_rate, self.slippage_rate)
                    order["execution_price"] = float(open_price * (1.0 + self.slippage_rate))
                    order["timestamp"] = dates[idx]
                    order["signal"] = 1
                    order_history.append(order)
                    open_trade = {
                        "entry_date": dates[idx],
                        "entry_price": order["execution_price"],
                        "quantity": float(quantity),
                        "cost": order["executed_cost"],
                    }
            elif not wants_position and portfolio.shares > 0:
                order = portfolio.sell(portfolio.shares, open_price, self.commission_rate, self.slippage_rate)
                order["execution_price"] = float(open_price * (1.0 - self.slippage_rate))
                order["timestamp"] = dates[idx]
                order["signal"] = 0
                order_history.append(order)
                if open_trade is not None:
                    round_trips.append(self._close_trade(open_trade, dates[idx], order["execution_price"], order["net_proceeds"], is_open=False, index=dates))
                    open_trade = None

            portfolio.update(float(closes[idx]))
            if portfolio.shares > 0:
                bars_invested += 1
            equity_curve.append(portfolio.portfolio_value)

        if open_trade is not None:
            # A position still open at the end counts at its final market value.
            final_close = float(closes[-1])
            value = portfolio.shares * final_close
            round_trips.append(self._close_trade(open_trade, dates[-1], final_close, value, is_open=True, index=dates))

        equity_curve = np.asarray(equity_curve, dtype=float)
        trade_returns = [trade["return_pct"] / 100.0 for trade in round_trips]
        metrics = calculate_metrics(
            equity_curve,
            trade_returns=np.asarray(trade_returns, dtype=float),
            total_transaction_costs=portfolio.total_commissions + portfolio.total_slippage,
        )
        metrics["exposure"] = bars_invested / max(len(aligned) - 1, 1)

        return {
            "strategy": strategy,
            "equity_curve": equity_curve,
            "dates": dates,
            "orders": order_history,
            "trades": round_trips,
            "portfolio": portfolio,
            "initial_value": self.initial_capital,
            "final_portfolio_value": float(equity_curve[-1]),
            "trade_returns": trade_returns,
            **metrics,
        }

    @staticmethod
    def _close_trade(trade: dict, exit_date, exit_price: float, proceeds: float, *, is_open: bool, index: pd.DatetimeIndex) -> dict:
        profit = float(proceeds) - float(trade["cost"])
        bars = int(index.get_loc(exit_date) - index.get_loc(trade["entry_date"]))
        return {
            "entry_date": trade["entry_date"].strftime("%Y-%m-%d") if hasattr(trade["entry_date"], "strftime") else str(trade["entry_date"]),
            "exit_date": exit_date.strftime("%Y-%m-%d") if hasattr(exit_date, "strftime") else str(exit_date),
            "entry_price": float(trade["entry_price"]),
            "exit_price": float(exit_price),
            "quantity": float(trade["quantity"]),
            "profit": profit,
            "return_pct": profit / float(trade["cost"]) * 100.0 if trade["cost"] else 0.0,
            "bars_held": bars,
            "open": is_open,
        }
