"""SQLite persistence for everything the dashboard shows.

One local file (``data/market.db``) holds prices, scalar time series (macro,
sentiment, breadth, option snapshots), JSON blobs, the watchlist and the status of
each data source. Connections are short-lived, so the store is safe to share across
the refresh threads and the web server's worker threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT_DIR / "data" / "market.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, adj_close REAL, volume REAL,
    PRIMARY KEY (symbol, date)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS symbol_meta (
    symbol TEXT PRIMARY KEY,
    history_from TEXT,
    fetched_at REAL
);

CREATE TABLE IF NOT EXISTS series (
    series_id TEXT NOT NULL,
    date TEXT NOT NULL,
    value REAL,
    PRIMARY KEY (series_id, date)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS short_volume (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    short_volume REAL,
    total_volume REAL,
    PRIMARY KEY (symbol, date)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT PRIMARY KEY,
    added_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS source_status (
    source TEXT PRIMARY KEY,
    last_attempt REAL,
    last_success REAL,
    last_error TEXT,
    detail TEXT
);
"""

PRICE_COLUMNS = ["Open", "High", "Low", "Close", "AdjClose", "Volume"]
_PRICE_FIELDS = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "AdjClose": "adj_close", "Volume": "volume"}


def _clean(value) -> float | None:
    """Convert a numeric value to something SQLite stores faithfully (NaN -> NULL)."""
    if value is None:
        return None
    number = float(value)
    return None if np.isnan(number) or np.isinf(number) else number


class Store:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else DEFAULT_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._version = 0
        self._version_lock = threading.Lock()
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ version
    @property
    def version(self) -> int:
        """Increases whenever new data lands, so derived caches know to recompute."""
        return self._version

    def bump_version(self) -> int:
        with self._version_lock:
            self._version += 1
            return self._version

    # ------------------------------------------------------------------- prices
    def upsert_prices(self, symbol: str, frame: pd.DataFrame) -> int:
        if frame is None or frame.empty:
            return 0
        rows = []
        for stamp, row in zip(frame.index, frame.to_dict("records")):
            rows.append((
                symbol,
                pd.Timestamp(stamp).strftime("%Y-%m-%d"),
                _clean(row.get("Open")),
                _clean(row.get("High")),
                _clean(row.get("Low")),
                _clean(row.get("Close")),
                _clean(row.get("AdjClose", row.get("Close"))),
                _clean(row.get("Volume")),
            ))
        with self.connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO prices (symbol, date, open, high, low, close, adj_close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def replace_prices(self, symbol: str, frame: pd.DataFrame) -> int:
        """Drop stored history for a symbol and write a fresh copy (after a split/dividend)."""
        with self.connect() as conn:
            conn.execute("DELETE FROM prices WHERE symbol = ?", (symbol,))
        return self.upsert_prices(symbol, frame)

    def get_prices(self, symbol: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        clauses, params = ["symbol = ?"], [symbol]
        if start:
            clauses.append("date >= ?")
            params.append(str(start))
        if end:
            clauses.append("date <= ?")
            params.append(str(end))
        query = (
            "SELECT date, open, high, low, close, adj_close, volume FROM prices WHERE "
            + " AND ".join(clauses)
            + " ORDER BY date"
        )
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        frame = pd.DataFrame(rows, columns=["date", *PRICE_COLUMNS])
        frame.index = pd.to_datetime(frame.pop("date"))
        frame.index.name = "Date"
        return frame.astype(float)

    def get_panel(self, symbols: Iterable[str], field: str = "Close", start: str | None = None) -> pd.DataFrame:
        """Return a date x symbol table of one price field."""
        column = _PRICE_FIELDS[field]
        symbols = list(symbols)
        if not symbols:
            return pd.DataFrame()
        placeholders = ",".join("?" * len(symbols))
        query = f"SELECT date, symbol, {column} FROM prices WHERE symbol IN ({placeholders})"
        params: list = list(symbols)
        if start:
            query += " AND date >= ?"
            params.append(str(start))
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        if not rows:
            return pd.DataFrame()
        long = pd.DataFrame(rows, columns=["date", "symbol", "value"])
        wide = long.pivot(index="date", columns="symbol", values="value")
        wide.index = pd.to_datetime(wide.index)
        return wide.sort_index().astype(float)

    def last_price_dates(self, symbols: Iterable[str] | None = None) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT symbol, MAX(date) FROM prices GROUP BY symbol").fetchall()
        last = {symbol: date for symbol, date in rows}
        if symbols is None:
            return last
        wanted = set(symbols)
        return {symbol: date for symbol, date in last.items() if symbol in wanted}

    def history_from(self, symbol: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT history_from FROM symbol_meta WHERE symbol = ?", (symbol,)).fetchone()
        return row[0] if row else None

    def set_history_from(self, symbol: str, start: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO symbol_meta (symbol, history_from, fetched_at) VALUES (?, ?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET history_from = excluded.history_from, fetched_at = excluded.fetched_at",
                (symbol, start, time.time()),
            )

    def touch_symbol(self, symbol: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO symbol_meta (symbol, fetched_at) VALUES (?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET fetched_at = excluded.fetched_at",
                (symbol, time.time()),
            )

    def symbol_fetched_at(self, symbol: str) -> float | None:
        with self.connect() as conn:
            row = conn.execute("SELECT fetched_at FROM symbol_meta WHERE symbol = ?", (symbol,)).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------- series
    def upsert_series(self, series_id: str, values: pd.Series) -> int:
        clean = values.dropna() if isinstance(values, pd.Series) else pd.Series(dtype=float)
        if clean.empty:
            return 0
        rows = [
            (series_id, pd.Timestamp(stamp).strftime("%Y-%m-%d"), _clean(value))
            for stamp, value in clean.items()
        ]
        with self.connect() as conn:
            conn.executemany("INSERT OR REPLACE INTO series (series_id, date, value) VALUES (?, ?, ?)", rows)
        return len(rows)

    def upsert_series_bulk(self, table: dict[str, pd.Series]) -> int:
        rows = []
        for series_id, values in table.items():
            clean = values.dropna()
            rows.extend(
                (series_id, pd.Timestamp(stamp).strftime("%Y-%m-%d"), _clean(value))
                for stamp, value in clean.items()
            )
        if rows:
            with self.connect() as conn:
                conn.executemany("INSERT OR REPLACE INTO series (series_id, date, value) VALUES (?, ?, ?)", rows)
        return len(rows)

    def get_series(self, series_id: str, start: str | None = None) -> pd.Series:
        query = "SELECT date, value FROM series WHERE series_id = ?"
        params: list = [series_id]
        if start:
            query += " AND date >= ?"
            params.append(str(start))
        query += " ORDER BY date"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        if not rows:
            return pd.Series(dtype=float, name=series_id, index=pd.DatetimeIndex([]))
        dates, values = zip(*rows)
        return pd.Series(values, index=pd.to_datetime(list(dates)), name=series_id, dtype=float)

    def existing_series_dates(self, series_id: str) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT date FROM series WHERE series_id = ?", (series_id,)).fetchall()
        return {row[0] for row in rows}

    def series_count(self, prefix: str) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM series WHERE series_id LIKE ?", (prefix + "%",)).fetchone()
        return int(row[0])

    # -------------------------------------------------------------- short volume
    def upsert_short_volume(self, rows: list[tuple[str, str, float, float]]) -> int:
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO short_volume (symbol, date, short_volume, total_volume) VALUES (?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def get_short_volume(self, symbol: str) -> pd.DataFrame:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT date, short_volume, total_volume FROM short_volume WHERE symbol = ? ORDER BY date",
                (symbol,),
            ).fetchall()
        frame = pd.DataFrame(rows, columns=["date", "short_volume", "total_volume"])
        frame.index = pd.to_datetime(frame.pop("date"))
        return frame.astype(float)

    def short_volume_dates(self) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT DISTINCT date FROM short_volume").fetchall()
        return {row[0] for row in rows}

    def prune_short_volume(self, keep_from: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM short_volume WHERE date < ?", (keep_from,))

    # ----------------------------------------------------------------------- kv
    def kv_set(self, key: str, value) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv (key, value, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )

    def kv_get(self, key: str, default=None, max_age: float | None = None):
        with self.connect() as conn:
            row = conn.execute("SELECT value, updated_at FROM kv WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        if max_age is not None and time.time() - row[1] > max_age:
            return default
        return json.loads(row[0])

    def kv_time(self, key: str) -> float | None:
        with self.connect() as conn:
            row = conn.execute("SELECT updated_at FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    # ---------------------------------------------------------------- watchlist
    def watchlist(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT symbol FROM watchlist ORDER BY added_at, symbol").fetchall()
        return [row[0] for row in rows]

    def watchlist_add(self, symbol: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO watchlist (symbol, added_at) VALUES (?, ?)", (symbol, time.time()))

    def watchlist_remove(self, symbol: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))

    def seed_watchlist(self, symbols: Iterable[str]) -> None:
        """Fill the watchlist once, on the very first run only."""
        if self.kv_get("watchlist:seeded"):
            return
        now = time.time()
        with self.connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO watchlist (symbol, added_at) VALUES (?, ?)",
                [(symbol, now + index / 1000) for index, symbol in enumerate(symbols)],
            )
        self.kv_set("watchlist:seeded", True)

    # ------------------------------------------------------------- source status
    def status_attempt(self, source: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO source_status (source, last_attempt) VALUES (?, ?) "
                "ON CONFLICT(source) DO UPDATE SET last_attempt = excluded.last_attempt",
                (source, time.time()),
            )

    def status_success(self, source: str, detail: str | None = None) -> None:
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO source_status (source, last_attempt, last_success, last_error, detail) VALUES (?, ?, ?, NULL, ?) "
                "ON CONFLICT(source) DO UPDATE SET last_attempt = excluded.last_attempt, last_success = excluded.last_success, "
                "last_error = NULL, detail = excluded.detail",
                (source, now, now, detail),
            )

    def status_error(self, source: str, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO source_status (source, last_attempt, last_error) VALUES (?, ?, ?) "
                "ON CONFLICT(source) DO UPDATE SET last_attempt = excluded.last_attempt, last_error = excluded.last_error",
                (source, time.time(), error[:500]),
            )

    def status_all(self) -> dict[str, dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT source, last_attempt, last_success, last_error, detail FROM source_status").fetchall()
        return {
            row[0]: {"last_attempt": row[1], "last_success": row[2], "last_error": row[3], "detail": row[4]}
            for row in rows
        }

    def file_size(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0
