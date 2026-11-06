from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    """Base abstraction for all market strategies.

    A strategy only produces a signal series, used for historical backtests. The
    `signal` at a given bar is the position to hold during that bar (1 = long, 0 = out),
    decided using nothing later than the previous bar's close.
    """

    name: str = "Base Strategy"

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """Return a dataframe containing at least a `signal` column."""
