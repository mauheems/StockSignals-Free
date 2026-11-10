"""What prediction markets expect: Fed decisions, recession, inflation, jobs and earnings.

Kalshi and Polymarket publish their market prices through public, keyless JSON endpoints.
A contract that pays $1 if something happens and trades at $0.76 means traders put the
odds at about 76%. Prices are read, never traded, and nothing about the user is sent.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time

import pandas as pd

from src.data.market_calendar import now_et
from src.data.store import Store
from src.data.web import fetch

logger = logging.getLogger(__name__)

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
POLYMARKET = "https://gamma-api.polymarket.com"
KALSHI_PAGE = "https://kalshi.com/markets/{series}"
POLYMARKET_PAGE = "https://polymarket.com/event/{slug}"

# Order of the Fed outcomes, from the biggest cut to the biggest hike.
FED_STEPS = {"C26": -2, "C25": -1, "H0": 0, "H25": 1, "H26": 2}
LADDERS = {
    # key: (Kalshi series, what it measures, unit, number format, whether results come in fixed steps)
    "cpi_mom": ("KXCPI", "CPI, monthly change", "%", "spct1", True),
    "core_cpi_mom": ("KXCPICORE", "Core CPI, monthly change", "%", "spct1", True),
    "cpi": ("KXCPIYOY", "CPI inflation, year over year", "%", "pct1", True),
    "unemployment": ("KXU3", "Unemployment rate", "%", "pct1", True),
    "payrolls": ("KXPAYROLLS", "Payrolls change", "jobs", "compact", False),
    "gdp": ("KXGDP", "Real GDP growth, annualized", "%", "pct1", False),
}
FED_PATH_MEETINGS = 8
MAX_SPREAD = 0.15  # wider bid-ask spreads say little; use the last trade instead
_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


# ------------------------------------------------------------------------ helpers
def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _close_time(market: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(market["close_time"].replace("Z", "+00:00"))


def kalshi_price(market: dict) -> float | None:
    """Probability (0-1) from a Kalshi market: the bid-ask midpoint, or the last trade."""
    bid = _number(market.get("yes_bid_dollars"))
    ask = _number(market.get("yes_ask_dollars"))
    if bid is not None and ask is not None and ask > 0 and ask - bid <= MAX_SPREAD:
        return (bid + ask) / 2
    return _number(market.get("last_price_dollars"))


def kalshi_quote(market: dict) -> float | None:
    """Probability from a live two-sided quote only: no stale last trade, no wide spread."""
    bid = _number(market.get("yes_bid_dollars"))
    ask = _number(market.get("yes_ask_dollars"))
    if bid is None or ask is None or ask <= 0 or ask - bid > MAX_SPREAD:
        return None
    return (bid + ask) / 2


def _kalshi_markets(series: str) -> list[dict]:
    response = fetch(f"{KALSHI}/markets?series_ticker={series}&status=open&limit=1000", browser=False, timeout=30)
    return response.json().get("markets", [])


def _next_event(markets: list[dict], now: dt.datetime) -> list[dict]:
    """Markets of the soonest event that hasn't closed yet."""
    events: dict[str, list[dict]] = {}
    for market in markets:
        if _close_time(market) > now:
            events.setdefault(market["event_ticker"], []).append(market)
    if not events:
        return []
    return min(events.values(), key=lambda group: _close_time(group[0]))


def _volume(markets: list[dict]) -> float:
    return float(sum(_number(market.get("volume_fp")) or 0.0 for market in markets))


def period_label(event_ticker: str, title: str = "") -> str | None:
    """The period a market is about: from its question when it says, else from its ticker.

    'Will real GDP increase by more than 4.0% in Q4 2026?' -> 'Q4 2026'; 'KXCPIYOY-26NOV' -> 'November 2026'.
    """
    stated = re.search(r" in (Q[1-4] \d{4}|[A-Z][a-z]+ \d{4})\??$", title or "")
    if stated:
        return stated.group(1)
    match = re.search(r"-(\d{2})([A-Z]{3})(?:\d{2})?$", event_ticker)
    if not match or match.group(2) not in _MONTHS:
        return None
    return dt.date(2000 + int(match.group(1)), _MONTHS[match.group(2)], 1).strftime("%B %Y")


# ---------------------------------------------------------------------- Kalshi
def fed_next(markets: list[dict], now: dt.datetime, strict: bool = False) -> dict | None:
    """Odds of each outcome at the next Fed meeting.

    With `strict`, every outcome needs a live two-sided quote, for thinly traded later meetings.
    """
    group = _next_event(markets, now)
    outcomes = []
    for market in group:
        step = FED_STEPS.get(market["ticker"].rsplit("-", 1)[-1])
        price = kalshi_quote(market) if strict else kalshi_price(market)
        if step is not None and price is None and strict:
            return None
        if step is None or price is None:
            continue
        outcomes.append({"step": step, "label": market.get("yes_sub_title") or market["ticker"], "prob": price})
    total = sum(item["prob"] for item in outcomes)
    if not outcomes or total <= 0:
        return None
    for item in outcomes:
        item["prob"] = item["prob"] / total  # quotes don't add up to exactly 100%
    outcomes.sort(key=lambda item: item["step"])
    likely = max(outcomes, key=lambda item: item["prob"])
    return {
        "event": group[0]["event_ticker"],
        "date": _close_time(group[0]).astimezone(now.tzinfo).date().isoformat(),
        "outcomes": outcomes,
        "likely": likely["label"],
        "likely_prob": likely["prob"],
        "hike": sum(item["prob"] for item in outcomes if item["step"] > 0),
        "cut": sum(item["prob"] for item in outcomes if item["step"] < 0),
        "hold": sum(item["prob"] for item in outcomes if item["step"] == 0),
        "volume": _volume(group),
        "source": "Kalshi",
        "url": KALSHI_PAGE.format(series="kxfeddecision"),
    }


def recession(markets: list[dict], now: dt.datetime) -> list[dict]:
    """Odds of a US recession starting in each listed year (as later dated by the NBER)."""
    rows = []
    for market in markets:
        match = re.search(r"-(\d{2})$", market["ticker"])
        price = kalshi_price(market)
        if not match or price is None or _close_time(market) <= now:
            continue
        rows.append({"year": 2000 + int(match.group(1)), "prob": price, "volume": _volume([market]), "ticker": market["ticker"]})
    return sorted(rows, key=lambda row: row["year"])


def next_year_recession(rows: list[dict], today: dt.date) -> dict | None:
    """The 'next year' market, which says the most about what lies ahead."""
    for row in rows:
        if row["year"] == today.year + 1:
            return row
    return rows[-1] if rows else None


def _buckets(strikes: list[float], above: list[float], discrete: bool) -> tuple[list[dict], float]:
    """Odds of each outcome between two listed levels, and the probability-weighted average.

    For results that come in fixed steps (CPI to one decimal, the Fed in quarter points) the
    outcome above level k and up to the next level is simply that next level.
    """
    gaps = sorted(b - a for a, b in zip(strikes, strikes[1:]))
    step = gaps[len(gaps) // 2] if gaps else 1.0
    rows = [{"lo": None, "hi": strikes[0], "prob": 1.0 - above[0], "value": strikes[0] if discrete else strikes[0] - step / 2}]
    for index in range(len(strikes) - 1):
        lo, hi = strikes[index], strikes[index + 1]
        rows.append({"lo": lo, "hi": hi, "prob": above[index] - above[index + 1], "value": hi if discrete else (lo + hi) / 2})
    rows.append({"lo": strikes[-1], "hi": None, "prob": above[-1], "value": strikes[-1] + step if discrete else strikes[-1] + step / 2})
    total = sum(row["prob"] for row in rows) or 1.0
    for row in rows:
        row["prob"] = max(0.0, row["prob"]) / total
    return rows, sum(row["prob"] * row["value"] for row in rows)


def ladder(markets: list[dict], now: dt.datetime, discrete: bool = False) -> dict | None:
    """Middle estimate, likely range and odds per outcome from a ladder of 'Above X' markets."""
    return ladder_from(_next_event(markets, now), now, discrete)


def ladder_from(group: list[dict], now: dt.datetime, discrete: bool = False) -> dict | None:
    if not group:
        return None
    # Only levels with a live two-sided quote: a far-off level often has no real market,
    # and its stale last trade would bend the whole estimate.
    points = []
    listed = []
    for market in group:
        strike = _number(market.get("floor_strike"))
        if strike is None:
            continue
        listed.append(strike)
        price = kalshi_quote(market)
        if price is not None:
            points.append((strike, price))
    points.sort()
    listed.sort()
    # Usable only when the quoted levels straddle the middle (odds beyond them go into the
    # outer buckets) and no big step in the odds hides behind a level without a quote.
    if len(points) < 3 or points[0][1] < 0.6 or points[-1][1] > 0.4:
        return None
    for (low, low_odds), (high, high_odds) in zip(points, points[1:]):
        skipped = any(low < strike < high for strike in listed)
        if skipped and low_odds - high_odds > 0.15:
            return None
    # The chance of being above a level can only fall as the level rises; smooth out quotes that disagree.
    strikes = [strike for strike, _ in points]
    above = []
    lowest = 1.0
    for _, price in points:
        lowest = min(lowest, price)
        above.append(lowest)

    def level_at(target: float) -> float | None:
        for index in range(1, len(strikes)):
            high, low = above[index - 1], above[index]
            if high >= target >= low and high != low:
                share = (high - target) / (high - low)
                return strikes[index - 1] + share * (strikes[index] - strikes[index - 1])
        return None

    buckets, expected = _buckets(strikes, above, discrete)
    likely = max(buckets, key=lambda row: row["prob"])

    def outcome_at(share: float) -> float:
        """For stepped results: the outcome where the running total of odds reaches `share`."""
        running = 0.0
        for row in buckets:
            running += row["prob"]
            if running >= share - 1e-9:
                return row["value"]
        return buckets[-1]["value"]

    # Stepped results (CPI to one decimal) can only land on a step, so their middle and range
    # are actual outcomes; continuous ones (GDP, payrolls) are read between the listed levels.
    middle, low, high = (outcome_at(0.5), outcome_at(0.1), outcome_at(0.9)) if discrete else (level_at(0.5), level_at(0.9), level_at(0.1))
    return {
        "event": group[0]["event_ticker"],
        "period": period_label(group[0]["event_ticker"], group[0].get("title") or ""),
        "release": _close_time(group[0]).astimezone(now.tzinfo).date().isoformat(),
        "median": middle,
        "low": low,    # about a 90% chance of coming in at or above this
        "high": high,  # about a 10% chance of coming in above this
        "expected": expected,
        "likely": likely,
        "discrete": discrete,
        "volume": _volume(group),
        "ladder": [{"strike": strike, "above": prob} for strike, prob in zip(strikes, above)],
        "buckets": buckets,
    }


def fed_path(rate_markets: list[dict], decision_markets: list[dict], now: dt.datetime) -> list[dict]:
    """Where traders expect the Fed's rate after each coming meeting, with that meeting's odds."""
    events: dict[str, list[dict]] = {}
    for market in rate_markets:
        if _close_time(market) > now:
            events.setdefault(market["event_ticker"], []).append(market)
    decisions: dict[str, list[dict]] = {}
    for market in decision_markets:
        if _close_time(market) > now:
            decisions.setdefault(_close_time(market).date().isoformat(), []).append(market)
    rows = []
    for group in sorted(events.values(), key=lambda markets: _close_time(markets[0]))[:FED_PATH_MEETINGS]:
        reading = ladder_from(group, now, discrete=True)
        if not reading:
            continue
        day = _close_time(group[0]).astimezone(now.tzinfo).date().isoformat()
        decision = fed_next(decisions.get(_close_time(group[0]).date().isoformat(), []), now, strict=True)
        rows.append({
            "date": day,
            "expected_rate": reading["expected"],
            "likely_rate": reading["likely"]["value"],
            "likely_prob": reading["likely"]["prob"],
            "cut": decision["cut"] if decision else None,
            "hold": decision["hold"] if decision else None,
            "hike": decision["hike"] if decision else None,
            "volume": reading["volume"] + (decision["volume"] if decision else 0.0),
        })
    return rows


def _candles(series: str, ticker: str, days: int = 365) -> pd.Series:
    """Daily closing probability of one Kalshi market, for history."""
    end = int(time.time())
    url = f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks?start_ts={end - days * 86400}&end_ts={end}&period_interval=1440"
    rows = fetch(url, browser=False, timeout=30).json().get("candlesticks", [])
    values = {}
    for row in rows:
        # The closing bid-ask midpoint, like today's reading. A thin market's last trade can be
        # days old or far off, so it is only the fallback.
        bid = _number((row.get("yes_bid") or {}).get("close_dollars"))
        ask = _number((row.get("yes_ask") or {}).get("close_dollars"))
        if bid is not None and ask is not None and 0 < ask and ask - bid <= MAX_SPREAD:
            price = (bid + ask) / 2
        else:
            price = _number((row.get("price") or {}).get("close_dollars"))
        if price is not None:
            day = dt.datetime.fromtimestamp(row["end_period_ts"], tz=dt.timezone.utc).date()
            values[pd.Timestamp(day)] = price * 100.0
    return pd.Series(values, dtype=float).sort_index()


# ------------------------------------------------------------------ Polymarket
_TICKER = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,7})\)")
_CONSENSUS = re.compile(r"consensus estimate[^$]*\$(-?\d+(?:\.\d+)?)", re.I)


def polymarket_price(market: dict) -> float | None:
    bid, ask = _number(market.get("bestBid")), _number(market.get("bestAsk"))
    if bid is not None and ask is not None and 0 < ask and ask - bid <= MAX_SPREAD:
        return (bid + ask) / 2
    try:
        return float(json.loads(market.get("outcomePrices") or "[]")[0])
    except (ValueError, IndexError, TypeError):
        return None


def earnings_odds(events: list[dict], today: dt.date) -> dict[str, dict]:
    """'Will X beat quarterly earnings?' markets, by Yahoo ticker."""
    found: dict[str, dict] = {}
    for event in events:
        title = event.get("title") or ""
        match = _TICKER.search(title)
        markets = event.get("markets") or []
        if not match or not markets or "earnings" not in title.lower():
            continue
        market = markets[0]
        end = (market.get("endDate") or event.get("endDate") or "")[:10]
        prob = polymarket_price(market)
        if not end or end < today.isoformat() or prob is None or market.get("closed"):
            continue
        symbol = match.group(1).replace(".", "-")
        consensus = _CONSENSUS.search(market.get("description") or "")
        row = {
            "symbol": symbol,
            "question": market.get("question") or title,
            "prob": prob,
            "volume": _number(market.get("volumeNum") or market.get("volume")) or 0.0,
            "date": end,
            "consensus_eps": float(consensus.group(1)) if consensus else None,
            "url": POLYMARKET_PAGE.format(slug=event.get("slug") or market.get("slug")),
        }
        current = found.get(symbol)
        if current is None or (row["date"], -row["volume"]) < (current["date"], -current["volume"]):
            found[symbol] = row
    return found


def _polymarket_earnings() -> list[dict]:
    events: list[dict] = []
    for offset in range(0, 2000, 500):
        url = f"{POLYMARKET}/events?closed=false&tag_slug=earnings&limit=500&offset={offset}"
        page = fetch(url, browser=False, timeout=40).json()
        events.extend(page)
        if len(page) < 500:
            break
    return events


# ---------------------------------------------------------------------- refresh
def refresh_prediction_markets(store: Store) -> str:
    """Read the markets, keep a summary for the pages and a daily reading for history."""
    now = now_et()
    today = now.date()
    stamp = pd.Timestamp(today)
    board: dict = {"updated": time.time()}
    daily: dict[str, pd.Series] = {}
    problems = []

    try:
        decision_markets = _kalshi_markets("KXFEDDECISION")
        fed = fed_next(decision_markets, now)
        try:
            board["fed_path"] = fed_path(_kalshi_markets("KXFED"), decision_markets, now)
        except Exception as exc:
            problems.append(f"Fed path: {exc}")
        if fed:
            board["fed"] = fed
            for key in ("hike", "cut", "hold"):
                daily[f"pm:fed_{key}"] = pd.Series({stamp: fed[key] * 100.0})
            if store.get_series("pm:fed_hike").empty:
                # First run: fill in the history this meeting's markets already have.
                closes = {}
                for outcome in ("H25", "H26", "C25", "C26", "H0"):
                    try:
                        closes[outcome] = _candles("KXFEDDECISION", f"{fed['event']}-{outcome}")
                    except Exception as exc:
                        logger.info("No history for %s-%s: %s", fed["event"], outcome, exc)
                if closes:
                    # Days when an outcome didn't trade keep its last price; days when most
                    # outcomes had no price yet are left out rather than scaled up to 100%.
                    table = pd.concat(closes, axis=1).sort_index().ffill()
                    table = table[table.notna().sum(axis=1) >= max(3, len(closes) - 1)].fillna(0.0)
                    total = table.sum(axis=1).replace(0.0, float("nan"))
                    hikes = table[[c for c in ("H25", "H26") if c in table]].sum(axis=1) / total * 100.0
                    cuts = table[[c for c in ("C25", "C26") if c in table]].sum(axis=1) / total * 100.0
                    daily["pm:fed_hike"] = pd.concat([hikes.dropna(), daily["pm:fed_hike"]])
                    daily["pm:fed_cut"] = pd.concat([cuts.dropna(), daily["pm:fed_cut"]])
    except Exception as exc:
        problems.append(f"Fed: {exc}")

    try:
        rows = recession(_kalshi_markets("KXRECSSNBER"), now)
        if rows:
            board["recession"] = rows
            ahead = next_year_recession(rows, today)
            board["recession_next"] = ahead
            daily["pm:recession"] = pd.Series({stamp: ahead["prob"] * 100.0})
            if store.get_series("pm:recession").empty:
                try:
                    daily["pm:recession"] = pd.concat([_candles("KXRECSSNBER", ahead["ticker"]), daily["pm:recession"]])
                except Exception as exc:
                    logger.info("No recession history: %s", exc)
    except Exception as exc:
        problems.append(f"recession: {exc}")

    for key, (series, label, unit, fmt, discrete) in LADDERS.items():
        try:
            reading = ladder(_kalshi_markets(series), now, discrete)
        except Exception as exc:
            problems.append(f"{label}: {exc}")
            continue
        if reading and reading["median"] is not None:
            board[key] = {**reading, "label": label, "unit": unit, "fmt": fmt, "url": KALSHI_PAGE.format(series=series.lower())}
            daily[f"pm:{key}"] = pd.Series({stamp: reading["median"]})

    try:
        earnings = earnings_odds(_polymarket_earnings(), today)
        store.kv_set("pm:earnings", earnings)
        board["earnings_count"] = len(earnings)
    except Exception as exc:
        problems.append(f"earnings: {exc}")

    if len(board) <= 1:
        raise RuntimeError("Prediction markets were unavailable: " + "; ".join(problems))
    store.kv_set("pm:board", board)
    for series_id, values in daily.items():
        values = values[~values.index.duplicated(keep="last")]
        store.upsert_series(series_id, values)
    summary = f"{len(board) - 1} market groups"
    if "earnings_count" in board:
        summary += f", earnings odds for {board['earnings_count']} companies"
    if problems:
        summary += f" ({len(problems)} unavailable)"
    return summary
