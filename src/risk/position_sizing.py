from __future__ import annotations


def calculate_position_size(portfolio_value: float, price: float, max_position_fraction: float = 1.0) -> float:
    """Determine the maximum dollar value allowed for a long position.

    For the MVP, the default fraction is 1.0 meaning the strategy can invest up to
    100% of portfolio value. This function is intentionally simple so future
    strategies can add 10%, 25%, 50%, or other fractions without changing the
    backtest engine.
    """
    if price <= 0:
        return 0.0
    if max_position_fraction < 0:
        raise ValueError("max_position_fraction cannot be negative.")
    max_fraction = min(max_position_fraction, 1.0)
    allowed_value = portfolio_value * max_fraction
    return max(0.0, allowed_value)
