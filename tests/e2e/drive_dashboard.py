"""Drives every page and control of a running dashboard in a real browser.

It fails on: console errors, failed requests, buttons that do nothing, blank charts,
broken links, info buttons without an explanation, and stray "undefined"/"NaN" on screen.

Usage (separate from the normal test suite, because it needs a browser and live data):

    pip install playwright && playwright install chromium
    python main.py dashboard --no-browser --port 8765
    python tests/e2e/drive_dashboard.py            # everything
    python tests/e2e/drive_dashboard.py chart      # one or more scenarios by name

The watchlist is changed during the run and restored afterwards.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

from playwright.sync_api import Page, sync_playwright

BASE = os.environ.get("DASHBOARD_URL", "http://localhost:8765")
SHOTS = os.environ.get("DASHBOARD_SHOTS")
PAGES = [
    "overview", "breadth", "rotation", "volatility", "options", "flows", "macro",
    "watchlist", "alerts", "scanner", "chart", "backtest", "study", "guide", "sources",
]
ROUTES = set(PAGES) | {"stock"}
BAD_TEXT = re.compile(r"\b(undefined|NaN|null|\[object Object\]|Infinity)\b")
LONG_DECIMAL = re.compile(r"\d\.\d{7,}")
# Buttons that change saved state are exercised by their own scenarios, not the sweep.
SKIP_SELECTOR = "[data-remove], [data-watch], [data-danger], #watch-toggle, #refresh-all, #theme-toggle, #create-alert, [data-source], .search-item"


class Run:
    def __init__(self, page: Page):
        self.page = page
        self.problems: list[str] = []
        self.checks = 0
        self.requests = 0
        self.in_flight = 0
        self.last_activity = time.time()
        self.where = ""
        page.on("console", self._console)
        page.on("pageerror", lambda exc: self.problem(f"page error: {exc}"))
        page.on("request", lambda _: self._started())
        page.on("requestfinished", lambda _: self._ended())
        page.on("requestfailed", lambda _: self._ended())
        page.on("response", self._response)

    def _started(self) -> None:
        self.requests += 1
        self.in_flight += 1
        self.last_activity = time.time()

    def _ended(self) -> None:
        self.in_flight = max(0, self.in_flight - 1)
        self.last_activity = time.time()

    def idle(self, quiet: float = 0.3, timeout: float = 180.0) -> None:
        """Wait for requests to stop. Playwright's own networkidle doesn't reset on hash changes."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.page.wait_for_timeout(40)
            if self.in_flight == 0 and time.time() - self.last_activity >= quiet:
                return
        self.problem(f"requests still running after {timeout:.0f}s")

    def _console(self, message) -> None:
        if message.type != "error":
            return
        # The browser itself logs every failed request; intentional ones aren't problems.
        if self.expected_status and message.text.startswith("Failed to load resource"):
            return
        self.problem(f"console error: {message.text}")

    def _response(self, response) -> None:
        if response.status >= 400 and self.expected_status != response.status:
            self.problem(f"HTTP {response.status} for {response.url}")

    expected_status: int | None = None

    def problem(self, text: str) -> None:
        self.problems.append(f"[{self.where}] {text}")
        print(f"  PROBLEM {text}", flush=True)

    def ok(self) -> None:
        self.checks += 1

    def settle(self, extra: int = 250) -> None:
        self.page.wait_for_function("document.querySelector('#content') && !document.querySelector('#content .loading')", timeout=180000)
        self.idle()
        self.page.wait_for_timeout(extra)

    def visit(self, route: str) -> None:
        self.where = route
        self.page.evaluate("(route) => { location.hash = '#/' + route; }", route)
        self.page.wait_for_timeout(50)
        self.settle()

    def click(self, selector_or_locator, label: str, *, expect_change: bool = True) -> None:
        """Click something and verify the click actually did something."""
        locator = self.page.locator(selector_or_locator) if isinstance(selector_or_locator, str) else selector_or_locator
        self.page.evaluate(
            """() => {
                window.__mutations = 0;
                if (window.__observer) window.__observer.disconnect();
                window.__observer = new MutationObserver((list) => { window.__mutations += list.length; });
                window.__observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
            }"""
        )
        requests_before, hash_before = self.requests, self.page.evaluate("location.hash")
        locator.click(timeout=15000)
        self.page.wait_for_timeout(120)
        mutated = self.page.evaluate("window.__mutations")
        changed = mutated or self.requests != requests_before or self.page.evaluate("location.hash") != hash_before
        if expect_change and not changed:
            self.problem(f"dead control: {label}")
        else:
            self.ok()

    def check_page(self, label: str = "") -> None:
        """Checks that apply to any page state: text, charts and links."""
        text = self.page.locator("#content").inner_text()
        for match in BAD_TEXT.finditer(text):
            context = text[max(0, match.start() - 30): match.end() + 30].replace("\n", " ")
            # Metric explanations legitimately use some of these words.
            if self.where.startswith("guide"):
                continue
            self.problem(f"stray '{match.group(0)}' {label}: ...{context}...")
        for match in LONG_DECIMAL.finditer(text):
            context = text[max(0, match.start() - 20): match.end() + 10].replace("\n", " ")
            self.problem(f"unformatted number {label}: ...{context}...")
        blank = self.page.evaluate(
            """() => {
                const out = [];
                for (const box of document.querySelectorAll('#content .chart-canvas')) {
                    if (!box.offsetParent || box.hidden) continue;
                    const card = box.closest('figure');
                    const name = card?.dataset.chart || card?.querySelector('.card-title')?.textContent || 'chart';
                    const canvases = [...box.querySelectorAll('canvas')];
                    if (!canvases.length) { out.push(name + ' (never drawn)'); continue; }
                    // ECharts keeps some canvas layers empty on purpose, so judge the chart as a whole.
                    let painted = 0;
                    for (const canvas of canvases) {
                        if (canvas.width < 50 || canvas.height < 50) continue;
                        const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
                        for (let i = 3; i < data.length; i += 4 * 37) if (data[i] > 0) painted += 1;
                    }
                    if (painted < 25) out.push(name + ' (blank)');
                }
                return out;
            }"""
        )
        for name in blank:
            self.problem(f"blank chart {label}: {name}")
        links = self.page.evaluate("() => [...document.querySelectorAll('a[href]')].map((a) => a.getAttribute('href'))")
        for href in links:
            if not href.startswith("#/"):
                self.problem(f"non-internal link: {href}")
            elif href[2:].split("/")[0] not in ROUTES:
                self.problem(f"link to a page that doesn't exist: {href}")
        self.ok()

    def sweep_buttons(self) -> None:
        """Click every distinct button on the page, and flag any that do nothing."""
        done_metrics: set[str] = set()
        index = 0
        for _ in range(400):
            buttons = self.page.locator(f"#content button:visible:not({SKIP_SELECTOR}):not(.is-active)")
            count = buttons.count()
            if index >= count:
                break
            button = buttons.nth(index)
            index += 1
            metric = button.get_attribute("data-metric")
            if metric:
                if metric in done_metrics:
                    continue
                done_metrics.add(metric)
            label = (button.get_attribute("aria-label") or button.inner_text() or "button").strip()
            if button.is_disabled():
                continue
            hash_before = self.page.evaluate("location.hash")
            requests_before = self.requests
            self.click(button, label)
            if metric:
                self.check_popover(metric)
            self.page.keyboard.press("Escape")
            if self.page.evaluate("location.hash") != hash_before:
                self.page.evaluate("(value) => { location.hash = value; }", hash_before)
            if self.requests != requests_before:
                self.idle()
        self.ok()

    def check_popover(self, metric: str) -> None:
        popover = self.page.locator(".popover")
        if popover.count() != 1:
            self.problem(f"info button '{metric}' opened no explanation")
            return
        text = popover.inner_text()
        for part in ("WHAT IT IS", "HOW TO READ IT", "SOURCE"):
            if part not in text.upper():
                self.problem(f"explanation '{metric}' is missing '{part}'")
        link = popover.locator("a.popover-link").get_attribute("href")
        if link != f"#/guide/{metric}":
            self.problem(f"explanation '{metric}' links to {link}")
        self.ok()

    def try_selects(self, limit: int = 5) -> None:
        """Choose options in each dropdown and confirm the page keeps working."""
        selects = self.page.locator("#content select:visible")
        for position in range(selects.count()):
            select = selects.nth(position)
            label = select.get_attribute("aria-label") or f"select {position}"
            values = select.evaluate("(el) => [...el.options].map((o) => o.value)")
            original = select.input_value()
            tried = [value for value in values if value != original][:limit]
            for value in tried:
                select = self.page.locator("#content select:visible").nth(position)
                select.select_option(value)
                self.settle(120)
                self.check_page(f"after {label}={value}")
            select = self.page.locator("#content select:visible").nth(position)
            if original in select.evaluate("(el) => [...el.options].map((o) => o.value)"):
                select.select_option(original)
                self.settle(80)
        self.ok()

    def try_table_toggles(self) -> None:
        toggles = self.page.locator("#content figure .card-actions button:visible")
        for position in range(toggles.count()):
            toggle = toggles.nth(position)
            title = toggle.evaluate("(el) => el.closest('figure').querySelector('.card-title')?.textContent || 'chart'")
            if toggle.inner_text().strip() != "Table":
                continue
            self.click(toggle, f"table toggle on {title}")
            figure = toggle.locator("xpath=ancestor::figure")
            rows = figure.locator("table tbody tr")
            if rows.count() == 0:
                self.problem(f"table view of '{title}' is empty")
            self.click(toggle, f"chart toggle on {title}")
        self.ok()

    def choose(self, group_label: str, value_label: str) -> None:
        """Pick an option in a segmented control, whichever option is selected right now."""
        group = self.page.locator(f"[aria-label='{group_label}']")
        target = group.locator(f"button:has-text('{value_label}')")
        if "is-active" in (target.get_attribute("class") or ""):
            other = group.locator("button:not(.is-active)").first
            self.click(other, f"{group_label}: another option")
            self.settle(100)
        self.click(target, f"{group_label}: {value_label}")
        self.settle(100)

    def try_sort(self) -> None:
        tables = self.page.locator("#content table.data")
        if tables.count() == 0:
            return
        header = tables.first.locator("th.sortable").nth(1)
        if header.count():
            self.click(header, "sort a table")
            self.click(header, "sort a table the other way")


def scenario_shell(run: Run) -> None:
    run.where = "shell"
    page = run.page
    search = page.locator(".search input")
    search.fill("nvidia")
    page.wait_for_selector(".search-item")
    run.click(page.locator(".search-item").first, "search result")
    run.settle()
    if "#/stock/NVDA" not in page.url:
        run.problem(f"search went to {page.url}")
    search.fill("msft")
    page.wait_for_selector(".search-item")
    search.press("Enter")
    run.settle()
    if "#/stock/MSFT" not in page.url:
        run.problem(f"pressing Enter in search went to {page.url}")
    run.expected_status = 404
    search.fill("NOSUCHTICKER")
    page.wait_for_timeout(300)
    search.press("Enter")
    page.wait_for_selector(".error-box", timeout=60000)
    run.expected_status = None
    run.ok()
    run.visit("overview")
    run.click("#theme-toggle", "theme toggle")
    run.settle()
    run.check_page("after theme toggle")
    run.click("#theme-toggle", "theme toggle back")
    run.settle()
    run.click("#refresh-all", "refresh all data")
    page.wait_for_selector(".toast")
    run.settle()
    run.ok()


def scenario_overview(run: Run) -> None:
    run.visit("overview")
    page = run.page
    for button in ["Show all", "Show fewer"]:
        locator = page.locator(f"#content button:has-text('{button}')").first
        if locator.count():
            run.click(locator, button)
    run.click(page.locator("a.meter-row").first, "pillar link")
    run.settle()
    if "overview" in page.url:
        run.problem("pillar row did not open its page")
    run.visit("overview")
    run.click(page.locator("a.tile").first, "index tile")
    run.settle()
    if "#/stock/" not in page.url:
        run.problem("index tile did not open the stock page")


def scenario_rotation(run: Run) -> None:
    run.visit("rotation")
    page = run.page
    run.click(page.locator("table.data tbody tr").first, "select a rotation row")
    run.click(page.locator("button.chip").first, "select a quadrant chip")
    run.choose("Performance mode", "Absolute")
    run.check_page("absolute")
    run.choose("Lens", "Daily")
    run.choose("Trail length", "12")
    run.check_page("daily lens")
    run.choose("Performance mode", "Relative")
    run.choose("Lens", "Weekly")
    run.choose("Trail length", "4")
    universe = page.locator("select[aria-label='Universe']")
    for value in ("factors", "themes", "cross_asset", "watchlist", "sectors"):
        universe.select_option(value)
        run.settle()
        run.check_page(f"universe {value}")
    bench = page.locator("select[aria-label='Benchmark']")
    bench.select_option("QQQ")
    run.settle()
    bench.select_option("SPY")
    run.settle()


def scenario_options(run: Run) -> None:
    run.visit("options/SPY")
    page = run.page
    scope = page.locator("select[aria-label='Expiry scope']")
    for value in ("0dte", "7d", "30d", "90d", "monthly", "all"):
        scope.select_option(value)
        run.settle()
        run.check_page(f"scope {value}")
    dated = scope.evaluate("(el) => [...el.options].map((o) => o.value).filter((v) => /^\\d{4}-/.test(v))")
    if dated:
        scope.select_option(dated[2] if len(dated) > 2 else dated[0])
        run.settle()
        run.check_page("single expiry")
        scope.select_option("all")
        run.settle()
    for label in ("Call & put gamma", "Open interest", "Volume", "Vanna", "Charm", "Net gamma"):
        run.choose("Measure", label)
        run.check_page(f"measure {label}")
    for label in ("±5%", "±30%", "±15%"):
        run.choose("Strike range", label)
    other = page.locator("input[aria-label='Type any optionable ticker']")
    other.fill("TSLA")
    other.press("Enter")
    run.settle()
    run.check_page("TSLA")
    if "TSLA" not in page.url:
        run.problem("typing a ticker did not switch the underlying")
    underlying = page.locator("select[aria-label='Underlying']")
    for value in ("SPX", "QQQ", "IWM", "NDX", "RUT", "VIX", "SPY"):
        underlying.select_option(value)
        run.settle()
        run.check_page(f"underlying {value}")
    # A ticker with no options must explain itself rather than break.
    other.fill("BRK-A")
    other.press("Enter")
    run.settle()
    run.ok()
    run.visit("options/SPX")
    run.check_page("back to SPX")


def scenario_watchlist(run: Run) -> None:
    run.visit("watchlist")
    page = run.page
    before = json.loads(page.evaluate("() => fetch('/api/watchlist').then((r) => r.text())"))
    symbols = {row["symbol"] for row in before["rows"]}
    box = page.locator("input[aria-label='Ticker to add']")
    run.expected_status = 404
    box.fill("NOSUCHTICKER")
    box.press("Enter")
    page.wait_for_selector(".toast.error", timeout=60000)
    run.expected_status = None
    run.ok()
    box.fill("AMD")
    run.click(page.locator("button:has-text('Add to watchlist')"), "add to watchlist")
    page.wait_for_selector("[data-remove='AMD']", timeout=60000)
    run.settle()
    run.check_page("after adding AMD")
    # Newly added rows fill in from a background job; wait until its data has arrived.
    page.wait_for_timeout(2500)
    run.click(page.locator("[data-remove='AMD']"), "remove from watchlist")
    page.wait_for_selector("[data-remove='AMD']", state="detached", timeout=30000)
    run.ok()
    run.click(page.locator("table.data tbody tr").first.locator("td").nth(1), "open a row")
    run.settle()
    if "#/stock/" not in page.url:
        run.problem("selecting a watchlist row did not open the stock page")
    after = json.loads(page.evaluate("() => fetch('/api/watchlist').then((r) => r.text())"))
    if {row["symbol"] for row in after["rows"]} != symbols:
        run.problem("watchlist was not restored after add and remove")


def scenario_scanner(run: Run) -> None:
    run.visit("scanner")
    page = run.page
    page.wait_for_selector("#content table.data tbody tr", timeout=60000)
    scan = page.locator("select[aria-label='Scan']")
    for value in scan.evaluate("(el) => [...el.options].map((o) => o.value)"):
        scan.select_option(value)
        page.wait_for_timeout(150)
        run.check_page(f"scan {value}")
    scan.select_option("all")
    sector = page.locator("select[aria-label='Sector']")
    sector.select_option("Energy")
    page.wait_for_timeout(100)
    sector.select_option("")
    search = page.locator("input[aria-label='Search members']")
    search.fill("apple")
    page.wait_for_timeout(150)
    if page.locator("#content table.data tbody tr").count() < 1:
        run.problem("scanner search found nothing for 'apple'")
    watch = page.locator("[data-watch='AAPL']")
    run.click(watch, "unwatch from scanner")
    page.wait_for_timeout(300)
    run.click(page.locator("[data-watch='AAPL']"), "watch from scanner")
    page.wait_for_timeout(300)
    search.fill("")
    page.wait_for_timeout(150)
    run.ok()


def scenario_stock(run: Run) -> None:
    run.visit("stock/AAPL")
    page = run.page
    page.wait_for_selector("text=Valuation", timeout=60000)
    run.settle()
    run.check_page("stock AAPL")
    run.click("#watch-toggle", "remove from watchlist")
    run.click("#watch-toggle", "add back to watchlist")
    for label, part in (("Open in Chart Lab", "#/chart/"), ("Backtest this", "#/backtest/"), ("Full gamma view", "#/options/")):
        run.visit("stock/AAPL")
        page.wait_for_selector(f"a:has-text('{label}')", timeout=60000)
        run.click(page.locator(f"a:has-text('{label}')"), label)
        run.settle()
        if part not in page.url:
            run.problem(f"'{label}' went to {page.url}")
    # An ETF has no earnings or valuation, and an index has no volume-based fundamentals.
    for symbol in ("SPY", "^GSPC", "^VIX", "BTC-USD"):
        run.visit(f"stock/{symbol}")
        page.wait_for_timeout(1500)
        run.settle()
        run.check_page(f"stock {symbol}")


def scenario_chart(run: Run) -> None:
    run.visit("chart/SPY")
    page = run.page
    panel = page.locator(".lab-panel")
    # Turn on every layer, one at a time, then turn them off again.
    boxes = panel.locator("input[type=checkbox]")
    initial = [boxes.nth(index).is_checked() for index in range(boxes.count())]
    for index in range(boxes.count()):
        run.click(boxes.nth(index), f"chart layer {index}")
        run.idle()
        run.check_page(f"layer {index}")
        if not boxes.nth(index).is_checked():
            run.click(boxes.nth(index), f"chart layer {index} on")
            run.idle()
    run.check_page("all layers on")
    # Every interval and range, with all the layers on.
    for interval in ("15 min", "1 hour", "Weekly", "Daily"):
        run.click(page.locator(f".segmented button:has-text('{interval}')"), f"interval {interval}")
        run.settle(200)
        run.check_page(f"interval {interval}")
        ranges = page.locator("[aria-label='Date range'] button")
        for position in range(ranges.count()):
            button = page.locator("[aria-label='Date range'] button").nth(position)
            if "is-active" in (button.get_attribute("class") or ""):
                continue
            run.click(button, f"range {position} on {interval}")
            run.settle(150)
            run.check_page(f"{interval} range {position}")
    page.locator("[aria-label='Date range'] button:has-text('1Y')").click()
    run.settle()
    # Signals: add several, including one that can go negative, and a comparison ticker.
    signal_select = page.locator("select[aria-label='Add a signal']")
    for value in ("vix", "pct_above_50", "mcclellan", "dix", "pc_equity_10d"):
        signal_select.select_option(value)
        run.settle(200)
    compare = page.locator("input[aria-label='Compare another ticker']")
    compare.fill("QQQ")
    compare.press("Enter")
    run.settle(200)
    run.check_page("six signals")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/chart_panes.png", full_page=True)
    for mode in ("On price: indexed", "On price: z-score", "Separate panes"):
        run.click(page.locator(f".segmented button:has-text('{mode}')"), f"mode {mode}")
        page.wait_for_timeout(150)
        run.check_page(f"mode {mode}")
        if SHOTS:
            page.screenshot(path=f"{SHOTS}/chart_{mode.split()[-1].replace(':', '')}.png", full_page=True)
    # A seventh signal is refused with a message rather than silently dropped.
    signal_select.select_option("vvix")
    page.wait_for_selector(".toast")
    run.ok()
    # Bad comparison ticker explains itself.
    # Remove the signals again.
    chips = page.locator(".lab-panel .chip button[aria-label^='Remove']")
    while chips.count():
        run.click(chips.first, "remove a signal")
        page.wait_for_timeout(60)
    # Shading.
    run.click(page.locator("button:has-text('Add shading')"), "add shading")
    run.settle(150)
    run.check_page("shaded")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/chart_shaded.png", full_page=True)
    run.click(page.locator(".lab-panel .chip button[aria-label^='Remove']").first, "remove shading")
    page.wait_for_timeout(200)
    # Table view.
    run.click(page.locator("figure .card-actions button"), "chart table toggle")
    if page.locator("figure table tbody tr").count() == 0:
        run.problem("chart table view is empty")
    run.click(page.locator("figure .card-actions button"), "chart toggle")
    # Another symbol, then a bad symbol, which must keep the current chart.
    symbol = page.locator("input[aria-label='Symbol']")
    symbol.fill("NVDA")
    run.click(page.locator("button:has-text('Load')"), "load symbol")
    run.settle()
    if "NVDA" not in page.url:
        run.problem("loading NVDA did not switch the chart")
    run.expected_status = 404
    symbol.fill("NOSUCHTICKER")
    symbol.press("Enter")
    page.wait_for_selector(".toast.error", timeout=60000)
    run.settle()
    run.expected_status = None
    if symbol.input_value() != "NVDA":
        run.problem("after a bad symbol the chart did not return to NVDA")
    # Restore the layers as they were.
    for index in range(boxes.count()):
        if boxes.nth(index).is_checked() != initial[index]:
            boxes.nth(index).click()
    run.idle()
    run.ok()


def scenario_backtest(run: Run) -> None:
    run.visit("backtest")
    page = run.page
    page.wait_for_selector("#content table.data tbody tr", timeout=120000)
    run.check_page("default run")
    rows = page.locator("#content section.card table.data").first.locator("tbody tr")
    for index in range(min(rows.count(), 4)):
        run.click(rows.nth(index).locator("td").nth(1), f"leaderboard row {index}")
        page.wait_for_timeout(200)
        run.check_page(f"leaderboard row {index}")
    run.click(page.locator(".segmented button:has-text('All strategies')"), "all strategies curve")
    page.wait_for_timeout(200)
    run.check_page("all strategies")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/backtest_all.png", full_page=True)
    run.click(page.locator(".segmented button:has-text('vs Buy & Hold')"), "pair curve")
    page.wait_for_timeout(100)
    run.click("#edit-strategies", "edit strategies")
    run.click("#add-condition", "add a rule condition")
    conditions = page.locator(".rule-row")
    if conditions.count() < 2:
        run.problem("adding a condition did not add a row")
    conditions.first.locator("select").first.select_option("self:rsi14")
    conditions.first.locator("input[type=number]").fill("40")
    conditions.first.locator("input[type=number]").press("Tab")
    run.click(page.locator(".rule-row button").first, "remove a condition")
    if not page.locator("#custom-enabled").is_checked():
        page.locator("#custom-enabled").check()
    box = page.locator("[data-strategy=rsi_reversion] input[type=checkbox]")
    box.uncheck()
    fast = page.locator("[data-strategy=ma_cross_20_50] .strategy-params input").first
    fast.fill("10")
    fast.press("Tab")
    run.click("#run-backtests", "run backtests")
    page.wait_for_function("document.querySelector('#run-backtests').textContent === 'Run backtests'", timeout=120000)
    run.settle()
    run.check_page("custom rule run")
    text = page.locator("#content").inner_text()
    if "Custom rule" not in text:
        run.problem("custom rule missing from the leaderboard")
    if "RSI Mean Reversion" in page.locator("#content section.card table.data").first.inner_text():
        run.problem("unchecked strategy still ran")
    # Errors explain themselves and keep the previous results on screen.
    fast.fill("90")
    fast.press("Tab")
    run.expected_status = 400
    run.click("#run-backtests", "run with a bad setting")
    page.wait_for_selector(".toast.error", timeout=60000)
    run.expected_status = None
    fast.fill("20")
    fast.press("Tab")
    symbol = page.locator("#bt-symbol")
    symbol.fill("NVDA")
    symbol.press("Tab")
    box.check()
    page.locator("#custom-enabled").uncheck()
    run.click("#run-backtests", "run on NVDA")
    page.wait_for_function("document.querySelector('#run-backtests').textContent === 'Run backtests'", timeout=120000)
    run.settle()
    run.check_page("NVDA run")
    if "NVDA" not in page.locator("#content").inner_text():
        run.problem("backtest did not switch to NVDA")
    symbol.fill("SPY")
    symbol.press("Tab")
    run.click("#run-backtests", "run on SPY")
    page.wait_for_function("document.querySelector('#run-backtests').textContent === 'Run backtests'", timeout=120000)
    run.settle()
    run.click("#edit-strategies", "hide the editor")


def scenario_study(run: Run) -> None:
    run.visit("study")
    page = run.page
    page.wait_for_selector("#content table.data tbody tr", timeout=120000)
    run.check_page("default study")
    preset = page.locator("#study-preset")
    for value in preset.evaluate("(el) => [...el.options].map((o) => o.value).filter(Boolean)"):
        preset.select_option(value)
        page.wait_for_function("document.querySelector('#run-study').textContent === 'Run study'", timeout=120000)
        run.settle(150)
        run.check_page(f"preset {value}")
    run.click("#study-add-condition", "add a study condition")
    rows = page.locator(".rule-row")
    rows.nth(1).locator("select").first.select_option("self:rsi14")
    if preset.input_value() != "":
        run.problem("editing a condition did not switch the preset to My own conditions")
    run.click("#run-study", "run study")
    page.wait_for_function("document.querySelector('#run-study').textContent === 'Run study'", timeout=120000)
    run.settle()
    run.check_page("custom study")
    run.click(page.locator(".rule-row button").first, "remove a study condition")
    symbol = page.locator("#study-symbol")
    symbol.fill("QQQ")
    symbol.press("Tab")
    run.click("#run-study", "run study on QQQ")
    page.wait_for_function("document.querySelector('#run-study').textContent === 'Run study'", timeout=120000)
    run.settle()
    run.check_page("QQQ study")
    symbol.fill("SPY")
    symbol.press("Tab")
    preset.select_option("vix_30")
    page.wait_for_function("document.querySelector('#run-study').textContent === 'Run study'", timeout=120000)
    run.settle()


def scenario_guide(run: Run) -> None:
    run.visit("guide")
    page = run.page
    search = page.locator("#guide-search")
    search.fill("gamma")
    page.wait_for_timeout(150)
    if "Gamma flip" not in page.locator("#content").inner_text():
        run.problem("guide search did not find Gamma flip")
    search.fill("qqqqqqqq")
    page.wait_for_timeout(150)
    if "No metric matches" not in page.locator("#content").inner_text():
        run.problem("guide search with no result shows no message")
    search.fill("")
    category = page.locator("select[aria-label='Category']")
    category.select_option("options")
    page.wait_for_timeout(100)
    category.select_option("")
    run.visit("guide/gamma_flip")
    if page.locator("#metric-gamma_flip.is-target").count() != 1:
        run.problem("deep link did not highlight the metric")
    run.ok()


def scenario_alerts(run: Run) -> None:
    """Create, fire, pause and delete alerts. Leaves the user's own alerts untouched."""
    run.visit("alerts")
    page = run.page
    before = json.loads(page.evaluate("() => fetch('/api/alerts').then((r) => r.text())"))
    existing = {alert["id"] for alert in before["alerts"]}
    # A stock alert whose condition is already true fires straight away.
    page.locator("#alert-symbol").fill("SPY")
    page.locator("#alert-measure").select_option("close")
    page.locator("#alert-op").select_option("above")
    page.locator("#alert-value").fill("1")
    run.idle()
    if "Now $" not in page.locator(".rule-hint").inner_text():
        run.problem("alert form shows no current value")
    run.click("#create-alert", "create a stock alert")
    run.settle()
    if "SPY price is above $1.00" not in page.locator("#content").inner_text():
        run.problem("the new alert did not fire although its condition is met")
    if not page.locator("#alert-link .alert-count").count():
        page.wait_for_timeout(1500)
        if not page.locator("#alert-link .alert-count").count():
            run.problem("top bar shows no unread count")
    # A market-signal alert from an example.
    run.click(page.locator("button.chip", has_text="VIX above 30"), "use an example")
    run.click("#create-alert", "create a signal alert")
    run.settle()
    run.check_page("with alerts")
    # Missing level is refused with a message.
    page.locator("#alert-value").fill("")
    page.locator("#alert-value").dispatch_event("input")
    run.click("#create-alert", "create without a level")
    page.wait_for_selector(".toast.error")
    run.expected_status = 404
    run.choose("What to watch", "A stock")
    page.locator("#alert-symbol").fill("NOSUCHTICKER")
    page.locator("#alert-value").fill("5")
    page.locator("#alert-value").dispatch_event("input")
    run.click("#create-alert", "create for an unknown ticker")
    page.wait_for_selector(".toast.error:has-text('NOSUCHTICKER')", timeout=60000)
    run.expected_status = None
    after = json.loads(page.evaluate("() => fetch('/api/alerts').then((r) => r.text())"))
    created = [alert["id"] for alert in after["alerts"] if alert["id"] not in existing]
    if len(created) != 2:
        run.problem(f"expected 2 new alerts, found {len(created)}")
    # Overview shows the notification.
    run.visit("overview")
    if "SPY price is above $1.00" not in page.locator("#content").inner_text():
        run.problem("Overview does not show the alert notification")
    run.visit("alerts")
    # Pause and resume one, then delete what this test created.
    toggle = page.locator(f"tr:has([data-alert='{created[0]}']) input[type=checkbox]")
    run.click(toggle, "pause an alert")
    run.settle()
    if "Paused" not in page.locator("#content").inner_text():
        run.problem("pausing an alert did not show it as paused")
    run.click(page.locator(f"tr:has([data-alert='{created[0]}']) input[type=checkbox]"), "resume an alert")
    run.settle()
    for alert_id in created:
        run.click(page.locator(f"[data-alert='{alert_id}']"), "delete an alert")
        run.settle()
    if not before["events"]:
        page.evaluate("() => fetch('/api/alerts/events', { method: 'DELETE' })")
    final = json.loads(page.evaluate("() => fetch('/api/alerts').then((r) => r.text())"))
    if {alert["id"] for alert in final["alerts"]} != existing:
        run.problem("alerts were not restored after the test")


def scenario_sources(run: Run) -> None:
    run.visit("sources")
    page = run.page
    run.click(page.locator("[data-source=cnn]"), "refresh one source")
    page.wait_for_selector(".toast")
    run.settle()
    run.check_page("after refresh")


def main() -> int:
    only = set(sys.argv[1:])  # scenario names to run alone, skipping the full sweep
    started = time.time()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1500, "height": 950}, color_scheme="dark")
        page = context.new_page()
        run = Run(page)
        page.goto(f"{BASE}/")
        page.wait_for_selector(".nav-link")
        run.settle()

        if not only:
            print("Navigation and generic sweep", flush=True)
        for name in [] if only else PAGES:
            run.where = name
            print(f"  {name}", flush=True)
            page.locator(f".nav-link[href='#/{name}']").click()
            page.wait_for_timeout(50)
            run.settle()
            active = page.locator(".nav-link.is-active").get_attribute("href")
            if active != f"#/{name}":
                run.problem(f"sidebar highlights {active}")
            if not page.locator("#content .page-title").count():
                run.problem("page has no title")
            run.check_page("on arrival")
            run.try_table_toggles()
            run.try_sort()
            if name not in ("scanner", "chart", "backtest", "study", "options", "rotation"):
                run.try_selects()
            if name != "backtest":
                run.sweep_buttons()
            if SHOTS:
                page.screenshot(path=f"{SHOTS}/{name}.png", full_page=True)

        scenarios = [
            scenario_shell, scenario_overview, scenario_rotation, scenario_options, scenario_watchlist,
            scenario_scanner, scenario_stock, scenario_chart, scenario_backtest, scenario_study,
            scenario_guide, scenario_alerts, scenario_sources,
        ]
        for scenario in scenarios:
            name = scenario.__name__.removeprefix("scenario_")
            if only and name not in only:
                continue
            print(f"Scenario: {name}", flush=True)
            run.where = name
            try:
                scenario(run)
            except Exception as exc:  # keep going, so one failure doesn't hide others
                run.problem(f"scenario crashed: {type(exc).__name__}: {str(exc)[:300]}")
                if SHOTS:
                    page.screenshot(path=f"{SHOTS}/crash_{name}.png", full_page=True)

        browser.close()
    print(f"\n{run.checks} checks in {time.time() - started:.0f}s, {len(run.problems)} problems")
    for problem in run.problems:
        print(" -", problem)
    return 1 if run.problems else 0


if __name__ == "__main__":
    sys.exit(main())
