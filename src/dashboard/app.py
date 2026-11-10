"""The local web app: a JSON API plus the static dashboard front end."""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from src.analytics import alerts as alert_logic
from src.analytics import portfolio as portfolio_analytics
from src.analytics.signals import SELF_SIGNALS, Ctx
from src.dashboard import payloads
from src.dashboard.metric_guide import GUIDE_CATEGORIES, METRICS
from src.data import backup, cboe, extended, fundamentals, macro_sources, refresh
from src.data.catalog import normalize_symbol
from src.data.market_data import SymbolNotFound, ensure_prices
from src.data.store import Store

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "testserver"}


class SafeJSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(payloads.sanitize(content), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


class PayloadCache:
    """Keeps computed page data until new data arrives (or an optional time limit)."""

    def __init__(self, max_items: int = 128):
        self.max_items = max_items
        self._items: dict[tuple, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple, compute: Callable[[], object], ttl: float | None = None):
        now = time.time()
        with self._lock:
            hit = self._items.get(key)
            if hit is not None and (ttl is None or now - hit[0] < ttl):
                return hit[1]
        value = compute()
        with self._lock:
            if len(self._items) >= self.max_items:
                self._items.pop(next(iter(self._items)))
            self._items[key] = (time.time(), value)
        return value


def _forget(cache: PayloadCache, *names: str) -> None:
    """Drop cached pages after a change that the data version doesn't track (tags, notes, positions)."""
    with cache._lock:
        for key in [key for key in cache._items if key[0] in names]:
            cache._items.pop(key, None)


class Services:
    def __init__(self, store: Store, *, background: bool):
        self.store = store
        self.background = background
        self.cache = PayloadCache()
        self.refresher = refresh.Refresher(store) if background else None
        self.side_jobs = ThreadPoolExecutor(max_workers=2, thread_name_prefix="side-job")
        self._ctx: Ctx | None = None
        self._ctx_lock = threading.Lock()
        self._alert_stop = threading.Event()
        # Pages rebuilt in the background as soon as new data arrives, so they open instantly.
        self.warmers: list[Callable[[], object]] = []
        self._alert_thread: threading.Thread | None = None

    def start_alert_checks(self) -> None:
        """Check alerts whenever new data has arrived."""

        def loop() -> None:
            checked_version = None
            while not self._alert_stop.is_set():
                if self.store.version != checked_version:
                    checked_version = self.store.version
                    try:
                        alert_logic.evaluate_all(self.store, self.ctx())
                    except Exception:
                        logger.exception("Checking alerts failed")
                    for warm in self.warmers:
                        try:
                            warm()
                        except Exception as exc:  # a page that fails here simply computes on its next visit
                            logger.debug("Preparing a page failed: %s", exc)
                self._alert_stop.wait(10)

        self._alert_thread = threading.Thread(target=loop, name="alert-checks", daemon=True)
        self._alert_thread.start()

    def stop_alert_checks(self) -> None:
        self._alert_stop.set()

    def ctx(self) -> Ctx:
        with self._ctx_lock:
            if self._ctx is None or self._ctx.version != self.store.version:
                self._ctx = Ctx(self.store)
            return self._ctx

    def cached(self, name: str, *args, compute: Callable[[], object], ttl: float | None = None):
        return self.cache.get((name, self.store.version, *args), compute, ttl)

    def prepare_new_symbol(self, symbol: str) -> None:
        """Fetch everything for a newly watched symbol, so its row fills in shortly after."""
        try:
            fundamentals.get_fundamentals(self.store, symbol, max_age=0)
            macro_sources.backfill_finra_symbol(self.store, symbol)
            try:
                chain = cboe.get_chain(symbol)
                refresh.save_option_snapshot(self.store, cboe.option_symbol(symbol), chain)
            except cboe.NoOptions:
                pass
            self.store.bump_version()
        except Exception as exc:
            logger.warning("Could not finish preparing %s: %s", symbol, exc)


def _symbol(raw: str) -> str:
    try:
        return normalize_symbol(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def create_app(store: Store | None = None, *, background: bool = True) -> FastAPI:
    store = store or Store()
    store.seed_watchlist(refresh.WATCHLIST_DEFAULTS)
    services = Services(store, background=background)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if services.refresher:
            services.refresher.start()
            services.start_alert_checks()
        yield
        services.stop_alert_checks()
        if services.refresher:
            services.refresher.stop()
        services.side_jobs.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Market Signals", default_response_class=SafeJSONResponse, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = services

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # This app has no login, so it refuses requests addressed to any other host name
        # (DNS rebinding) and changes coming from another website's pages.
        host = request.headers.get("host", "").rsplit(":", 1)[0].lower()
        if host not in LOCAL_HOSTS:
            return JSONResponse({"detail": "This dashboard only answers on localhost."}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
            origin_host = origin.split("//", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0].lower()
            if origin_host not in LOCAL_HOSTS:
                return JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(SymbolNotFound)
    async def symbol_not_found(_: Request, exc: SymbolNotFound):
        return SafeJSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def bad_value(_: Request, exc: ValueError):
        return SafeJSONResponse({"detail": str(exc)}, status_code=400)

    # ------------------------------------------------------------ status & reference
    @app.get("/api/status")
    def status():
        return refresh.status_payload(store, services.refresher)

    @app.post("/api/refresh")
    def trigger_refresh(body: dict = Body(default={})):
        if services.refresher is None:
            raise HTTPException(status_code=409, detail="Background refresh is switched off in this session.")
        source = body.get("source")
        if source:
            if source not in refresh.SOURCE_BY_ID:
                raise HTTPException(status_code=400, detail=f"Unknown data source '{source}'.")
            started = 1 if services.refresher.trigger(source) else 0
        else:
            started = services.refresher.trigger_all()
        return {"started": started}

    @app.get("/api/market")
    def market():
        return payloads.market_state()

    @app.get("/api/metrics")
    def metrics():
        return {"categories": GUIDE_CATEGORIES, "metrics": METRICS}

    @app.get("/api/search")
    def search(q: str = Query(default="", max_length=40)):
        return {"results": payloads.search(store, q)}

    # ----------------------------------------------------------------- market pages
    @app.get("/api/overview")
    def overview():
        # The chosen Key signals are part of the key, so a changed list is never served stale.
        chosen = tuple(payloads.overview_signal_ids(store))
        return services.cached("overview", chosen, compute=lambda: payloads.overview(store, services.ctx()), ttl=60)

    @app.post("/api/overview/signals")
    def overview_signals_set(body: dict = Body(...)):
        return {"ids": payloads.set_overview_signals(store, body.get("ids")), "custom": True}

    @app.delete("/api/overview/signals")
    def overview_signals_reset():
        return {"ids": payloads.reset_overview_signals(store), "custom": False}

    # The most visited pages are prepared whenever new data arrives (see Services.start_alert_checks).
    services.warmers = [overview, lambda: watchlist()]

    @app.get("/api/breadth")
    def breadth(range: str = "1y"):
        return services.cached("breadth", range, compute=lambda: payloads.breadth(store, services.ctx(), range))

    @app.get("/api/rotation")
    def rotation(universe: str = "sectors", benchmark: str = "SPY", lens: str = Query(default="weekly", pattern="^(weekly|daily)$"), tail: int = Query(default=8, ge=2, le=26)):
        bench = _symbol(benchmark)
        return services.cached(
            "rotation", universe, bench, lens, tail, tuple(store.watchlist()) if universe == "watchlist" else (),
            compute=lambda: payloads.rotation(store, services.ctx(), universe, bench, lens, tail),
        )

    @app.get("/api/volatility")
    def volatility(range: str = "1y"):
        return services.cached("volatility", range, compute=lambda: payloads.volatility(services.ctx(), range))

    @app.get("/api/flows")
    def flows(range: str = "1y"):
        return services.cached("flows", range, compute=lambda: payloads.flows(store, services.ctx(), range))

    @app.get("/api/macro")
    def macro(range: str = "2y"):
        return services.cached("macro", range, compute=lambda: payloads.macro(services.ctx(), range))

    @app.get("/api/prediction")
    def prediction():
        return services.cached("prediction", tuple(store.watchlist()), compute=lambda: payloads.prediction(store, services.ctx()))

    @app.get("/api/options/underlyings")
    def option_underlyings():
        return {"underlyings": payloads.option_underlyings(store)}

    @app.get("/api/options/{symbol}")
    def options(symbol: str, scope: str = "all", window: float = Query(default=15.0, ge=2.0, le=50.0)):
        target = _symbol(symbol)
        if scope not in ("all", "0dte", "7d", "30d", "90d", "monthly"):
            payloads._validate_date(scope, "Expiry")
        return services.cached(
            "options", target, scope, window,
            compute=lambda: payloads.options_page(store, target, scope, window),
            ttl=60,
        )

    @app.get("/api/options/{symbol}/levels")
    def option_levels(symbol: str):
        target = _symbol(symbol)
        return services.cached("levels", target, compute=lambda: payloads.gamma_levels(target), ttl=120)

    # ----------------------------------------------------------------------- stocks
    @app.get("/api/watchlist")
    def watchlist():
        return services.cached(
            "watchlist", tuple(store.watchlist()),
            compute=lambda: {"rows": payloads.watchlist_rows(store, services.ctx()), "market": payloads.market_state()},
        )

    @app.post("/api/watchlist")
    def watchlist_add(body: dict = Body(...)):
        symbol = payloads.price_symbol(_symbol(str(body.get("symbol", ""))))
        ensure_prices(store, symbol)
        store.watchlist_add(symbol)
        store.bump_version()
        services.side_jobs.submit(services.prepare_new_symbol, symbol)
        return {"symbol": symbol, "watchlist": store.watchlist()}

    @app.post("/api/watchlist/order")
    def watchlist_order(body: dict = Body(...)):
        symbols = body.get("symbols")
        if not isinstance(symbols, list) or not all(isinstance(item, str) for item in symbols):
            raise HTTPException(status_code=400, detail="Send the watchlist order as a list of tickers.")
        # Cached pages key on the watchlist's order already, so no data refresh is needed.
        return {"watchlist": store.watchlist_reorder(symbols)}

    @app.delete("/api/watchlist/{symbol}")
    def watchlist_remove(symbol: str):
        target = _symbol(symbol)
        store.watchlist_remove(target)
        store.bump_version()
        return {"symbol": target, "watchlist": store.watchlist()}

    # ----------------------------------------------------------------------- alerts
    @app.get("/api/alerts")
    def alerts():
        ctx = services.ctx()
        return {
            "alerts": [alert_logic.alert_view(alert, store, ctx) for alert in store.alerts()],
            "events": store.alert_events(),
            "unread": store.alert_events_unread(),
            "operators": [{"value": key, "label": label} for key, label in alert_logic.OPERATORS.items()],
            "stock_measures": [
                {"value": key, "label": SELF_SIGNALS[key]["label"], "metric": SELF_SIGNALS[key]["metric"], "fmt": fmt}
                for key, fmt in alert_logic.STOCK_FORMATS.items()
            ],
        }

    def alert_body(body: dict) -> dict:
        # Index names such as SPX (from the options page) watch their Yahoo price symbol.
        if body.get("kind") == "stock" and body.get("symbol"):
            return {**body, "symbol": payloads.price_symbol(str(body["symbol"]).strip().upper())}
        return body

    @app.post("/api/alerts")
    def alert_create(body: dict = Body(...)):
        body = alert_body(body)
        if len(store.alerts()) >= alert_logic.MAX_ALERTS:
            raise HTTPException(status_code=400, detail=f"You can have up to {alert_logic.MAX_ALERTS} alerts. Delete one first.")
        rule = alert_logic.parse_rule(body, store, lambda db, symbol: ensure_prices(db, symbol))
        alert_id = store.alert_add(rule)
        ctx = services.ctx()
        alert_logic.evaluate_new(store, ctx, alert_id)
        return alert_logic.alert_view(store.alert(alert_id), store, ctx)

    @app.post("/api/alerts/preview")
    def alert_preview(body: dict = Body(...)):
        # Called while the user types, so it only reads stored data and never downloads:
        # half-typed tickers must not be fetched or saved.
        rule = alert_logic.parse_rule(alert_body(body), store, lambda db, symbol: None)
        view = alert_logic.alert_view({"id": 0, "rule": rule, "enabled": 1, "created_at": None, "last_triggered": None}, store, services.ctx())
        return {"label": view["label"], "fmt": view["fmt"], "status": view["status"]}

    @app.post("/api/alerts/order")
    def alerts_order(body: dict = Body(...)):
        ids = body.get("ids")
        if not isinstance(ids, list) or not all(isinstance(item, int) and not isinstance(item, bool) for item in ids):
            raise HTTPException(status_code=400, detail="Send the alert order as a list of alert ids.")
        return {"ids": store.alerts_reorder(ids)}

    @app.post("/api/alerts/read")
    def alerts_mark_read():
        store.alert_events_mark_read()
        return {"unread": 0}

    @app.delete("/api/alerts/events")
    def alerts_clear():
        store.alert_events_clear()
        return {"unread": 0}

    @app.patch("/api/alerts/{alert_id}")
    def alert_toggle(alert_id: int, body: dict = Body(...)):
        if not store.alert_set_enabled(alert_id, bool(body.get("enabled"))):
            raise HTTPException(status_code=404, detail="That alert no longer exists.")
        if body.get("enabled"):
            alert_logic.evaluate_new(store, services.ctx(), alert_id)
        return alert_logic.alert_view(store.alert(alert_id), store, services.ctx())

    @app.delete("/api/alerts/{alert_id}")
    def alert_remove(alert_id: int):
        if not store.alert_delete(alert_id):
            raise HTTPException(status_code=404, detail="That alert no longer exists.")
        return {"deleted": alert_id}

    # -------------------------------------------------------------- morning brief
    @app.get("/api/brief")
    def brief():
        return services.cached("brief", compute=lambda: payloads.brief(store, services.ctx()), ttl=120)

    # ------------------------------------------------------------------ positions
    def number(body: dict, key: str, *, required: bool = False, positive: bool = False) -> float | None:
        raw = body.get(key)
        if raw in (None, ""):
            if required:
                raise HTTPException(status_code=400, detail=f"Enter the {key.replace('_', ' ')}.")
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"The {key.replace('_', ' ')} must be a number.") from exc
        if not math.isfinite(value) or (positive and value <= 0):
            raise HTTPException(status_code=400, detail=f"The {key.replace('_', ' ')} must be above zero.")
        return value

    def day(body: dict, key: str, default: str | None = None) -> str | None:
        raw = str(body.get(key) or "").strip()
        if not raw:
            return default
        try:
            return dt.date.fromisoformat(raw).isoformat()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Dates must look like YYYY-MM-DD.") from exc

    def changed_own_data() -> None:
        _forget(services.cache, "brief", "watchlist", "stock")

    @app.get("/api/portfolio")
    def portfolio():
        return payloads.portfolio_page(store, services.ctx())

    @app.post("/api/positions")
    def position_add(body: dict = Body(...)):
        symbol = payloads.price_symbol(_symbol(str(body.get("symbol", ""))))
        ensure_prices(store, symbol)
        position_id = store.position_add(
            symbol,
            number(body, "shares", required=True, positive=True),
            number(body, "cost", required=True, positive=True),
            day(body, "opened"),
            number(body, "stop", positive=True),
            number(body, "target", positive=True),
            str(body.get("note") or "")[:300] or None,
        )
        services.side_jobs.submit(services.prepare_new_symbol, symbol)
        changed_own_data()
        return store.position(position_id)

    @app.patch("/api/positions/{position_id}")
    def position_edit(position_id: int, body: dict = Body(...)):
        fields = {}
        for key, positive in (("shares", True), ("cost", True), ("stop", True), ("target", True)):
            if key in body:
                fields[key] = number(body, key, required=key in ("shares", "cost"), positive=positive)
        if "opened" in body:
            fields["opened"] = day(body, "opened")
        if "note" in body:
            fields["note"] = str(body.get("note") or "")[:300] or None
        if not store.position_update(position_id, **fields):
            raise HTTPException(status_code=404, detail="That position no longer exists.")
        changed_own_data()
        return store.position(position_id)

    @app.delete("/api/positions/{position_id}")
    def position_remove(position_id: int):
        if not store.position_delete(position_id):
            raise HTTPException(status_code=404, detail="That position no longer exists.")
        changed_own_data()
        return {"deleted": position_id}

    @app.post("/api/positions/order")
    def positions_order(body: dict = Body(...)):
        ids = body.get("ids")
        if not isinstance(ids, list) or not all(isinstance(item, int) and not isinstance(item, bool) for item in ids):
            raise HTTPException(status_code=400, detail="Send the order as a list of position ids.")
        return {"ids": store.positions_reorder(ids)}

    # -------------------------------------------------------------------- journal
    @app.get("/api/journal")
    def journal():
        return payloads.journal_page(store, services.ctx())

    @app.post("/api/journal")
    def journal_add(body: dict = Body(...)):
        action = str(body.get("action", ""))
        if action not in ("buy", "sell", "note"):
            raise HTTPException(status_code=400, detail="Choose whether you bought, sold or are just writing a note.")
        symbol = payloads.price_symbol(_symbol(str(body.get("symbol", ""))))
        ensure_prices(store, symbol)
        reason = str(body.get("reason") or "").strip()[:4000]
        if not reason:
            raise HTTPException(status_code=400, detail="Write down why: that is what makes the journal useful later.")
        trade = action in ("buy", "sell")
        shares = number(body, "shares", required=trade, positive=True)
        price = number(body, "price", required=trade, positive=True)
        when = day(body, "date", dt.date.today().isoformat())
        message = None
        if trade and body.get("update_positions"):
            try:
                message = portfolio_analytics.apply_trade(store, symbol, action, shares, price)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        snapshot = portfolio_analytics.signal_snapshot(store, services.ctx(), symbol)
        entry_id = store.journal_add(when, symbol, action, shares, price, reason, snapshot)
        changed_own_data()
        return {"entry": store.journal_entry(entry_id), "positions": message}

    @app.patch("/api/journal/{entry_id}")
    def journal_edit(entry_id: int, body: dict = Body(...)):
        reason = str(body.get("reason") or "").strip()[:4000]
        if not reason:
            raise HTTPException(status_code=400, detail="The reason can't be empty.")
        if not store.journal_update(entry_id, reason):
            raise HTTPException(status_code=404, detail="That journal entry no longer exists.")
        return store.journal_entry(entry_id)

    @app.delete("/api/journal/{entry_id}")
    def journal_remove(entry_id: int):
        if not store.journal_delete(entry_id):
            raise HTTPException(status_code=404, detail="That journal entry no longer exists.")
        return {"deleted": entry_id}

    # -------------------------------------------------------------- tags and notes
    def tag_name(raw) -> str:
        tag = " ".join(str(raw or "").split())[:30]
        if not tag:
            raise HTTPException(status_code=400, detail="Give the list a name.")
        return tag

    @app.post("/api/tags")
    def tag_add(body: dict = Body(...)):
        symbol = payloads.price_symbol(_symbol(str(body.get("symbol", ""))))
        store.tag_add(symbol, tag_name(body.get("tag")))
        changed_own_data()
        return {"symbol": symbol, "tags": store.tags_by_symbol().get(symbol, []), "all": store.tag_names()}

    @app.post("/api/tags/remove")
    def tag_remove(body: dict = Body(...)):
        symbol = payloads.price_symbol(_symbol(str(body.get("symbol", ""))))
        store.tag_remove(symbol, tag_name(body.get("tag")))
        changed_own_data()
        return {"symbol": symbol, "tags": store.tags_by_symbol().get(symbol, []), "all": store.tag_names()}

    @app.post("/api/notes/{symbol}")
    def note_save(symbol: str, body: dict = Body(...)):
        target = payloads.price_symbol(_symbol(symbol))
        store.note_set(target, str(body.get("text") or "")[:5000])
        changed_own_data()
        return {"symbol": target, "note": store.note(target)}

    # ------------------------------------------------------------- backup and restore
    @app.get("/api/backup")
    def backup_export():
        return backup.export(store)

    @app.post("/api/backup/restore")
    def backup_restore(body: dict = Body(...)):
        mode = body.get("mode", "merge")
        if mode not in ("merge", "replace"):
            raise HTTPException(status_code=400, detail="Choose to merge or replace.")
        counts = backup.restore(store, body.get("data"), replace=mode == "replace")
        store.bump_version()
        return {"restored": counts}

    @app.get("/api/settings/storage")
    def storage():
        size = store.path.stat().st_size if store.path.exists() else 0
        wal = Path(f"{store.path}-wal")
        return {"path": str(store.path.resolve()), "bytes": size + (wal.stat().st_size if wal.exists() else 0)}

    # -------------------------------------------------------------- calendars, timeline
    @app.get("/api/earnings")
    def earnings():
        return services.cached("earnings", tuple(store.watchlist()), tuple(store.position_symbols()), compute=lambda: payloads.earnings_page(store))

    @app.get("/api/calendar")
    def calendar():
        return services.cached("calendar", compute=lambda: payloads.calendar_page(store))

    @app.get("/api/timeline")
    def timeline(date: str = Query(default="", max_length=10)):
        return services.cached("timeline", date, compute=lambda: payloads.timeline_page(services.ctx(), date or None))

    @app.get("/api/stock/{symbol}/filings")
    def stock_filings(symbol: str):
        return payloads.stock_filings(store, _symbol(symbol))

    @app.get("/api/scanner")
    def scanner():
        return services.cached("scanner", tuple(store.watchlist()), compute=lambda: payloads.scanner(store))

    @app.get("/api/stock/{symbol}")
    def stock(symbol: str):
        target = _symbol(symbol)
        return services.cached("stock", target, compute=lambda: payloads.stock_page(store, services.ctx(), target), ttl=60)

    @app.get("/api/stock/{symbol}/fundamentals")
    def stock_fundamentals(symbol: str):
        target = _symbol(symbol)
        return payloads.stock_fundamentals(store, target)

    @app.get("/api/stock/{symbol}/options")
    def stock_options(symbol: str):
        target = _symbol(symbol)
        return payloads.stock_options_summary(store, target)

    @app.get("/api/chart/{symbol}")
    def chart(symbol: str, interval: str = "1d", range: str = "1y", extended: bool = False):
        target = _symbol(symbol)
        return services.cached(
            "chart", target, interval, range, extended,
            compute=lambda: payloads.chart(store, services.ctx(), target, interval, range, extended),
            ttl=60,
        )

    @app.get("/api/extended")
    def extended_prices(symbols: str = Query(default="", max_length=2000)):
        # Not tied to the data version: pre-market prices move while nothing else refreshes.
        wanted = [payloads.price_symbol(_symbol(item)) for item in symbols.split(",") if item.strip()][:100]
        return {"quotes": extended.extended_quotes(store, wanted), "market": payloads.market_state()}

    @app.get("/api/futures")
    def futures():
        # Index futures plus the watchlist's biggest moves outside regular hours.
        quotes = extended.extended_quotes(store, store.watchlist())
        movers = sorted(quotes.values(), key=lambda quote: abs(quote["change_pct"]), reverse=True)
        return {"rows": extended.futures_board(store), "movers": movers[:8], "market": payloads.market_state()}

    @app.get("/api/signals")
    def signals():
        return payloads.signal_catalog()

    @app.get("/api/signals/current")
    def signals_current():
        return services.cached("signals_current", compute=lambda: payloads.signal_current(services.ctx()), ttl=300)

    @app.get("/api/signals/aligned")
    def signals_aligned(symbol: str, interval: str = "1d", range: str = "1y", ids: str = Query(default="", max_length=2000)):
        target = _symbol(symbol)
        requested = []
        for item in (part.strip() for part in ids.split(",")):
            if not item:
                continue
            requested.append(f"px:{_symbol(item.removeprefix('px:'))}" if item.startswith("px:") else item)
        return services.cached(
            "aligned", target, interval, range, tuple(requested),
            compute=lambda: payloads.aligned_signals(store, services.ctx(), target, interval, range, requested),
            ttl=60,
        )

    # --------------------------------------------------------------------- research
    @app.get("/api/research/options")
    def research_options():
        return payloads.research_options()

    @app.post("/api/backtest")
    def backtest(body: dict = Body(...)):
        return payloads.run_backtests(store, services.ctx(), body)

    @app.post("/api/study")
    def study(body: dict = Body(...)):
        return payloads.run_study(store, services.ctx(), body)

    # -------------------------------------------------------------------- front end
    @app.get("/api/{rest:path}")
    def unknown_api(rest: str):
        raise HTTPException(status_code=404, detail=f"No such API route: {rest}")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        response = FileResponse(STATIC_DIR / "index.html")
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    return app
