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
    added_at REAL NOT NULL,
    position REAL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    last_state INTEGER,
    last_value REAL,
    last_date TEXT,
    last_checked REAL,
    last_event_key TEXT,
    last_triggered REAL,
    position REAL
);

CREATE TABLE IF NOT EXISTS alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL,
    at REAL NOT NULL,
    date TEXT,
    message TEXT NOT NULL,
    value REAL,
    read INTEGER NOT NULL DEFAULT 0
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
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Bring databases made by older versions up to date. Safe to run every time."""
        # The watchlist and alerts gained a user-chosen order. Existing rows keep the
        # order they had: watchlist by date added, alerts by creation.
        for table, order in (("watchlist", "added_at, symbol"), ("alerts", "id")):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "position" in columns:
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN position REAL")
            except sqlite3.OperationalError as exc:  # another process got there first
                if "duplicate column" not in str(exc):
                    raise
                continue
            key = "symbol" if table == "watchlist" else "id"
            rows = conn.execute(f"SELECT {key} FROM {table} ORDER BY {order}").fetchall()
            conn.executemany(f"UPDATE {table} SET position = ? WHERE {key} = ?", [(index + 1, row[0]) for index, row in enumerate(rows)])

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

    def kv_delete(self, key: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM kv WHERE key = ?", (key,))

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
            rows = conn.execute("SELECT symbol FROM watchlist ORDER BY position IS NULL, position, added_at, symbol").fetchall()
        return [row[0] for row in rows]

    def watchlist_add(self, symbol: str) -> None:
        """Add a symbol at the end of the list."""
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist (symbol, added_at, position) VALUES (?, ?, (SELECT COALESCE(MAX(position), 0) + 1 FROM watchlist))",
                (symbol, time.time()),
            )

    def watchlist_reorder(self, symbols: Iterable[str]) -> list[str]:
        """Put the watchlist in the given order. Symbols not mentioned keep their place at the end."""
        current = self.watchlist()
        wanted = [symbol for symbol in dict.fromkeys(symbols) if symbol in current]
        order = wanted + [symbol for symbol in current if symbol not in wanted]
        with self.connect() as conn:
            conn.executemany("UPDATE watchlist SET position = ? WHERE symbol = ?", [(index + 1, symbol) for index, symbol in enumerate(order)])
        return order

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
                "INSERT OR IGNORE INTO watchlist (symbol, added_at, position) VALUES (?, ?, ?)",
                [(symbol, now + index / 1000, index + 1) for index, symbol in enumerate(symbols)],
            )
        self.kv_set("watchlist:seeded", True)

    # ------------------------------------------------------------------- alerts
    _ALERT_COLUMNS = "id, rule, enabled, created_at, last_state, last_value, last_date, last_checked, last_event_key, last_triggered"

    def _alert_row(self, row) -> dict:
        keys = [key.strip() for key in self._ALERT_COLUMNS.split(",")]
        alert = dict(zip(keys, row))
        alert["rule"] = json.loads(alert["rule"])
        return alert

    def alerts(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(f"SELECT {self._ALERT_COLUMNS} FROM alerts ORDER BY position IS NULL, position, id").fetchall()
        return [self._alert_row(row) for row in rows]

    def alerts_reorder(self, ids: Iterable[int]) -> list[int]:
        """Put the alerts in the given order. Alerts not mentioned keep their place at the end."""
        current = [alert["id"] for alert in self.alerts()]
        wanted = [alert_id for alert_id in dict.fromkeys(ids) if alert_id in current]
        order = wanted + [alert_id for alert_id in current if alert_id not in wanted]
        with self.connect() as conn:
            conn.executemany("UPDATE alerts SET position = ? WHERE id = ?", [(index + 1, alert_id) for index, alert_id in enumerate(order)])
        return order

    def alert(self, alert_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(f"SELECT {self._ALERT_COLUMNS} FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        return self._alert_row(row) if row else None

    def alert_add(self, rule: dict) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO alerts (rule, created_at, position) VALUES (?, ?, (SELECT COALESCE(MAX(position), 0) + 1 FROM alerts))",
                (json.dumps(rule), time.time()),
            )
            return int(cursor.lastrowid)

    def alert_set_enabled(self, alert_id: int, enabled: bool) -> bool:
        with self.connect() as conn:
            # Re-enabling starts fresh, so a condition that is still true is reported again.
            cursor = conn.execute(
                "UPDATE alerts SET enabled = ?, last_state = CASE WHEN ? THEN NULL ELSE last_state END WHERE id = ?",
                (int(enabled), int(enabled), alert_id),
            )
            return cursor.rowcount > 0

    def alert_delete(self, alert_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
            return cursor.rowcount > 0

    def alert_update(self, alert_id: int, *, last_checked_only: bool = False, last_state: int | None = None,
                     last_value: float | None = None, last_date: str | None = None,
                     last_event_key: str | None = None, fired: bool = False) -> None:
        now = time.time()
        with self.connect() as conn:
            if last_checked_only:
                conn.execute("UPDATE alerts SET last_checked = ? WHERE id = ?", (now, alert_id))
                return
            conn.execute(
                "UPDATE alerts SET last_state = ?, last_value = ?, last_date = ?, last_checked = ?, last_event_key = ?, "
                "last_triggered = CASE WHEN ? THEN ? ELSE last_triggered END WHERE id = ?",
                (last_state, _clean(last_value), last_date, now, last_event_key, int(fired), now, alert_id),
            )

    def alert_symbols(self) -> list[str]:
        symbols = set()
        for alert in self.alerts():
            if alert["rule"].get("kind") == "stock":
                symbols.add(alert["rule"]["symbol"])
        return sorted(symbols)

    def alert_event_add(self, alert_id: int, date: str | None, message: str, value: float | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO alert_events (alert_id, at, date, message, value) VALUES (?, ?, ?, ?, ?)",
                (alert_id, time.time(), date, message, _clean(value)),
            )
            # Keep the history to a sensible size.
            conn.execute("DELETE FROM alert_events WHERE id NOT IN (SELECT id FROM alert_events ORDER BY id DESC LIMIT 500)")

    def alert_events(self, limit: int = 100) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, alert_id, at, date, message, value, read FROM alert_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        keys = ["id", "alert_id", "at", "date", "message", "value", "read"]
        return [{**dict(zip(keys, row)), "read": bool(row[6])} for row in rows]

    def alert_events_unread(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM alert_events WHERE read = 0").fetchone()[0])

    def alert_events_mark_read(self) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE alert_events SET read = 1 WHERE read = 0")

    def alert_events_clear(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM alert_events")

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
