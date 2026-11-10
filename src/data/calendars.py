"""Upcoming company earnings for the whole US market, from Yahoo Finance's earnings calendar."""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd
import yfinance as yf

from src.data.market_calendar import ET, now_et
from src.data.store import Store

logger = logging.getLogger(__name__)

CALENDAR_KEY = "earnings:calendar"
DAYS_AHEAD = 28
MIN_MARKET_CAP = 2e9  # smaller companies rarely matter for a market overview
TIMING = {"BMO": "before", "AMC": "after", "TAS": "during", "TNS": None}


def _over_the_counter(symbol: str) -> bool:
    """Foreign shares traded over the counter (five letters ending in F or Y, such as KBCSY).

    They duplicate companies listed abroad and are hard to trade, so they only clutter the list.
    """
    return len(symbol) == 5 and symbol.isalpha() and symbol[-1] in "FY"


def _rows(frame: pd.DataFrame) -> list[dict]:
    rows = []
    for symbol, row in frame.iterrows():
        start = row.get("Event Start Date")
        if pd.isna(start) or _over_the_counter(str(symbol)):
            continue
        stamp = pd.Timestamp(start)
        stamp = stamp.tz_convert(ET) if stamp.tzinfo else stamp.tz_localize("UTC").tz_convert(ET)
        estimate = row.get("EPS Estimate")
        cap = row.get("Marketcap")
        rows.append({
            "symbol": str(symbol).replace(".", "-"),
            "company": str(row.get("Company") or symbol),
            "date": stamp.date().isoformat(),
            # Before the open, after the close, or not announced yet.
            "timing": TIMING.get(str(row.get("Timing") or "TNS").upper()),
            "eps_estimate": None if pd.isna(estimate) else float(estimate),
            "market_cap": None if pd.isna(cap) else float(cap),
            "event": str(row.get("Event Name") or ""),
        })
    return rows


def refresh_earnings_calendar(store: Store) -> str:
    today = now_et().date()
    calendar = yf.Calendars(start=today - dt.timedelta(days=1), end=today + dt.timedelta(days=DAYS_AHEAD))
    frames = []
    for offset in range(0, 3000, 100):
        page = calendar.get_earnings_calendar(market_cap=MIN_MARKET_CAP, filter_most_active=False, limit=100, offset=offset, force=True)
        if page is None or page.empty:
            break
        frames.append(page)
        if len(page) < 100:
            break
    if not frames:
        raise RuntimeError("Yahoo's earnings calendar returned nothing.")
    rows = _rows(pd.concat(frames))
    # One row per company: the soonest report.
    soonest: dict[str, dict] = {}
    for row in sorted(rows, key=lambda item: item["date"]):
        soonest.setdefault(row["symbol"], row)
    store.kv_set(CALENDAR_KEY, sorted(soonest.values(), key=lambda item: (item["date"], -(item["market_cap"] or 0))))
    return f"{len(soonest)} companies reporting in the next {DAYS_AHEAD} days"
