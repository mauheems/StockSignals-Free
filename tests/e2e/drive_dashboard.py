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
from pathlib import Path
import os
import re
import sys
import time

from playwright.sync_api import Page, sync_playwright

BASE = os.environ.get("DASHBOARD_URL", "http://localhost:8765")
SHOTS = os.environ.get("DASHBOARD_SHOTS")
PAGES = [
    "overview", "breadth", "rotation", "volatility", "options", "flows", "macro", "prediction", "earnings", "calendar",
    "portfolio", "journal", "timeline", "settings",
    "watchlist", "alerts", "scanner", "chart", "backtest", "study", "guide", "sources",
]
ROUTES = set(PAGES) | {"stock"}
BAD_TEXT = re.compile(r"\b(undefined|NaN|null|\[object Object\]|Infinity)\b")
LONG_DECIMAL = re.compile(r"\d\.\d{7,}")
# Buttons that change saved state are exercised by their own scenarios, not the sweep.
EXTERNAL_SOURCES = ("https://kalshi.com/", "https://polymarket.com/", "https://finance.yahoo.com/")
SKIP_SELECTOR = "[data-remove], [data-watch], [data-danger], #watch-toggle, #refresh-all, #theme-toggle, #create-alert, #drawer-create-alert, #stock-add-alert, #edit-signals, .row-handle, [data-keep-order], [data-source], #notify-toggle, #backup-download, .journal-entry .card-actions button, .search-item"


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
        links = self.page.evaluate("() => [...document.querySelectorAll('a[href]')].map((a) => [a.getAttribute('href'), a.target, a.rel])")
        for href, target, rel in links:
            # The only outside links are to where prediction market odds come from, in a new tab.
            if href.startswith(EXTERNAL_SOURCES):
                if target != "_blank" or "noopener" not in rel:
                    self.problem(f"outside link that doesn't open in a new tab: {href}")
            elif not href.startswith("#/"):
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
    run.click(page.locator("a.tile[href^='#/stock/']").first, "index tile")
    run.settle()
    if "#/stock/" not in page.url:
        run.problem("index tile did not open the stock page")


def scenario_key_signals(run: Run) -> None:
    """Edit the Overview's Key signals: remove, reorder, add, reset. Restores the user's list."""
    page = run.page
    before = json.loads(page.evaluate("() => fetch('/api/overview').then((r) => r.text())"))
    original, custom = before["signal_ids"], before["signals_custom"]
    try:
        run.visit("overview")
        run.click("#edit-signals", "edit Key signals")
        page.wait_for_selector(".edit-list .edit-row")
        rows = lambda: page.evaluate("() => [...document.querySelectorAll('.edit-row')].map((row) => row.dataset.signal)")
        start = rows()
        run.click(page.locator(f"[data-remove-signal='{start[0]}']"), "remove a signal")
        run.idle()
        if start[0] in rows():
            run.problem("removing a Key signal left it in the list")
        run.click(page.locator(".edit-row").first.locator("button[aria-label^='Move'][aria-label$='down']"), "move a signal down")
        run.idle()
        if rows()[:2] != [start[2], start[1]]:
            run.problem(f"moving down gave {rows()[:2]}")
        # Drag the fourth row to the top (both on screen: a simulated drag doesn't scroll).
        page.evaluate("() => document.querySelector('.edit-list').scrollIntoView({ block: 'center' })")
        dragged = rows()[3]
        page.locator(".edit-row").nth(3).drag_to(page.locator(".edit-row").first, target_position={"x": 40, "y": 3})
        run.idle()
        if rows()[0] != dragged:
            run.problem(f"dragging the fourth row to the top gave {rows()[:3]}")
        page.locator("#add-key-signal").select_option(start[0])
        page.wait_for_function(f"() => [...document.querySelectorAll('.edit-row')].some((row) => row.dataset.signal === '{start[0]}')")
        run.idle()
        run.click("#done-signals", "finish editing")
        run.settle()
        saved = json.loads(page.evaluate("() => fetch('/api/overview').then((r) => r.text())"))
        table = page.locator("#content table.data").first.locator("tbody tr").count()
        if table != len([row for row in saved["signals"]]):
            run.problem("the Key signals table does not match the saved list")
        if saved["signal_ids"][0] != dragged or saved["signal_ids"][-1] != start[0]:
            run.problem(f"the saved order is not what was arranged: {saved['signal_ids'][:3]}")
        if SHOTS:
            page.screenshot(path=f"{SHOTS}/key_signals.png")
        run.click("#edit-signals", "edit again")
        if SHOTS:
            page.locator("#content .split-2-1").first.screenshot(path=f"{SHOTS}/key_signals_edit.png")
        run.click("#reset-signals", "reset to default")
        page.wait_for_timeout(1500)
        run.idle()
        reset = json.loads(page.evaluate("() => fetch('/api/overview').then((r) => r.text())"))
        if reset["signals_custom"]:
            run.problem("reset did not return to the default list")
        run.click("#done-signals", "finish editing after reset")
        run.check_page("after reset")
    finally:
        if custom:
            page.evaluate("(ids) => fetch('/api/overview/signals', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }) })", original)
        else:
            page.evaluate("() => fetch('/api/overview/signals', { method: 'DELETE' })")
        page.wait_for_timeout(300)
    after = json.loads(page.evaluate("() => fetch('/api/overview').then((r) => r.text())"))
    if after["signal_ids"] != original or after["signals_custom"] != custom:
        run.problem("the Key signals list was not restored after the test")


def scenario_ordering(run: Run) -> None:
    """Your own order for the watchlist and alerts: drag, keyboard, sort and keep. Restores both."""
    page = run.page
    get = lambda path: json.loads(page.evaluate(f"() => fetch('{path}').then((r) => r.text())"))
    watch_before = [row["symbol"] for row in get("/api/watchlist")["rows"]]
    alerts_before = [alert["id"] for alert in get("/api/alerts")["alerts"]]
    shown = lambda: page.evaluate("() => [...document.querySelectorAll('#content [data-handle]')].map((el) => el.dataset.handle)")
    try:
        run.visit("watchlist")
        page.wait_for_selector("#content [data-handle]")
        page.evaluate("() => localStorage.removeItem('ms.table:watchlist')")
        run.visit("overview")
        run.visit("watchlist")
        page.wait_for_selector("#content [data-handle]")
        # Outside market hours an extra price column arrives a moment later and shifts the rest.
        run.idle()
        start = shown()
        if start != watch_before:
            run.problem(f"watchlist on screen {start[:3]} is not in the saved order {watch_before[:3]}")
        # Drag the third stock to the top.
        page.locator(f"[data-handle='{start[2]}']").drag_to(page.locator("#content tbody tr").first, target_position={"x": 60, "y": 3})
        run.idle()
        if shown()[0] != start[2] or get("/api/watchlist")["rows"][0]["symbol"] != start[2]:
            run.problem(f"dragging a stock to the top gave {shown()[:3]}")
        # Keyboard: move the top stock down one place.
        page.locator(f"[data-handle='{start[2]}']").focus()
        page.keyboard.press("ArrowDown")
        run.idle()
        if shown()[1] != start[2]:
            run.problem(f"the down arrow key did not move the stock: {shown()[:3]}")
        if page.evaluate("() => document.activeElement?.dataset.handle") != start[2]:
            run.problem("keyboard focus did not follow the moved stock")
        # Sort by a column, then keep that as your order.
        # A click event on the header itself: the middle of a short title is its info button,
        # and columns can shift while prices outside market hours arrive.
        # (A plain text match would also find "Ext. hours" + its "i" button: "hoursi".)
        page.locator("#content th").filter(has_text=re.compile(r"^RSI")).first.dispatch_event("click")
        run.idle()
        tools = page.locator("#content .table-tools").inner_text()
        if "Sorted by RSI" not in tools:
            run.problem(f"sorting does not say what it is sorted by: {tools!r}")
        sorted_view = shown()
        run.click(page.locator("[data-keep-order]"), "keep the sorted order")
        run.idle()
        if [row["symbol"] for row in get("/api/watchlist")["rows"]] != sorted_view:
            run.problem("'Keep this order' did not save the sorted order")
        # The third click on a header returns to your own order.
        for _ in range(3):
            page.locator("#content th:has-text('1 week')").first.dispatch_event("click")
        if shown() != sorted_view:
            run.problem("the third header click did not return to your own order")
        # The order survives a reload.
        page.reload()
        page.wait_for_selector("#content [data-handle]")
        if shown() != sorted_view:
            run.problem("your watchlist order was lost after reloading")
        if SHOTS:
            page.screenshot(path=f"{SHOTS}/ordering_watchlist.png")
        if len(alerts_before) >= 2:
            run.visit("alerts")
            page.wait_for_selector("#content [data-handle]")
            first = shown()[0]
            page.locator(f"[data-handle='{first}']").focus()
            page.keyboard.press("ArrowDown")
            run.idle()
            saved = [alert["id"] for alert in get("/api/alerts")["alerts"]]
            if str(saved[1]) != first:
                run.problem(f"moving an alert down was not saved: {saved}")
    finally:
        page.evaluate("(symbols) => fetch('/api/watchlist/order', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbols }) })", watch_before)
        page.evaluate("(ids) => fetch('/api/alerts/order', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }) })", alerts_before)
        page.wait_for_timeout(300)
    if [row["symbol"] for row in get("/api/watchlist")["rows"]] != watch_before:
        run.problem("the watchlist order was not restored after the test")
    if [alert["id"] for alert in get("/api/alerts")["alerts"]] != alerts_before:
        run.problem("the alert order was not restored after the test")


def scenario_rotation(run: Run) -> None:
    run.visit("rotation")
    page = run.page
    run.click(page.locator("table.data tbody tr").first, "select a rotation row")
    run.click(page.locator("button.quad-item").first, "select a quadrant member")
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
    if [row["symbol"] for row in after["rows"]] != [row["symbol"] for row in before["rows"]]:
        run.problem("watchlist was not restored, in the same order, after add and remove")


def restore_watchlist_order(page: Page, symbols: list[str]) -> None:
    """Re-adding a stock puts it at the end, so tests that do that put the order back."""
    page.evaluate("(symbols) => fetch('/api/watchlist/order', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbols }) })", symbols)


def watchlist_order(page: Page) -> list[str]:
    return [row["symbol"] for row in json.loads(page.evaluate("() => fetch('/api/watchlist').then((r) => r.text())"))["rows"]]


def scenario_scanner(run: Run) -> None:
    run.visit("scanner")
    page = run.page
    order = watchlist_order(page)
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
    restore_watchlist_order(page, order)
    run.ok()


def scenario_stock(run: Run) -> None:
    run.visit("stock/AAPL")
    page = run.page
    order = watchlist_order(page)
    page.wait_for_selector("text=Valuation", timeout=60000)
    run.settle()
    run.check_page("stock AAPL")
    run.click("#watch-toggle", "remove from watchlist")
    run.click("#watch-toggle", "add back to watchlist")
    run.idle()
    restore_watchlist_order(page, order)
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
    # Price as a line, then back to candles. The page sweep may have left it on Line already.
    line = page.locator("[aria-label='Price style'] button:has-text('Line')")
    if "is-active" in (line.get_attribute("class") or ""):
        run.click(page.locator("[aria-label='Price style'] button:has-text('Candles')"), "price as candles first")
    run.click(line, "price as a line")
    run.check_page("price as a line")
    if "closing price" not in page.locator(".lab-chart-note").inner_text():
        run.problem("the chart note still describes candles in line mode")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/chart_line.png", full_page=True)
    run.click(page.locator("[aria-label='Price style'] button:has-text('Candles')"), "price as candles")
    for label in ("Vs previous close", "Like the candle"):
        button = page.locator(f"[aria-label='Volume bar colour'] button:has-text('{label}')")
        if "is-active" not in (button.get_attribute("class") or ""):
            run.click(button, f"volume colour {label}")
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
    try:
        page.wait_for_function("() => (document.querySelector('#content .rule-hint')?.textContent || '').startsWith('Now $')", timeout=15000)
    except Exception:
        run.problem(f"alert form shows no current value: {page.locator('#content .rule-hint').inner_text()!r}")
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


def scenario_drawer(run: Run) -> None:
    """The alert drawer: opens on any page, starts from what's on screen, creates an alert."""
    page = run.page
    before = json.loads(page.evaluate("() => fetch('/api/alerts').then((r) => r.text())"))
    existing = {alert["id"] for alert in before["alerts"]}
    run.visit("volatility")
    run.click("#alert-link", "open the alert drawer")
    page.wait_for_selector(".drawer.is-open #drawer-alert-signal", timeout=30000)
    run.idle()
    if page.locator("#drawer-alert-signal").input_value() != "vix":
        run.problem("on the Volatility page the drawer did not start from VIX")
    if not page.locator("#drawer-alert-signal optgroup[label='On this page']").count():
        run.problem("drawer does not list the page's own signals first")
    if page.evaluate("() => document.activeElement?.id") != "drawer-alert-value":
        run.problem("opening the drawer did not put the cursor in the level field")
    # On a wide screen the page makes room, so the drawer hides nothing.
    content_right = page.evaluate("() => document.querySelector('#content').getBoundingClientRect().right")
    drawer_left = page.evaluate("() => document.querySelector('.drawer').getBoundingClientRect().left")
    if content_right > drawer_left + 1:
        run.problem("the drawer covers part of the page")
    run.check_page("volatility with drawer")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/drawer_volatility.png")
    # Moving to a stock, with the form untouched, follows the page.
    run.visit("stock/NVDA")
    page.wait_for_function("() => document.querySelector('#drawer-alert-symbol')?.value === 'NVDA'", timeout=30000)
    # A level far away, so the test alert never fires and leaves no notification behind.
    page.locator("#drawer-alert-op").select_option("above")
    page.locator("#drawer-alert-value").fill("100000")
    run.click("#drawer-create-alert", "create an alert from the drawer")
    page.wait_for_selector(".toast:has-text('Alert created')", timeout=30000)
    run.settle()
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/drawer_stock.png")
    after = json.loads(page.evaluate("() => fetch('/api/alerts').then((r) => r.text())"))
    created = [alert for alert in after["alerts"] if alert["id"] not in existing]
    if len(created) != 1 or created[0]["rule"].get("symbol") != "NVDA":
        run.problem(f"expected one new NVDA alert from the drawer, found {[alert['label'] for alert in created]}")
    # Escape closes it, and the page gets its full width back.
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    if page.locator(".drawer.is-open").count():
        run.problem("Escape did not close the drawer")
    # The stock page's own button opens it too, and Chart Lab passes its symbol.
    run.click("#stock-add-alert", "add alert from the stock page")
    page.wait_for_function("() => document.querySelector('#drawer-alert-symbol')?.value === 'NVDA'", timeout=30000)
    run.click("#close-alert-drawer", "close the drawer")
    page.wait_for_timeout(400)
    run.visit("options/SPX")
    run.click("#alert-link", "open the drawer on the options page")
    page.wait_for_function("() => document.querySelector('#drawer-alert-symbol')?.value === 'SPX'", timeout=30000)
    # The current value arrives from a preview request that starts 300 ms after the form changes.
    try:
        page.wait_for_function("() => (document.querySelector('.drawer .rule-hint')?.textContent || '').startsWith('Now')", timeout=15000)
    except Exception:
        run.problem(f"an SPX alert from the options page shows no current value: {page.locator('.drawer .rule-hint').inner_text()!r}")
    run.click("#alert-link", "close the drawer from the top bar")
    page.wait_for_timeout(400)
    for alert in created:
        page.evaluate(f"() => fetch('/api/alerts/{alert['id']}', {{ method: 'DELETE' }})")
    final = json.loads(page.evaluate("() => fetch('/api/alerts').then((r) => r.text())"))
    if {alert["id"] for alert in final["alerts"]} != existing:
        run.problem("alerts were not restored after the drawer test")
    run.ok()


def scenario_extended(run: Run) -> None:
    """Pre-market, after-hours and futures prices. Only checkable outside regular hours."""
    page = run.page
    page.goto(f"{BASE}/")
    market = json.loads(page.evaluate("() => fetch('/api/market').then((r) => r.text())"))
    if market["state"] == "open":
        print("    market is open: extended prices are hidden by design, skipping", flush=True)
        run.visit("chart/SPY")
        return
    run.visit("overview")
    page.wait_for_selector("text=index futures", timeout=30000)
    if page.locator("#content a.tile[href^='#/chart/ES']").count() != 1:
        run.problem("overview has no S&P 500 futures tile")
    run.check_page("overview futures")
    if SHOTS:
        page.locator("#content").screenshot(path=f"{SHOTS}/ext_overview.png")
    run.visit("watchlist")
    page.wait_for_selector("th:has-text('Ext. hours')", timeout=30000)
    run.check_page("watchlist extended column")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/ext_watchlist.png")
    run.visit("stock/NVDA")
    page.wait_for_selector(".ext-line .ext-tag", timeout=30000)
    run.check_page("stock extended line")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/ext_stock.png")
    run.visit("chart/NVDA")
    run.click(page.locator(".segmented button:has-text('15 min')"), "interval 15 min")
    run.settle(200)
    toggle = page.locator(".lab-panel label:has-text('Pre-market and after hours') input")
    if not toggle.count():
        run.problem("intraday chart has no extended-hours toggle")
        return
    if not toggle.is_checked():
        run.click(toggle, "extended hours on")
        run.settle(200)
    sub = page.locator("figure .card-sub")
    if "Gray bands" not in sub.inner_text():
        run.problem("intraday chart with extended hours does not mention the gray bands")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/ext_chart.png")
    run.click(toggle, "extended hours off")
    run.settle(200)
    if "Gray bands" in sub.inner_text():
        run.problem("switching extended hours off kept the gray bands")
    run.click(toggle, "extended hours back on")
    run.settle(200)
    run.click(page.locator(".segmented button:has-text('Daily')"), "back to daily")
    run.settle(200)
    # Daily charts mark the latest price outside regular hours with a dashed line.
    try:
        page.wait_for_function("() => (document.querySelector('figure .card-sub')?.textContent || '').includes('the dashed line')", timeout=20000)
    except Exception:
        run.problem("the daily chart does not mention the pre-market or after-hours price")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/ext_chart_daily.png")
    # The options page shows the move of the index's future next to the spot price.
    run.visit("options/SPX")
    try:
        page.wait_for_selector("text=S&P 500 futures", timeout=30000)
    except Exception:
        run.problem("the SPX options page does not show the S&P 500 futures move")
    # Rotation adds a column for the funds' moves outside regular hours.
    run.visit("rotation")
    try:
        page.wait_for_selector("#content th:has-text('Ext. hours')", timeout=30000)
    except Exception:
        run.problem("rotation has no extended-hours column outside market hours")
    run.ok()


def scenario_prediction(run: Run) -> None:
    """Prediction market odds on the Macro page, the Overview and stock pages."""
    page = run.page
    run.visit("prediction")
    try:
        page.wait_for_selector("#content .odds-row", timeout=60000)
    except Exception:
        run.problem("the Prediction Markets page shows no Fed decision odds")
        return
    rows = page.locator("#content .odds-row")
    total = sum(int(rows.nth(index).locator(".odds-value").inner_text().rstrip("%")) for index in range(rows.count()))
    if not 97 <= total <= 103:
        run.problem(f"Fed decision odds add up to {total}%")
    run.check_page("prediction markets")
    # Every number's outcome chart, through the picker.
    for button in page.locator("[aria-label='Number to show'] button").all():
        if "is-active" not in (button.get_attribute("class") or ""):
            run.click(button, f"odds for {button.inner_text()}")
            run.check_page(f"odds for {button.inner_text()}")
    # The page sweep may have left the filter on either choice; switch to the other one first.
    for label in ("Your watchlist", "All companies"):
        button = page.locator(f"[aria-label='Which companies'] button:has-text('{label}')")
        if "is-active" not in (button.get_attribute("class") or ""):
            run.click(button, f"earnings for {label.lower()}")
    if SHOTS:
        page.locator("#content").screenshot(path=f"{SHOTS}/prediction_page.png")
    body = json.loads(page.evaluate("() => fetch('/api/prediction').then((r) => r.text())"))
    if not body["fed"]:
        run.problem("no Fed odds in the prediction data")
    run.visit("macro")
    if not page.locator("#content a[href='#/prediction']").count():
        run.problem("Macro & Credit does not point to Prediction Markets")
    run.visit("overview")
    catalysts = json.loads(page.evaluate("() => fetch('/api/overview').then((r) => r.text())"))["catalysts"]
    if any(event.get("odds") for event in catalysts):
        more = page.locator("#content section:has-text('Next catalysts') button:has-text('Show all')")
        if more.count():
            run.click(more.first, "show all catalysts")
        if page.locator("#content .odds-note").count() == 0:
            run.problem("Next catalysts show no prediction market odds")


def api_json(page: Page, path: str):
    return json.loads(page.evaluate(f"() => fetch('{path}').then((r) => r.text())"))


def scenario_brief(run: Run) -> None:
    """The Overview's brief, the stock checklist and data-age labels."""
    page = run.page
    run.visit("overview")
    page.wait_for_selector("#brief .brief-line", timeout=60000)
    if page.locator("#brief .brief-line").count() < 2:
        run.problem("the brief has fewer than two lines")
    if not page.locator("#content .card-age").filter(has_text=re.compile("Data to|Stale")).count():
        run.problem("no chart shows how recent its data is")
    if SHOTS:
        page.locator("#brief").screenshot(path=f"{SHOTS}/brief.png")
    run.visit("stock/NVDA")
    page.wait_for_selector("#checklist .check", timeout=60000)
    run.check_page("stock checklist")
    page.wait_for_selector("text=Insider trades", timeout=60000)
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/stock_new.png", full_page=True)


def scenario_portfolio(run: Run) -> None:
    """Add, edit, alert on and remove a position. Leaves your own positions untouched."""
    page = run.page
    before = {row["id"] for row in api_json(page, "/api/portfolio")["rows"]}
    created = []
    try:
        run.visit("portfolio")
        page.locator("#position-symbol").fill("QQQ")
        page.locator("#position-shares").fill("2")
        page.locator("#position-cost").fill("100")
        page.locator("#position-stop").fill("90")
        page.locator("#position-note").fill("browser test position")
        run.click("#position-save", "add a position")
        page.wait_for_selector(".toast:has-text('Added')", timeout=60000)
        run.settle()
        created = [row["id"] for row in api_json(page, "/api/portfolio")["rows"] if row["id"] not in before]
        if len(created) != 1:
            run.problem(f"expected one new position, found {len(created)}")
            return
        position = created[0]
        run.check_page("with a position")
        run.click(page.locator(f"[data-edit='{position}']"), "edit the position")
        page.locator("#position-shares").fill("3")
        run.click("#position-save", "save the edit")
        run.settle()
        row = next(row for row in api_json(page, "/api/portfolio")["rows"] if row["id"] == position)
        if row["shares"] != 3:
            run.problem("editing the position did not save")
        run.click(page.locator(f"tr:has([data-position='{position}']) button:has-text('Stop alert')"), "stop alert")
        page.wait_for_function("() => document.querySelector('#drawer-alert-value')?.value === '90'", timeout=30000)
        run.click("#close-alert-drawer", "close the drawer")
        if SHOTS:
            page.screenshot(path=f"{SHOTS}/portfolio.png", full_page=True)
        page.once("dialog", lambda dialog: dialog.accept())
        run.click(page.locator(f"[data-position='{position}']"), "remove the position")
        run.settle()
    finally:
        for position in [row["id"] for row in api_json(page, "/api/portfolio")["rows"] if row["id"] not in before]:
            page.evaluate(f"() => fetch('/api/positions/{position}', {{ method: 'DELETE' }})")
    if {row["id"] for row in api_json(page, "/api/portfolio")["rows"]} != before:
        run.problem("positions were not restored after the test")


def scenario_journal(run: Run) -> None:
    """Write a note entry, look at its signals, delete it. Leaves your journal untouched."""
    page = run.page
    before = {row["id"] for row in api_json(page, "/api/journal")["rows"]}
    try:
        run.visit("journal/new/SPY")
        if page.locator("#journal-symbol").input_value() != "SPY":
            run.problem("the journal did not start from the stock in the address")
        run.click(page.locator("[aria-label='What you did'] button:has-text('Just a note')"), "a note entry")
        run.click("#journal-save", "save without a reason")
        page.wait_for_selector(".toast.error", timeout=15000)
        page.locator("#journal-reason").fill("Browser test entry: watching SPY at its 50-day.")
        run.click("#journal-save", "save the entry")
        page.wait_for_selector(".toast:has-text('Saved')", timeout=60000)
        run.settle()
        created = [row for row in api_json(page, "/api/journal")["rows"] if row["id"] not in before]
        if len(created) != 1 or not created[0]["snapshot"]:
            run.problem("the entry was not saved with a signal snapshot")
            return
        card = page.locator(f"[data-entry='{created[0]['id']}']")
        run.click(card.locator("button:has-text('What the signals said then')"), "show the signals")
        run.check_page("journal entry")
        if SHOTS:
            page.screenshot(path=f"{SHOTS}/journal.png", full_page=True)
        page.once("dialog", lambda dialog: dialog.accept())
        run.click(card.locator("button:has-text('Delete')"), "delete the entry")
        run.settle()
    finally:
        for entry in [row["id"] for row in api_json(page, "/api/journal")["rows"] if row["id"] not in before]:
            page.evaluate(f"() => fetch('/api/journal/{entry}', {{ method: 'DELETE' }})")
    if {row["id"] for row in api_json(page, "/api/journal")["rows"]} != before:
        run.problem("the journal was not restored after the test")


def scenario_lists(run: Run) -> None:
    """Put a stock in a list, filter the watchlist by it, write a note. Restores both."""
    page = run.page
    tag = "Browser test list"
    original = api_json(page, "/api/stock/NVDA")["note"]
    try:
        run.visit("stock/NVDA")
        page.locator("#tag-input").fill(tag)
        run.click(page.locator("form:has(#tag-input) button[type=submit]"), "add to a list")
        page.wait_for_selector(f".chip:has-text('{tag}')", timeout=15000)
        page.locator("#stock-note").fill("Browser test note")
        page.wait_for_selector("text=Saved", timeout=15000)
        run.visit("watchlist")
        page.wait_for_selector(f"[aria-label='Show list'] button:has-text('{tag}')", timeout=30000)
        run.click(page.locator(f"[aria-label='Show list'] button:has-text('{tag}')"), "show the test list")
        rows = page.locator("#content tbody tr").count()
        if rows != 1:
            run.problem(f"the list filter shows {rows} stocks instead of 1")
        if not page.locator("#content .note-mark").count():
            run.problem("the watchlist doesn't mark the stock's note")
        run.click(page.locator("[aria-label='Show list'] button:has-text('All')"), "show all again")
    finally:
        page.evaluate(f"() => fetch('/api/tags/remove', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ symbol: 'NVDA', tag: '{tag}' }}) }})")
        text = (original or {}).get("text", "")
        page.evaluate("(text) => fetch('/api/notes/NVDA', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) })", text)
        page.wait_for_timeout(300)
    after = api_json(page, "/api/stock/NVDA")
    if tag in after["tags"] or (after["note"] or {}).get("text", "") != (original or {}).get("text", ""):
        run.problem("NVDA's lists or note were not restored after the test")


def scenario_calendars(run: Run) -> None:
    """Earnings Season and the Economic Calendar: every filter."""
    page = run.page
    run.visit("earnings")
    page.wait_for_selector("#content table.data, #content .empty", timeout=60000)
    for group, labels in (("Which companies", ("S&P 500", "All over $2B", "Your stocks")), ("Period", ("Next 4 weeks", "Next 7 days", "Next 14 days"))):
        for label in labels:
            button = page.locator(f"[aria-label='{group}'] button:has-text('{label}')")
            if "is-active" not in (button.get_attribute("class") or ""):
                run.click(button, f"{group}: {label}")
                run.check_page(f"earnings {label}")
    run.click(page.locator("[aria-label='Which companies'] button:has-text('All over $2B')"), "all companies")
    page.locator("input[aria-label='Filter companies']").fill("NV")
    page.wait_for_timeout(200)
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/earnings.png", full_page=True)
    page.locator("input[aria-label='Filter companies']").fill("")
    run.click(page.locator("[aria-label='Which companies'] button:has-text('Your stocks')"), "back to your stocks")
    run.visit("calendar")
    page.wait_for_selector("#content .cal-row, #content .empty", timeout=60000)
    for label in ("With option expirations", "Releases only"):
        button = page.locator(f"[aria-label='Show option expirations'] button:has-text('{label}')")
        if "is-active" not in (button.get_attribute("class") or ""):
            run.click(button, label)
    run.check_page("economic calendar")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/calendar.png", full_page=True)


def scenario_timeline(run: Run) -> None:
    page = run.page
    run.visit("timeline")
    page.wait_for_selector("#timeline-date", timeout=60000)
    run.idle()
    for label in ("1 year ago", "3 months ago", "1 week ago"):
        run.click(page.locator(f"[aria-label='Quick picks'] button:has-text('{label}')"), label)
        run.idle()
        run.check_page(f"timeline {label}")
    options = page.locator("#timeline-notable option")
    if options.count() > 1:
        page.locator("#timeline-notable").select_option(index=1)
        run.idle()
        run.check_page("notable date")
    page.locator("#timeline-date").fill("2025-04-08")
    page.locator("#timeline-date").dispatch_event("change")
    run.idle()
    if page.locator("#timeline-date").input_value() != "2025-04-08":
        run.problem("typing a date did not move the timeline")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/timeline.png", full_page=True)


def scenario_settings(run: Run) -> None:
    """Notifications, backup download and a no-op restore of that backup."""
    page = run.page
    page.context.grant_permissions(["notifications"], origin=BASE)
    was_on = page.evaluate("() => localStorage.getItem('ms.notify.enabled')")
    run.visit("settings")
    run.click("#notify-toggle", "turn notifications on")
    page.wait_for_selector("#notify-test:not([disabled])", timeout=15000)
    run.click("#notify-test", "test notification")
    run.click("#notify-toggle", "turn notifications off")
    if was_on is not None:
        page.evaluate("(value) => localStorage.setItem('ms.notify.enabled', value)", was_on)
    with page.expect_download() as download:
        page.locator("#backup-download").click()
    path = download.value.path()
    data = json.loads(Path(path).read_text())
    if data.get("format") != "market-signals-backup":
        run.problem("the backup file has the wrong format")
    before = api_json(page, "/api/backup")
    alerts_before = {alert["id"] for alert in api_json(page, "/api/alerts")["alerts"]}
    positions_before = {row["id"] for row in api_json(page, "/api/portfolio")["rows"]}
    journal_before = {row["id"] for row in api_json(page, "/api/journal")["rows"]}
    try:
        page.locator("#backup-file").set_input_files(path)
        run.click("#backup-restore", "restore the same backup (adds nothing)")
        page.wait_for_selector(".toast:has-text('Restored')", timeout=30000)
        after = api_json(page, "/api/backup")
        for key in ("watchlist", "alerts", "positions", "journal", "tags", "notes"):
            if before[key] != after[key]:
                run.problem(f"restoring the same backup changed {key}")
    finally:
        # Safety net: anything a faulty restore added to your own data is removed again.
        for alert in api_json(page, "/api/alerts")["alerts"]:
            if alert["id"] not in alerts_before:
                page.evaluate(f"() => fetch('/api/alerts/{alert['id']}', {{ method: 'DELETE' }})")
        for row in api_json(page, "/api/portfolio")["rows"]:
            if row["id"] not in positions_before:
                page.evaluate(f"() => fetch('/api/positions/{row['id']}', {{ method: 'DELETE' }})")
        for row in api_json(page, "/api/journal")["rows"]:
            if row["id"] not in journal_before:
                page.evaluate(f"() => fetch('/api/journal/{row['id']}', {{ method: 'DELETE' }})")
    run.check_page("settings")


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
            scenario_shell, scenario_overview, scenario_key_signals, scenario_rotation, scenario_options, scenario_watchlist,
            scenario_ordering, scenario_scanner, scenario_stock, scenario_chart, scenario_backtest, scenario_study,
            scenario_guide, scenario_alerts, scenario_drawer, scenario_extended, scenario_prediction,
            scenario_brief, scenario_portfolio, scenario_journal, scenario_lists, scenario_calendars, scenario_timeline,
            scenario_settings, scenario_sources,
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
