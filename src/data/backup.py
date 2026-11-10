"""Back up and restore everything you set up yourself, as one JSON file.

Market data isn't included: it downloads again by itself. What can't be downloaded again is
yours: the watchlist and its order, tags, notes, alerts, positions, the trade journal and the
Overview's Key signals.
"""

from __future__ import annotations

import datetime as dt
import json

from src.data.store import Store

FORMAT = "market-signals-backup"
VERSION = 1


def export(store: Store) -> dict:
    return {
        "format": FORMAT,
        "version": VERSION,
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "watchlist": store.watchlist(),
        "tags": store.tags_by_symbol(),
        "notes": {symbol: note["text"] for symbol, note in store.notes().items()},
        "alerts": [{"rule": alert["rule"], "enabled": bool(alert["enabled"])} for alert in store.alerts()],
        "positions": [{key: row[key] for key in ("symbol", "shares", "cost", "opened", "stop", "target", "note")} for row in store.positions()],
        "journal": [{key: row[key] for key in ("date", "symbol", "action", "shares", "price", "reason", "snapshot", "created_at")} for row in reversed(store.journal())],
        "overview_signals": store.kv_get("overview:signals"),
    }


def _canonical(value):
    """The same rule written the same way: a browser turns 165.0 into 165 when it saves JSON."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _rule_key(rule) -> str:
    return json.dumps(_canonical(rule), sort_keys=True)


def _check(data: dict) -> None:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("This file isn't a Market Signals backup.")
    if int(data.get("version", 0)) > VERSION:
        raise ValueError("This backup was made by a newer version of the app. Update the app first.")


def restore(store: Store, data: dict, *, replace: bool = False) -> dict:
    """Bring a backup back. Merging adds what is missing; replacing first clears your own data."""
    _check(data)
    counts = {"watchlist": 0, "alerts": 0, "positions": 0, "journal": 0, "notes": 0, "tags": 0}
    if replace:
        for symbol in store.watchlist():
            store.watchlist_remove(symbol)
        for alert in store.alerts():
            store.alert_delete(alert["id"])
        for row in store.positions():
            store.position_delete(row["id"])
        for row in store.journal():
            store.journal_delete(row["id"])
        for symbol, tags in store.tags_by_symbol().items():
            for tag in tags:
                store.tag_remove(symbol, tag)
        for symbol in store.notes():
            store.note_set(symbol, "")
    current = set(store.watchlist())
    for symbol in data.get("watchlist") or []:
        if isinstance(symbol, str) and symbol not in current:
            store.watchlist_add(symbol)
            counts["watchlist"] += 1
    store.watchlist_reorder([symbol for symbol in data.get("watchlist") or [] if isinstance(symbol, str)])
    for symbol, tags in (data.get("tags") or {}).items():
        for tag in tags:
            store.tag_add(symbol, str(tag)[:30])
            counts["tags"] += 1
    for symbol, text in (data.get("notes") or {}).items():
        existing = store.note(symbol)
        if not existing or replace:
            store.note_set(symbol, str(text)[:5000])
            counts["notes"] += 1
    rules = {_rule_key(alert["rule"]) for alert in store.alerts()}
    for item in data.get("alerts") or []:
        key = _rule_key(item.get("rule"))
        if key in rules or not isinstance(item.get("rule"), dict):
            continue
        alert_id = store.alert_add(item["rule"])
        if not item.get("enabled", True):
            store.alert_set_enabled(alert_id, False)
        rules.add(key)
        counts["alerts"] += 1
    held = {(row["symbol"], row["shares"], row["cost"]) for row in store.positions()}
    for row in data.get("positions") or []:
        if (row.get("symbol"), row.get("shares"), row.get("cost")) in held:
            continue
        store.position_add(row["symbol"], float(row["shares"]), float(row["cost"]), row.get("opened"), row.get("stop"), row.get("target"), row.get("note"))
        counts["positions"] += 1
    logged = {(row["date"], row["symbol"], row["action"], row["reason"]) for row in store.journal()}
    for row in data.get("journal") or []:
        if (row.get("date"), row.get("symbol"), row.get("action"), row.get("reason")) in logged:
            continue
        store.journal_add(row["date"], row["symbol"], row["action"], row.get("shares"), row.get("price"), row.get("reason") or "", row.get("snapshot"), row.get("created_at"))
        counts["journal"] += 1
    if isinstance(data.get("overview_signals"), list):
        store.kv_set("overview:signals", data["overview_signals"])
    return counts
