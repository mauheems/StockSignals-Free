from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Portfolio:
    """Long-only portfolio tracking cash, shares, and costs."""

    cash: float
    shares: float = 0.0
    last_price: float = 0.0
    total_commissions: float = 0.0
    total_slippage: float = 0.0
    average_cost_basis: float = 0.0
    cost_basis_shares: float = 0.0
    orders: list[dict] = field(default_factory=list)

    def update(self, price: float) -> None:
        self.last_price = float(price)

    @property
    def position_value(self) -> float:
        return self.shares * self.last_price

    @property
    def portfolio_value(self) -> float:
        return self.cash + self.position_value

    def buy(self, quantity: float, price: float, commission_rate: float = 0.0, slippage_rate: float = 0.0) -> dict:
        if quantity <= 0:
            raise ValueError("Buy quantity must be positive.")
        if self.cash < 0:
            raise ValueError("Cash cannot be negative.")

        notional = quantity * price
        slippage = notional * slippage_rate
        commission = notional * commission_rate
        cost = notional + slippage + commission

        if cost > self.cash + 1e-9:
            raise ValueError("Buy order exceeds available cash.")

        self.cash -= cost
        self.shares += quantity
        self.total_commissions += commission
        self.total_slippage += slippage

        total_basis = self.average_cost_basis * self.cost_basis_shares + cost
        self.cost_basis_shares += quantity
        self.average_cost_basis = (total_basis / self.cost_basis_shares) if self.cost_basis_shares else 0.0
        self.update(price)

        order = {
            "side": "BUY",
            "quantity": float(quantity),
            "price": float(price),
            "commission": float(commission),
            "slippage": float(slippage),
            "notional": float(notional),
            "executed_cost": float(cost),
        }
        self.orders.append(order)
        return order

    def sell(self, quantity: float, price: float, commission_rate: float = 0.0, slippage_rate: float = 0.0) -> dict:
        if quantity <= 0:
            raise ValueError("Sell quantity must be positive.")
        if quantity > self.shares + 1e-9:
            raise ValueError("Cannot sell more shares than currently owned.")

        notional = quantity * price
        slippage = notional * slippage_rate
        commission = notional * commission_rate
        gross_proceeds = notional
        net_proceeds = gross_proceeds - slippage - commission

        cost_basis = self.average_cost_basis * min(quantity, self.cost_basis_shares)
        realized_pnl = net_proceeds - cost_basis
        trade_return = realized_pnl / cost_basis if cost_basis else 0.0

        self.cash += net_proceeds
        self.shares -= quantity
        self.total_commissions += commission
        self.total_slippage += slippage

        remaining_basis = self.average_cost_basis * self.cost_basis_shares - cost_basis
        self.cost_basis_shares -= quantity
        self.average_cost_basis = (remaining_basis / self.cost_basis_shares) if self.cost_basis_shares else 0.0
        self.update(price)

        order = {
            "side": "SELL",
            "quantity": float(quantity),
            "price": float(price),
            "commission": float(commission),
            "slippage": float(slippage),
            "notional": float(notional),
            "net_proceeds": float(net_proceeds),
            "realized_pnl": float(realized_pnl),
            "trade_return": float(trade_return),
        }
        self.orders.append(order)
        return order
