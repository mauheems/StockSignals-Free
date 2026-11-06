"""The single registry of market signals.

Each signal is declared once here: how to compute its daily history, how to score it
(+1 supportive, 0 neutral, -1 risk) and how long the source takes to publish it. The
Overview, chart overlays, backtest rules and signal studies all read from this list,
so a number means the same thing everywhere in the app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from src.analytics import technicals as ta
from src.data.catalog import RISK_RATIOS
from src.data.store import Store

PILLARS = {
    "trend": {"label": "Trend", "page": "breadth"},
    "breadth": {"label": "Breadth", "page": "breadth"},
    "volatility": {"label": "Volatility", "page": "volatility"},
    "gamma": {"label": "Gamma & dark pool", "page": "options"},
    "credit": {"label": "Credit & macro", "page": "macro"},
    "rotation": {"label": "Rotation", "page": "rotation"},
}
CROWD_PILLAR = {"id": "crowd", "label": "Crowd positioning", "page": "flows"}


class Ctx:
    """Loads and memoizes everything derived from one version of the stored data."""

    def __init__(self, store: Store):
        self.store = store
        self.version = store.version
        self._prices: dict[str, pd.DataFrame] = {}
        self._series: dict[str, pd.Series] = {}
        self._signals: dict[str, pd.Series] = {}
        self._scores: dict[str, pd.Series] = {}
        self._composite: dict[bool, pd.DataFrame] = {}

    def prices(self, symbol: str) -> pd.DataFrame:
        if symbol not in self._prices:
            self._prices[symbol] = self.store.get_prices(symbol)
        return self._prices[symbol]

    def close(self, symbol: str) -> pd.Series:
        return self.prices(symbol)["Close"].dropna()

    def adj(self, symbol: str) -> pd.Series:
        return self.prices(symbol)["AdjClose"].dropna()

    def series(self, series_id: str) -> pd.Series:
        if series_id not in self._series:
            self._series[series_id] = self.store.get_series(series_id)
        return self._series[series_id]

    def calendar(self) -> pd.DatetimeIndex:
        spy = self.close("SPY")
        return spy.index if not spy.empty else self.close("^GSPC").index

    def signal(self, signal_id: str) -> pd.Series:
        if signal_id not in self._signals:
            self._signals[signal_id] = REGISTRY[signal_id].compute(self).dropna()
        return self._signals[signal_id]

    def score(self, signal_id: str) -> pd.Series:
        if signal_id not in self._scores:
            definition = REGISTRY[signal_id]
            values = self.signal(signal_id)
            self._scores[signal_id] = definition.score(values) if definition.score and not values.empty else pd.Series(dtype=float)
        return self._scores[signal_id]


@dataclass(frozen=True)
class Signal:
    id: str
    label: str
    group: str
    fmt: str
    compute: Callable[[Ctx], pd.Series]
    pillar: str | None = None
    score: Callable[[pd.Series], pd.Series] | None = None
    lag: int = 0            # trading days between the labelled date and publication
    ffill: int = 5          # how long a reading stays current (longer for weekly/monthly data)
    higher_is: str | None = None  # "good", "bad" or None when direction has no fixed meaning
    page: str = "overview"
    positive_only: bool = True    # False when values can be zero or negative

    @property
    def metric(self) -> str:
        return self.id


# ------------------------------------------------------------------------ score rules

def _bands(low: float, high: float, below: int, above: int) -> Callable[[pd.Series], pd.Series]:
    """Score `below` under the low threshold, `above` over the high one, 0 between."""

    def rule(values: pd.Series) -> pd.Series:
        result = pd.Series(0.0, index=values.index)
        result[values < low] = float(below)
        result[values > high] = float(above)
        return result.where(values.notna())

    return rule


def higher_better(low: float, high: float):
    return _bands(low, high, -1, 1)


def lower_better(low: float, high: float):
    return _bands(low, high, 1, -1)


def above_own_sma(window: int = 50, tolerance: float = 0.0):
    """+1 when a series sits above its own moving average, -1 when below."""

    def rule(values: pd.Series) -> pd.Series:
        distance = (values / ta.sma(values, window) - 1.0) * 100.0
        result = pd.Series(np.nan, index=values.index)
        result[distance > tolerance] = 1.0
        result[distance < -tolerance] = -1.0
        result[(distance.abs() <= tolerance) & distance.notna()] = 0.0
        return result

    return rule


def below_own_sma(window: int = 50, tolerance: float = 0.0):
    rule = above_own_sma(window, tolerance)
    # Subtracting from zero (instead of negating) keeps neutral readings at 0.0, not -0.0.
    return lambda values: 0.0 - rule(values)


def percentile_rule(window: int = 252, high: float = 70.0, low: float = 30.0):
    """Judge a reading against its own past year: top of the range +1, bottom of it -1."""

    def rule(values: pd.Series) -> pd.Series:
        rank = values.rolling(window, min_periods=60).rank(pct=True) * 100.0
        result = pd.Series(0.0, index=values.index)
        result[rank >= high] = 1.0
        result[rank <= low] = -1.0
        return result.where(rank.notna())

    return rule


def contrarian_percentile(window: int = 252, high: float = 80.0, low: float = 20.0):
    """A crowded extreme reads the other way: much put buying (fear) scores +1."""
    return percentile_rule(window, high, low)


def gex_rule(values: pd.Series) -> pd.Series:
    """Negative gamma is the documented risk state; otherwise compare with the past year."""
    rank = values.rolling(252, min_periods=60).rank(pct=True) * 100.0
    result = pd.Series(1.0, index=values.index)
    result[rank <= 30.0] = 0.0
    result[values < 0.0] = -1.0
    return result.where(rank.notna())


# ------------------------------------------------------------------- series builders

def _close(symbol: str) -> Callable[[Ctx], pd.Series]:
    return lambda ctx: ctx.close(symbol)


def _stored(series_id: str, scale: float = 1.0) -> Callable[[Ctx], pd.Series]:
    return lambda ctx: ctx.series(series_id) * scale


def _vs_sma(symbol: str, window: int) -> Callable[[Ctx], pd.Series]:
    def compute(ctx: Ctx) -> pd.Series:
        close = ctx.close(symbol)
        return (close / ta.sma(close, window) - 1.0) * 100.0

    return compute


def _ma_spread(symbol: str) -> Callable[[Ctx], pd.Series]:
    def compute(ctx: Ctx) -> pd.Series:
        close = ctx.close(symbol)
        return (ta.sma(close, 50) / ta.sma(close, 200) - 1.0) * 100.0

    return compute


def _drawdown(symbol: str) -> Callable[[Ctx], pd.Series]:
    def compute(ctx: Ctx) -> pd.Series:
        close = ctx.close(symbol)
        return (close / close.rolling(ta.TRADING_DAYS, min_periods=20).max() - 1.0) * 100.0

    return compute


def price_ratio(ctx: Ctx, numerator: str, denominator: str) -> pd.Series:
    joined = pd.concat([ctx.adj(numerator), ctx.adj(denominator)], axis=1, join="inner").dropna()
    if joined.empty:
        return pd.Series(dtype=float)
    return joined.iloc[:, 0] / joined.iloc[:, 1]


def _ratio(numerator: str, denominator: str) -> Callable[[Ctx], pd.Series]:
    return lambda ctx: price_ratio(ctx, numerator, denominator)


def _index_ratio(numerator: str, denominator: str) -> Callable[[Ctx], pd.Series]:
    def compute(ctx: Ctx) -> pd.Series:
        joined = pd.concat([ctx.close(numerator), ctx.close(denominator)], axis=1, join="inner").dropna()
        return joined.iloc[:, 0] / joined.iloc[:, 1] if not joined.empty else pd.Series(dtype=float)

    return compute


def _realized_vol(ctx: Ctx) -> pd.Series:
    return ta.historical_volatility(ctx.close("^GSPC"), 20)


def _vrp(ctx: Ctx) -> pd.Series:
    joined = pd.concat([ctx.close("^VIX"), _realized_vol(ctx)], axis=1, join="inner").dropna()
    return joined.iloc[:, 0] - joined.iloc[:, 1]


def _percentile_1y(symbol: str) -> Callable[[Ctx], pd.Series]:
    return lambda ctx: ctx.close(symbol).rolling(ta.TRADING_DAYS, min_periods=60).rank(pct=True) * 100.0


def _year_over_year(series_id: str) -> Callable[[Ctx], pd.Series]:
    return lambda ctx: ctx.series(series_id).pct_change(12, fill_method=None) * 100.0


def _net_liquidity(ctx: Ctx) -> pd.Series:
    """Fed balance sheet minus Treasury cash account minus reverse repo, in $ trillions."""
    assets = ctx.series("fred:WALCL") / 1e6            # millions
    treasury = ctx.series("fred:WTREGEN") / 1e6        # millions
    reverse_repo = ctx.series("fred:RRPONTSYD") / 1e3  # billions
    if assets.empty:
        return pd.Series(dtype=float)
    frame = pd.concat([assets, treasury, reverse_repo], axis=1).sort_index().ffill()
    return (frame.iloc[:, 0] - frame.iloc[:, 1] - frame.iloc[:, 2]).reindex(assets.index)


def _risk_on_share(ctx: Ctx) -> pd.Series:
    parts = []
    for ratio in RISK_RATIOS:
        series = price_ratio(ctx, ratio["a"], ratio["b"])
        if series.empty:
            continue
        average = ta.sma(series, 50)
        parts.append((series > average).astype(float).where(average.notna()))
    if not parts:
        return pd.Series(dtype=float)
    frame = pd.concat(parts, axis=1).reindex(ctx.calendar()).ffill(limit=3)
    return frame.mean(axis=1) * 100.0


def _pc_average(series_id: str, window: int) -> Callable[[Ctx], pd.Series]:
    return lambda ctx: ta.sma(ctx.series(series_id), window)


def _composite_series(column: str) -> Callable[[Ctx], pd.Series]:
    return lambda ctx: pillar_history(ctx, lagged=True).get(column, pd.Series(dtype=float))


def _ratio_signal(ratio: dict, score=None, pillar: str | None = "rotation") -> Signal:
    # Copper/gold is a small number, so it needs more digits to show any movement.
    return Signal(
        ratio["id"], ratio["label"], "Rotation", "num4" if ratio["id"] == "copper_gold" else "num3", _ratio(ratio["a"], ratio["b"]),
        pillar=pillar, score=score or (above_own_sma(50) if pillar else None), higher_is="good", page="rotation",
    )


_SCORED_RATIOS = {"xly_xlp", "iwm_spy", "smh_spy", "sphb_splv", "kre_spy", "iwf_iwd", "eem_spy", "copper_gold"}

SIGNALS: list[Signal] = [
    # Trend
    Signal("spx", "S&P 500", "Trend", "num0", _close("^GSPC"), higher_is="good", page="breadth"),
    Signal("spx_vs_50", "S&P 500 vs 50-day average", "Trend", "spct1", _vs_sma("^GSPC", 50), "trend", higher_better(-0.5, 0.5), higher_is="good", page="breadth", positive_only=False),
    Signal("spx_vs_200", "S&P 500 vs 200-day average", "Trend", "spct1", _vs_sma("^GSPC", 200), "trend", higher_better(-1.0, 1.0), higher_is="good", page="breadth", positive_only=False),
    Signal("spx_ma_spread", "50-day vs 200-day average", "Trend", "spct1", _ma_spread("^GSPC"), "trend", higher_better(0.0, 0.0), higher_is="good", page="breadth", positive_only=False),
    Signal("spx_drawdown", "S&P 500 drawdown from 52-week high", "Trend", "spct1", _drawdown("^GSPC"), "trend", higher_better(-10.0, -5.0), higher_is="good", page="breadth", positive_only=False),
    Signal("soxx", "SOXX semiconductors ETF", "Trend", "usd2", _close("SOXX"), score=above_own_sma(50), higher_is="good", page="stock/SOXX"),
    Signal("ndx_vs_200", "Nasdaq 100 vs 200-day average", "Trend", "spct1", _vs_sma("^NDX", 200), "trend", higher_better(-1.0, 1.0), higher_is="good", page="breadth", positive_only=False),
    Signal("rut_vs_200", "Russell 2000 vs 200-day average", "Trend", "spct1", _vs_sma("^RUT", 200), "trend", higher_better(-1.0, 1.0), higher_is="good", page="breadth", positive_only=False),

    # Breadth
    Signal("pct_above_20", "Stocks above 20-day average", "Breadth", "pct0", _stored("breadth:pct20"), higher_is="good", page="breadth"),
    Signal("pct_above_50", "Stocks above 50-day average", "Breadth", "pct0", _stored("breadth:pct50"), "breadth", higher_better(40.0, 60.0), higher_is="good", page="breadth"),
    Signal("pct_above_200", "Stocks above 200-day average", "Breadth", "pct0", _stored("breadth:pct200"), "breadth", higher_better(40.0, 60.0), higher_is="good", page="breadth"),
    Signal("mcclellan", "McClellan Oscillator", "Breadth", "num0", _stored("breadth:mcclellan"), "breadth", higher_better(-25.0, 25.0), higher_is="good", page="breadth", positive_only=False),
    Signal("mcclellan_sum", "McClellan Summation", "Breadth", "num0", _stored("breadth:mcclellan_sum"), higher_is="good", page="breadth", positive_only=False),
    Signal("ad_line", "Advance/Decline line", "Breadth", "num0", _stored("breadth:ad_line"), higher_is="good", page="breadth", positive_only=False),
    Signal("net_new_highs", "Net new 52-week highs", "Breadth", "snum0", _stored("breadth:net_highs"), higher_is="good", page="breadth", positive_only=False),
    Signal("net_highs_10d", "Net new highs, 10-day average", "Breadth", "snum1", _stored("breadth:net_highs_10d"), "breadth", higher_better(-5.0, 5.0), higher_is="good", page="breadth", positive_only=False),
    Signal("hl_index", "High-Low Index", "Breadth", "pct0", _stored("breadth:hl_index"), higher_is="good", page="breadth"),
    Signal("up_vol_share", "Share of volume in rising stocks", "Breadth", "pct0", _stored("breadth:up_vol_share"), higher_is="good", page="breadth"),
    Signal("up_vol_10d", "Up-volume share, 10-day average", "Breadth", "pct0", _stored("breadth:up_vol_10d"), "breadth", higher_better(45.0, 55.0), higher_is="good", page="breadth"),
    Signal("rsp_spy", "Equal weight vs cap weight (RSP/SPY)", "Breadth", "num3", _ratio("RSP", "SPY"), "breadth", above_own_sma(50), higher_is="good", page="breadth"),

    # Volatility
    Signal("vix", "VIX", "Volatility", "num1", _close("^VIX"), "volatility", lower_better(18.0, 25.0), higher_is="bad", page="volatility"),
    Signal("vix9d", "VIX9D (9-day)", "Volatility", "num1", _close("^VIX9D"), higher_is="bad", page="volatility"),
    Signal("vix3m", "VIX3M (3-month)", "Volatility", "num1", _close("^VIX3M"), higher_is="bad", page="volatility"),
    Signal("vix_term", "VIX / VIX3M term structure", "Volatility", "num2", _index_ratio("^VIX", "^VIX3M"), "volatility", lower_better(0.90, 1.00), higher_is="bad", page="volatility"),
    Signal("vix9d_ratio", "VIX9D / VIX", "Volatility", "num2", _index_ratio("^VIX9D", "^VIX"), higher_is="bad", page="volatility"),
    Signal("vvix", "VVIX", "Volatility", "num0", _close("^VVIX"), "volatility", lower_better(95.0, 115.0), higher_is="bad", page="volatility"),
    Signal("skew", "Cboe SKEW Index", "Volatility", "num0", _close("^SKEW"), page="volatility"),
    Signal("move", "MOVE (bond volatility)", "Volatility", "num0", _close("^MOVE"), "volatility", lower_better(100.0, 130.0), higher_is="bad", page="volatility"),
    Signal("rv20", "S&P 500 realized volatility (20-day)", "Volatility", "num1", _realized_vol, higher_is="bad", page="volatility"),
    Signal("vrp", "Volatility risk premium", "Volatility", "snum1", _vrp, "volatility", higher_better(0.0, 3.0), higher_is="good", page="volatility", positive_only=False),
    Signal("vix_pctile", "VIX 1-year percentile", "Volatility", "pct0", _percentile_1y("^VIX"), higher_is="bad", page="volatility"),

    # Options, gamma and dark pool flows
    Signal("sm_gex", "Market gamma exposure (GEX)", "Options & flows", "usdbn", _stored("sm:gex"), "gamma", gex_rule, lag=1, higher_is="good", page="flows", positive_only=False),
    Signal("dix", "Dark Index (DIX)", "Options & flows", "pct1", _stored("sm:dix"), "gamma", percentile_rule(), lag=1, higher_is="good", page="flows"),
    Signal("spx_net_gex", "SPX dealer gamma (this app's estimate)", "Options & flows", "usdbn", _stored("opt:SPX:net_gex", 1e-9), "gamma", higher_better(0.0, 0.0), higher_is="good", page="options", positive_only=False),
    Signal("spx_flip_dist", "SPX distance from gamma flip", "Options & flows", "spct1", _stored("opt:SPX:flip_distance_pct"), higher_is="good", page="options", positive_only=False),
    Signal("pc_total", "Total put/call ratio", "Options & flows", "num2", _stored("cboe:pc_total"), page="flows"),
    Signal("pc_equity", "Equity put/call ratio", "Options & flows", "num2", _stored("cboe:pc_equity"), page="flows"),
    Signal("pc_index", "Index put/call ratio", "Options & flows", "num2", _stored("cboe:pc_index"), page="flows"),
    Signal("pc_equity_10d", "Equity put/call, 10-day average", "Options & flows", "num2", _pc_average("cboe:pc_equity", 10), "crowd", contrarian_percentile(), page="flows"),

    # Credit and macro
    Signal("hy_oas", "High-yield credit spread", "Credit & macro", "pct2", _stored("fred:BAMLH0A0HYM2"), "credit", lower_better(3.5, 5.0), lag=1, higher_is="bad", page="macro"),
    Signal("hy_oas_chg", "High-yield spread, 3-month change", "Credit & macro", "snum2", lambda ctx: ctx.series("fred:BAMLH0A0HYM2").diff(63), "credit", lower_better(-0.25, 0.75), lag=1, higher_is="bad", page="macro", positive_only=False),
    Signal("ig_oas", "Investment-grade credit spread", "Credit & macro", "pct2", _stored("fred:BAMLC0A0CM"), lag=1, higher_is="bad", page="macro"),
    Signal("ccc_oas", "CCC credit spread", "Credit & macro", "pct2", _stored("fred:BAMLH0A3HYC"), lag=1, higher_is="bad", page="macro"),
    Signal("t10y2y", "Yield curve: 10-year minus 2-year", "Credit & macro", "spct2", _stored("fred:T10Y2Y"), lag=1, higher_is="good", page="macro", positive_only=False),
    Signal("t10y3m", "Yield curve: 10-year minus 3-month", "Credit & macro", "spct2", _stored("fred:T10Y3M"), "credit", higher_better(0.0, 0.5), lag=1, higher_is="good", page="macro", positive_only=False),
    Signal("dgs10", "10-year Treasury yield", "Credit & macro", "pct2", _stored("fred:DGS10"), lag=1, page="macro"),
    Signal("dgs2", "2-year Treasury yield", "Credit & macro", "pct2", _stored("fred:DGS2"), lag=1, page="macro"),
    Signal("real10y", "10-year real yield", "Credit & macro", "pct2", _stored("fred:DFII10"), lag=1, higher_is="bad", page="macro", positive_only=False),
    Signal("breakeven10y", "10-year breakeven inflation", "Credit & macro", "pct2", _stored("fred:T10YIE"), lag=1, page="macro"),
    Signal("fed_funds", "Effective fed funds rate", "Credit & macro", "pct2", _stored("fred:DFF"), lag=1, page="macro"),
    Signal("nfci", "Chicago Fed financial conditions", "Credit & macro", "num2", _stored("fred:NFCI"), "credit", lower_better(-0.3, 0.0), lag=4, ffill=10, higher_is="bad", page="macro", positive_only=False),
    Signal("stlfsi", "St. Louis Fed stress index", "Credit & macro", "num2", _stored("fred:STLFSI4"), lag=4, ffill=10, higher_is="bad", page="macro", positive_only=False),
    Signal("net_liquidity", "Net Fed liquidity", "Credit & macro", "usdtn", _net_liquidity, lag=1, ffill=10, higher_is="good", page="macro"),
    Signal("dxy", "US Dollar Index", "Credit & macro", "num1", _close("DX-Y.NYB"), "credit", below_own_sma(50, 1.0), page="macro"),
    Signal("oil", "WTI crude oil", "Credit & macro", "usd2", _close("CL=F"), page="macro"),
    Signal("gold", "Gold", "Credit & macro", "usd0", _close("GC=F"), page="macro"),
    Signal("copper", "Copper", "Credit & macro", "usd2", _close("HG=F"), page="macro"),
    Signal("bitcoin", "Bitcoin", "Credit & macro", "usd0", _close("BTC-USD"), page="macro"),
    Signal("claims", "Initial jobless claims", "Credit & macro", "num0", _stored("fred:ICSA"), lag=5, ffill=10, higher_is="bad", page="macro"),
    Signal("unrate", "Unemployment rate", "Credit & macro", "pct1", _stored("fred:UNRATE"), lag=30, ffill=45, higher_is="bad", page="macro"),
    Signal("sahm", "Sahm recession indicator", "Credit & macro", "num2", _stored("fred:SAHMREALTIME"), "credit", lower_better(0.3, 0.5), lag=30, ffill=45, higher_is="bad", page="macro", positive_only=False),
    Signal("cpi_yoy", "CPI inflation (year over year)", "Credit & macro", "pct1", _year_over_year("fred:CPIAUCSL"), lag=40, ffill=45, higher_is="bad", page="macro"),
    Signal("core_cpi_yoy", "Core CPI inflation (year over year)", "Credit & macro", "pct1", _year_over_year("fred:CPILFESL"), lag=40, ffill=45, higher_is="bad", page="macro"),
    Signal("core_pce_yoy", "Core PCE inflation (year over year)", "Credit & macro", "pct1", _year_over_year("fred:PCEPILFE"), lag=55, ffill=45, higher_is="bad", page="macro"),

    # Rotation ratios
    *[_ratio_signal(item, pillar="rotation" if item["id"] in _SCORED_RATIOS else None) for item in RISK_RATIOS if item["id"] != "hyg_ief"],
    _ratio_signal(next(item for item in RISK_RATIOS if item["id"] == "hyg_ief"), pillar="credit"),
    Signal("risk_on_share", "Risk-on ratios above their 50-day average", "Rotation", "pct0", _risk_on_share, higher_is="good", page="rotation"),

    # Sentiment and positioning (read as contrarian)
    Signal("fear_greed", "CNN Fear & Greed Index", "Sentiment", "num0", _stored("cnn:fg"), "crowd", lower_better(25.0, 75.0), page="flows"),
    Signal("aaii_spread", "AAII bulls minus bears", "Sentiment", "snum1", _stored("aaii:spread"), "crowd", lower_better(-20.0, 20.0), ffill=10, page="flows", positive_only=False),
    Signal("aaii_bull", "AAII bullish share", "Sentiment", "pct1", _stored("aaii:bull"), ffill=10, page="flows"),
    Signal("aaii_bear", "AAII bearish share", "Sentiment", "pct1", _stored("aaii:bear"), ffill=10, page="flows"),
    Signal("cot_am_net", "Asset managers' S&P futures net position", "Sentiment", "num0", _stored("cot:es:asset_mgr"), lag=3, ffill=10, page="flows", positive_only=False),
    Signal("cot_lev_net", "Leveraged funds' S&P futures net position", "Sentiment", "num0", _stored("cot:es:lev_funds"), lag=3, ffill=10, page="flows", positive_only=False),

    # Composite readings (lagged, so they are safe to use in backtests)
    Signal("market_health", "Market Health score", "Composite", "num0", _composite_series("health"), higher_is="good", page="overview", lag=0),
    Signal("crowd_fear", "Crowd Fear gauge", "Composite", "num0", _composite_series("crowd"), page="flows", lag=0),
]

REGISTRY: dict[str, Signal] = {item.id: item for item in SIGNALS}
SIGNAL_GROUPS = ["Composite", "Trend", "Breadth", "Volatility", "Options & flows", "Rotation", "Credit & macro", "Sentiment"]

# Technical readings of whichever symbol is being tested; rules refer to them as "self:<id>".
SELF_SIGNALS: dict[str, dict] = {
    "close_vs_sma20": {"label": "Price vs its 20-day average (%)", "metric": "vs_sma"},
    "close_vs_sma50": {"label": "Price vs its 50-day average (%)", "metric": "vs_sma"},
    "close_vs_sma200": {"label": "Price vs its 200-day average (%)", "metric": "vs_sma"},
    "sma50_vs_sma200": {"label": "50-day vs 200-day average (%)", "metric": "golden_cross"},
    "rsi14": {"label": "RSI (14)", "metric": "rsi"},
    "macd_hist": {"label": "MACD histogram", "metric": "macd"},
    "adx14": {"label": "ADX (14)", "metric": "adx"},
    "rvol20": {"label": "Relative volume (20-day)", "metric": "rvol"},
    "cmf20": {"label": "Chaikin Money Flow (20-day)", "metric": "cmf"},
    "atr_pct": {"label": "ATR as % of price", "metric": "atr"},
    "hv20": {"label": "Realized volatility (20-day)", "metric": "hv"},
    "ret_21": {"label": "1-month return (%)", "metric": "period_returns"},
    "ret_63": {"label": "3-month return (%)", "metric": "period_returns"},
    "ret_126": {"label": "6-month return (%)", "metric": "period_returns"},
    "drawdown_52w": {"label": "Drawdown from 52-week high (%)", "metric": "off_high"},
}


def self_signal(signal_id: str, frame: pd.DataFrame) -> pd.Series:
    """Compute one technical reading on a symbol's own OHLCV data."""
    close = frame["Close"]
    if signal_id.startswith("close_vs_sma"):
        window = int(signal_id.removeprefix("close_vs_sma"))
        return (close / ta.sma(close, window) - 1.0) * 100.0
    if signal_id == "sma50_vs_sma200":
        return (ta.sma(close, 50) / ta.sma(close, 200) - 1.0) * 100.0
    if signal_id == "rsi14":
        return ta.rsi(close)
    if signal_id == "macd_hist":
        return ta.macd(close)["hist"]
    if signal_id == "adx14":
        return ta.adx(frame)["adx"]
    if signal_id == "rvol20":
        return ta.relative_volume(frame["Volume"])
    if signal_id == "cmf20":
        return ta.cmf(frame)
    if signal_id == "atr_pct":
        return ta.atr(frame) / close * 100.0
    if signal_id == "hv20":
        return ta.historical_volatility(close)
    if signal_id.startswith("ret_"):
        bars = int(signal_id.removeprefix("ret_"))
        return close.pct_change(bars, fill_method=None) * 100.0
    if signal_id == "drawdown_52w":
        return (close / close.rolling(ta.TRADING_DAYS, min_periods=20).max() - 1.0) * 100.0
    raise KeyError(signal_id)


def research_series(ctx: Ctx, signal_id: str, index: pd.DatetimeIndex, frame: pd.DataFrame | None = None) -> pd.Series:
    """A signal aligned to given dates as it was actually known on each of those dates."""
    if signal_id.startswith("self:"):
        if frame is None:
            raise KeyError(signal_id)
        return self_signal(signal_id.removeprefix("self:"), frame).reindex(index)
    definition = REGISTRY[signal_id]
    values = ctx.signal(signal_id)
    if values.empty:
        return pd.Series(np.nan, index=index)
    calendar = ctx.calendar().union(index)
    aligned = values.reindex(values.index.union(calendar)).ffill(limit=definition.ffill).reindex(calendar)
    if definition.lag:
        aligned = aligned.shift(definition.lag)
    return aligned.reindex(index)


# --------------------------------------------------------------------------- composite

def _aligned_scores(ctx: Ctx, lagged: bool) -> dict[str, pd.Series]:
    """Each scored signal's score on every trading date, held forward while still current."""
    calendar = ctx.calendar()
    result: dict[str, pd.Series] = {}
    for definition in SIGNALS:
        if definition.score is None or definition.pillar is None:
            continue
        scores = ctx.score(definition.id).dropna()
        if scores.empty:
            continue
        aligned = scores.reindex(scores.index.union(calendar)).ffill(limit=definition.ffill).reindex(calendar)
        if lagged and definition.lag:
            aligned = aligned.shift(definition.lag)
        result[definition.id] = aligned
    return result


def pillar_history(ctx: Ctx, lagged: bool = False) -> pd.DataFrame:
    """Daily 0-100 score per pillar, the overall Market Health and the Crowd gauge."""
    if lagged in ctx._composite:
        return ctx._composite[lagged]
    scores = _aligned_scores(ctx, lagged)
    pillars: dict[str, pd.Series] = {}
    for pillar in [*PILLARS, CROWD_PILLAR["id"]]:
        members = [series for name, series in scores.items() if REGISTRY[name].pillar == pillar]
        if members:
            pillars[pillar] = (pd.concat(members, axis=1).mean(axis=1) + 1.0) * 50.0
    frame = pd.DataFrame(pillars)
    main = [name for name in PILLARS if name in frame]
    if main:
        # Require most pillars, otherwise the score would swing as sources come and go.
        filled = frame[main].notna().sum(axis=1)
        frame["health"] = frame[main].mean(axis=1).where(filled >= max(3, len(main) - 2))
    if CROWD_PILLAR["id"] in frame:
        frame["crowd"] = frame[CROWD_PILLAR["id"]]
    ctx._composite[lagged] = frame
    return frame


def health_label(score: float | None) -> dict:
    if score is None or pd.isna(score):
        return {"label": "Not enough data", "tone": "neutral"}
    if score >= 60:
        return {"label": "Healthy", "tone": "good"}
    if score >= 45:
        return {"label": "Mixed", "tone": "neutral"}
    if score >= 30:
        return {"label": "Cautious", "tone": "warning"}
    return {"label": "Risk-off", "tone": "critical"}


def crowd_label(score: float | None) -> dict:
    if score is None or pd.isna(score):
        return {"label": "Not enough data", "tone": "neutral"}
    if score >= 65:
        return {"label": "Crowd is fearful", "tone": "neutral"}
    if score <= 35:
        return {"label": "Crowd is greedy", "tone": "neutral"}
    return {"label": "Crowd is balanced", "tone": "neutral"}


def signal_row(ctx: Ctx, signal_id: str, spark_points: int = 60) -> dict | None:
    """Current reading of a signal for tables: value, change, score, sparkline, freshness."""
    definition = REGISTRY[signal_id]
    values = ctx.signal(signal_id)
    if values.empty:
        return None
    score_series = ctx.score(signal_id).dropna() if definition.score else pd.Series(dtype=float)
    score = float(score_series.iloc[-1]) if not score_series.empty else None
    latest_date = values.index[-1]
    earlier = values[values.index <= latest_date - pd.Timedelta(days=7)]
    previous = earlier.iloc[-1] if not earlier.empty else None
    window = values[values.index >= latest_date - pd.Timedelta(days=365)]
    return {
        "id": signal_id,
        "label": definition.label,
        "group": definition.group,
        "pillar": definition.pillar,
        "fmt": definition.fmt,
        "value": float(values.iloc[-1]),
        "date": values.index[-1].strftime("%Y-%m-%d"),
        "change_1w": float(values.iloc[-1] - previous) if previous is not None else None,
        # A rank against only a few readings means nothing, so it is left out until there are enough.
        "pctile_1y": float((window <= values.iloc[-1]).mean() * 100.0) if len(window) >= 20 else None,
        "score": score,
        "higher_is": definition.higher_is,
        "page": definition.page,
        "metric": definition.metric,
        "spark": [float(value) for value in values.tail(spark_points)],
    }


def recent_flips(ctx: Ctx, sessions: int = 15, limit: int = 14) -> list[dict]:
    """Scored signals whose state changed recently, newest first."""
    calendar = ctx.calendar()
    if len(calendar) < sessions + 2:
        return []
    cutoff = calendar[-sessions]
    flips: list[dict] = []
    for definition in SIGNALS:
        if definition.score is None:
            continue
        scores = ctx.score(definition.id).dropna()
        if len(scores) < 2:
            continue
        changes = scores[scores != scores.shift(1)].iloc[1:]
        changes = changes[changes.index >= cutoff]
        if changes.empty:
            continue
        date = changes.index[-1]
        position = scores.index.get_loc(date)
        values = ctx.signal(definition.id)
        flips.append({
            "id": definition.id,
            "label": definition.label,
            "pillar": definition.pillar,
            "date": date.strftime("%Y-%m-%d"),
            "from": float(scores.iloc[position - 1]),
            "to": float(scores.iloc[position]),
            "value": float(values.reindex([date]).iloc[0]) if date in values.index else float(values.iloc[-1]),
            "fmt": definition.fmt,
            "metric": definition.metric,
            "page": definition.page,
            "contrarian": definition.pillar == CROWD_PILLAR["id"],
        })
    flips.sort(key=lambda item: item["date"], reverse=True)
    return flips[:limit]
