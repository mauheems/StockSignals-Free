"""Macro, credit, sentiment and positioning data from free public files."""

from __future__ import annotations

import datetime as dt
import gzip
import html
import io
import logging
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from src.data.market_calendar import is_trading_day, last_completed_session, now_et
from src.data.store import ROOT_DIR, Store
from src.data.web import NotFound, fetch

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- FRED

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

FRED_SERIES = [
    "DGS3MO", "DGS2", "DGS5", "DGS10", "DGS30", "T10Y2Y", "T10Y3M",
    "BAMLH0A0HYM2", "BAMLC0A0CM", "BAMLH0A3HYC",
    "NFCI", "STLFSI4",
    "WALCL", "WTREGEN", "RRPONTSYD", "M2SL",
    "ICSA", "UNRATE", "SAHMREALTIME", "PAYEMS",
    "CPIAUCSL", "CPILFESL", "PCEPILFE",
    "INDPRO", "UMCSENT",
    "DFII10", "T10YIE", "DFF",
    "USREC",
    "A191RL1Q225SBEA",  # real GDP growth, annualized, for the economic calendar
]


def _fetch_fred(series: str) -> tuple[str, pd.Series | None]:
    try:
        # FRED stalls on browser user agents and answers plain clients immediately.
        response = fetch(FRED_URL.format(series=series), browser=False, timeout=45)
    except Exception as exc:
        logger.warning("FRED %s failed: %s", series, exc)
        return series, None
    frame = pd.read_csv(io.StringIO(response.text))
    if frame.shape[1] < 2:
        return series, None
    values = pd.to_numeric(frame.iloc[:, 1], errors="coerce")
    result = pd.Series(values.values, index=pd.to_datetime(frame.iloc[:, 0]), dtype=float).dropna()
    return series, result


def refresh_fred(store: Store) -> str:
    table: dict[str, pd.Series] = {}
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for series, values in pool.map(_fetch_fred, FRED_SERIES):
            if values is None or values.empty:
                failed.append(series)
            else:
                table[f"fred:{series}"] = values
    if not table:
        raise RuntimeError("FRED returned no series.")
    store.upsert_series_bulk(table)
    detail = f"{len(table)} of {len(FRED_SERIES)} series"
    if failed:
        detail += f" (missing: {', '.join(failed)})"
    return detail


# ---------------------------------------------------------------- US Treasury curve

TREASURY_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
    "{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv"
)
TREASURY_TENORS = {
    "1 Mo": "1M", "2 Mo": "2M", "3 Mo": "3M", "4 Mo": "4M", "6 Mo": "6M", "1 Yr": "1Y", "2 Yr": "2Y",
    "3 Yr": "3Y", "5 Yr": "5Y", "7 Yr": "7Y", "10 Yr": "10Y", "20 Yr": "20Y", "30 Yr": "30Y",
}


def refresh_treasury(store: Store) -> str:
    year = now_et().year
    years = [year] if store.kv_get("treasury:backfilled") else [year, year - 1, year - 2]
    table: dict[str, list[pd.Series]] = {}
    latest = None
    for current in years:
        response = fetch(TREASURY_URL.format(year=current), timeout=45)
        frame = pd.read_csv(io.StringIO(response.text))
        if frame.empty or "Date" not in frame.columns:
            continue
        frame.index = pd.to_datetime(frame.pop("Date"), format="%m/%d/%Y")
        latest = max(latest, frame.index.max()) if latest is not None else frame.index.max()
        for column, tenor in TREASURY_TENORS.items():
            if column in frame.columns:
                table.setdefault(f"ust:{tenor}", []).append(pd.to_numeric(frame[column], errors="coerce"))
    if not table:
        raise RuntimeError("Treasury returned no yield-curve rows.")
    store.upsert_series_bulk({key: pd.concat(parts).dropna() for key, parts in table.items()})
    store.kv_set("treasury:backfilled", True)
    return f"Yield curve through {latest:%b %d, %Y}"


# ------------------------------------------------------------------- SqueezeMetrics

DIX_URL = "https://squeezemetrics.com/monitor/static/DIX.csv"


def refresh_squeeze(store: Store) -> str:
    response = fetch(DIX_URL, timeout=45)
    frame = pd.read_csv(io.StringIO(response.text))
    frame.index = pd.to_datetime(frame.pop("date"))
    store.upsert_series_bulk({
        "sm:dix": pd.to_numeric(frame["dix"], errors="coerce") * 100.0,
        "sm:gex": pd.to_numeric(frame["gex"], errors="coerce") / 1e9,
    })
    return f"DIX and GEX through {frame.index.max():%b %d, %Y}"


# ------------------------------------------------------------------ CNN Fear & Greed

CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
CNN_COMPONENTS = {
    "market_momentum_sp500": "Market momentum",
    "stock_price_strength": "Stock price strength",
    "stock_price_breadth": "Stock price breadth",
    "put_call_options": "Put and call options",
    "market_volatility_vix": "Market volatility",
    "junk_bond_demand": "Junk bond demand",
    "safe_haven_demand": "Safe haven demand",
}


def refresh_cnn(store: Store) -> str:
    payload = fetch(CNN_URL, timeout=30).json()
    current = payload["fear_and_greed"]
    history = payload.get("fear_and_greed_historical", {}).get("data", [])
    values = {pd.Timestamp(item["x"], unit="ms").normalize(): float(item["y"]) for item in history}
    if values:
        store.upsert_series("cnn:fg", pd.Series(values, dtype=float))
    components = []
    for key, label in CNN_COMPONENTS.items():
        item = payload.get(key)
        if isinstance(item, dict) and item.get("score") is not None:
            components.append({"id": key, "label": label, "score": float(item["score"]), "rating": item.get("rating")})
    store.kv_set("cnn:current", {
        "score": float(current["score"]),
        "rating": current.get("rating"),
        "timestamp": current.get("timestamp"),
        "previous_close": current.get("previous_close"),
        "previous_1_week": current.get("previous_1_week"),
        "previous_1_month": current.get("previous_1_month"),
        "previous_1_year": current.get("previous_1_year"),
        "components": components,
    })
    return f"Score {float(current['score']):.0f} ({current.get('rating')})"


# ------------------------------------------------------------------------------ AAII

AAII_URL = "https://www.aaii.com/files/surveys/sentiment.xls"


def parse_aaii(content: bytes) -> pd.DataFrame:
    # Excel's .xls files start with this signature. Anything else is a web page, usually the
    # site's bot check. That is the site's call to make, so this just waits and tries later.
    if not content.startswith(b"\xd0\xcf\x11\xe0"):
        raise ValueError("AAII answered with a web page instead of its spreadsheet, probably a bot check. Keeping the last survey and trying again later.")
    sheet = pd.read_excel(io.BytesIO(content), sheet_name="SENTIMENT", header=None, engine="xlrd")
    header_row = None
    for index in range(min(15, len(sheet))):
        cells = [str(cell).strip().lower() for cell in sheet.iloc[index].tolist()]
        if "bullish" in cells and "bearish" in cells:
            header_row = index
            break
    if header_row is None:
        raise ValueError("AAII sheet layout changed: no Bullish/Bearish header found.")
    cells = [str(cell).strip().lower() for cell in sheet.iloc[header_row].tolist()]
    bull_col, neutral_col, bear_col = cells.index("bullish"), cells.index("neutral"), cells.index("bearish")
    body = sheet.iloc[header_row + 1:]
    dates = pd.to_datetime(body.iloc[:, 0], errors="coerce")
    frame = pd.DataFrame({
        "bull": pd.to_numeric(body.iloc[:, bull_col], errors="coerce"),
        "neutral": pd.to_numeric(body.iloc[:, neutral_col], errors="coerce"),
        "bear": pd.to_numeric(body.iloc[:, bear_col], errors="coerce"),
    })
    frame.index = pd.DatetimeIndex(dates)
    frame = frame[frame.index.notna()].dropna(how="any")
    if frame["bull"].abs().max() <= 1.5:
        frame = frame * 100.0
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def refresh_aaii(store: Store) -> str:
    response = fetch(AAII_URL, timeout=60)
    frame = parse_aaii(response.content)
    store.upsert_series_bulk({
        "aaii:bull": frame["bull"],
        "aaii:neutral": frame["neutral"],
        "aaii:bear": frame["bear"],
        "aaii:spread": frame["bull"] - frame["bear"],
    })
    return f"Survey through {frame.index.max():%b %d, %Y}"


# ------------------------------------------------------------------------- CFTC COT

COT_URL = "https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip"
# CFTC renamed these contracts over the years, so each market lists all its spellings.
COT_MARKETS = {
    "es": ("E-MINI S&P 500 -", "E-MINI S&P 500 STOCK INDEX -"),
    "nq": ("NASDAQ MINI -", "NASDAQ-100 STOCK INDEX (MINI) -"),
    "vx": ("VIX FUTURES -",),
}


def parse_cot(content: bytes) -> dict[str, pd.Series]:
    archive = zipfile.ZipFile(io.BytesIO(content))
    frame = pd.read_csv(archive.open(archive.namelist()[0]), low_memory=False)
    frame["date"] = pd.to_datetime(frame["Report_Date_as_YYYY-MM-DD"])
    names = frame["Market_and_Exchange_Names"].astype(str)
    result: dict[str, pd.Series] = {}
    for key, prefixes in COT_MARKETS.items():
        rows = frame[names.str.startswith(prefixes)].set_index("date").sort_index()
        if rows.empty:
            continue
        rows = rows[~rows.index.duplicated(keep="last")]
        result[f"cot:{key}:asset_mgr"] = (rows["Asset_Mgr_Positions_Long_All"] - rows["Asset_Mgr_Positions_Short_All"]).astype(float)
        result[f"cot:{key}:lev_funds"] = (rows["Lev_Money_Positions_Long_All"] - rows["Lev_Money_Positions_Short_All"]).astype(float)
        result[f"cot:{key}:dealer"] = (rows["Dealer_Positions_Long_All"] - rows["Dealer_Positions_Short_All"]).astype(float)
        result[f"cot:{key}:oi"] = rows["Open_Interest_All"].astype(float)
    return result


def refresh_cot(store: Store) -> str:
    year = now_et().year
    years = [year] if store.kv_get("cot:backfilled") else list(range(year - 5, year + 1))
    latest = None
    for current in years:
        try:
            response = fetch(COT_URL.format(year=current), timeout=60)
        except NotFound:
            continue
        table = parse_cot(response.content)
        store.upsert_series_bulk(table)
        for series in table.values():
            latest = max(latest, series.index.max()) if latest is not None else series.index.max()
    if latest is None:
        raise RuntimeError("CFTC returned no financial futures reports.")
    store.kv_set("cot:backfilled", True)
    return f"Weekly report through {latest:%b %d, %Y}"


# ------------------------------------------------------------- FINRA short volume

FINRA_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date}.txt"
FINRA_CACHE_DIR = ROOT_DIR / "data" / "cache" / "finra"
FINRA_SESSIONS = 70


def _finra_sessions() -> list[dt.date]:
    sessions: list[dt.date] = []
    day = last_completed_session()
    while len(sessions) < FINRA_SESSIONS:
        if is_trading_day(day):
            sessions.append(day)
        day -= dt.timedelta(days=1)
    return sessions


def _finra_download(day: dt.date) -> tuple[dt.date, bytes | None]:
    path = FINRA_CACHE_DIR / f"{day:%Y%m%d}.txt.gz"
    if path.exists():
        return day, path.read_bytes()
    try:
        content = fetch(FINRA_URL.format(date=f"{day:%Y%m%d}"), timeout=30, retries=1, not_found=(403, 404)).content
    except Exception as exc:
        logger.debug("FINRA file for %s unavailable: %s", day, exc)
        return day, None
    FINRA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    compressed = gzip.compress(content)
    path.write_bytes(compressed)
    return day, compressed


def _finra_rows(day: dt.date, compressed: bytes, wanted: set[str] | None) -> list[tuple[str, str, float, float]]:
    text = gzip.decompress(compressed).decode("utf-8", errors="replace")
    frame = pd.read_csv(io.StringIO(text), sep="|", usecols=["Symbol", "ShortVolume", "TotalVolume"], dtype={"Symbol": str})
    frame = frame.dropna(subset=["Symbol"])
    # FINRA writes share classes with a slash or dot, Yahoo (and this app) with a dash.
    frame["Symbol"] = frame["Symbol"].str.replace(r"[./]", "-", regex=True)
    if wanted is not None:
        frame = frame[frame["Symbol"].isin(wanted)]
    iso = day.isoformat()
    return [
        (symbol, iso, float(short), float(total))
        for symbol, short, total in zip(frame["Symbol"], frame["ShortVolume"], frame["TotalVolume"])
        if total and total > 0
    ]


def refresh_finra(store: Store, symbols: set[str]) -> str:
    """Store off-exchange short-sale volume for the tracked symbols."""
    sessions = _finra_sessions()
    have = store.short_volume_dates()
    known = set(store.kv_get("finra:symbols", []))
    new_symbols = symbols - known
    with ThreadPoolExecutor(max_workers=4) as pool:
        downloads = list(pool.map(_finra_download, sessions))
    latest = None
    for day, compressed in downloads:
        if compressed is None:
            continue
        latest = max(latest, day) if latest else day
        if day.isoformat() in have:
            if not new_symbols:
                continue
            rows = _finra_rows(day, compressed, new_symbols)
        else:
            rows = _finra_rows(day, compressed, symbols)
        store.upsert_short_volume(rows)
    keep_from = sessions[-1].isoformat()
    store.prune_short_volume(keep_from)
    if FINRA_CACHE_DIR.exists():
        keep_names = {f"{day:%Y%m%d}.txt.gz" for day in sessions}
        for path in FINRA_CACHE_DIR.iterdir():
            if path.name not in keep_names:
                path.unlink(missing_ok=True)
    store.kv_set("finra:symbols", sorted(symbols | known))
    if latest is None:
        raise RuntimeError("FINRA short-volume files were unavailable.")
    return f"{len(sessions)} sessions through {latest:%b %d, %Y}"


def backfill_finra_symbol(store: Store, symbol: str) -> None:
    """Fill in short volume for a newly added ticker from the cached daily files."""
    if not FINRA_CACHE_DIR.exists():
        return
    for path in sorted(FINRA_CACHE_DIR.glob("*.txt.gz")):
        day = dt.datetime.strptime(path.name[:8], "%Y%m%d").date()
        store.upsert_short_volume(_finra_rows(day, path.read_bytes(), {symbol}))
    known = set(store.kv_get("finra:symbols", []))
    store.kv_set("finra:symbols", sorted(known | {symbol}))


# ------------------------------------------------------------------ economic events

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FRED_CALENDAR_URL = "https://fred.stlouisfed.org/releases/calendar?rid={rid}&ve={end}&vs={start}"
FRED_RELEASES = {
    10: ("CPI inflation report", "cpi_yoy"),
    50: ("Jobs report (nonfarm payrolls)", "payrolls"),
    54: ("PCE inflation and spending", "core_pce_yoy"),
    53: ("GDP report", "econ_calendar"),
    46: ("PPI producer prices", "econ_calendar"),
    9: ("Retail sales", "econ_calendar"),
}
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def parse_fomc(text: str, today: dt.date) -> list[dict]:
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", html.unescape(plain))
    events: list[dict] = []
    # The page is organised by "<year> FOMC Meetings" headings, each listing date ranges.
    sections = re.split(r"(\d{4}) FOMC Meetings", plain)
    for index in range(1, len(sections) - 1, 2):
        year = int(sections[index])
        body = sections[index + 1]
        # The day (or range) must be taken whole: otherwise "January 25-26, 2028" and "October 28, 2026"
        # could still match as "January 25-2" and "October 2" and slip past the year check.
        for match in re.finditer(rf"(?<!Released )({_MONTHS})(?:/({_MONTHS}))? (\d{{1,2}})(?:-(\d{{1,2}}))?(?![\d-])(\*)?(?!,? \d{{4}})", body):
            first_month, second_month, first_day, last_day, projections = match.groups()
            month_name = second_month or first_month
            try:
                day = dt.datetime.strptime(f"{month_name} {last_day or first_day} {year}", "%B %d %Y").date()
            except ValueError:
                continue
            if day >= today:
                label = "FOMC rate decision and projections" if projections else "FOMC rate decision"
                events.append({"date": day.isoformat(), "label": label, "kind": "macro", "metric": "econ_calendar"})
    unique = {item["date"]: item for item in events}
    return [unique[key] for key in sorted(unique)]


def parse_fred_calendar(text: str) -> list[dt.date]:
    found = re.findall(rf"font-weight: bold;\">\w+ ((?:{_MONTHS}) \d{{1,2}}, \d{{4}})<", text)
    days = []
    for item in found:
        try:
            days.append(dt.datetime.strptime(item, "%B %d, %Y").date())
        except ValueError:
            continue
    return sorted(set(days))


def refresh_events(store: Store) -> str:
    today = now_et().date()
    events: list[dict] = []
    errors = 0
    try:
        events.extend(parse_fomc(fetch(FOMC_URL, timeout=30).text, today))
    except Exception as exc:
        logger.warning("FOMC calendar failed: %s", exc)
        errors += 1
    end = today + dt.timedelta(days=120)
    for rid, (label, metric) in FRED_RELEASES.items():
        try:
            url = FRED_CALENDAR_URL.format(rid=rid, start=today.isoformat(), end=end.isoformat())
            days = parse_fred_calendar(fetch(url, browser=False, timeout=30).text)
        except Exception as exc:
            logger.warning("FRED release calendar %s failed: %s", rid, exc)
            errors += 1
            continue
        events.extend({"date": day.isoformat(), "label": label, "kind": "macro", "metric": metric} for day in days if day >= today)
    if not events and errors:
        raise RuntimeError("Economic calendars were unavailable.")
    store.kv_set("events:macro", sorted(events, key=lambda item: item["date"]))
    return f"{len(events)} upcoming macro events"
