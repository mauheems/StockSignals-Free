"""Per-stock details from Yahoo Finance: valuation, short interest, earnings, insiders."""

from __future__ import annotations

import datetime as dt
import logging
import math
import threading

import pandas as pd
import yfinance as yf

from src.data.store import Store

logger = logging.getLogger(__name__)

FUNDAMENTALS_MAX_AGE = 12 * 3600

_INFO_FIELDS = [
    "shortName", "longName", "quoteType", "sector", "industry", "currency", "exchange",
    "marketCap", "enterpriseValue", "beta",
    "trailingPE", "forwardPE", "pegRatio", "trailingPegRatio", "priceToSalesTrailing12Months", "priceToBook",
    "enterpriseToEbitda", "revenueGrowth", "earningsGrowth", "grossMargins", "operatingMargins", "profitMargins",
    "returnOnEquity", "freeCashflow", "debtToEquity", "dividendYield", "trailingAnnualDividendYield",
    "shortPercentOfFloat", "shortRatio", "sharesShort", "sharesShortPriorMonth", "dateShortInterest", "floatShares",
    "heldPercentInsiders", "heldPercentInstitutions",
    "targetMeanPrice", "targetHighPrice", "targetLowPrice", "recommendationKey", "recommendationMean",
    "numberOfAnalystOpinions", "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "exDividendDate", "longBusinessSummary",
]

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(symbol: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(symbol, threading.Lock())


def _plain(value):
    """Reduce numpy and pandas scalars to JSON-safe Python values."""
    # Yahoo uses pandas' missing markers for fields it lacks, such as foreign firms' insider sales.
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if hasattr(value, "item"):
        return _plain(value.item())
    return value


def _earnings_dates(ticker: yf.Ticker) -> tuple[str | None, list[dict]]:
    today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    next_date, history = None, []
    try:
        table = ticker.earnings_dates
    except Exception as exc:
        logger.debug("earnings_dates failed: %s", exc)
        table = None
    if table is not None and not table.empty:
        for stamp, row in table.sort_index().iterrows():
            day = pd.Timestamp(stamp).tz_localize(None) if pd.Timestamp(stamp).tzinfo else pd.Timestamp(stamp)
            day = day.normalize()
            if day >= today:
                if next_date is None:
                    next_date = day.strftime("%Y-%m-%d")
                continue
            reported = row.get("Reported EPS")
            if pd.notna(reported):
                history.append({
                    "date": day.strftime("%Y-%m-%d"),
                    "estimate": _plain(row.get("EPS Estimate")),
                    "reported": _plain(reported),
                    "surprise_pct": _plain(row.get("Surprise(%)")),
                })
    if next_date is None:
        try:
            calendar = ticker.calendar
            dates = calendar.get("Earnings Date") if isinstance(calendar, dict) else None
            upcoming = [pd.Timestamp(item) for item in dates or [] if pd.Timestamp(item) >= today]
            if upcoming:
                next_date = min(upcoming).strftime("%Y-%m-%d")
        except Exception as exc:
            logger.debug("calendar failed: %s", exc)
    return next_date, history[-12:][::-1]


def _insiders(ticker: yf.Ticker) -> dict | None:
    try:
        table = ticker.insider_purchases
    except Exception as exc:
        logger.debug("insider_purchases failed: %s", exc)
        return None
    if table is None or table.empty:
        return None
    label_column = table.columns[0]
    lookup = {str(row[label_column]): row for _, row in table.iterrows()}

    def shares(key: str):
        row = lookup.get(key)
        return _plain(row["Shares"]) if row is not None else None

    def trades(key: str):
        row = lookup.get(key)
        return _plain(row["Trans"]) if row is not None and pd.notna(row["Trans"]) else None

    return {
        "bought_shares": shares("Purchases"),
        "bought_trades": trades("Purchases"),
        "sold_shares": shares("Sales"),
        "sold_trades": trades("Sales"),
        "net_shares": shares("Net Shares Purchased (Sold)"),
        "net_pct": shares("% Net Shares Purchased (Sold)"),
    }


def _analyst_trend(ticker: yf.Ticker) -> dict | None:
    try:
        table = ticker.recommendations_summary
    except Exception as exc:
        logger.debug("recommendations_summary failed: %s", exc)
        return None
    if table is None or table.empty:
        return None
    row = table.iloc[0]
    return {key: _plain(row.get(key)) for key in ("strongBuy", "buy", "hold", "sell", "strongSell")}


def fetch_fundamentals(symbol: str) -> dict:
    ticker = yf.Ticker(symbol)
    try:
        info = ticker.info or {}
    except Exception as exc:
        logger.debug("info failed for %s: %s", symbol, exc)
        info = {}
    details = {field: _plain(info.get(field)) for field in _INFO_FIELDS}
    if details.get("dateShortInterest") and isinstance(details["dateShortInterest"], (int, float)):
        details["dateShortInterest"] = dt.datetime.fromtimestamp(details["dateShortInterest"], dt.timezone.utc).strftime("%Y-%m-%d")
    if details.get("exDividendDate") and isinstance(details["exDividendDate"], (int, float)):
        details["exDividendDate"] = dt.datetime.fromtimestamp(details["exDividendDate"], dt.timezone.utc).strftime("%Y-%m-%d")
    is_equity = str(details.get("quoteType") or "").upper() == "EQUITY"
    next_earnings, earnings_history = _earnings_dates(ticker) if is_equity else (None, [])
    return {
        "symbol": symbol,
        "info": details,
        "next_earnings": next_earnings,
        "earnings_history": earnings_history,
        "insiders": _insiders(ticker) if is_equity else None,
        "analysts": _analyst_trend(ticker) if is_equity else None,
    }


def get_fundamentals(store: Store, symbol: str, *, max_age: float = FUNDAMENTALS_MAX_AGE, allow_fetch: bool = True) -> dict | None:
    key = f"fund:{symbol}"
    cached = store.kv_get(key, max_age=max_age)
    if cached is not None or not allow_fetch:
        return cached if cached is not None else store.kv_get(key)
    with _lock_for(symbol):
        cached = store.kv_get(key, max_age=max_age)
        if cached is not None:
            return cached
        try:
            details = fetch_fundamentals(symbol)
        except Exception as exc:
            logger.warning("Fundamentals failed for %s: %s", symbol, exc)
            return store.kv_get(key)
        store.kv_set(key, details)
        return details
