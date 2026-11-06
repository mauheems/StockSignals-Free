"""S&P 500 membership, used for breadth, the stock scanner and ticker search."""

from __future__ import annotations

import io
import logging

import pandas as pd

from src.data.store import Store
from src.data.web import fetch

logger = logging.getLogger(__name__)

WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    table = frame.rename(columns={
        "Symbol": "symbol",
        "Security": "name",
        "GICS Sector": "sector",
        "GICS Sub-Industry": "industry",
    })[["symbol", "name", "sector", "industry"]].copy()
    table["symbol"] = table["symbol"].astype(str).str.strip().str.upper().str.replace(".", "-", regex=False)
    return table.drop_duplicates("symbol").sort_values("symbol").reset_index(drop=True)


def fetch_sp500() -> pd.DataFrame:
    response = fetch(WIKIPEDIA_URL, timeout=30)
    tables = pd.read_html(io.StringIO(response.text), attrs={"id": "constituents"})
    return _normalize(tables[0])


def refresh_universe(store: Store) -> str:
    """Update membership from Wikipedia. The last good list is kept if this ever fails."""
    table = fetch_sp500()
    if len(table) < 450:
        raise RuntimeError(f"S&P 500 list looks incomplete ({len(table)} rows).")
    store.kv_set("universe:sp500", table.to_dict("records"))
    return f"{len(table)} members"


def load_sp500(store: Store) -> pd.DataFrame:
    records = store.kv_get("universe:sp500")
    if records:
        return pd.DataFrame(records)
    # Nothing is bundled with the app, so this is empty until the first download finishes.
    return pd.DataFrame(columns=["symbol", "name", "sector", "industry"])
