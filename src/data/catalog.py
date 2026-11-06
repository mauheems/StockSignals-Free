"""The fixed market universe the dashboard tracks, and how each symbol is labelled."""

from __future__ import annotations

import re

INDICES = {
    "^GSPC": "S&P 500",
    "^NDX": "Nasdaq 100",
    "^RUT": "Russell 2000",
    "^DJI": "Dow Jones Industrial",
    "^SPXEW": "S&P 500 Equal Weight",
}

BROAD_ETFS = {
    "SPY": "S&P 500 ETF",
    "QQQ": "Nasdaq 100 ETF",
    "IWM": "Russell 2000 ETF",
    "DIA": "Dow Jones ETF",
    "RSP": "S&P 500 Equal Weight ETF",
}

SECTORS = {
    "XLK": "Technology",
    "XLC": "Communication Services",
    "XLY": "Consumer Discretionary",
    "XLF": "Financials",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
}

# Maps each sector ETF to the GICS sector name used by the S&P 500 member list.
SECTOR_TO_GICS = {
    "XLK": "Information Technology",
    "XLC": "Communication Services",
    "XLY": "Consumer Discretionary",
    "XLF": "Financials",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
}

FACTORS = {
    "MTUM": "Momentum",
    "QUAL": "Quality",
    "VLUE": "Value",
    "SIZE": "Size",
    "USMV": "Minimum Volatility",
    "IWF": "Large Cap Growth",
    "IWD": "Large Cap Value",
    "SPHB": "High Beta",
    "SPLV": "Low Volatility",
}

THEMES = {
    "SMH": "Semiconductors",
    "IGV": "Software",
    "KRE": "Regional Banks",
    "XBI": "Biotech",
    "XHB": "Homebuilders",
    "XRT": "Retail",
    "ITA": "Aerospace & Defense",
    "XOP": "Oil & Gas Producers",
    "GDX": "Gold Miners",
    "ARKK": "Disruptive Innovation",
    "KWEB": "China Internet",
    "TAN": "Solar",
}

CROSS_ASSET = {
    "SPY": "US Large Caps",
    "QQQ": "US Tech & Growth",
    "IWM": "US Small Caps",
    "EFA": "Developed Markets ex-US",
    "EEM": "Emerging Markets",
    "TLT": "Long-Term Treasuries",
    "IEF": "7-10 Year Treasuries",
    "HYG": "High-Yield Credit",
    "LQD": "Investment-Grade Credit",
    "GLD": "Gold",
    "DBC": "Broad Commodities",
    "UUP": "US Dollar",
}

VOLATILITY = {
    "^VIX": "VIX (30-day S&P 500 implied volatility)",
    "^VIX9D": "VIX9D (9-day)",
    "^VIX3M": "VIX3M (3-month)",
    "^VIX6M": "VIX6M (6-month)",
    "^VIX1Y": "VIX1Y (1-year)",
    "^VVIX": "VVIX (volatility of VIX)",
    "^SKEW": "Cboe SKEW Index",
    "^MOVE": "MOVE (Treasury volatility)",
    "^VXN": "VXN (Nasdaq 100 volatility)",
    "^OVX": "OVX (oil volatility)",
    "^GVZ": "GVZ (gold volatility)",
}

MACRO_MARKETS = {
    "^TNX": "10-Year Treasury Yield",
    "^IRX": "13-Week Treasury Bill",
    "^FVX": "5-Year Treasury Yield",
    "^TYX": "30-Year Treasury Yield",
    "DX-Y.NYB": "US Dollar Index",
    "CL=F": "WTI Crude Oil",
    "GC=F": "Gold",
    "HG=F": "Copper",
    "NG=F": "Natural Gas",
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ether",
}

# Tracked for the signals table only, so they don't add a near-duplicate to the rotation map.
KEY_ETFS = {"SOXX": "Semiconductors ETF (SOXX)"}

# Everything refreshed on the fast core schedule, with deep history for research.
CORE_GROUPS = [INDICES, BROAD_ETFS, SECTORS, FACTORS, THEMES, CROSS_ASSET, VOLATILITY, MACRO_MARKETS, KEY_ETFS]
CORE_HISTORY_START = "2000-01-01"
BREADTH_HISTORY_START = "2020-01-01"

CORE_NAMES: dict[str, str] = {}
for _group in CORE_GROUPS:
    for _symbol, _name in _group.items():
        CORE_NAMES.setdefault(_symbol, _name)
CORE_SYMBOLS = list(CORE_NAMES)

ROTATION_UNIVERSES = {
    "sectors": {"label": "Sectors", "symbols": SECTORS},
    "factors": {"label": "Factors & styles", "symbols": FACTORS},
    "themes": {"label": "Industry themes", "symbols": THEMES},
    "cross_asset": {"label": "Cross-asset", "symbols": {k: v for k, v in CROSS_ASSET.items() if k != "SPY"}},
}

# Pairs read as "risk appetite": when the first leg outperforms the second, investors
# are reaching for risk. Every ratio is oriented so that rising means risk-on.
RISK_RATIOS = [
    {"id": "xly_xlp", "a": "XLY", "b": "XLP", "label": "Discretionary vs Staples"},
    {"id": "iwm_spy", "a": "IWM", "b": "SPY", "label": "Small caps vs Large caps"},
    {"id": "smh_spy", "a": "SMH", "b": "SPY", "label": "Semiconductors vs S&P 500"},
    {"id": "sphb_splv", "a": "SPHB", "b": "SPLV", "label": "High beta vs Low volatility"},
    {"id": "kre_spy", "a": "KRE", "b": "SPY", "label": "Regional banks vs S&P 500"},
    {"id": "iwf_iwd", "a": "IWF", "b": "IWD", "label": "Growth vs Value"},
    {"id": "eem_spy", "a": "EEM", "b": "SPY", "label": "Emerging markets vs S&P 500"},
    {"id": "copper_gold", "a": "HG=F", "b": "GC=F", "label": "Copper vs Gold"},
    {"id": "hyg_ief", "a": "HYG", "b": "IEF", "label": "High yield vs Treasuries"},
    {"id": "spy_tlt", "a": "SPY", "b": "TLT", "label": "Stocks vs Long bonds"},
]

# Underlyings kept fresh by the background option refresh.
OPTION_INDEX_SYMBOLS = {"SPX": "_SPX", "NDX": "_NDX", "RUT": "_RUT", "VIX": "_VIX", "DJX": "_DJX", "XSP": "_XSP"}
OPTION_YAHOO_ALIASES = {"^GSPC": "SPX", "^SPX": "SPX", "^NDX": "NDX", "^RUT": "RUT", "^VIX": "VIX", "^DJI": "DJX"}
OPTION_PRICE_SYMBOL = {"SPX": "^GSPC", "NDX": "^NDX", "RUT": "^RUT", "VIX": "^VIX", "DJX": "^DJI", "XSP": "^GSPC"}
OPTION_CORE = ["SPX", "SPY", "QQQ", "IWM"]

_SYMBOL_PATTERN = re.compile(r"^\^?[A-Z0-9][A-Z0-9.\-=]{0,11}$")


def normalize_symbol(symbol: str) -> str:
    """Uppercase and validate a user-supplied ticker; raises ValueError on anything odd."""
    cleaned = (symbol or "").strip().upper().replace("/", "-")
    # Tickers such as BRK.B are written with a dash on Yahoo Finance.
    if re.fullmatch(r"[A-Z]{1,5}\.[A-Z]", cleaned):
        cleaned = cleaned.replace(".", "-")
    if not _SYMBOL_PATTERN.match(cleaned):
        raise ValueError(f"'{symbol}' is not a valid ticker symbol.")
    return cleaned


def display_name(symbol: str) -> str | None:
    return CORE_NAMES.get(symbol)
