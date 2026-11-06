"""Cboe public data: delayed option chains and market-wide put/call statistics.

No account or key is needed. Chains are delayed about 15 minutes and include open
interest, volume, implied volatility and greeks for every listed contract.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from src.data.catalog import OPTION_INDEX_SYMBOLS, OPTION_YAHOO_ALIASES
from src.data.market_calendar import is_market_open, is_trading_day, last_completed_session
from src.data.store import ROOT_DIR, Store
from src.data.web import NotFound, fetch

logger = logging.getLogger(__name__)

CHAIN_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"
DAILY_STATS_URL = "https://cdn.cboe.com/data/us/options/market_statistics/daily/{date}_daily_options"
ARCHIVE_URL = "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/{name}.csv"

CHAIN_CACHE_DIR = ROOT_DIR / "data" / "cache" / "chains"
CHAIN_MEMORY_SECONDS_OPEN = 15 * 60
CHAIN_MEMORY_SECONDS_CLOSED = 6 * 3600

# Cboe lists a few non-standard roots (adjusted contracts, binaries) under the same
# underlying. Only these roots trade with the standard 100 multiplier we assume.
INDEX_ROOTS = {
    "SPX": {"SPX", "SPXW"},
    "NDX": {"NDX", "NDXP"},
    "RUT": {"RUT", "RUTW"},
    "VIX": {"VIX", "VIXW"},
    "DJX": {"DJX", "DJXW"},
    "XSP": {"XSP", "XSPW"},
}

CHAIN_COLUMNS = [
    "expiry", "type", "strike", "bid", "ask", "iv", "open_interest", "volume",
    "delta", "gamma", "vega", "theta", "last", "contract",
]


class NoOptions(LookupError):
    """Cboe lists no options for this symbol."""


def option_symbol(symbol: str) -> str:
    """Translate a dashboard or Yahoo ticker to the name used for its option chain."""
    symbol = symbol.upper()
    return OPTION_YAHOO_ALIASES.get(symbol, symbol)


def _cboe_path(symbol: str) -> str:
    if symbol in OPTION_INDEX_SYMBOLS:
        return OPTION_INDEX_SYMBOLS[symbol]
    return symbol.replace("-", "").replace(".", "")


def parse_chain(payload: dict, symbol: str) -> dict:
    """Turn a raw Cboe chain response into a tidy frame plus quote details."""
    data = payload.get("data") or {}
    options = data.get("options") or []
    if not options:
        raise NoOptions(f"Cboe returned no contracts for {symbol}.")

    raw = pd.DataFrame(options)
    parts = raw["option"].str.extract(r"^([A-Z0-9]+?)(\d{6})([CP])(\d{8})$")
    roots = parts[0]
    allowed = INDEX_ROOTS.get(symbol, {_cboe_path(symbol)})
    mask = roots.isin(allowed)
    if not mask.any():
        # Tickers whose chain root differs from the ticker: use the most active root.
        mask = roots == roots.value_counts().idxmax()

    frame = pd.DataFrame({
        "expiry": pd.to_datetime(parts[1][mask], format="%y%m%d"),
        "type": parts[2][mask],
        "strike": parts[3][mask].astype(float) / 1000.0,
        "bid": pd.to_numeric(raw["bid"][mask], errors="coerce").fillna(0.0),
        "ask": pd.to_numeric(raw["ask"][mask], errors="coerce").fillna(0.0),
        "iv": pd.to_numeric(raw["iv"][mask], errors="coerce").fillna(0.0),
        "open_interest": pd.to_numeric(raw["open_interest"][mask], errors="coerce").fillna(0.0),
        "volume": pd.to_numeric(raw["volume"][mask], errors="coerce").fillna(0.0),
        "delta": pd.to_numeric(raw["delta"][mask], errors="coerce").fillna(0.0),
        "gamma": pd.to_numeric(raw["gamma"][mask], errors="coerce").fillna(0.0),
        "vega": pd.to_numeric(raw["vega"][mask], errors="coerce").fillna(0.0),
        "theta": pd.to_numeric(raw["theta"][mask], errors="coerce").fillna(0.0),
        "last": pd.to_numeric(raw["last_trade_price"][mask], errors="coerce").fillna(0.0),
        "contract": raw["option"][mask],
    }).reset_index(drop=True)

    spot = data.get("current_price") or data.get("close") or data.get("prev_day_close")
    last_trade = data.get("last_trade_time")
    return {
        "symbol": symbol,
        "spot": float(spot) if spot else None,
        "frame": frame,
        "source_time": last_trade,
        "fetched_at": time.time(),
        "iv30": _to_float(data.get("iv30")),
        "prev_close": _to_float(data.get("prev_day_close")),
        "price_change_pct": _to_float(data.get("price_change_percent")),
    }


def _to_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(number) else number


_chain_cache: dict[str, dict] = {}
_chain_locks: dict[str, threading.Lock] = {}
_chain_guard = threading.Lock()


def _lock_for(symbol: str) -> threading.Lock:
    with _chain_guard:
        return _chain_locks.setdefault(symbol, threading.Lock())


def _disk_path(symbol: str):
    return CHAIN_CACHE_DIR / f"{symbol}.csv.gz"


def _save_to_disk(chain: dict) -> None:
    CHAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    meta = pd.DataFrame([{
        "spot": chain["spot"],
        "source_time": chain["source_time"],
        "fetched_at": chain["fetched_at"],
        "iv30": chain["iv30"],
    }])
    temp = _disk_path(chain["symbol"]).with_suffix(".tmp")
    frame = chain["frame"].copy()
    for column, value in meta.iloc[0].items():
        frame[f"meta_{column}"] = value
    frame.to_csv(temp, index=False, compression="gzip")
    temp.replace(_disk_path(chain["symbol"]))


def _load_from_disk(symbol: str) -> dict | None:
    path = _disk_path(symbol)
    if not path.exists():
        return None
    try:
        frame = pd.read_csv(path, compression="gzip", parse_dates=["expiry"])
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        logger.warning("Ignoring unreadable chain cache for %s: %s", symbol, exc)
        return None
    if frame.empty:
        return None
    meta_columns = [column for column in frame.columns if column.startswith("meta_")]
    meta = frame.iloc[0][meta_columns]
    frame = frame.drop(columns=meta_columns)
    return {
        "symbol": symbol,
        "spot": _to_float(meta.get("meta_spot")),
        "frame": frame,
        "source_time": meta.get("meta_source_time") if isinstance(meta.get("meta_source_time"), str) else None,
        "fetched_at": _to_float(meta.get("meta_fetched_at")) or 0.0,
        "iv30": _to_float(meta.get("meta_iv30")),
        "prev_close": None,
        "price_change_pct": None,
    }


def get_chain(symbol: str, *, force: bool = False) -> dict:
    """Latest chain for a symbol: memory first, then disk, then Cboe.

    If Cboe is unreachable the previous copy is returned, marked by its old
    ``fetched_at`` time, so pages stay useful and can show how old the data is.
    """
    symbol = option_symbol(symbol)
    max_age = CHAIN_MEMORY_SECONDS_OPEN if is_market_open() else CHAIN_MEMORY_SECONDS_CLOSED
    with _lock_for(symbol):
        cached = _chain_cache.get(symbol)
        path = _disk_path(symbol)
        # Background workers write fresher chains to disk, so a newer file wins over memory.
        if cached is None or (path.exists() and path.stat().st_mtime > cached["fetched_at"] + 1):
            on_disk = _load_from_disk(symbol)
            if on_disk is not None:
                cached = on_disk
                _chain_cache[symbol] = cached
        if cached is not None and not force and time.time() - cached["fetched_at"] < max_age:
            return cached
        try:
            response = fetch(CHAIN_URL.format(symbol=_cboe_path(symbol)), timeout=60, not_found=(403, 404))
        except NotFound as exc:
            raise NoOptions(f"Cboe lists no options for {symbol}.") from exc
        except Exception:
            if cached is not None:
                return cached
            raise
        chain = parse_chain(response.json(), symbol)
        _chain_cache[symbol] = chain
        try:
            _save_to_disk(chain)
        except OSError as exc:
            logger.warning("Could not cache chain for %s: %s", symbol, exc)
        return chain


# ------------------------------------------------------------------- daily put/call

ARCHIVES = {
    "total": "totalpc",
    "equity": "equitypc",
    "index": "indexpc",
    "etp": "etppc",
    "vix": "vixpc",
    "spx": "spxpc",
}

RATIO_NAMES = {
    "TOTAL PUT/CALL RATIO": "total",
    "EQUITY PUT/CALL RATIO": "equity",
    "INDEX PUT/CALL RATIO": "index",
    "EXCHANGE TRADED PRODUCTS PUT/CALL RATIO": "etp",
    "CBOE VOLATILITY INDEX (VIX) PUT/CALL RATIO": "vix",
    "SPX + SPXW PUT/CALL RATIO": "spx",
}

VOLUME_SECTIONS = {"SUM OF ALL PRODUCTS": "total", "EQUITY OPTIONS": "equity", "INDEX OPTIONS": "index"}

# The daily JSON files start here; older days live in the single-file CSV archives.
DAILY_STATS_START = dt.date(2019, 10, 7)
MAX_DAYS_PER_RUN = 400


def parse_archive(text: str) -> pd.Series:
    """Read a Cboe put/call CSV archive; the ratio's column position differs per file."""
    lines = text.splitlines()
    header_index = next(index for index, line in enumerate(lines) if line.upper().replace(" ", "").startswith("DATE,"))
    header = [cell.strip().upper() for cell in lines[header_index].split(",")]
    ratio_column = next(index for index, cell in enumerate(header) if "RATIO" in cell)
    values: dict[pd.Timestamp, float] = {}
    for line in lines[header_index + 1:]:
        cells = [cell.strip() for cell in line.split(",")]
        if len(cells) <= ratio_column:
            continue
        try:
            day = pd.to_datetime(cells[0], format="%m/%d/%Y")
        except ValueError:
            continue
        value = _to_float(cells[ratio_column])
        if value is not None and value > 0:
            values[day] = value
    return pd.Series(values, dtype=float).sort_index()


def _load_archive(store: Store) -> int:
    written = 0
    for key, name in ARCHIVES.items():
        response = fetch(ARCHIVE_URL.format(name=name), timeout=60)
        written += store.upsert_series(f"cboe:pc_{key}", parse_archive(response.text))
    return written


def _parse_daily_stats(payload: dict) -> dict[str, float]:
    result: dict[str, float] = {}
    for item in payload.get("ratios", []):
        key = RATIO_NAMES.get(str(item.get("name", "")).strip().upper())
        value = _to_float(item.get("value"))
        if key and value is not None and value > 0:
            result[f"cboe:pc_{key}"] = value
    for section, key in VOLUME_SECTIONS.items():
        for row in payload.get(section, []) or []:
            if str(row.get("name", "")).upper() == "VOLUME":
                call, put = _to_float(row.get("call")), _to_float(row.get("put"))
                if call:
                    result[f"cboe:call_vol_{key}"] = call
                if put:
                    result[f"cboe:put_vol_{key}"] = put
    return result


def _fetch_daily_stats(day: dt.date) -> tuple[dt.date, dict[str, float] | None]:
    try:
        response = fetch(DAILY_STATS_URL.format(date=day.isoformat()), timeout=30, retries=1, not_found=(403, 404))
        return day, _parse_daily_stats(response.json())
    except NotFound:
        return day, None
    except Exception as exc:  # one missing day must not stop the whole backfill
        logger.debug("Daily Cboe stats for %s failed: %s", day, exc)
        return day, {}


def refresh_daily_ratios(store: Store) -> str:
    """Load put/call history. Newest days first, a slice per run, so nothing blocks."""
    if not store.kv_get("cboe:archive_loaded"):
        store.kv_set("cboe:archive_loaded", _load_archive(store) > 0)

    have = store.existing_series_dates("cboe:pc_total")
    checked = set(store.kv_get("cboe:empty_days", []))
    today = last_completed_session()
    day = today
    missing: list[dt.date] = []
    while day >= DAILY_STATS_START:
        iso = day.isoformat()
        if is_trading_day(day) and iso not in have and iso not in checked:
            missing.append(day)
        day -= dt.timedelta(days=1)

    batch = missing[:MAX_DAYS_PER_RUN]
    if batch:
        collected: dict[str, dict[str, float]] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for day, values in pool.map(_fetch_daily_stats, batch):
                if values is None:
                    checked.add(day.isoformat())
                elif values:
                    for series_id, value in values.items():
                        collected.setdefault(series_id, {})[day.isoformat()] = value
        store.upsert_series_bulk({
            series_id: pd.Series({pd.Timestamp(day): value for day, value in values.items()}, dtype=float)
            for series_id, values in collected.items()
        })
        store.kv_set("cboe:empty_days", sorted(checked))

    remaining = max(0, len(missing) - len(batch))
    store.kv_set("cboe:backfill_remaining", remaining)
    total = len(store.existing_series_dates("cboe:pc_total"))
    detail = f"{total:,} days of put/call history"
    if remaining:
        detail += f", {remaining:,} older days still loading"
    return detail
