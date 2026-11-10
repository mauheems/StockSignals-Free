"""Keeps every data source fresh in the background, each on its own schedule.

Failures stay isolated: if one source is down its panels show the last good data with
its age, while every other source carries on. The dashboard reads status from here.
"""

from __future__ import annotations

import logging
import multiprocessing
import signal
import threading
import time
from pathlib import Path
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from src.analytics import options as option_analytics
from src.analytics.breadth import refresh_breadth_tables
from src.data import calendars, cboe, fundamentals, macro_sources, prediction_markets
from src.data.catalog import BREADTH_HISTORY_START, CORE_HISTORY_START, CORE_SYMBOLS, OPTION_CORE
from src.data.market_calendar import is_market_open, last_completed_session, now_et
from src.data.market_data import refresh_prices
from src.data.store import Store
from src.data.universe import load_sp500, refresh_universe

logger = logging.getLogger(__name__)

MINUTE = 60
HOUR = 3600

FOLLOW_UP_SECONDS = 90
WATCHLIST_DEFAULTS = ["NVDA", "AAPL", "MSFT", "AMZN", "TSLA"]


def save_option_snapshot(store: Store, symbol: str, chain: dict) -> dict | None:
    """Analyse a chain and keep today's key numbers, building a daily history over time."""
    if not chain.get("spot"):
        return None
    source_time = chain.get("source_time")
    session = source_time[:10] if isinstance(source_time, str) and len(source_time) >= 10 else last_completed_session().isoformat()
    result = option_analytics.analyze_chain(chain["frame"], chain["spot"], session=session)
    if not result.get("available") or result.get("empty_scope"):
        return None
    day = pd.Timestamp(session)
    store.upsert_series_bulk({
        f"opt:{symbol}:{key}": pd.Series({day: value}, dtype=float)
        for key, value in option_analytics.snapshot_values(result).items()
    })
    store.kv_set(f"opt:summary:{symbol}", {
        **result["kpis"],
        "source_time": source_time,
        "fetched_at": chain.get("fetched_at"),
    })
    return result


def _refresh_core_prices(store: Store) -> str:
    result = refresh_prices(store, CORE_SYMBOLS, CORE_HISTORY_START)
    detail = f"{result['updated']} of {len(CORE_SYMBOLS)} symbols"
    if result["failed"]:
        detail += f" (no data: {', '.join(result['failed'][:6])})"
    if result["updated"] == 0:
        raise RuntimeError("Yahoo Finance returned no prices.")
    return detail


def _refresh_breadth(store: Store) -> str:
    if load_sp500(store).empty:
        # On a first run this can start before the member list has been downloaded.
        refresh_universe(store)
    members = load_sp500(store)["symbol"].tolist()
    result = refresh_prices(store, members, BREADTH_HISTORY_START, batch_size=100)
    if result["updated"] == 0 and not store.last_price_dates(members):
        raise RuntimeError("Yahoo Finance returned no prices for S&P 500 members.")
    return refresh_breadth_tables(store)


def _refresh_watchlist(store: Store) -> str:
    # Your positions and stocks with alerts are kept fresh too, even if they aren't on the watchlist.
    watched = list(dict.fromkeys([*store.watchlist(), *store.position_symbols()]))
    symbols = list(dict.fromkeys([*watched, *store.alert_symbols()]))
    if not symbols:
        return "Watchlist is empty"
    result = refresh_prices(store, symbols, CORE_HISTORY_START, pause=0)
    for symbol in watched:
        fundamentals.get_fundamentals(store, symbol)
    return f"{result['updated']} of {len(symbols)} symbols"


def _refresh_prediction(store: Store) -> str:
    from src.analytics.events import log_expectations

    outcome = prediction_markets.refresh_prediction_markets(store)
    # Remember each number's last expectation before release, for the calendar's surprises.
    log_expectations(store, store.kv_get("pm:board") or {})
    return outcome


def _refresh_options(store: Store) -> str:
    symbols = list(dict.fromkeys([*OPTION_CORE, *store.watchlist()]))
    done, unavailable = 0, []
    errors = []

    def one(symbol: str) -> None:
        nonlocal done
        try:
            chain = cboe.get_chain(symbol, force=True)
            save_option_snapshot(store, cboe.option_symbol(symbol), chain)
            done += 1
        except cboe.NoOptions:
            unavailable.append(symbol)
        except Exception as exc:
            logger.warning("Option chain for %s failed: %s", symbol, exc)
            errors.append(symbol)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(one, symbols))
    if done == 0 and errors:
        raise RuntimeError(f"Cboe chains failed for {', '.join(errors[:6])}.")
    detail = f"{done} of {len(symbols)} underlyings"
    if unavailable:
        detail += f" (no listed options: {', '.join(unavailable[:6])})"
    return detail


def _refresh_finra(store: Store) -> str:
    symbols = set(load_sp500(store)["symbol"]) | set(store.watchlist())
    return macro_sources.refresh_finra(store, symbols)


def _refresh_ratios(store: Store) -> str:
    return cboe.refresh_daily_ratios(store)


def _ratios_have_more(store: Store) -> bool:
    """Old put/call history loads a slice at a time, across restarts if need be."""
    return store.kv_get("cboe:backfill_remaining", 0) > 0


@dataclass(frozen=True)
class Source:
    id: str
    label: str
    provider: str
    delay: str
    feeds: str
    run: Callable[[Store], object]
    every_open: float
    every_closed: float
    has_more_work: Callable[[Store], bool] | None = None
    retry_after: float = 10 * MINUTE     # wait this long after a failure
    refresh_all_gap: float = 1 * MINUTE  # "Refresh data" skips sources fetched more recently


SOURCES: list[Source] = [
    Source("prices_core", "Indices, ETFs, futures, volatility", "Yahoo Finance", "About 15 minutes for US stocks; indices vary",
           "Overview tiles, rotation, volatility, ratios", _refresh_core_prices, 15 * MINUTE, 3 * HOUR),
    Source("universe", "S&P 500 member list", "Wikipedia", "Updated when membership changes",
           "Breadth, scanner, ticker search", refresh_universe, 24 * HOUR, 24 * HOUR, refresh_all_gap=1 * HOUR),
    Source("options", "Option chains with greeks", "Cboe delayed quotes", "About 15 minutes",
           "Options & Gamma page, gamma levels", _refresh_options, 15 * MINUTE, 6 * HOUR),
    Source("breadth", "S&P 500 member prices", "Yahoo Finance", "About 15 minutes, recomputed every 30 minutes",
           "Trend & Breadth, scanner, RS rating", _refresh_breadth, 30 * MINUTE, 6 * HOUR),
    Source("watchlist", "Watchlist and alert prices, fundamentals", "Yahoo Finance", "About 15 minutes; fundamentals daily",
           "Watchlist, stock pages, alerts", _refresh_watchlist, 15 * MINUTE, 3 * HOUR),
    Source("cboe_ratios", "Market-wide put/call ratios", "Cboe market statistics", "End of day",
           "Flows & Sentiment", _refresh_ratios, 1 * HOUR, 6 * HOUR, _ratios_have_more),
    Source("squeeze", "Dark Index (DIX) and GEX", "SqueezeMetrics", "Published the next morning",
           "Flows & Sentiment, Market Health", macro_sources.refresh_squeeze, 3 * HOUR, 6 * HOUR),
    Source("fred", "Rates, credit, liquidity, economy", "FRED (St. Louis Fed)", "Daily to monthly, by series",
           "Macro & Credit", macro_sources.refresh_fred, 3 * HOUR, 6 * HOUR),
    Source("treasury", "Treasury yield curve", "US Treasury", "End of day",
           "Macro & Credit", macro_sources.refresh_treasury, 3 * HOUR, 6 * HOUR),
    Source("cnn", "Fear & Greed Index", "CNN", "Updates during the day",
           "Flows & Sentiment", macro_sources.refresh_cnn, 1 * HOUR, 6 * HOUR),
    Source("aaii", "Investor sentiment survey", "AAII", "Weekly, on Thursdays",
           "Flows & Sentiment", macro_sources.refresh_aaii, 12 * HOUR, 12 * HOUR, retry_after=12 * HOUR, refresh_all_gap=12 * HOUR),
    Source("cot", "Futures positioning (COT)", "CFTC", "Weekly, Friday for the prior Tuesday",
           "Flows & Sentiment", macro_sources.refresh_cot, 12 * HOUR, 12 * HOUR, refresh_all_gap=1 * HOUR),
    Source("finra", "Off-exchange short volume", "FINRA", "Daily, in the evening",
           "Stock pages", _refresh_finra, 6 * HOUR, 6 * HOUR),
    Source("events", "Fed and economic release calendar", "Federal Reserve, FRED", "Checked daily",
           "Overview catalysts", macro_sources.refresh_events, 24 * HOUR, 24 * HOUR, refresh_all_gap=1 * HOUR),
    Source("prediction", "Fed, recession, inflation and earnings odds", "Kalshi, Polymarket", "Live market prices, checked every hour",
           "Prediction Markets, Economic Calendar, Overview catalysts, stock pages", _refresh_prediction, 1 * HOUR, 3 * HOUR,
           refresh_all_gap=15 * MINUTE),
    Source("earnings_calendar", "Company earnings calendar", "Yahoo Finance", "Checked twice a day",
           "Earnings Season, morning brief", calendars.refresh_earnings_calendar, 12 * HOUR, 12 * HOUR, refresh_all_gap=1 * HOUR),
]
SOURCE_BY_ID = {source.id: source for source in SOURCES}


def _init_worker() -> None:
    # Ctrl+C is handled by the main process, which then stops the workers itself.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")


def _run_in_worker(db_path: str, source_id: str) -> bool:
    return run_source(Store(db_path), SOURCE_BY_ID[source_id])


class Refresher:
    """Runs due sources in worker processes; pages only ever read from the store.

    Downloads and recalculations are CPU-heavy, so they run in separate processes. Inside
    the web server's process they would compete with page requests for Python's global
    lock, and the dashboard would stall whenever data is refreshing.
    """

    def __init__(self, store: Store, workers: int = 4):
        self.store = store
        self.workers = workers
        self._pool = self._new_pool()
        self._running: set[str] = set()
        self._retry_at: dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _new_pool(self) -> ProcessPoolExecutor:
        # Forking a multi-threaded server is unsafe, so each worker starts as a fresh interpreter.
        return ProcessPoolExecutor(
            max_workers=self.workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_init_worker,
            max_tasks_per_child=25,
        )

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="refresh-scheduler", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # A worker may be mid-download; SQLite keeps the database consistent if it is killed.
        for process in list((self._pool._processes or {}).values()):
            process.terminate()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_due()
            except Exception:
                logger.exception("Refresh scheduler error")
            self._stop.wait(20)

    def _is_due(self, source: Source, status: dict) -> bool:
        now = time.time()
        retry_at = self._retry_at.get(source.id)
        if retry_at is not None:
            return now >= retry_at
        last = status.get("last_success")
        if last is not None and source.has_more_work and source.has_more_work(self.store):
            return now - last >= FOLLOW_UP_SECONDS
        interval = source.every_open if is_market_open() else source.every_closed
        return last is None or now - last >= interval

    def run_due(self) -> list[str]:
        statuses = self.store.status_all()
        started = []
        for source in SOURCES:
            if self._is_due(source, statuses.get(source.id, {})) and self.trigger(source.id):
                started.append(source.id)
        return started

    def trigger(self, source_id: str) -> bool:
        """Start one source now, unless it is already running."""
        if source_id not in SOURCE_BY_ID:
            raise KeyError(source_id)
        with self._lock:
            if source_id in self._running:
                return False
            try:
                future = self._pool.submit(_run_in_worker, str(self.store.path), source_id)
            except BrokenProcessPool:
                self._pool = self._new_pool()
                future = self._pool.submit(_run_in_worker, str(self.store.path), source_id)
            self._running.add(source_id)
        future.add_done_callback(lambda done: self._finished(source_id, done))
        return True

    def trigger_all(self) -> int:
        """Refresh everything except what was fetched too recently to have changed."""
        statuses = self.store.status_all()
        now = time.time()
        started = 0
        for source in SOURCES:
            last = statuses.get(source.id, {}).get("last_attempt")
            if (last is None or now - last >= source.refresh_all_gap) and self.trigger(source.id):
                started += 1
        return started

    def _finished(self, source_id: str, future: Future) -> None:
        succeeded = False
        if not future.cancelled():
            error = future.exception()
            if error is None:
                succeeded = future.result()
            else:
                # The worker itself died (out of memory, for example), not just the download.
                logger.warning("Refresh worker for %s failed: %s", source_id, error)
                self.store.status_error(source_id, f"Worker stopped unexpectedly: {error}")
        with self._lock:
            self._running.discard(source_id)
            if succeeded:
                self._retry_at.pop(source_id, None)
            else:
                # Back off and try again later, without hammering the provider.
                self._retry_at[source_id] = time.time() + SOURCE_BY_ID[source_id].retry_after
        self.store.bump_version()

    def running(self) -> list[str]:
        with self._lock:
            return sorted(self._running)


def run_source(store: Store, source: Source) -> bool:
    """Run one source and record the outcome; any failure stays contained to this source."""
    store.status_attempt(source.id)
    started = time.time()
    try:
        outcome = source.run(store)
    except Exception as exc:
        logger.warning("%s failed: %s", source.label, exc)
        store.status_error(source.id, f"{type(exc).__name__}: {exc}")
        return False
    store.status_success(source.id, str(outcome))
    logger.info("%s: %s (%.1fs)", source.label, outcome, time.time() - started)
    return True


_STARTED = time.time()
_CODE_ROOT = Path(__file__).resolve().parents[1]


def code_changed_since_start() -> bool:
    """Whether the app's Python code was updated after this server started.

    The browser always gets the newest page code from disk, but the server keeps running
    the code it started with, so the two can disagree until the app is restarted.
    """
    for path in _CODE_ROOT.rglob("*.py"):
        try:
            if path.stat().st_mtime > _STARTED:
                return True
        except OSError:
            continue
    return False


def status_payload(store: Store, refresher: Refresher | None) -> dict:
    statuses = store.status_all()
    running = set(refresher.running()) if refresher else set()
    rows = []
    for source in SOURCES:
        status = statuses.get(source.id, {})
        last_success = status.get("last_success")
        state = "ok" if last_success else "pending"
        if status.get("last_error"):
            state = "error" if not last_success else "stale"
        if source.id in running:
            state = "running"
        rows.append({
            "id": source.id,
            "label": source.label,
            "provider": source.provider,
            "delay": source.delay,
            "feeds": source.feeds,
            "state": state,
            "last_success": last_success,
            "last_attempt": status.get("last_attempt"),
            "last_error": status.get("last_error"),
            "detail": status.get("detail"),
            "every_seconds": source.every_open if is_market_open() else source.every_closed,
        })
    return {
        "code_changed": code_changed_since_start(),
        "version": store.version,
        "sources": rows,
        "running": sorted(running),
        "loaded": sum(1 for row in rows if row["last_success"]),
        "total": len(rows),
        "first_run": not any(row["last_success"] for row in rows if row["id"] in ("prices_core", "breadth")),
        "server_time": time.time(),
        "et_time": now_et().strftime("%H:%M"),
        "db_bytes": store.file_size(),
        "alerts_unread": store.alert_events_unread(),
    }
