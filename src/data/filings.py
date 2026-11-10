"""A company's SEC filings and insider trades, as published by Yahoo Finance.

Yahoo republishes each company's EDGAR filing list and the insider transactions from
Form 4 filings. Fetched when a stock page is opened, and kept for a day.
"""

from __future__ import annotations

import logging
import re
import threading

import pandas as pd
import yfinance as yf

from src.data.store import Store

logger = logging.getLogger(__name__)

MAX_AGE = 24 * 3600
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()

# What each common form is, in plain words.
FORMS = {
    "10-K": "Annual report",
    "10-Q": "Quarterly report",
    "8-K": "Important event",
    "4": "Insider trade",
    "3": "New insider",
    "S-1": "Registration (new shares)",
    "S-3": "Registration (new shares)",
    "S-8": "Shares for employee plans",
    "DEF 14A": "Proxy for the shareholder meeting",
    "SC 13G": "Large holder (5%+), passive",
    "SC 13D": "Large holder (5%+), may seek influence",
    "SC 13G/A": "Large holder update",
    "SC 13D/A": "Large holder update",
    "144": "Planned insider sale",
    "6-K": "Foreign company report",
    "20-F": "Foreign company annual report",
}
# Filings that most often move a stock.
NOTABLE = {"8-K", "10-K", "10-Q", "SC 13D", "SC 13D/A", "S-1", "S-3", "6-K", "20-F"}


def _lock(symbol: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(symbol, threading.Lock())


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def _kind(text: str) -> str:
    """'Sale at price 222.19 - 223.75 per share.' -> 'sale'."""
    lowered = text.lower()
    for word, kind in (("purchase", "buy"), ("buy", "buy"), ("sale", "sell"), ("sold", "sell"), ("award", "award"),
                       ("grant", "award"), ("gift", "gift"), ("exercise", "option exercise"), ("conversion", "option exercise")):
        if word in lowered:
            return kind
    return "other"


def fetch_filings(symbol: str) -> dict:
    ticker = yf.Ticker(symbol)
    filings = []
    try:
        for row in (ticker.sec_filings or [])[:40]:
            form = str(row.get("type") or "")
            filings.append({
                "date": str(row.get("date") or "")[:10],
                "form": form,
                "kind": FORMS.get(form, ""),
                "title": str(row.get("title") or ""),
                "url": str(row.get("edgarUrl") or ""),
                "notable": form in NOTABLE,
            })
    except Exception as exc:
        logger.info("No SEC filings for %s: %s", symbol, exc)
    trades = []
    try:
        frame = ticker.insider_transactions
        if frame is not None and not frame.empty:
            for _, row in frame.head(60).iterrows():
                text = str(row.get("Text") or "")
                price = re.search(r"price ([\d.]+)", text)
                start = row.get("Start Date")
                trades.append({
                    "date": pd.Timestamp(start).strftime("%Y-%m-%d") if not pd.isna(start) else None,
                    "insider": str(row.get("Insider") or "").title(),
                    "position": str(row.get("Position") or ""),
                    "kind": _kind(text),
                    "text": text.strip().rstrip("."),
                    "shares": _number(row.get("Shares")),
                    "value": _number(row.get("Value")),
                    "price": float(price.group(1)) if price and float(price.group(1)) > 0 else None,
                })
    except Exception as exc:
        logger.info("No insider trades for %s: %s", symbol, exc)
    return {"symbol": symbol, "filings": filings, "trades": trades}


def get_filings(store: Store, symbol: str) -> dict:
    key = f"filings:{symbol}"
    cached = store.kv_get(key, max_age=MAX_AGE)
    if cached is not None:
        return cached
    with _lock(symbol):
        cached = store.kv_get(key, max_age=MAX_AGE)
        if cached is not None:
            return cached
        try:
            result = fetch_filings(symbol)
        except Exception as exc:
            logger.warning("Filings failed for %s: %s", symbol, exc)
            return store.kv_get(key) or {"symbol": symbol, "filings": [], "trades": []}
        store.kv_set(key, result)
        return result


def insider_summary(trades: list[dict], since: str) -> dict:
    """Open-market buys and sales since a date: what insiders did with their own money."""
    recent = [trade for trade in trades if trade["date"] and trade["date"] >= since]
    buys = [trade for trade in recent if trade["kind"] == "buy"]
    sells = [trade for trade in recent if trade["kind"] == "sell"]
    return {
        "buys": len(buys),
        "sells": len(sells),
        "bought_value": sum(trade["value"] or 0 for trade in buys),
        "sold_value": sum(trade["value"] or 0 for trade in sells),
        "buyers": len({trade["insider"] for trade in buys}),
    }
