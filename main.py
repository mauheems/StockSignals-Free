"""Market Signals: a local dashboard for US market signals, plus a backtest command line."""

from __future__ import annotations

import argparse
import logging
import threading
import time
import webbrowser
from pathlib import Path

from src.backtest.engine import BacktestEngine
from src.config import load_config
from src.data.market_data import MarketDataProvider
from src.strategies.buy_and_hold import BuyAndHoldStrategy
from src.strategies.momentum import MomentumStrategy
from src.strategies.moving_average import MovingAverageStrategy
from src.visualization.plots import generate_backtest_plots

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def resolve_strategy(cfg: dict):
    strategy_cfg = cfg.get("strategy", {})
    name = strategy_cfg.get("name", "moving_average")

    if name == "moving_average":
        return MovingAverageStrategy(
            fast_window=int(strategy_cfg.get("fast_window", 20)),
            slow_window=int(strategy_cfg.get("slow_window", 50)),
        )
    if name == "momentum":
        return MomentumStrategy(lookback=int(strategy_cfg.get("lookback", 20)))
    if name == "buy_and_hold":
        return BuyAndHoldStrategy()
    raise ValueError(f"Unsupported strategy name: {name}")


def _format_money(value: float) -> str:
    return f"${value:,.2f}"


def _format_percent(value: float) -> str:
    return f"{value * 100.0:.2f}%"


def write_backtest_report(output_path: str | Path, symbol: str, strategy, strategy_result: dict, benchmark_result: dict, start: str, end: str, initial_capital: float) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    slippage_costs = strategy_result["portfolio"].total_slippage
    transaction_costs = strategy_result["portfolio"].total_commissions + slippage_costs
    report = f"""========================================
BACKTEST REPORT
========================================

Symbol: {symbol}
Period: {start} to {end}
Initial capital: {_format_money(initial_capital)}

Strategy:
{strategy.name}

----------------------------------------
RESULTS
----------------------------------------

Strategy return:       {_format_percent(strategy_result['total_return'])}
Buy & hold return:     {_format_percent(benchmark_result['total_return'])}

Strategy CAGR:         {_format_percent(strategy_result['cagr'])}
Buy & hold CAGR:       {_format_percent(benchmark_result['cagr'])}

Strategy volatility:   {_format_percent(strategy_result['volatility'])}
Sharpe ratio:          {strategy_result['sharpe_ratio']:.2f}

Maximum drawdown:      {strategy_result['max_drawdown'] * 100.0:.2f}%
Time invested:         {_format_percent(strategy_result['exposure'])}

Number of trades:      {strategy_result['number_of_trades']}
Win rate:              {strategy_result['win_rate'] * 100.0:.2f}%

Transaction costs:     {_format_money(transaction_costs)}
Slippage costs:        {_format_money(slippage_costs)}

Final portfolio:       {_format_money(strategy_result['final_portfolio_value'])}

========================================
"""
    path.write_text(report, encoding="utf-8")
    return path


def run_backtest_command(args: argparse.Namespace) -> int:
    cfg = load_config()
    market_cfg = cfg.get("market", {})
    backtest_cfg = cfg.get("backtest", {})
    risk_cfg = cfg.get("risk", {})

    symbol = args.symbol or market_cfg.get("symbol", "SPY")
    start = args.start or market_cfg.get("start_date", "2018-01-01")
    end = args.end or market_cfg.get("end_date")
    initial_capital = args.initial_capital or float(market_cfg.get("initial_capital", 10000.0))
    commission_rate = args.commission_rate if args.commission_rate is not None else float(backtest_cfg.get("commission_rate", 0.0005))
    slippage_rate = args.slippage_rate if args.slippage_rate is not None else float(backtest_cfg.get("slippage_rate", 0.0005))
    max_position_fraction = float(risk_cfg.get("max_position_fraction", 1.0))

    logger.info("Loading %s data from %s...", symbol, start)
    provider = MarketDataProvider()
    data = provider.get_daily_data(symbol=symbol, start=start, end=end, adjusted=True)
    logger.info("Loaded %s rows for %s.", len(data), symbol)

    strategy = resolve_strategy(cfg)
    engine = BacktestEngine(
        initial_capital=initial_capital,
        commission_rate=commission_rate,
        slippage_rate=slippage_rate,
    )

    strategy_result = engine.run(data, strategy, max_position_fraction=max_position_fraction)
    benchmark_result = engine.run(data, BuyAndHoldStrategy(), max_position_fraction=max_position_fraction)

    plots = generate_backtest_plots(
        data=data,
        strategy_equity=strategy_result["equity_curve"],
        benchmark_equity=benchmark_result["equity_curve"],
        orders=strategy_result["orders"],
        output_dir="results/plots",
    )

    report_path = write_backtest_report(
        output_path="results/reports/backtest_report.txt",
        symbol=symbol,
        strategy=strategy,
        strategy_result=strategy_result,
        benchmark_result=benchmark_result,
        start=data.index[0].strftime("%Y-%m-%d"),
        end=data.index[-1].strftime("%Y-%m-%d"),
        initial_capital=initial_capital,
    )

    print("\nBacktest complete.")
    print(f"Strategy return:     {_format_percent(strategy_result['total_return'])}")
    print(f"Buy & hold return:   {_format_percent(benchmark_result['total_return'])}")
    print(f"Sharpe:              {strategy_result['sharpe_ratio']:.2f}")
    print(f"Max drawdown:        {strategy_result['max_drawdown'] * 100.0:.2f}%")
    print(f"Trades:              {strategy_result['number_of_trades']}")
    print("\nResults written to:")
    print(f"{report_path}")
    print(f"{plots['equity_curve']}")
    print(f"{plots['drawdown']}")
    print(f"{plots['trades']}")
    return 0


def run_dashboard_command(args: argparse.Namespace) -> int:
    import uvicorn

    from src.dashboard.app import create_app

    port = args.port or int(load_config().get("dashboard", {}).get("port", 8000))
    url = f"http://localhost:{port}"
    print(f"\nMarket Signals is starting at {url}")
    print("Press Ctrl+C to stop. Data keeps refreshing while it runs.\n")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    # The dashboard has no login, so it is only ever available on this computer.
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
    return 0


def run_refresh_command(args: argparse.Namespace) -> int:
    from src.data import refresh
    from src.data.store import Store

    store = Store()
    store.seed_watchlist(refresh.WATCHLIST_DEFAULTS)
    sources = refresh.SOURCES
    if args.source:
        if args.source not in refresh.SOURCE_BY_ID:
            print(f"Unknown source '{args.source}'. Choose from: {', '.join(refresh.SOURCE_BY_ID)}")
            return 2
        sources = [refresh.SOURCE_BY_ID[args.source]]
    failed = 0
    for source in sources:
        started = time.time()
        refresh.run_source(store, source)
        status = store.status_all().get(source.id, {})
        if status.get("last_error"):
            failed += 1
            print(f"FAILED  {source.label}: {status['last_error']}")
        else:
            print(f"ok      {source.label}: {status.get('detail')} ({time.time() - started:.0f}s)")
    return 1 if failed else 0


def run_info_command(args: argparse.Namespace) -> int:
    from src.data import refresh
    from src.data.store import Store

    cfg = load_config()
    market_cfg = cfg.get("market", {})
    store = Store()
    statuses = store.status_all()
    print("Market Signals")
    print(f"Database: {store.path} ({store.file_size() / 1e6:,.0f} MB)")
    print(f"Watchlist: {', '.join(store.watchlist()) or 'empty'}")
    print(f"Backtest defaults: {market_cfg.get('symbol', 'SPY')} from {market_cfg.get('start_date', '2018-01-01')}")
    print("\nData sources:")
    for source in refresh.SOURCES:
        status = statuses.get(source.id, {})
        last = status.get("last_success")
        age = f"{(time.time() - last) / 3600:.1f}h ago" if last else "never"
        print(f"  {source.label:40s} {source.provider:28s} {age}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Market Signals: dashboard and backtests for US markets")
    subparsers = parser.add_subparsers(dest="command", required=False)

    dashboard_parser = subparsers.add_parser("dashboard", help="Start the dashboard (the default)")
    dashboard_parser.add_argument("--port", type=int, default=None)
    dashboard_parser.add_argument("--no-browser", action="store_true", help="Do not open a browser window")
    dashboard_parser.set_defaults(func=run_dashboard_command)

    refresh_parser = subparsers.add_parser("refresh", help="Download the latest data once, without the dashboard")
    refresh_parser.add_argument("--source", default=None, help="Only refresh this source")
    refresh_parser.set_defaults(func=run_refresh_command)

    backtest_parser = subparsers.add_parser("backtest", help="Run one historical backtest from config/config.yaml")
    backtest_parser.add_argument("--symbol", default=None)
    backtest_parser.add_argument("--start", default=None)
    backtest_parser.add_argument("--end", default=None)
    backtest_parser.add_argument("--initial-capital", type=float, default=None)
    backtest_parser.add_argument("--commission-rate", type=float, default=None)
    backtest_parser.add_argument("--slippage-rate", type=float, default=None)
    backtest_parser.set_defaults(func=run_backtest_command)

    info_parser = subparsers.add_parser("info", help="Show the watchlist and how fresh each data source is")
    info_parser.set_defaults(func=run_info_command)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "func"):
        return run_dashboard_command(argparse.Namespace(port=None, no_browser=False))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
