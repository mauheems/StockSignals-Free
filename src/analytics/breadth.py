"""Market breadth from S&P 500 members: how many stocks are taking part in a move.

Membership is today's list, so older readings carry some survivorship bias (companies
that were dropped from the index are missing). The Metric Guide explains this.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.analytics import technicals as ta
from src.data.catalog import BREADTH_HISTORY_START
from src.data.store import Store
from src.data.universe import load_sp500

# A date with fewer members than this has incomplete data (the session is just starting
# or Yahoo is still updating), and would distort every count.
MIN_MEMBER_COVERAGE = 0.8


def compute_breadth_series(close: pd.DataFrame, volume: pd.DataFrame) -> dict[str, pd.Series]:
    coverage = close.notna().sum(axis=1) / close.shape[1]
    close = close[coverage >= MIN_MEMBER_COVERAGE].ffill(limit=5)
    volume = volume.reindex(index=close.index, columns=close.columns)

    result: dict[str, pd.Series] = {}
    for window in (20, 50, 200):
        average = close.rolling(window, min_periods=window).mean()
        valid = average.notna().sum(axis=1)
        share = (close > average).sum(axis=1) / valid.replace(0, np.nan) * 100.0
        result[f"breadth:pct{window}"] = share.where(valid >= 0.5 * close.shape[1])

    change = close.diff()
    advancing = (change > 0).sum(axis=1).astype(float)
    declining = (change < 0).sum(axis=1).astype(float)
    # The first row has no prior close to compare with.
    advancing.iloc[0], declining.iloc[0] = np.nan, np.nan
    net = advancing - declining
    result["breadth:adv"] = advancing
    result["breadth:dec"] = declining
    result["breadth:net_adv"] = net
    result["breadth:ad_line"] = net.fillna(0.0).cumsum().where(net.notna())

    # Ratio-adjusted McClellan: stays comparable when the number of members changes.
    adjusted = net / (advancing + declining).replace(0.0, np.nan) * 1000.0
    fast = adjusted.ewm(span=19, adjust=False, min_periods=19).mean()
    slow = adjusted.ewm(span=39, adjust=False, min_periods=39).mean()
    oscillator = fast - slow
    result["breadth:mcclellan"] = oscillator
    result["breadth:mcclellan_sum"] = oscillator.fillna(0.0).cumsum().where(oscillator.notna())

    highest = close.rolling(ta.TRADING_DAYS, min_periods=ta.TRADING_DAYS).max()
    lowest = close.rolling(ta.TRADING_DAYS, min_periods=ta.TRADING_DAYS).min()
    new_highs = (close >= highest).sum(axis=1).astype(float).where(highest.notna().sum(axis=1) > 0)
    new_lows = (close <= lowest).sum(axis=1).astype(float).where(lowest.notna().sum(axis=1) > 0)
    net_highs = new_highs - new_lows
    result["breadth:new_highs"] = new_highs
    result["breadth:new_lows"] = new_lows
    result["breadth:net_highs"] = net_highs
    result["breadth:net_highs_10d"] = net_highs.rolling(10, min_periods=10).mean()
    share_of_highs = new_highs / (new_highs + new_lows).replace(0.0, np.nan) * 100.0
    result["breadth:hl_index"] = share_of_highs.ffill().rolling(10, min_periods=10).mean()

    up_volume = volume.where(change > 0).sum(axis=1)
    down_volume = volume.where(change < 0).sum(axis=1)
    both = (up_volume + down_volume).replace(0.0, np.nan)
    up_share = up_volume / both * 100.0
    up_share.iloc[0] = np.nan
    result["breadth:up_vol_share"] = up_share
    result["breadth:up_vol_10d"] = up_share.rolling(10, min_periods=10).mean()

    thrust_base = advancing / (advancing + declining).replace(0.0, np.nan)
    result["breadth:thrust"] = thrust_base.ewm(span=10, adjust=False, min_periods=10).mean() * 100.0
    return result


def last_breadth_thrust(thrust: pd.Series, window: int = 10) -> str | None:
    """Most recent Zweig thrust: 10-day advance share goes from under 40% to over 61.5%."""
    values = thrust.dropna()
    if values.empty:
        return None
    low_recently = values.rolling(window + 1, min_periods=1).min() < 40.0
    crossed_up = (values > 61.5) & (values.shift(1) <= 61.5)
    fired = values[crossed_up & low_recently]
    return fired.index[-1].strftime("%Y-%m-%d") if not fired.empty else None


def member_rows(close: pd.DataFrame, adj: pd.DataFrame, volume: pd.DataFrame, members: pd.DataFrame) -> list[dict]:
    """One row per member for the stock scanner, with the RS rating across the index."""
    coverage = close.notna().sum(axis=1) / close.shape[1]
    complete = coverage >= MIN_MEMBER_COVERAGE
    close, adj, volume = close[complete].ffill(limit=5), adj[complete].ffill(limit=5), volume[complete]
    info = members.set_index("symbol")
    scores: dict[str, float] = {}
    rows: list[dict] = []
    for symbol in close.columns:
        series = close[symbol].dropna()
        adjusted = adj[symbol].dropna() if symbol in adj else series
        if len(series) < 22:
            continue
        last = float(series.iloc[-1])
        sma50 = ta.sma(series, 50).iloc[-1] if len(series) >= 50 else np.nan
        sma200 = ta.sma(series, 200).iloc[-1] if len(series) >= 200 else np.nan
        window = series.tail(ta.TRADING_DAYS)
        rvol_series = ta.relative_volume(volume[symbol]) if symbol in volume else pd.Series(dtype=float)
        rvol = rvol_series.iloc[-1] if not rvol_series.empty else np.nan
        rsi_value = ta.rsi(series.tail(120)).iloc[-1]
        spread = ta.sma(series, 50) - ta.sma(series, 200)
        crossed_up = bool(len(spread.dropna()) >= 6 and spread.iloc[-1] > 0 and (spread.iloc[-6:-1] <= 0).any())
        crossed_down = bool(len(spread.dropna()) >= 6 and spread.iloc[-1] < 0 and (spread.iloc[-6:-1] >= 0).any())
        score = ta.relative_strength_score(adjusted)
        if score is not None:
            scores[symbol] = score
        record = info.loc[symbol] if symbol in info.index else None
        state = ta.trend_state(last, None if np.isnan(sma50) else float(sma50), None if np.isnan(sma200) else float(sma200))
        rows.append({
            "symbol": symbol,
            "name": str(record["name"]) if record is not None else symbol,
            "sector": str(record["sector"]) if record is not None else "",
            "industry": str(record["industry"]) if record is not None else "",
            "last": last,
            "chg_1d": ta.period_return(series, 1),
            "ret_5d": ta.period_return(adjusted, 5),
            "ret_1m": ta.period_return(adjusted, 21),
            "ret_3m": ta.period_return(adjusted, 63),
            "ret_ytd": ta.year_to_date_return(adjusted),
            "rvol": None if pd.isna(rvol) else float(rvol),
            "rsi": None if pd.isna(rsi_value) else float(rsi_value),
            "vs_sma50": None if np.isnan(sma50) else (last / float(sma50) - 1.0) * 100.0,
            "vs_sma200": None if np.isnan(sma200) else (last / float(sma200) - 1.0) * 100.0,
            "off_high": (last / float(window.max()) - 1.0) * 100.0,
            "at_high": bool(last >= float(window.max())) if len(window) >= ta.TRADING_DAYS else False,
            "at_low": bool(last <= float(window.min())) if len(window) >= ta.TRADING_DAYS else False,
            "trend": state,
            "golden_cross": crossed_up,
            "death_cross": crossed_down,
        })
    ranks = pd.Series(scores, dtype=float).rank(pct=True) * 99.0 if scores else pd.Series(dtype=float)
    for row in rows:
        row["rs_rating"] = int(round(ranks[row["symbol"]])) if row["symbol"] in ranks else None
    return rows


def sector_rows(close: pd.DataFrame, members: pd.DataFrame) -> list[dict]:
    """Breadth inside each GICS sector, now and one week ago."""
    coverage = close.notna().sum(axis=1) / close.shape[1]
    close = close[coverage >= MIN_MEMBER_COVERAGE].ffill(limit=5)
    rows: list[dict] = []
    averages = {window: close.rolling(window, min_periods=window).mean() for window in (20, 50, 200)}
    change = close.diff()
    for sector, group in members.groupby("sector"):
        symbols = [symbol for symbol in group["symbol"] if symbol in close.columns]
        if not symbols:
            continue
        row = {"sector": sector, "members": len(symbols)}
        for window, average in averages.items():
            above = close[symbols] > average[symbols]
            valid = average[symbols].notna().sum(axis=1).replace(0, np.nan)
            share = above.sum(axis=1) / valid * 100.0
            row[f"pct{window}"] = None if pd.isna(share.iloc[-1]) else float(share.iloc[-1])
            row[f"pct{window}_1w"] = None if len(share) < 6 or pd.isna(share.iloc[-6]) else float(share.iloc[-6])
        last_change = change[symbols].iloc[-1]
        row["advancing"] = int((last_change > 0).sum())
        row["declining"] = int((last_change < 0).sum())
        rows.append(row)
    return sorted(rows, key=lambda item: -(item.get("pct50") or 0.0))


def refresh_breadth_tables(store: Store) -> str:
    """Recompute all breadth results from stored member prices."""
    members = load_sp500(store)
    symbols = members["symbol"].tolist()
    close = store.get_panel(symbols, "Close", start=BREADTH_HISTORY_START)
    if close.empty or close.shape[1] < 100:
        raise RuntimeError("Not enough member prices stored yet to compute breadth.")
    volume = store.get_panel(symbols, "Volume", start=BREADTH_HISTORY_START)
    adj = store.get_panel(symbols, "AdjClose", start=BREADTH_HISTORY_START)
    series = compute_breadth_series(close, volume)
    store.upsert_series_bulk(series)
    rows = member_rows(close, adj, volume, members)
    store.kv_set("breadth:members", rows)
    store.kv_set("breadth:sectors", sector_rows(close, members))
    store.kv_set("breadth:last_thrust", last_breadth_thrust(series["breadth:thrust"]))
    store.kv_set(
        "breadth:rs_scores",
        {
            symbol: round(value, 3)
            for symbol, value in (
                (column, ta.relative_strength_score(adj[column].dropna())) for column in adj.columns
            )
            if value is not None
        },
    )
    latest = series["breadth:pct50"].dropna()
    as_of = latest.index[-1].strftime("%b %d, %Y") if not latest.empty else "n/a"
    return f"{close.shape[1]} members through {as_of}"


def rs_rating_for(store: Store, score: float | None) -> int | None:
    """Place any stock's performance score within the S&P 500 distribution (1-99)."""
    scores = store.kv_get("breadth:rs_scores")
    if score is None or not scores:
        return None
    values = np.asarray(list(scores.values()), dtype=float)
    return int(np.clip(round((values < score).mean() * 99.0), 1, 99))
