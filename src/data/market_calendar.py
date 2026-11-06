"""US market calendar helpers: session state, holidays and option expirations."""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
    sunday_to_monday,
)

ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

SESSION_OPEN = dt.time(9, 30)
SESSION_CLOSE = dt.time(16, 0)


class NYSEHolidayCalendar(AbstractHolidayCalendar):
    rules = [
        # The exchange does not observe New Year's Day on the prior Friday.
        Holiday("New Year", month=1, day=1, observance=sunday_to_monday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


@lru_cache(maxsize=8)
def _holidays(year: int) -> frozenset[dt.date]:
    days = NYSEHolidayCalendar().holidays(start=f"{year}-01-01", end=f"{year}-12-31")
    return frozenset(day.date() for day in days)


def now_et() -> dt.datetime:
    return dt.datetime.now(ET)


def is_trading_day(day: dt.date) -> bool:
    return day.weekday() < 5 and day not in _holidays(day.year)


def is_market_open(moment: dt.datetime | None = None) -> bool:
    moment = (moment or now_et()).astimezone(ET)
    if not is_trading_day(moment.date()):
        return False
    return SESSION_OPEN <= moment.time() < SESSION_CLOSE


def previous_trading_day(day: dt.date) -> dt.date:
    day -= dt.timedelta(days=1)
    while not is_trading_day(day):
        day -= dt.timedelta(days=1)
    return day


def next_trading_day(day: dt.date) -> dt.date:
    day += dt.timedelta(days=1)
    while not is_trading_day(day):
        day += dt.timedelta(days=1)
    return day


def last_completed_session(moment: dt.datetime | None = None) -> dt.date:
    """The most recent session whose close has already happened."""
    moment = (moment or now_et()).astimezone(ET)
    day = moment.date()
    if is_trading_day(day) and moment.time() >= SESSION_CLOSE:
        return day
    return previous_trading_day(day)


def market_state(moment: dt.datetime | None = None) -> dict:
    moment = (moment or now_et()).astimezone(ET)
    day = moment.date()
    if is_market_open(moment):
        state, label = "open", "Market open"
    elif is_trading_day(day) and moment.time() < SESSION_OPEN:
        state, label = "pre", "Pre-market"
    elif is_trading_day(day) and moment.time() >= SESSION_CLOSE:
        state, label = "post", "After hours"
    else:
        state, label = "closed", "Market closed"
    return {
        "state": state,
        "label": label,
        "time_et": moment.strftime("%a %b %d, %H:%M ET"),
        "last_session": last_completed_session(moment).isoformat(),
    }


def third_friday(year: int, month: int) -> dt.date:
    first = dt.date(year, month, 1)
    first_friday = first + dt.timedelta(days=(4 - first.weekday()) % 7)
    return first_friday + dt.timedelta(days=14)


def monthly_opex(year: int, month: int) -> dt.date:
    """Standard monthly option expiration, moved to Thursday when Friday is a holiday."""
    day = third_friday(year, month)
    return day if is_trading_day(day) else previous_trading_day(day)


def vix_expiration(year: int, month: int) -> dt.date:
    """VIX futures and options expire 30 days before the next month's third Friday."""
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    day = third_friday(next_year, next_month) - dt.timedelta(days=30)
    return day if is_trading_day(day) else previous_trading_day(day)


def upcoming_expirations(today: dt.date | None = None, months: int = 3) -> list[dict]:
    today = today or now_et().date()
    events: list[dict] = []
    year, month = today.year, today.month
    for _ in range(months + 1):
        opex = monthly_opex(year, month)
        if opex >= today:
            quarterly = month in (3, 6, 9, 12)
            events.append({
                "date": opex.isoformat(),
                "label": "Quarterly option expiration (triple witching)" if quarterly else "Monthly option expiration",
                "kind": "opex",
                "metric": "opex",
            })
        vix = vix_expiration(year, month)
        if vix >= today:
            events.append({"date": vix.isoformat(), "label": "VIX expiration", "kind": "opex", "metric": "opex"})
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return sorted(events, key=lambda item: item["date"])


def to_iso_utc(timestamp: float | None) -> str | None:
    if not timestamp:
        return None
    return dt.datetime.fromtimestamp(timestamp, UTC).isoformat()


def trading_days_between(start: dt.date, end: dt.date) -> int:
    if end <= start:
        return 0
    days = pd.bdate_range(start + dt.timedelta(days=1), end)
    return sum(1 for day in days if day.date() not in _holidays(day.year))
