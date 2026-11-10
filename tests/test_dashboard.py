"""The API and shared catalogues, run offline against a small generated database."""

import datetime as dt
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.analytics.signals import PILLARS, REGISTRY, SELF_SIGNALS, SIGNALS, Ctx, research_series
from src.analytics.studies import Condition
from src.dashboard.app import create_app
from src.dashboard.metric_guide import GUIDE_CATEGORIES, METRICS
from src.dashboard.payloads import sanitize
from src.data import cboe, fundamentals, macro_sources
from src.data.catalog import CORE_SYMBOLS, normalize_symbol
from src.data.market_calendar import monthly_opex, vix_expiration
from src.data.store import Store
from src.data.universe import load_sp500
from src.strategies.rules import STRATEGY_TEMPLATES

STATIC = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "static" / "js"


def price_frame(seed: int, days: int = 700, drift: float = 0.0004) -> pd.DataFrame:
    random = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(random.normal(drift, 0.01, days)))
    index = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=days)
    return pd.DataFrame({
        "Open": close * 0.999, "High": close * 1.01, "Low": close * 0.99, "Close": close,
        "AdjClose": close, "Volume": random.integers(1_000_000, 5_000_000, days).astype(float),
    }, index=index)


@pytest.fixture(scope="module")
def store(tmp_path_factory, monkeypatch_module):
    db = Store(tmp_path_factory.mktemp("db") / "test.db")
    for position, symbol in enumerate(CORE_SYMBOLS):
        frame = price_frame(position + 1)
        if symbol == "^VIX":
            frame[["Open", "High", "Low", "Close", "AdjClose"]] = frame[["Open", "High", "Low", "Close", "AdjClose"]] / 5.0
        db.upsert_prices(symbol, frame)
    for symbol in ("NVDA", "AAPL", "MSFT", "AMZN", "TSLA"):
        db.upsert_prices(symbol, price_frame(hash(symbol) % 1000))
    now = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=500)
    random = np.random.default_rng(5)
    db.upsert_series_bulk({
        "breadth:pct50": pd.Series(random.uniform(20, 80, 500), index=now),
        "breadth:pct200": pd.Series(random.uniform(30, 70, 500), index=now),
        "breadth:mcclellan": pd.Series(random.uniform(-50, 50, 500), index=now),
        "breadth:net_highs_10d": pd.Series(random.uniform(-20, 20, 500), index=now),
        "breadth:up_vol_10d": pd.Series(random.uniform(30, 70, 500), index=now),
        "breadth:adv": pd.Series(300.0, index=now),
        "breadth:dec": pd.Series(200.0, index=now),
        "sm:gex": pd.Series(random.uniform(-1, 6, 500), index=now),
        "sm:dix": pd.Series(random.uniform(38, 50, 500), index=now),
        "cboe:pc_equity": pd.Series(random.uniform(0.4, 0.9, 500), index=now),
        "cboe:pc_total": pd.Series(random.uniform(0.7, 1.2, 500), index=now),
        "fred:BAMLH0A0HYM2": pd.Series(random.uniform(3, 6, 500), index=now),
        "fred:T10Y3M": pd.Series(random.uniform(-1, 2, 500), index=now),
    })
    db.seed_watchlist(["NVDA", "AAPL", "MSFT", "AMZN", "TSLA"])
    db.kv_set("universe:sp500", [{"symbol": "NVDA", "name": "Nvidia", "sector": "Information Technology", "industry": "Semiconductors"}])

    def no_network(*_args, **_kwargs):
        raise AssertionError("Tests must not touch the network.")

    monkeypatch_module.setattr(cboe, "fetch", no_network)
    monkeypatch_module.setattr(fundamentals, "fetch_fundamentals", lambda symbol: {"symbol": symbol, "info": {}, "next_earnings": None, "earnings_history": [], "insiders": None, "analysts": None})
    monkeypatch_module.setattr("src.data.market_data.yahoo_download", lambda *args, **kwargs: {})
    monkeypatch_module.setattr(macro_sources, "FINRA_CACHE_DIR", Path("/nonexistent-finra-cache"))
    from src.data import filings

    monkeypatch_module.setattr(filings, "fetch_filings", lambda symbol: {
        "symbol": symbol,
        "filings": [{"date": "2026-08-26", "form": "8-K", "kind": "Important event", "title": "Results", "url": "https://finance.yahoo.com/x", "notable": True}],
        "trades": [
            {"date": "2099-01-02", "insider": "A Person", "position": "Director", "kind": "sell", "text": "Sale", "shares": 1000.0, "value": 200000.0, "price": 200.0},
            {"date": "2099-01-03", "insider": "B Person", "position": "CEO", "kind": "buy", "text": "Purchase", "shares": 500.0, "value": 90000.0, "price": 180.0},
        ],
    })
    return db


@pytest.fixture(scope="module")
def monkeypatch_module():
    patcher = pytest.MonkeyPatch()
    yield patcher
    patcher.undo()


@pytest.fixture(scope="module")
def client(store):
    return TestClient(create_app(store, background=False), base_url="http://localhost")


# ------------------------------------------------------------------- explanations

def test_every_signal_is_explained():
    assert [item.id for item in SIGNALS if item.metric not in METRICS] == []


def test_every_technical_rule_signal_is_explained():
    assert [key for key, value in SELF_SIGNALS.items() if value["metric"] not in METRICS] == []


def test_every_strategy_is_explained():
    assert [item["id"] for item in STRATEGY_TEMPLATES if item["metric"] not in METRICS] == []


def test_every_pillar_is_explained():
    assert [key for key in PILLARS if f"pillar_{key}" not in METRICS] == []


def test_every_metric_used_by_the_front_end_is_explained():
    """Every id passed to an info button anywhere in the JavaScript has a guide entry."""
    patterns = [
        r"""info\(\s*"([a-z0-9_]+)"\s*\)""",
        r"""metric:\s*"([a-z0-9_]+)\"""",
        r"""kvRow\(\s*"[^"]*",\s*"([a-z0-9_]+)\"""",
        r"""row\(\s*"[^"]*",\s*"([a-z0-9_]+)\"""",
        r"""panelTitle\(\s*"[^"]*",\s*"([a-z0-9_]+)\"""",
        r"""field\(\s*"[^"]*",[^\n]*?,\s*"([a-z0-9_]+)"\)""",
    ]
    used: set[str] = set()
    for path in STATIC.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            used.update(re.findall(pattern, text))
    assert len(used) > 60, "The scan should find many metric ids; the patterns may have broken."
    assert sorted(used - set(METRICS)) == []


@pytest.mark.parametrize("metric_id", sorted(METRICS))
def test_explanations_are_complete(metric_id):
    entry = METRICS[metric_id]
    for field in ("name", "what", "read", "calc", "source"):
        assert entry.get(field, "").strip(), f"{metric_id} has no {field}"
    assert entry["category"] in {item["id"] for item in GUIDE_CATEGORIES}


def test_market_scoring_rules_are_described():
    """Scored signals state their thresholds, so a score can always be traced."""
    missing = [item.id for item in SIGNALS if item.score and item.pillar and "score" not in METRICS[item.metric]]
    assert missing == []


# ---------------------------------------------------------------------------- pages

@pytest.mark.parametrize("path", [
    "/api/status", "/api/market", "/api/metrics", "/api/signals", "/api/signals/current", "/api/research/options",
    "/api/overview", "/api/breadth", "/api/breadth?range=3m", "/api/rotation", "/api/rotation?universe=factors&lens=daily&tail=12",
    "/api/rotation?universe=watchlist", "/api/volatility", "/api/flows", "/api/macro", "/api/watchlist", "/api/scanner",
    "/api/stock/NVDA", "/api/chart/NVDA", "/api/chart/NVDA?interval=1wk&range=5y", "/api/options/underlyings",
    "/api/signals/aligned?symbol=SPY&ids=vix,pct_above_50,px:QQQ",
    "/api/brief", "/api/portfolio", "/api/journal", "/api/earnings", "/api/calendar", "/api/timeline",
    "/api/timeline?date=2025-06-02", "/api/stock/NVDA/filings", "/api/backup", "/api/settings/storage", "/api/prediction",
])
def test_pages_answer_with_valid_json(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.json() is not None


def test_pages_survive_when_a_source_has_no_data(client):
    # Flows, macro and sentiment have barely any data in this database, and still answer.
    body = client.get("/api/macro").json()
    assert body["rows"]["t10y2y"] is None
    assert body["charts"]["nfci"]["dates"] == []


def test_overview_has_a_score_and_pillars(client):
    body = client.get("/api/overview").json()
    assert 0 <= body["health"]["score"] <= 100
    assert {item["id"] for item in body["pillars"]} == set(PILLARS)
    assert body["health"]["label"]


def test_unknown_symbol_is_a_clean_404(client):
    response = client.get("/api/stock/NOSUCHTICK")
    assert response.status_code == 404
    assert "NOSUCHTICK" in response.json()["detail"]


def test_bad_symbol_is_rejected(client):
    assert client.get("/api/stock/DROP%20TABLE").status_code == 400


def test_bad_chart_interval_is_rejected(client):
    assert client.get("/api/chart/NVDA?interval=7m").status_code == 400


def test_unknown_api_route_is_404(client):
    assert client.get("/api/nothing-here").status_code == 404


def test_requests_for_other_hosts_are_refused(store):
    other = TestClient(create_app(store, background=False), base_url="http://evil.example")
    assert other.get("/api/status").status_code == 403


def test_cross_site_changes_are_refused(client):
    response = client.post("/api/watchlist", json={"symbol": "TSLA"}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_watchlist_add_and_remove(client, store):
    store.upsert_prices("AMD", price_frame(44))
    assert client.post("/api/watchlist", json={"symbol": "amd"}).json()["symbol"] == "AMD"
    assert "AMD" in [row["symbol"] for row in client.get("/api/watchlist").json()["rows"]]
    client.delete("/api/watchlist/AMD")
    assert "AMD" not in store.watchlist()


def test_watchlist_refuses_a_ticker_with_no_data(client, store):
    assert client.post("/api/watchlist", json={"symbol": "NOSUCHTICK"}).status_code == 404
    assert "NOSUCHTICK" not in store.watchlist()


def test_chart_includes_moving_averages_and_panes(client):
    body = client.get("/api/chart/NVDA?range=1y").json()
    for key in ("sma50", "sma200", "rsi", "macd", "obv", "adx", "bb_upper", "vwap"):
        assert key in body["indicators"]
    assert len(body["dates"]) == len(body["close"]) == len(body["indicators"]["sma200"])
    assert body["indicators"]["sma200"][-1] is not None


def test_signals_line_up_with_chart_dates(client):
    chart = client.get("/api/chart/SPY?range=1y").json()
    aligned = client.get("/api/signals/aligned?symbol=SPY&range=1y&ids=vix,px:QQQ").json()
    assert aligned["dates"] == chart["dates"]
    assert len(aligned["signals"]["vix"]["values"]) == len(chart["dates"])
    assert aligned["signals"]["px:QQQ"]["label"] == "QQQ"


def test_signals_cannot_overlay_intraday_charts(client):
    assert client.get("/api/signals/aligned?symbol=SPY&interval=15m&ids=vix").status_code == 400


def test_unknown_overlay_signal_reports_an_error_for_that_signal_only(client):
    body = client.get("/api/signals/aligned?symbol=SPY&ids=vix,not_a_signal").json()
    assert "error" in body["signals"]["not_a_signal"]
    assert "values" in body["signals"]["vix"]


# ------------------------------------------------------------------------- research

def test_backtest_overview_compares_strategies(client):
    response = client.post("/api/backtest", json={
        "symbol": "SPY", "start": "2024-06-01",
        "strategies": [{"id": "buy_and_hold"}, {"id": "ma_cross_20_50", "params": {"fast_window": 10}},
                       {"id": "custom", "kind": "rule", "name": "Mine", "conditions": [{"signal": "self:rsi14", "op": ">", "value": 50}]}],
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["id"] for row in body["rows"]] == ["buy_and_hold", "ma_cross_20_50", "custom"]
    assert body["details"]["custom"]["equity"][0] == 10000
    assert len(body["details"]["custom"]["equity"]) == len(body["dates"])


def test_backtest_adds_the_benchmark_when_left_out(client):
    body = client.post("/api/backtest", json={"symbol": "SPY", "start": "2024-06-01", "strategies": [{"id": "trend_filter_200"}]}).json()
    assert body["rows"][0]["id"] == "buy_and_hold"


@pytest.mark.parametrize("payload,fragment", [
    ({"symbol": "", "start": "2024-01-01"}, "valid ticker"),
    ({"symbol": "SPY", "start": "not-a-date"}, "YYYY-MM-DD"),
    ({"symbol": "SPY", "start": "2024-01-01", "capital": -5}, "positive"),
    ({"symbol": "SPY", "start": "2024-01-01", "strategies": [{"id": "nope"}]}, "Unknown strategy"),
    ({"symbol": "SPY", "start": "2024-01-01", "strategies": [{"id": "ma_cross_20_50", "params": {"fast_window": 90}}]}, "smaller"),
    ({"symbol": "SPY", "start": "2024-01-01", "strategies": [{"id": "c", "kind": "rule", "conditions": []}]}, "at least one condition"),
    ({"symbol": "SPY", "start": "2024-01-01", "strategies": [{"id": "c", "kind": "rule", "conditions": [{"signal": "os.system", "op": ">", "value": 1}]}]}, "Unknown signal"),
    ({"symbol": "SPY", "start": "2024-01-01", "strategies": [{"id": "c", "kind": "rule", "conditions": [{"signal": "vix", "op": "!=", "value": 1}]}]}, "Unknown operator"),
    ({"symbol": "SPY", "start": "2024-01-01", "strategies": [{"id": "c", "kind": "rule", "conditions": [{"signal": "vix", "op": ">", "value": "abc"}]}]}, "must be a number"),
])
def test_backtest_bad_requests_explain_themselves(client, payload, fragment):
    response = client.post("/api/backtest", json=payload)
    assert response.status_code == 400, response.text
    assert fragment in response.json()["detail"]


def test_study_compares_events_with_a_baseline(client):
    response = client.post("/api/study", json={"symbol": "SPY", "conditions": [{"signal": "self:rsi14", "op": "<", "value": 40}], "cooldown": 10})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["event_count"] > 0
    assert {row["horizon"] for row in body["horizons"]} >= {"1 week", "1 month"}
    first = body["horizons"][0]
    assert first["signal"]["count"] <= first["baseline"]["count"]


def test_study_without_conditions_explains_itself(client):
    response = client.post("/api/study", json={"symbol": "SPY", "conditions": []})
    assert response.status_code == 400


def test_study_on_a_signal_with_no_data_explains_itself(client):
    response = client.post("/api/study", json={"symbol": "SPY", "conditions": [{"signal": "fear_greed", "op": "<", "value": 25}]})
    assert response.status_code == 400
    assert "no data" in response.json()["detail"]


def test_research_signals_respect_publication_delay(store):
    ctx = Ctx(store)
    index = ctx.calendar()
    raw = ctx.signal("hy_oas").reindex(index)
    lagged = research_series(ctx, "hy_oas", index)
    assert REGISTRY["hy_oas"].lag == 1
    assert lagged.iloc[-1] == pytest.approx(raw.iloc[-2])


def test_condition_labels_read_like_sentences():
    assert Condition.parse({"signal": "vix", "op": ">", "value": 30}).label() == "VIX > 30"


# -------------------------------------------------------------------------- helpers

def test_sanitize_makes_values_json_safe():
    cleaned = sanitize({"a": float("nan"), "b": [np.float64(1.5), np.int64(2), float("inf")], "c": np.bool_(True)})
    assert cleaned == {"a": None, "b": [1.5, 2, None], "c": True}


@pytest.mark.parametrize("raw,expected", [("nvda", "NVDA"), (" brk.b ", "BRK-B"), ("^vix", "^VIX"), ("BTC-USD", "BTC-USD"), ("CL=F", "CL=F")])
def test_symbols_are_normalized(raw, expected):
    assert normalize_symbol(raw) == expected


@pytest.mark.parametrize("raw", ["", "A B", "../etc", "<script>", "WAYTOOLONGTICKER1"])
def test_bad_symbols_are_refused(raw):
    with pytest.raises(ValueError):
        normalize_symbol(raw)


def test_option_expiration_dates():
    assert monthly_opex(2026, 11).isoformat() == "2026-11-20"
    # April 2025's third Friday was Good Friday, so expiration moved to Thursday.
    assert monthly_opex(2025, 4).isoformat() == "2025-04-17"
    assert vix_expiration(2026, 11).isoformat() == "2026-11-18"


def test_cboe_archive_reads_the_ratio_from_either_column_layout():
    standard = "note\nDATE,CALLS,PUTS,TOTAL,P/C Ratio\n10/03/2019, 100, 137, 237, 1.37\n"
    reordered = "note\nDate,VIX Put/Call Ratio,Puts,Calls,Total\n10/04/2019, 0.49, 1, 2, 3\n"
    assert cboe.parse_archive(standard).iloc[0] == pytest.approx(1.37)
    assert cboe.parse_archive(reordered).iloc[0] == pytest.approx(0.49)


def test_cboe_chain_keeps_standard_contracts_only():
    payload = {"data": {"current_price": 100.0, "options": [
        {"option": "SPY261120C00100000", "bid": 1, "ask": 2, "iv": 0.2, "open_interest": 5, "volume": 1, "delta": 0.5, "gamma": 0.01, "vega": 0.1, "theta": -0.1, "last_trade_price": 1.5},
        {"option": "B1SPY261120C00100000", "bid": 1, "ask": 2, "iv": 0.0, "open_interest": 0, "volume": 9, "delta": 0.5, "gamma": 0.01, "vega": 0.1, "theta": -0.1, "last_trade_price": 1.5},
    ]}}
    chain = cboe.parse_chain(payload, "SPY")
    assert len(chain["frame"]) == 1
    assert chain["frame"]["strike"].iloc[0] == 100.0
    assert chain["frame"]["type"].iloc[0] == "C"


def test_fomc_parser_ignores_past_minutes_release_dates():
    import datetime as dt

    page = "2026 FOMC Meetings January 27-28 Minutes (Released February 18, 2026) December 8-9* Statement"
    events = macro_sources.parse_fomc(page, dt.date(2026, 2, 1))
    assert [item["date"] for item in events] == ["2026-12-09"]
    assert "projections" in events[0]["label"]


def test_redownload_after_a_dividend_keeps_deep_history(tmp_path, monkeypatch):
    from src.data import market_data

    db = Store(tmp_path / "prices.db")
    old = price_frame(1, days=30)
    db.upsert_prices("XYZ", old)
    db.set_history_from("XYZ", "2005-01-01")
    requests: list[str | None] = []

    def fake_download(symbols, *, start=None, **_kwargs):
        requests.append(start)
        changed = old.copy()
        # Older adjusted prices moved, which is how a new dividend shows up.
        changed["AdjClose"] = changed["AdjClose"] * 0.99
        return {symbol: changed for symbol in symbols}

    monkeypatch.setattr(market_data, "yahoo_download", fake_download)
    market_data.refresh_prices(db, ["XYZ"], "2020-01-01", pause=0)
    assert requests[-1] == "2005-01-01"
    assert db.history_from("XYZ") == "2005-01-01"


def test_a_bot_check_page_is_reported_in_plain_language():
    with pytest.raises(ValueError, match="bot check"):
        macro_sources.parse_aaii(b"<!DOCTYPE html><html></html>")


def test_refresh_all_skips_sources_fetched_too_recently(tmp_path):
    from src.data import refresh

    db = Store(tmp_path / "status.db")
    for source in refresh.SOURCES:
        db.status_attempt(source.id)
    refresher = refresh.Refresher.__new__(refresh.Refresher)
    refresher.store = db
    triggered: list[str] = []
    refresher.trigger = lambda source_id: triggered.append(source_id) or True
    assert refresher.trigger_all() == 0
    with db.connect() as conn:
        conn.execute("UPDATE source_status SET last_attempt = last_attempt - 7200")
    refresher.trigger_all()
    assert "prices_core" in triggered and "cot" in triggered
    assert "aaii" not in triggered


def test_first_breadth_run_downloads_the_member_list_itself(tmp_path, monkeypatch):
    from src.data import refresh

    db = Store(tmp_path / "fresh.db")
    assert load_sp500(db).empty
    calls = []

    def fake_universe(store):
        calls.append("universe")
        store.kv_set("universe:sp500", [{"symbol": "AAA", "name": "A", "sector": "Energy", "industry": "Oil"}])

    monkeypatch.setattr(refresh, "refresh_universe", fake_universe)
    monkeypatch.setattr(refresh, "refresh_prices", lambda store, members, start, **kwargs: calls.append(members) or {"updated": 1, "failed": []})
    monkeypatch.setattr(refresh, "refresh_breadth_tables", lambda store: "done")
    assert refresh._refresh_breadth(db) == "done"
    assert calls == ["universe", ["AAA"]]


def test_missing_markers_from_yahoo_can_be_saved(tmp_path):
    """ASML's insider table has <NA> for sales, which crashed saving its company data."""
    import json

    class Ticker:
        insider_purchases = pd.DataFrame({
            "Insider Purchases Last 6m": ["Purchases", "Sales", "Net Shares Purchased (Sold)"],
            "Shares": pd.array([0, pd.NA, 0], dtype="Int64"),
            "Trans": pd.array([0, 0, pd.NA], dtype="Int64"),
        })

    insiders = fundamentals._insiders(Ticker())
    assert insiders["sold_shares"] is None
    assert insiders["net_shares"] == 0
    assert insiders["net_pct"] is None
    json.dumps(insiders)
    for marker in (pd.NA, pd.NaT, None, float("nan")):
        assert fundamentals._plain(marker) is None
    Store(tmp_path / "kv.db").kv_set("fund:TEST", {"insiders": insiders})


def test_soxx_is_a_key_signal_outside_market_health(client):
    body = client.get("/api/overview").json()
    row = next(item for item in body["signals"] if item["id"] == "soxx")
    assert row["score"] in (-1, 0, 1)
    assert REGISTRY["soxx"].pillar is None
    assert row["page"] == "stock/SOXX"


# --------------------------------------------------------------------------- alerts

def _alert(db, rule):
    from src.analytics import alerts as logic

    alert_id = db.alert_add(rule)
    return alert_id, logic


def _series_frame(values):
    index = pd.bdate_range("2026-01-05", periods=len(values))
    close = pd.Series(values, index=index, dtype=float)
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "AdjClose": close, "Volume": 1.0})


def test_is_above_alert_fires_once_and_rearms_after_dropping_back(tmp_path):
    db = Store(tmp_path / "alerts.db")
    alert_id, logic = _alert(db, {"kind": "stock", "symbol": "ABC", "metric": "close", "op": "above", "value": 100.0, "note": ""})
    ctx = Ctx(db)
    for prices, expected_events in ([90, 95], 0), ([90, 95, 105], 1), ([90, 95, 105, 106], 1), ([90, 95, 105, 106, 99], 1), ([90, 95, 105, 106, 99, 101], 2):
        db.replace_prices("ABC", _series_frame(prices))
        logic.evaluate_all(db, ctx)
        assert len(db.alert_events()) == expected_events, prices
    assert db.alert_events()[0]["message"] == "ABC price is above $100.00 (now $101.00)"
    assert db.alert(alert_id)["last_triggered"] is not None


def test_cross_alert_fires_once_per_crossing_day(tmp_path):
    db = Store(tmp_path / "alerts.db")
    _, logic = _alert(db, {"kind": "stock", "symbol": "ABC", "metric": "close", "op": "crosses_below", "value": 100.0, "note": ""})
    ctx = Ctx(db)
    db.replace_prices("ABC", _series_frame([110, 105, 99]))
    logic.evaluate_all(db, ctx)
    logic.evaluate_all(db, ctx)
    assert len(db.alert_events()) == 1
    assert "crossed below" in db.alert_events()[0]["message"]
    db.replace_prices("ABC", _series_frame([110, 105, 99, 98]))
    logic.evaluate_all(db, ctx)
    assert len(db.alert_events()) == 1


def test_paused_alerts_stay_quiet(tmp_path):
    db = Store(tmp_path / "alerts.db")
    alert_id, logic = _alert(db, {"kind": "stock", "symbol": "ABC", "metric": "close", "op": "above", "value": 1.0, "note": ""})
    db.set_history_from("ABC", "2026-01-01")
    db.replace_prices("ABC", _series_frame([5, 6]))
    db.alert_set_enabled(alert_id, False)
    logic.evaluate_all(db, Ctx(db))
    assert db.alert_events() == []
    db.alert_set_enabled(alert_id, True)
    logic.evaluate_all(db, Ctx(db))
    assert len(db.alert_events()) == 1


def test_alert_api_round_trip(client, store):
    created = client.post("/api/alerts", json={"kind": "signal", "signal": "vix", "op": "above", "value": 0, "note": "test"}).json()
    assert created["label"].startswith("VIX is above")
    body = client.get("/api/alerts").json()
    assert any(alert["id"] == created["id"] for alert in body["alerts"])
    assert body["unread"] >= 1
    assert client.get("/api/status").json()["alerts_unread"] >= 1
    assert client.patch(f"/api/alerts/{created['id']}", json={"enabled": False}).json()["enabled"] is False
    assert client.post("/api/alerts/read").status_code == 200
    assert client.get("/api/alerts").json()["unread"] == 0
    assert client.delete(f"/api/alerts/{created['id']}").status_code == 200
    assert client.delete(f"/api/alerts/{created['id']}").status_code == 404
    client.delete("/api/alerts/events")
    assert client.get("/api/alerts").json()["events"] == []


def test_alert_preview_reports_the_current_value(client):
    body = client.post("/api/alerts/preview", json={"kind": "stock", "symbol": "NVDA", "metric": "rsi14", "op": "below", "value": 30}).json()
    assert 0 <= body["status"]["value"] <= 100


@pytest.mark.parametrize("payload,status", [
    ({"kind": "signal", "signal": "vix", "op": "sideways", "value": 1}, 400),
    ({"kind": "signal", "signal": "vix", "op": "above", "value": "lots"}, 400),
    ({"kind": "signal", "signal": "not_a_signal", "op": "above", "value": 1}, 400),
    ({"kind": "stock", "symbol": "NVDA", "metric": "astrology", "op": "above", "value": 1}, 400),
    ({"kind": "stock", "symbol": "NOSUCHTICK", "metric": "close", "op": "above", "value": 1}, 404),
    ({"kind": "stock", "symbol": "../etc", "metric": "close", "op": "above", "value": 1}, 400),
    ({"kind": "email", "op": "above", "value": 1}, 400),
])
def test_bad_alerts_are_refused(client, payload, status):
    assert client.post("/api/alerts", json=payload).status_code == status


def test_alert_stocks_are_kept_fresh(tmp_path, monkeypatch):
    from src.data import refresh

    db = Store(tmp_path / "fresh.db")
    db.alert_add({"kind": "stock", "symbol": "XYZ", "metric": "close", "op": "above", "value": 1, "note": ""})
    seen = []
    monkeypatch.setattr(refresh, "refresh_prices", lambda store, symbols, start, **kwargs: seen.extend(symbols) or {"updated": len(symbols), "failed": []})
    refresh._refresh_watchlist(db)
    assert "XYZ" in seen


def test_alert_preview_never_downloads(client, store, monkeypatch):
    from src.data import market_data

    monkeypatch.setattr(market_data, "yahoo_download", lambda *a, **k: (_ for _ in ()).throw(AssertionError("downloaded")))
    body = client.post("/api/alerts/preview", json={"kind": "stock", "symbol": "NV", "metric": "close", "op": "above", "value": 1})
    assert body.status_code == 200
    assert body.json()["status"]["available"] is False
    assert store.get_prices("NV").empty


# ------------------------------------------------------------ pre-market & after hours
def _bars(points: list[tuple[str, float]]) -> pd.DataFrame:
    """5-minute bars at the given ET times, as Yahoo returns them."""
    index = pd.DatetimeIndex([pd.Timestamp(stamp) for stamp, _ in points]).tz_localize("America/New_York")
    close = np.array([price for _, price in points])
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 0.0}, index=index)


def _et(text: str):
    return pd.Timestamp(text).tz_localize("America/New_York").to_pydatetime()


@pytest.fixture
def daily_store(tmp_path):
    db = Store(tmp_path / "ext.db")
    days = pd.DatetimeIndex(["2026-11-05", "2026-11-06"])
    db.upsert_prices("ABC", pd.DataFrame({"Open": [99.0, 100.0], "High": [99.0, 100.0], "Low": [99.0, 100.0], "Close": [99.0, 100.0], "AdjClose": [99.0, 100.0], "Volume": [1e6, 1e6]}, index=days))
    return db


FRIDAY_TO_MONDAY = [("2026-11-06 15:55", 100.0), ("2026-11-06 19:55", 101.0), ("2026-11-09 07:45", 103.0)]


def test_pre_market_change_is_against_the_previous_close(daily_store):
    from src.data import extended

    quote = extended._quote(daily_store, "ABC", _bars(FRIDAY_TO_MONDAY), _et("2026-11-09 08:00"))
    assert quote["session"] == "pre"
    assert quote["reference"] == 100.0
    assert quote["change_pct"] == pytest.approx(3.0)
    assert quote["when"] == "07:45 ET"


def test_after_hours_change_is_against_the_same_days_close(daily_store):
    from src.data import extended

    quote = extended._quote(daily_store, "ABC", _bars(FRIDAY_TO_MONDAY[:2]), _et("2026-11-08 12:00"))
    assert quote["session"] == "post"
    assert quote["reference"] == 100.0
    assert quote["change_pct"] == pytest.approx(1.0)
    assert quote["when"] == "Fri 19:55 ET"


def test_no_extended_price_when_the_last_trade_is_regular_or_stale(daily_store):
    from src.data import extended

    assert extended._quote(daily_store, "ABC", _bars(FRIDAY_TO_MONDAY[:1]), _et("2026-11-06 15:58")) is None
    assert extended._quote(daily_store, "ABC", _bars(FRIDAY_TO_MONDAY), _et("2026-11-16 08:00")) is None


def test_futures_change_is_against_the_last_completed_close(daily_store):
    from src.data import extended

    bars = [("2026-11-06 15:55", 7400.0), ("2026-11-09 03:20", 7416.5), ("2026-11-09 09:55", 7450.0)]
    overnight = extended._quote(daily_store, "ES=F", _bars(bars[:2]), _et("2026-11-09 03:30"))
    assert overnight["session"] == "futures"
    assert overnight["reference"] == 7400.0
    # During Monday's session the reference is still Friday's close, not the latest bar.
    during = extended._quote(daily_store, "ES=F", _bars(bars), _et("2026-11-09 10:00"))
    assert during["reference"] == 7400.0
    assert during["change"] == pytest.approx(50.0)


def test_during_regular_hours_only_futures_are_fetched(daily_store, monkeypatch):
    from src.data import extended

    requested = []
    monkeypatch.setattr(extended, "_cache", {})
    monkeypatch.setattr(extended, "now_et", lambda: _et("2026-11-09 10:00"))
    monkeypatch.setattr(extended.market_data, "yahoo_download", lambda symbols, **kwargs: requested.extend(symbols) or {})
    assert extended.extended_quotes(daily_store, ["ABC"]) == {}
    assert requested == []
    extended.extended_quotes(daily_store, ["ABC", "ES=F"])
    assert requested == ["ES=F"]


def test_a_failed_extended_download_leaves_pages_working(client, monkeypatch):
    from src.data import extended

    def broken(*_args, **_kwargs):
        raise RuntimeError("Yahoo is down")

    monkeypatch.setattr(extended, "_cache", {})
    monkeypatch.setattr(extended, "now_et", lambda: _et("2026-11-09 08:00"))
    monkeypatch.setattr(extended.market_data, "yahoo_download", broken)
    assert client.get("/api/extended?symbols=NVDA,AAPL").json()["quotes"] == {}
    assert client.get("/api/futures").json()["rows"] == []


def test_extended_hours_on_intraday_charts(client, monkeypatch):
    from src.dashboard import payloads

    times = pd.date_range("2026-11-06 04:00", "2026-11-06 19:45", freq="15min")
    close = np.linspace(100, 110, len(times))
    frame = pd.DataFrame({"Open": close, "High": close + 0.5, "Low": close - 0.5, "Close": close, "AdjClose": close, "Volume": 1000.0}, index=times)
    calls = []

    def fake_intraday(symbol, interval, prepost=False):
        calls.append(prepost)
        return frame if prepost else frame[(frame.index.time >= pd.Timestamp("09:30").time()) & (frame.index.time < pd.Timestamp("16:00").time())]

    monkeypatch.setattr(payloads, "intraday_prices", fake_intraday)
    body = client.get("/api/chart/NVDA?interval=15m&range=5d&extended=true").json()
    assert body["extended"] is True
    assert {"pre", "regular", "post"} == set(body["sessions"])
    vwap = body["indicators"]["vwap"]
    for session, value in zip(body["sessions"], vwap):
        assert (value is None) == (session != "regular")
    plain = client.get("/api/chart/NVDA?interval=15m&range=5d&extended=false").json()
    assert plain["extended"] is False
    assert set(plain["sessions"]) == {"regular"}
    assert calls == [True, False]
    daily = client.get("/api/chart/NVDA?interval=1d&range=1y&extended=true").json()
    assert daily["extended"] is False and daily["sessions"] == []


def test_futures_bars_are_never_marked_as_extended():
    from src.dashboard.payloads import bar_sessions

    index = pd.DatetimeIndex(["2026-11-06 03:00", "2026-11-06 12:00", "2026-11-06 18:00"])
    assert bar_sessions(index, "ES=F") == ["regular"] * 3
    assert bar_sessions(index, "SPY") == ["pre", "regular", "post"]


# ------------------------------------------------------------------- alert drawer
def test_alert_on_an_index_name_watches_the_index(client):
    body = client.post("/api/alerts/preview", json={"kind": "stock", "symbol": "spx", "metric": "close", "op": "above", "value": 1}).json()
    assert body["status"]["available"] is True
    assert body["label"].startswith("^GSPC")


def test_signal_catalog_says_which_page_shows_each_signal(client):
    signals = [item for group in client.get("/api/signals").json()["groups"] for item in group["signals"]]
    pages = {item["id"]: item["page"] for item in signals}
    assert pages["vix"] == "volatility"
    assert all(pages.values())


# --------------------------------------------------------------- Key signals choice
def test_key_signals_can_be_chosen_reordered_and_reset(client):
    from src.dashboard.payloads import OVERVIEW_SIGNALS

    saved = client.post("/api/overview/signals", json={"ids": ["dix", "vix", "dix"]}).json()
    assert saved["ids"] == ["dix", "vix"]
    body = client.get("/api/overview").json()
    assert body["signal_ids"] == ["dix", "vix"]
    assert [row["id"] for row in body["signals"]] == ["dix", "vix"]
    assert body["signals_custom"] is True
    assert client.post("/api/overview/signals", json={"ids": ["no_such_signal"]}).status_code == 400
    assert client.post("/api/overview/signals", json={"ids": "vix"}).status_code == 400
    assert client.post("/api/overview/signals", json={"ids": []}).json()["ids"] == []
    assert client.get("/api/overview").json()["signals"] == []
    assert client.delete("/api/overview/signals").json()["ids"] == OVERVIEW_SIGNALS
    body = client.get("/api/overview").json()
    assert body["signal_ids"] == OVERVIEW_SIGNALS
    assert body["signals_custom"] is False


def test_retired_signal_ids_are_skipped(store):
    from src.dashboard.payloads import OVERVIEW_SIGNALS_KEY, overview_signal_ids

    store.kv_set(OVERVIEW_SIGNALS_KEY, ["vix", "signal_from_an_old_version"])
    try:
        assert overview_signal_ids(store) == ["vix"]
    finally:
        store.kv_delete(OVERVIEW_SIGNALS_KEY)


# ------------------------------------------------------------------- list order
def test_watchlist_order_is_saved_and_new_symbols_go_last(client, store):
    original = store.watchlist()
    try:
        reversed_order = list(reversed(original))
        assert client.post("/api/watchlist/order", json={"symbols": reversed_order}).json()["watchlist"] == reversed_order
        assert [row["symbol"] for row in client.get("/api/watchlist").json()["rows"]] == reversed_order
        # Unknown tickers are ignored and missing ones keep their place at the end.
        partial = client.post("/api/watchlist/order", json={"symbols": [original[0], "ZZZZ"]}).json()["watchlist"]
        assert partial[0] == original[0] and sorted(partial) == sorted(original)
        assert client.post("/api/watchlist/order", json={"symbols": "NVDA"}).status_code == 400
        store.watchlist_add("SPY")
        assert store.watchlist()[-1] == "SPY"
    finally:
        store.watchlist_remove("SPY")
        store.watchlist_reorder(original)


def test_alert_order_is_saved(tmp_path):
    db = Store(tmp_path / "order.db")
    first = db.alert_add({"kind": "signal", "signal": "vix", "op": "above", "value": 30, "note": ""})
    second = db.alert_add({"kind": "signal", "signal": "dix", "op": "above", "value": 45, "note": ""})
    assert [alert["id"] for alert in db.alerts()] == [first, second]
    db.alerts_reorder([second])
    assert [alert["id"] for alert in db.alerts()] == [second, first]
    third = db.alert_add({"kind": "signal", "signal": "vvix", "op": "above", "value": 120, "note": ""})
    assert db.alerts()[-1]["id"] == third


def test_older_databases_get_an_order_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE watchlist (symbol TEXT PRIMARY KEY, added_at REAL NOT NULL)")
        conn.executemany("INSERT INTO watchlist VALUES (?, ?)", [("MSFT", 2.0), ("AAPL", 1.0), ("NVDA", 3.0)])
    db = Store(path)
    assert db.watchlist() == ["AAPL", "MSFT", "NVDA"]
    db.watchlist_add("AMD")
    assert db.watchlist() == ["AAPL", "MSFT", "NVDA", "AMD"]
    Store(path)  # opening again is harmless
    assert db.watchlist() == ["AAPL", "MSFT", "NVDA", "AMD"]


# ------------------------------------------------------------- prediction markets
def _kalshi(ticker, event, close, bid, ask, **extra):
    return {"ticker": ticker, "event_ticker": event, "close_time": close, "yes_bid_dollars": str(bid), "yes_ask_dollars": str(ask),
            "last_price_dollars": str(bid), "volume_fp": "1000", **extra}


def test_fed_odds_are_scaled_to_the_next_meeting():
    from src.data import prediction_markets as pm

    now = _et("2026-11-09 08:00")
    markets = [
        _kalshi("KXFEDDECISION-26DEC-H25", "KXFEDDECISION-26DEC", "2026-12-09T18:59:00Z", 0.70, 0.72, yes_sub_title="Hike 25bps"),
        _kalshi("KXFEDDECISION-26DEC-H0", "KXFEDDECISION-26DEC", "2026-12-09T18:59:00Z", 0.24, 0.26, yes_sub_title="Fed maintains rate"),
        _kalshi("KXFEDDECISION-26DEC-C25", "KXFEDDECISION-26DEC", "2026-12-09T18:59:00Z", 0.04, 0.06, yes_sub_title="Cut 25bps"),
        _kalshi("KXFEDDECISION-27JAN-H0", "KXFEDDECISION-27JAN", "2027-01-27T18:59:00Z", 0.5, 0.52, yes_sub_title="Fed maintains rate"),
    ]
    fed = pm.fed_next(markets, now)
    assert fed["event"] == "KXFEDDECISION-26DEC"
    assert [item["step"] for item in fed["outcomes"]] == [-1, 0, 1]
    assert sum(item["prob"] for item in fed["outcomes"]) == pytest.approx(1.0)
    assert fed["likely"] == "Hike 25bps"
    assert fed["hike"] == pytest.approx(0.71 / 1.01)  # midpoints 0.71 + 0.25 + 0.05


def test_ladder_finds_the_middle_estimate_and_range():
    from src.data import prediction_markets as pm

    now = _et("2026-11-09 08:00")
    close = "2026-12-10T13:29:00Z"
    # Chance of CPI coming in above 3.4, 3.6, ... 4.2%. One quote out of line (3.8 above 3.6).
    rows = [(3.4, 0.95), (3.6, 0.70), (3.8, 0.75), (4.0, 0.30), (4.2, 0.05)]
    markets = [_kalshi(f"KXCPIYOY-26NOV-T{k}", "KXCPIYOY-26NOV", close, p - 0.005, p + 0.005, floor_strike=k) for k, p in rows]
    reading = pm.ladder(markets, now)
    assert reading["period"] == "November 2026"
    # Smoothed: above 3.8 is capped at 70%; 50% falls halfway between 3.8 (70%) and 4.0 (30%).
    assert reading["median"] == pytest.approx(3.9)
    assert 3.4 < reading["low"] < 3.6 and 4.0 < reading["high"] < 4.2


def test_wide_quotes_fall_back_to_the_last_trade():
    from src.data import prediction_markets as pm

    assert pm.kalshi_price({"yes_bid_dollars": "0.10", "yes_ask_dollars": "0.90", "last_price_dollars": "0.40"}) == 0.40
    assert pm.kalshi_price({"yes_bid_dollars": "0.40", "yes_ask_dollars": "0.44", "last_price_dollars": "0.10"}) == pytest.approx(0.42)


def test_earnings_odds_are_matched_to_tickers():
    from src.data import prediction_markets as pm

    events = [
        {"title": "Will Nvidia (NVDA) beat quarterly earnings?", "slug": "nvda-q", "markets": [
            {"question": "Will Nvidia (NVDA) beat quarterly earnings?", "outcomePrices": '["0.8", "0.2"]', "bestBid": 0.78, "bestAsk": 0.82,
             "volumeNum": 900.0, "endDate": "2026-11-17T22:00:00Z", "description": "The Street consensus estimate for Nvidia's non-GAAP EPS is $2.53 as of market creation."}]},
        {"title": "Will Moog (MOG.A) beat quarterly earnings?", "slug": "mog", "markets": [
            {"outcomePrices": '["0.6", "0.4"]', "volumeNum": 50.0, "endDate": "2026-11-20T22:00:00Z", "description": ""}]},
        {"title": "Will Old Co (OLD) beat quarterly earnings?", "slug": "old", "markets": [
            {"outcomePrices": '["0.6", "0.4"]', "volumeNum": 50.0, "endDate": "2026-10-01T22:00:00Z"}]},
    ]
    odds = pm.earnings_odds(events, dt.date(2026, 11, 9))
    assert set(odds) == {"NVDA", "MOG-A"}
    assert odds["NVDA"]["prob"] == pytest.approx(0.80)
    assert odds["NVDA"]["consensus_eps"] == 2.53
    assert odds["NVDA"]["url"].endswith("/nvda-q")


def test_catalysts_show_what_the_markets_expect(store):
    from src.dashboard.payloads import _catalysts

    store.kv_set("events:macro", [{"date": "2099-12-09", "label": "FOMC rate decision", "kind": "macro", "metric": "econ_calendar"}])
    store.kv_set("pm:board", {"fed": {"date": "2099-12-09", "likely": "Hike 25bps", "likely_prob": 0.76}})
    try:
        fomc = [event for event in _catalysts(store, limit=50) if event["label"].startswith("FOMC")][0]
        assert fomc["odds"] == {"text": "Hike 25bps 76%", "metric": "fed_odds"}
    finally:
        store.kv_delete("events:macro")
        store.kv_delete("pm:board")


def test_prediction_markets_page(client, store):
    store.kv_set("pm:earnings", {"NVDA": {"symbol": "NVDA", "question": "Will Nvidia (NVDA) beat quarterly earnings?", "prob": 0.9,
                                          "volume": 900.0, "date": "2099-11-17", "consensus_eps": 2.53, "url": "https://polymarket.com/event/x"}})
    try:
        body = client.get("/api/prediction").json()
        assert [item["key"] for item in body["indicators"]] == ["cpi_mom", "core_cpi_mom", "cpi", "unemployment", "payrolls", "gdp"]
        assert body["earnings"][0]["name"] == "Nvidia" and body["earnings"][0]["in_watchlist"] is True
        assert "prediction" not in client.get("/api/macro").json()
    finally:
        store.kv_delete("pm:earnings")


def test_ladders_with_unquoted_gaps_are_left_out():
    from src.data import prediction_markets as pm

    now = _et("2026-11-09 08:00")
    close = "2027-04-28T18:00:00Z"
    quotes = [(3.75, 0.95, 0.97), (4.0, 0.78, 0.94), (4.25, 0.72, 0.85), (4.5, 0.05, 0.85), (5.5, 0.0, 0.01)]
    markets = [_kalshi(f"KXFED-27APR-T{k}", "KXFED-27APR", close, b, a, floor_strike=k) for k, b, a in quotes]
    # 4.0 and 4.5 have no usable quote, and the odds fall from 78% to 0.5% across them.
    assert pm.ladder(markets, now, discrete=True) is None


def test_stepped_numbers_get_odds_per_step():
    from src.data import prediction_markets as pm

    now = _et("2026-11-09 08:00")
    close = "2026-11-10T13:29:00Z"
    above = [(0.0, 0.995), (0.1, 0.965), (0.2, 0.545), (0.3, 0.09), (0.4, 0.015)]
    markets = [_kalshi(f"KXCPI-26OCT-T{k}", "KXCPI-26OCT", close, p - 0.005, p + 0.005, floor_strike=k,
                       title="Will CPI rise more than 0.2% in October 2026?") for k, p in above]
    reading = pm.ladder(markets, now, discrete=True)
    by_value = {round(row["value"], 1): row["prob"] for row in reading["buckets"]}
    assert by_value[0.3] == pytest.approx(0.455, abs=0.01)
    assert by_value[0.2] == pytest.approx(0.42, abs=0.01)
    assert reading["median"] == pytest.approx(0.3)  # 0.0 to 0.2 add up to only 45.5%
    assert reading["period"] == "October 2026"
    assert sum(row["prob"] for row in reading["buckets"]) == pytest.approx(1.0)



# --------------------------------------------------------------- positions and journal
def test_positions_are_valued_and_summed(client, store):
    added = client.post("/api/positions", json={"symbol": "msft", "shares": 10, "cost": 50, "stop": 40}).json()
    try:
        assert added["symbol"] == "MSFT"
        body = client.get("/api/portfolio").json()
        row = next(row for row in body["rows"] if row["id"] == added["id"])
        last = float(store.get_prices("MSFT")["Close"].iloc[-1])
        assert row["value"] == pytest.approx(10 * last)
        assert row["pnl_pct"] == pytest.approx((last / 50 - 1) * 100)
        assert row["stop_distance"] == pytest.approx((40 / last - 1) * 100)
        assert body["totals"]["count"] == 1 and body["sectors"][0]["weight"] == pytest.approx(100.0)
        totals = body["totals"]
        assert totals["stress_change"] == pytest.approx(totals["value"] * totals["beta"] * -0.05)
        assert client.post("/api/positions", json={"symbol": "MSFT", "shares": -1, "cost": 5}).status_code == 400
        assert client.patch(f"/api/positions/{added['id']}", json={"shares": 12}).json()["shares"] == 12
    finally:
        client.delete(f"/api/positions/{added['id']}")
    assert client.get("/api/portfolio").json()["rows"] == []


def test_journal_trades_update_positions(client, store):
    first = client.post("/api/journal", json={"symbol": "AAPL", "action": "buy", "shares": 10, "price": 100, "reason": "Breakout", "update_positions": True}).json()
    second = client.post("/api/journal", json={"symbol": "AAPL", "action": "buy", "shares": 10, "price": 120, "reason": "Adding", "update_positions": True}).json()
    try:
        position = next(row for row in store.positions() if row["symbol"] == "AAPL")
        assert position["shares"] == 20 and position["cost"] == pytest.approx(110)
        assert first["entry"]["snapshot"]["stock"]["rsi"] is not None
        assert client.post("/api/journal", json={"symbol": "AAPL", "action": "sell", "shares": 25, "price": 130, "reason": "Too many", "update_positions": True}).status_code == 400
        sold = client.post("/api/journal", json={"symbol": "AAPL", "action": "sell", "shares": 20, "price": 130, "reason": "Target hit", "update_positions": True}).json()
        assert not any(row["symbol"] == "AAPL" for row in store.positions())
        assert client.post("/api/journal", json={"symbol": "AAPL", "action": "note", "reason": ""}).status_code == 400
        rows = {row["id"]: row for row in client.get("/api/journal").json()["rows"]}
        last = float(store.get_prices("AAPL")["Close"].iloc[-1])
        assert rows[first["entry"]["id"]]["result"] == pytest.approx((last / 100 - 1) * 100)
        assert rows[sold["entry"]["id"]]["result"] == pytest.approx(-(last / 130 - 1) * 100)
    finally:
        for entry in store.journal():
            store.journal_delete(entry["id"])
        for row in store.positions():
            store.position_delete(row["id"])


def test_lists_and_notes(client, store):
    try:
        assert client.post("/api/tags", json={"symbol": "NVDA", "tag": "  Semis  "}).json()["tags"] == ["Semis"]
        client.post("/api/notes/NVDA", json={"text": "Wait for the 200-day."})
        row = next(row for row in client.get("/api/watchlist").json()["rows"] if row["symbol"] == "NVDA")
        assert row["tags"] == ["Semis"] and row["note"] == "Wait for the 200-day."
        page = client.get("/api/stock/NVDA").json()
        assert page["note"]["text"] == "Wait for the 200-day." and page["tags"] == ["Semis"]
        assert client.post("/api/tags", json={"symbol": "NVDA", "tag": ""}).status_code == 400
    finally:
        store.tag_remove("NVDA", "Semis")
        store.note_set("NVDA", "")


def test_backup_round_trip(tmp_path, store):
    from src.data import backup

    store.tag_add("NVDA", "Core")
    store.note_set("NVDA", "Long term")
    position = store.position_add("NVDA", 5, 100.0)
    try:
        data = backup.export(store)
    finally:
        store.tag_remove("NVDA", "Core")
        store.note_set("NVDA", "")
        store.position_delete(position)
    fresh = Store(tmp_path / "restored.db")
    counts = backup.restore(fresh, data)
    assert fresh.watchlist() == store.watchlist()
    assert fresh.tags_by_symbol() == {"NVDA": ["Core"]}
    assert fresh.note("NVDA")["text"] == "Long term"
    assert counts["positions"] == 1
    # Merging the same backup again adds nothing twice, also after a browser has rewritten
    # whole numbers such as 165.0 as 165.
    def like_a_browser(value):
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, dict):
            return {key: like_a_browser(item) for key, item in value.items()}
        if isinstance(value, list):
            return [like_a_browser(item) for item in value]
        return value

    fresh.alert_add({"kind": "stock", "symbol": "NVDA", "metric": "close", "op": "above", "value": 165.0, "note": ""})
    data = {**data, "alerts": [{"rule": {"kind": "stock", "symbol": "NVDA", "metric": "close", "op": "above", "value": 165.0, "note": ""}, "enabled": True}]}
    again = backup.restore(fresh, like_a_browser(json.loads(json.dumps(data))))
    assert again["positions"] == 0 and again["alerts"] == 0 and len(fresh.positions()) == 1
    with pytest.raises(ValueError):
        backup.restore(fresh, {"format": "something else"})
    backup.restore(fresh, {**data, "positions": [], "watchlist": ["SPY"]}, replace=True)
    assert fresh.positions() == [] and fresh.watchlist() == ["SPY"]


# ------------------------------------------------------------------ stock checklist
def test_checklist_flags_what_matters():
    from src.analytics import checklist

    snapshot = {"trend": {"label": "Downtrend", "tone": "critical"}, "vs_sma50": -8.0, "vs_sma200": -12.0, "rsi": 27.0, "rs_rating": 15.0,
                "rvol": 3.0, "change_pct": -4.0, "atr": 5.0, "last": 100.0}
    option = {"expected_move_week": 9.5, "expected_move_week_expiry": "2026-11-20", "net_gex": -1.0}
    checks = checklist.build(snapshot, next_earnings="2026-11-17", option=option, short_pct=0.2, health=40.0,
                             beat_odds={"prob": 0.9}, today=dt.date(2026, 11, 10))
    states = {check["id"]: check["state"] for check in checks}
    assert states == {"trend": "bad", "rsi": "warn", "rs": "bad", "volume": "bad", "earnings": "warn", "market": "bad",
                      "gamma": "warn", "short": "warn", "risk": "info"}
    earnings = next(check for check in checks if check["id"] == "earnings")
    assert "±9.5%" in earnings["text"] and "90%" in earnings["text"]
    assert checklist.summary(checks) == {"good": 0, "warn": 4, "bad": 4}
    assert checklist.implied_move(option, "2026-11-21") == (None, None)


# --------------------------------------------------------------- calendars and timeline
def test_typical_earnings_move():
    from src.analytics.events import typical_move

    days = pd.bdate_range("2026-01-01", periods=60)
    close = pd.Series(100.0, index=days)
    close.loc[days[21]:] = 110.0  # +10% across the report on days[20]
    close.loc[days[41]:] = 104.5  # -5% across the report on days[40]
    frame = pd.DataFrame({"Close": close})
    result = typical_move(frame, [{"date": days[40].strftime("%Y-%m-%d")}, {"date": days[20].strftime("%Y-%m-%d")}])
    assert result["average"] == pytest.approx(7.5) and result["largest"] == pytest.approx(10.0) and result["reports"] == 2


def test_economic_calendar_compares_expectations_with_results(tmp_path):
    from src.analytics import events

    db = Store(tmp_path / "calendar.db")
    months = pd.date_range("2025-01-01", periods=22, freq="MS")
    db.upsert_series("fred:CPIAUCSL", pd.Series([300 * 1.003 ** index for index in range(22)], index=months))
    assert events.period_start("Q4 2026") == pd.Timestamp(2026, 10, 1)
    assert events.period_start("October 2026") == pd.Timestamp(2026, 10, 1)
    events.log_expectations(db, {"cpi_mom": {"period": months[-1].strftime("%B %Y"), "median": 0.2, "release": "2020-01-01"}})
    past = events.economic_calendar(db)["past"]
    assert past[0]["actual"] == pytest.approx(0.3) and past[0]["surprise"] == pytest.approx(0.1)
    db.kv_set("events:macro", [{"date": "2099-12-10", "label": "CPI inflation report", "kind": "macro", "metric": "cpi_yoy"}])
    db.kv_set("pm:board", {"cpi_mom": {"release": "2099-12-10", "period": "November 2099", "median": 0.3, "low": 0.2, "high": 0.4, "volume": 1.0}})
    upcoming = events.economic_calendar(db, days=40000)["upcoming"]
    assert upcoming[0]["time"] == "08:30"
    assert upcoming[0]["expectations"][0]["previous"] == pytest.approx(0.3)


def test_timeline_looks_back_with_publication_delays(client):
    body = client.get("/api/timeline?date=2025-06-02").json()
    assert body["available"] and body["date"] <= "2025-06-02"
    assert len(body["pillars"]) == len(PILLARS)
    assert {item["label"] for item in body["forward"]} == {"1 week", "1 month", "3 months", "6 months"}
    assert client.get("/api/timeline?date=June").status_code == 400


def test_notable_dates_find_falls():
    from src.analytics.timeline import notable_dates

    days = pd.bdate_range("2025-01-01", periods=30)
    prices = pd.Series([100, 105, 110, 100, 95, 99, 112, 113] + [113] * 22, index=days, dtype=float)
    found = notable_dates(prices, days[0])
    assert found[0]["date"] == days[2].strftime("%Y-%m-%d") and "14%" in found[0]["label"]
    assert found[1]["date"] == days[4].strftime("%Y-%m-%d")


def test_brief_sums_up_the_day(client):
    body = client.get("/api/brief").json()
    assert body["title"] and isinstance(body["lines"], list)
    assert any("Market Health" in line["text"] for line in body["lines"])


def test_filings_summarise_insider_trades(client):
    body = client.get("/api/stock/NVDA/filings").json()
    assert body["filings"][0]["form"] == "8-K"
    assert body["summary"]["buys"] == 1 and body["summary"]["sells"] == 1 and body["summary"]["buyers"] == 1


def test_stale_readings_are_marked(store):
    from src.analytics.signals import signal_row

    ctx = Ctx(store)
    fresh = signal_row(ctx, "vix")
    assert fresh["stale"] is False and fresh["age"] == 0
    old = signal_row(ctx, "hy_oas")
    assert old["age"] >= 0 and old["stale"] == (old["age"] > 5 + 1)



def test_fomc_parser_ignores_dates_with_a_year():
    text = ("<h4>2027 FOMC Meetings</h4> January 26-27 March 16-17* October 26-27 December 7-8* * Meeting associated with a "
            "Summary of Economic Projections. Note: A two-day meeting is scheduled for January 25-26, 2028. "
            "Last Update: October 28, 2026")
    days = [event["date"] for event in macro_sources.parse_fomc(text, dt.date(2026, 11, 1))]
    assert days == ["2027-01-27", "2027-03-17", "2027-10-27", "2027-12-08"]


def test_earnings_calendar_leaves_out_foreign_otc_shares():
    from src.data.calendars import _rows

    frame = pd.DataFrame({
        "Company": ["NVIDIA", "KBC Group", "Lakefront"], "Marketcap": [4e12, 5e10, 2e9],
        "Event Name": ["Q3", "Q3", "Q3"], "Timing": ["AMC", "BMO", "TNS"], "EPS Estimate": [2.47, 1.6, float("nan")],
        "Event Start Date": [pd.Timestamp("2026-11-17 21:00", tz="UTC")] * 3,
    }, index=["NVDA", "KBCSY", "GLPGF"])
    rows = _rows(frame)
    assert [row["symbol"] for row in rows] == ["NVDA"]
    assert rows[0]["timing"] == "after" and rows[0]["date"] == "2026-11-17"
