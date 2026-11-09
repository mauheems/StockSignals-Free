"""Daily and intraday prices from Yahoo Finance, cached in the local store."""

from __future__ import annotations

import logging
import threading
import time
from typing import Iterable

import pandas as pd
import yfinance as yf

from src.data.market_calendar import is_market_open, last_completed_session
from src.data.store import Store

logger = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

BATCH_SIZE = 80
BATCH_PAUSE_SECONDS = 1.0
# Adjusted prices are rewritten by Yahoo after each dividend or split, so a change this
# large in overlapping history means the stored copy has to be downloaded again.
ADJUSTMENT_TOLERANCE = 0.0005

_RENAME = {"Open": "Open", "High": "High", "Low": "Low", "Close": "Close", "Adj Close": "AdjClose", "Volume": "Volume"}


class SymbolNotFound(ValueError):
    """Yahoo Finance has no price data for this ticker."""


def _chunks(items: list, size: int) -> Iterable[list]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def yahoo_download(
    symbols: list[str],
    *,
    start: str | None = None,
    period: str | None = None,
    interval: str = "1d",
    timeout: float = 30,
    prepost: bool = False,
) -> dict[str, pd.DataFrame]:
    """Download a batch of symbols; symbols with no data are simply left out.

    ``prepost`` adds pre-market and after-hours bars (intraday intervals only).
    """
    if not symbols:
        return {}
    raw = yf.download(
        tickers=list(symbols),
        start=start,
        period=period if start is None else None,
        interval=interval,
        prepost=prepost,
        group_by="ticker",
        auto_adjust=False,
        actions=False,
        threads=True,
        progress=False,
        timeout=timeout,
        multi_level_index=True,
    )
    frames: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return frames
    available = set(raw.columns.get_level_values(0))
    for symbol in symbols:
        if symbol not in available:
            continue
        frame = raw[symbol].rename(columns=_RENAME)
        keep = [column for column in _RENAME.values() if column in frame.columns]
        frame = frame.loc[:, keep].dropna(subset=["Close"])
        if frame.empty:
            continue
        if "AdjClose" not in frame.columns:
            frame["AdjClose"] = frame["Close"]
        if interval.endswith(("d", "wk", "mo")):
            index = pd.DatetimeIndex(frame.index)
            frame.index = index.tz_localize(None).normalize() if index.tz is not None else index.normalize()
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        frames[symbol] = frame
    return frames


def _adjustment_changed(store: Store, symbol: str, fresh: pd.DataFrame) -> bool:
    """Detect a split or dividend by comparing fresh prices with the stored overlap."""
    # The newest bars may legitimately change (live session), so only older ones count.
    overlap = fresh.iloc[:-2] if len(fresh) > 2 else fresh.iloc[:0]
    if overlap.empty:
        return False
    stored = store.get_prices(symbol, start=overlap.index[0].strftime("%Y-%m-%d"))
    common = overlap.index.intersection(stored.index)
    if len(common) == 0:
        return False
    for column in ("Close", "AdjClose"):
        ratio = overlap.loc[common, column] / stored.loc[common, column]
        if ((ratio - 1.0).abs() > ADJUSTMENT_TOLERANCE).any():
            return True
    return False


def refresh_prices(
    store: Store,
    symbols: Iterable[str],
    full_start: str,
    *,
    batch_size: int = BATCH_SIZE,
    pause: float = BATCH_PAUSE_SECONDS,
) -> dict:
    """Bring a set of symbols up to date with as little downloading as possible."""
    symbols = list(dict.fromkeys(symbols))
    last_dates = store.last_price_dates(symbols)
    full = [symbol for symbol in symbols if symbol not in last_dates]
    incremental = [symbol for symbol in symbols if symbol in last_dates]
    updated: set[str] = set()
    failed: set[str] = set()

    if incremental:
        oldest = min(last_dates[symbol] for symbol in incremental)
        since = (pd.Timestamp(oldest) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        for chunk in _chunks(incremental, batch_size):
            frames = yahoo_download(chunk, start=since)
            for symbol in chunk:
                frame = frames.get(symbol)
                if frame is None:
                    failed.add(symbol)
                elif _adjustment_changed(store, symbol, frame):
                    full.append(symbol)
                else:
                    store.upsert_prices(symbol, frame)
                    store.touch_symbol(symbol)
                    updated.add(symbol)
            if pause:
                time.sleep(pause)

    # A re-download keeps at least the depth of history the symbol already had.
    by_start: dict[str, list[str]] = {}
    for symbol in full:
        existing = store.history_from(symbol)
        by_start.setdefault(min(existing, full_start) if existing else full_start, []).append(symbol)
    for start, group in by_start.items():
        for chunk in _chunks(group, batch_size):
            frames = yahoo_download(chunk, start=start)
            for symbol in chunk:
                frame = frames.get(symbol)
                if frame is None:
                    failed.add(symbol)
                    continue
                store.replace_prices(symbol, frame)
                store.set_history_from(symbol, start)
                updated.add(symbol)
                failed.discard(symbol)
            if pause:
                time.sleep(pause)

    return {"updated": len(updated), "failed": sorted(failed)}


_fetch_locks: dict[str, threading.Lock] = {}
_fetch_locks_guard = threading.Lock()


def _symbol_lock(symbol: str) -> threading.Lock:
    with _fetch_locks_guard:
        return _fetch_locks.setdefault(symbol, threading.Lock())


def ensure_prices(store: Store, symbol: str, start: str = "2000-01-01", max_age: float | None = None) -> pd.DataFrame:
    """Return stored prices for one symbol, downloading on demand when missing or old.

    Falls back to the stored copy when Yahoo is unreachable, and raises SymbolNotFound
    only when there is nothing at all to show.
    """
    if max_age is None:
        max_age = 15 * 60 if is_market_open() else 6 * 3600
    with _symbol_lock(symbol):
        last = store.last_price_dates([symbol]).get(symbol)
        fetched_at = store.symbol_fetched_at(symbol)
        history_from = store.history_from(symbol)
        needs_deeper_history = last is None or (history_from is not None and start < history_from)
        age = time.time() - fetched_at if fetched_at else float("inf")
        behind_last_session = last is not None and last < last_completed_session().isoformat()
        stale = age > max_age or (behind_last_session and age > 900)

        if needs_deeper_history or stale:
            try:
                if needs_deeper_history:
                    frames = yahoo_download([symbol], start=start)
                    frame = frames.get(symbol)
                    if frame is not None:
                        store.replace_prices(symbol, frame)
                        store.set_history_from(symbol, start)
                else:
                    refresh_prices(store, [symbol], start, pause=0)
            except Exception as exc:  # network trouble must not break a page with stored data
                logger.warning("Could not refresh %s: %s", symbol, exc)
        frame = store.get_prices(symbol)
    if frame.empty:
        raise SymbolNotFound(f"No price data found for {symbol}.")
    return frame


_intraday_cache: dict[tuple[str, str, bool], tuple[float, pd.DataFrame]] = {}
_intraday_guard = threading.Lock()


def intraday_prices(symbol: str, interval: str, prepost: bool = False) -> pd.DataFrame:
    """Recent 15-minute or hourly bars (Yahoo keeps 60 days and 2 years respectively).

    With ``prepost`` the pre-market and after-hours bars are included too.
    """
    period = {"15m": "60d", "1h": "730d"}[interval]
    key = (symbol, interval, prepost)
    with _intraday_guard:
        cached = _intraday_cache.get(key)
    if cached and time.time() - cached[0] < 300:
        return cached[1]
    frames = yahoo_download([symbol], period=period, interval=interval, prepost=prepost)
    frame = frames.get(symbol)
    if frame is None:
        if cached:
            return cached[1]
        raise SymbolNotFound(f"No intraday data found for {symbol}.")
    frame = frame.copy()
    index = pd.DatetimeIndex(frame.index)
    frame.index = index.tz_convert("America/New_York").tz_localize(None) if index.tz is not None else index
    with _intraday_guard:
        _intraday_cache[key] = (time.time(), frame)
    return frame


class MarketDataProvider:
    """Backtest-friendly access to daily OHLCV data.

    With ``adjusted=True`` all prices are scaled by Yahoo's dividend-and-split factor, so
    a backtest earns dividends instead of silently ignoring them.
    """

    def __init__(self, store: Store | None = None):
        self.store = store or Store()

    def get_daily_data(self, symbol: str, start: str, end: str | None = None, adjusted: bool = False) -> pd.DataFrame:
        symbol = symbol.upper()
        frame = ensure_prices(self.store, symbol, start=str(start))
        frame = frame.loc[str(start):]
        if end:
            frame = frame.loc[:str(end)]
        if frame.empty:
            raise SymbolNotFound(f"No data found for {symbol} between {start} and {end or 'today'}.")
        columns = ["Open", "High", "Low", "Close", "Volume"]
        if not adjusted:
            return frame[columns].dropna(subset=["Open", "Close"])
        factor = (frame["AdjClose"] / frame["Close"]).fillna(1.0)
        result = frame[columns].copy()
        for column in ("Open", "High", "Low", "Close"):
            result[column] = result[column] * factor
        return result.dropna(subset=["Open", "Close"])
