# investor

A multi-sector momentum robot for US stocks, running on an Alpaca paper account, plus the
backtesting lab that was used to validate it and later to take it apart. The original backtest
showed about 36% a year on a hand-picked list of 36 market leaders. Re-running the same rules on
the stocks that were actually in the S&P 500 on each date, including the ones that later
collapsed or were acquired, cut the result to 14.4% a year, below simply holding QQQ (18.8%).
The edge came from choosing the stock list with hindsight, not from the strategy.

> This is a personal, educational project. It is not financial advice. The robot trades a
> paper account with simulated money and has never traded real money. Backtest results do not
> predict future returns.

## The finding

Same rules, same engine, same data and dates (2018-10-01 to 2026-06-26, price return, 10 bps
one-way costs); only the universe changes. Source: `research/backtest_v10_pit.py`, sections A-C.

| Universe | CAGR | Max drawdown | Sharpe | Calmar |
|---|---:|---:|---:|---:|
| 36 leaders chosen in 2026 (the robot's list) | 29.2% | -29.7% | 1.04 | 0.98 |
| S&P 500 point-in-time (678 historical members) | 14.4% | -35.2% | 0.68 | 0.41 |
| QQQ buy and hold | 18.8% | -35.6% | 0.83 | 0.53 |

Choosing the list in 2026 is worth 14.9 points of CAGR. On the honest universe the strategy
loses to QQQ on return, drawdown and Calmar, and it also trails QQQ out of sample (2023-26:
27.7% against 32.6%).

The 36% figure came from earlier, simpler backtests on the same 36 names (close-only stops,
same-day execution): 36.5% CAGR in `backtest_v5_multisector.py` and 37.4% for the robot's
exact configuration in `backtest_v9_hysteresis.py`. The v10 engine replays the robot's real
mechanics (intraday trailing stop, next-open execution, stop reset on rebalance) and brings
the same 36 names down to 29.2%. The universe does the rest.

[docs/METHODOLOGY.md](docs/METHODOLOGY.md) explains how the point-in-time universe was built,
the rest of the v10 results (costs, crises, parameter grid, walk-forward) and the limits of
the audit, including known errors in the split-adjustment heuristic.

## What else was tested and rejected

Rules fixed before looking at results, compared with QQQ over the same window.

| Test | Script | Window | Result | QQQ |
|---|---|---|---|---|
| Multi-sector momentum, point-in-time universe | `backtest_v10_pit.py` | 2018-10 to 2026-06 | 14.4% CAGR, -35.2% DD | 18.8%, -35.6% |
| Sector rotation, 11 SPDR ETFs, 7 variants | `backtest_v11_etf.py` §3 | 2007-06 to 2026-07 | best 10.3% CAGR; best Calmar 0.28 | 16.3%, Calmar 0.31 |
| Country/region rotation, 14 ETFs, 4 variants | `backtest_v11_etf.py` §4 | 2007-06 to 2026-07 | best 7.5% CAGR, -47.0% DD | 16.3%, -53.4% |
| "Beaten-down quality" value screen, SEC EDGAR fundamentals, point-in-time | `backtest_v13_value.py` | 2018-10 to 2026-06 | -0.6% CAGR, -62.4% DD | 18.8%, -35.6% |

Every sector and country variant trails QQQ on both CAGR and Calmar in that window. The v11
rotations spend their first year in cash because the 12-month signal is computed only from
data inside the window. From June 2007 to June 2008 QQQ returned -4.4% and cash +3.6%, so that
year helps the rotations rather than QQQ.
v11 and v12 use total-return data (dividends included); v10 and v13 use price return for the
strategy and the benchmark alike.

In the value test, the quality filter alone returned 9.9%, while requiring a 35% drop from the
52-week high produced the loss: the screen kept buying falling stocks. Fundamentals were
available for 560 of the 678 tickers; the gaps include delisted companies with no current
ticker mapping, which can only bias the test in favor of the screen.

## What survives

A regime filter on the Nasdaq-100, with a small leveraged satellite:

- While QQQ closes more than 1% above its 200-day moving average: 80% QQQ + 20% QLD (2x QQQ).
- While it closes more than 1% below: 100% cash (T-bills). Between the two levels, no change.
- The signal is read at the close and traded at the next open.

Validation, `research/backtest_v12_wf_combo.py` (total return, 10 bps one-way):

| Test | CAGR | Max drawdown | Calmar | QQQ over the same period |
|---|---:|---:|---:|---|
| Anchored walk-forward, 4 folds, stitched out-of-sample 2008-2026 | 12.9% | -31.5% | 0.41 | 16.5%, -49.4%, 0.33 |
| Fixed SMA200 / 1% band, 2008-2026 | 14.3% | -30.0% | 0.48 | 16.5%, -49.4%, 0.33 |
| Fixed SMA200 / 1% band, 2006-07 to 2026 | 14.7% | -30.0% | 0.49 | 16.7%, -53.4%, 0.31 |

Walk-forward design: each fold picks the SMA length and band from a 5 x 4 grid (SMA 150-250,
band 0-2%) using only data from 1999 up to the cutoff, then trades the following years without
changes (cutoffs 2007, 2011, 2015, 2019). All 20 grid cells beat QQQ's Calmar and have a
smaller drawdown over 2006-2026, so the result does not depend on one lucky cell.

What it does and does not do:

- It **does not beat QQQ on return**. In every long-window test it earns 2-4 points a year
  less. None of the 20 grid cells beats QQQ's 16.7% CAGR over 2006-2026 (best 16.6%). What it
  buys is a shallower drawdown: about -30% instead of -49% to -53% through 2008.
- The 20% satellite weight was chosen from the full-period map to land near a -30% drawdown.
  That choice is in-sample.
- The walk-forward picked SMA225 in three of four folds; the live rule uses SMA200.
- The rule family was chosen after seeing v11 on the full 1999-2026 history. In v11's own
  test (rules picked with data up to 2015, evaluated on 2016-2026), the pick was the monthly
  SMA200 check, not the daily one with a band. Over 2007-2026, a 12-month absolute-momentum
  filter on QQQ had a better Calmar (0.53) than the daily band (0.34); both overlays start
  that window without a warm-up period.
- The backtest rebalances the 80/20 mix every day. The dashboard only suggests rebalancing when
  QLD drifts outside 15-25% of the invested amount, and that rule was not backtested.
- There are few decisions: 54 signal switches across the four test segments.
- QQQ itself is a hindsight choice: the Nasdaq-100 was one of the best indices of the period.

## What runs today

- **Dashboard** (`dashboard/`, FastAPI + Chart.js, times in Lima time). It shows the QQQ/QLD
  signal with its exact trigger levels, a chart, and an order calculator. The calculator
  suggests orders for amounts typed in the browser and places nothing. Below it, the robot's
  paper account: equity curve, holdings with sector and P&L, drawdown against the cap, the
  explanation of each rebalance, and health checks.
- **Robot** (`engine/`), on an Alpaca **paper** account with simulated money. It has never
  traded real money. The original plan was to move it to real money after a paper trial; the
  audit above ended the project before that happened. It keeps the top 5 of the 36 names by
  90-day risk-adjusted momentum, with native 20% trailing stops, a daily hysteresis band, and a
  circuit breaker that goes to cash at a 25% drawdown. `INVESTOR_DRY_RUN=true` is the default;
  live trading needs an explicit `INVESTOR_ALPACA_LIVE=true`.

[DEPLOY.md](DEPLOY.md) covers the systemd setup that ran both on a Linux VM.

## Architecture

```
engine/          the robot
  allocator.py         signal: universe, risk-adjusted momentum, sector cap (pure function)
  execution_alpaca.py  Alpaca orders, trailing stops, DRY_RUN / PAPER / LIVE modes
  orchestrator.py      heartbeat loop, monthly/daily rebalance, circuit breaker, stop reconciliation
  db.py                SQLite (WAL) persistence: equity, orders, rationale, logs
  ai_explain.py        optional AI-written explanation of each rebalance (falls back to plain text)
db/schema.sql    database schema
dashboard/       FastAPI backend (server.py) + single-page frontend (index.html)
research/        every backtest, in the order it was written
  backtest_deep.py, backtest_v2..v9     survivorship-biased development on the 36 names (Databento)
  pit_fetch.py, pit_universe.py         point-in-time S&P 500 prices and membership
  backtest_v10_pit.py                   the audit
  backtest_v11_etf.py, backtest_v12_wf_combo.py   ETF rotations, regime filter, walk-forward (yfinance)
  backtest_v13_value.py                 value screen with SEC EDGAR fundamentals
  data_pit/                             versioned membership CSVs and sector map (see SOURCES.md)
tests/           smoke tests (plain scripts, no framework)
docs/            METHODOLOGY.md
scripts/, deploy.sh, setup_vm.sh, *.service    Windows launchers and Linux/systemd deployment
```

## Running it locally

The steps below were checked on a fresh clone with Python 3.14 on Windows (other versions untested).

```bash
git clone https://github.com/oscardanielnc/momentum-investor.git
cd momentum-investor
python -m venv .venv
source .venv/bin/activate          # Windows (Git Bash): source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env               # add Alpaca PAPER keys for anything that needs market data
```

Tests (offline; the Alpaca checks that need keys are skipped when no key is set):

```bash
python tests/smoke_allocator.py
python tests/smoke_db.py
python tests/smoke_orchestrator.py
python tests/smoke_alpaca.py
```

Robot and dashboard:

```bash
python engine/orchestrator.py          # one cycle; DRY_RUN by default, sends no orders
python engine/orchestrator.py --loop   # continuous loop (heartbeat every 15 min)
python dashboard/server.py             # http://127.0.0.1:8080
```

Without Alpaca keys, the robot cycle ends with `no_data` and the QQQ/QLD panel shows "Signal
unavailable"; the rest of the dashboard loads. The server listens on all interfaces and has no
authentication, so keep port 8080 behind a firewall.

Research (needs the extra packages; the download scripts need API keys and, for Databento,
paid credit):

```bash
pip install databento pyarrow yfinance
python research/backtest_v11_etf.py    # downloads ETF history from yfinance on first run
python research/backtest_v12_wf_combo.py
python research/db_fetch.py            # DATABENTO_API_KEY: benchmarks + the 36 names
python research/pit_fetch.py           # DATABENTO_API_KEY: every historical S&P 500 member
python research/backtest_v10_pit.py    # add --full for the parameter grid and walk-forward
python research/backtest_v13_value.py  # SEC_USER_AGENT: downloads EDGAR fundamentals
```

The numbers in this README were produced from locally cached data. The data downloads were
not re-run when it was written.

## License and disclaimer

MIT, see [LICENSE](LICENSE). Third-party data in `research/data_pit/` keeps its own license
(see [SOURCES.md](research/data_pit/SOURCES.md)).

This repository is an educational record of a personal experiment. Nothing in it is
investment advice or a recommendation to buy or sell any security. Trading involves risk of
loss, and leveraged ETFs such as QLD can lose value quickly. Backtests are simulations with
known limitations, described above and in the methodology document.
