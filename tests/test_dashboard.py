"""The API and shared catalogues, run offline against a small generated database."""

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
