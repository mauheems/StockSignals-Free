"""The local web app: a JSON API plus the static dashboard front end."""

from __future__ import annotations

import json
import logging
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
from src.analytics.signals import SELF_SIGNALS, Ctx
from src.dashboard import payloads
from src.dashboard.metric_guide import GUIDE_CATEGORIES, METRICS
from src.data import cboe, extended, fundamentals, macro_sources, refresh
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
        return services.cached("overview", compute=lambda: payloads.overview(store, services.ctx()), ttl=60)

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
        return {"rows": extended.futures_board(store), "market": payloads.market_state()}

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
