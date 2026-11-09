"""Prices outside regular US trading hours: pre-market, after-hours and index futures.

Stocks and ETFs trade from 4:00 to 9:30 ET (pre-market) and 16:00 to 20:00 ET
(after-hours). Index futures trade almost around the clock, so they show where the market
is heading before the open. Everything comes from one batch of 5-minute bars.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time

import pandas as pd

from src.data.market_calendar import ET, is_market_open, is_trading_day, last_completed_session, now_et
from src.data import market_data
from src.data.store import Store

logger = logging.getLogger(__name__)

INDEX_FUTURES = [
    ("ES=F", "S&P 500 futures", "^GSPC"),
    ("NQ=F", "Nasdaq 100 futures", "^NDX"),
    ("RTY=F", "Russell 2000 futures", "^RUT"),
    ("YM=F", "Dow futures", "^DJI"),
]
CACHE_SECONDS = 180
MAX_AGE = dt.timedelta(days=4)
OPEN, CLOSE = dt.time(9, 30), dt.time(16, 0)

_cache: dict[tuple, tuple[float, dict]] = {}
_guard = threading.Lock()


def _bars(symbols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    with _guard:
        hit = _cache.get(symbols)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        frames = market_data.yahoo_download(list(symbols), period="5d", interval="5m", prepost=True)
    except Exception as exc:  # extended prices are a bonus: never let them break a page
        logger.warning("Extended-hours prices failed: %s", exc)
        frames = hit[1] if hit else {}
    with _guard:
        _cache[symbols] = (time.time(), frames)
    return frames


def _regular_close(store: Store, symbol: str, before: dt.date, frame: pd.DataFrame, inclusive: bool) -> float | None:
    """Official close of the last regular session on (or before) a date."""
    daily = store.get_prices(symbol)["Close"].dropna()
    cutoff = pd.Timestamp(before)
    daily = daily[daily.index <= cutoff] if inclusive else daily[daily.index < cutoff]
    if not daily.empty:
        return float(daily.iloc[-1])
    # Not stored yet: fall back to the last regular-hours bar in the downloaded data.
    regular = frame[[OPEN <= stamp.time() < CLOSE and (stamp.date() <= before if inclusive else stamp.date() < before) for stamp in frame.index]]
    return float(regular["Close"].iloc[-1]) if not regular.empty else None


def _futures_reference(frame: pd.DataFrame, now: dt.datetime) -> float | None:
    """Futures price at the last completed 4pm ET stock-market close, so the change
    lines up with how stocks are measured."""
    session = last_completed_session(now)
    closes = frame[[stamp.date() <= session and OPEN <= stamp.time() < CLOSE and is_trading_day(stamp.date()) for stamp in frame.index]]
    return float(closes["Close"].iloc[-1]) if not closes.empty else None


def _quote(store: Store, symbol: str, frame: pd.DataFrame, now: dt.datetime) -> dict | None:
    frame = frame.dropna(subset=["Close"])
    if frame.empty:
        return None
    index = pd.DatetimeIndex(frame.index)
    index = index.tz_convert(ET) if index.tz is not None else index.tz_localize(ET)
    frame = frame.set_axis(index)
    last_time = index[-1]
    if now - last_time.to_pydatetime() > MAX_AGE:
        return None
    price = float(frame["Close"].iloc[-1])
    if symbol.endswith("=F"):
        session, label = "futures", "Futures"
        reference = _futures_reference(frame, now)
    else:
        clock = last_time.time()
        if OPEN <= clock < CLOSE and is_trading_day(last_time.date()):
            # The latest trade is from regular hours, so there is nothing extended to show.
            return None
        if clock < OPEN:
            session, label = "pre", "Pre-market"
            reference = _regular_close(store, symbol, last_time.date(), frame, inclusive=False)
        else:
            session, label = "post", "After hours"
            reference = _regular_close(store, symbol, last_time.date(), frame, inclusive=True)
    if not reference:
        return None
    today = now.date()
    day = last_time.date()
    when = last_time.strftime("%H:%M ET") if day == today else last_time.strftime("%a %H:%M ET")
    volume = frame["Volume"][[stamp.date() == day and not (OPEN <= stamp.time() < CLOSE) for stamp in index]].sum()
    return {
        "symbol": symbol,
        "session": session,
        "label": label,
        "price": price,
        "reference": reference,
        "change": price - reference,
        "change_pct": (price / reference - 1.0) * 100.0,
        "time": last_time.isoformat(),
        "when": when,
        "volume": float(volume) if volume else None,
    }


def extended_quotes(store: Store, symbols: list[str]) -> dict[str, dict]:
    """Latest pre-market, after-hours or futures price per symbol, where there is one.

    During regular hours stocks return nothing, because the regular price is the price.
    """
    wanted = tuple(sorted({symbol for symbol in symbols if not symbol.startswith("^")}))
    if not wanted:
        return {}
    now = now_et()
    if is_market_open(now):
        # During regular hours the regular price is the price; only futures still matter.
        wanted = tuple(symbol for symbol in wanted if symbol.endswith("=F"))
        if not wanted:
            return {}
    frames = _bars(wanted)
    quotes = {}
    for symbol in wanted:
        frame = frames.get(symbol)
        if frame is None:
            continue
        quote = _quote(store, symbol, frame, now)
        if quote:
            quotes[symbol] = quote
    return quotes


def futures_board(store: Store) -> list[dict]:
    quotes = extended_quotes(store, [symbol for symbol, _, _ in INDEX_FUTURES])
    rows = []
    for symbol, name, index in INDEX_FUTURES:
        if symbol in quotes:
            rows.append({**quotes[symbol], "name": name, "index": index})
    return rows
