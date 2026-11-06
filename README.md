# Market Signals

A local dashboard of the signals that define the US market: trend, breadth, rotation,
volatility, puts and calls, dealer gamma, dark pools, sentiment, credit and macro. Add your
own stocks, layer signals over any chart, and test whether a signal has actually helped.

It is an information tool. Nothing here places trades.

## Run it

You need Python 3.11 or newer. No accounts or API keys are required.

```bash
git clone https://github.com/mauheems/StockSignals-Free.git
cd StockSignals-Free
python3 -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

It opens at <http://localhost:8000>. This repository contains no market data, so the first run
downloads its own copy, which takes a few minutes (older put/call history keeps loading in the
background for about ten more). Panels fill in as the data arrives. After that,
data keeps refreshing while the dashboard runs: about every 15 minutes during market hours for
prices and option chains.

Everything stays on your computer. Your data and watchlist are stored locally in `data/`, the app
only answers on localhost, and it only contacts the public data providers listed below. Nothing
is sent to the author of this project.

| Command | What it does |
|---|---|
| `python main.py` | Start the dashboard (same as `python main.py dashboard`) |
| `python main.py dashboard --port 9000 --no-browser` | Choose a port, and don't open a browser |
| `python main.py refresh` | Download the latest data once, without the dashboard |
| `python main.py backtest --symbol QQQ` | Run one backtest and write a report to `results/` |
| `python main.py info` | Show the watchlist and how fresh each data source is |

## What's in it

| Page | What it answers |
|---|---|
| **Overview** | Market Health score, what changed recently, next catalysts |
| **Trend & Breadth** | Are indices trending, and are most stocks taking part? 50/200-day averages, % of stocks above them, McClellan, new highs and lows |
| **Rotation** | Where is money moving? Rotation map, relative performance, risk-appetite ratios |
| **Volatility** | VIX curve, realized vs implied, VVIX, SKEW, MOVE |
| **Options & Gamma** | Dealer gamma by strike, gamma flip, call and put walls, max pain, put/call, skew, expected move, unusual activity, for any optionable ticker |
| **Flows & Sentiment** | Put/call ratios, DIX, GEX, Fear & Greed, AAII, futures positioning |
| **Macro & Credit** | Yield curve, credit spreads, financial conditions, liquidity, inflation, jobs |
| **Watchlist** | Your stocks with every signal side by side |
| **S&P 500 Scanner** | Rank and filter all members to find names |
| **Chart Lab** | Candles, moving averages, indicators, and any signal layered over price |
| **Backtests** | Leaderboard of strategies against Buy & Hold, including your own rules |
| **Signal Study** | What did the market do after a signal fired, compared with any other day? |
| **Metric Guide** | Every number explained. Each info button on screen opens the same text |
| **Data Sources** | Status and freshness of each source, with refresh buttons |

## Data

No accounts or API keys are needed. Everything comes from free public files:

| Provider | Used for | Delay |
|---|---|---|
| Yahoo Finance (`yfinance`) | Prices, indices, futures, company data | About 15 minutes for US stocks |
| Cboe | Option chains with greeks, daily put/call ratios | About 15 minutes; ratios end of day |
| SqueezeMetrics | DIX and market GEX history | Next morning |
| FRED, US Treasury | Rates, credit, liquidity, economy | Daily to monthly |
| CNN, AAII | Sentiment | Intraday, weekly |
| CFTC, FINRA | Futures positioning, off-exchange short volume | Weekly, daily |
| Federal Reserve, FRED | Economic calendar | Checked daily |
| Wikipedia | S&P 500 membership | When it changes |

These endpoints come with no guarantee and can change without notice. If one fails, its panels
keep the last good data, the Data Sources page shows the error, and everything else carries on.
Data is stored in `data/market.db` (about 100 MB), which is safe to delete: it rebuilds itself.
Only the watchlist and history that free sources don't offer (daily gamma snapshots, Fear & Greed
beyond one year) would be lost.

### Things to know when reading the numbers

- **Gamma figures are estimates.** Public data shows how many contracts exist, not who holds
  them. Following the original SqueezeMetrics method, dealers are assumed to own the calls and be
  short the puts. This app's figures are per 1% move. SqueezeMetrics' GEX is per 1 index point, so
  it looks about 70 times smaller for SPX, and the two can even differ in sign.
- **Breadth uses today's S&P 500 members**, so older readings leave out companies that later
  dropped from the index.
- **Market Health and its thresholds are rules of thumb** chosen for this dashboard, not official
  levels. Use Signal Study to test them. Good past results for rules picked with hindsight will
  usually look better than they would have live.
- **Backtests** include dividends and costs, and trade at the next day's open. Data with a
  publishing delay is lagged, so a rule only sees what was known at the time. Cash earns no
  interest. Economic data can also be revised later, which can't be undone here.

## Project layout

```
main.py                 Command line
src/data/               Data sources, local store and refresh schedule
src/analytics/          Signal registry and the calculations behind it
src/backtest/, src/strategies/, src/risk/, src/visualization/
                        Backtest engine, strategies, position sizing and report charts
src/dashboard/          Web app: API, page data, Metric Guide and front end
tests/                  Unit and API tests (`pytest`)
tests/e2e/              Clicks through every page in a browser (see the file for usage)
```

To add a signal, declare it once in `src/analytics/signals.py` and explain it in
`src/dashboard/metric_guide.py`. It then works in chart overlays, backtest rules and signal
studies. A test fails if a signal has no explanation.

This software is for research and education, and is not investment advice.
