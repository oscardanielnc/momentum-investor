# Methodology: survivorship bias and the point-in-time audit

This document explains why the original backtest of the momentum robot was wrong, how the
point-in-time (PIT) universe used to audit it was built, and what the audit can and cannot
tell you. Every number below is printed by a script in `research/`; the script and section
are cited next to it.

## 1. What survivorship bias is

A backtest needs a universe: the list of stocks the strategy is allowed to buy on each date.
If that list is written today, it contains the companies that are large and successful today.
Many of them became large *during* the backtest window, which is exactly the move a momentum
strategy tries to catch. Companies that collapsed, were acquired, or dropped out of the index
along the way are missing, so the strategy can never hold them.

The result is a backtest that buys from a list of known winners. It measures the quality of
the list, which was chosen with hindsight, more than the quality of the strategy.

This project's robot used a universe of 36 large caps across 7 sectors (semis, software,
energy, health, financials, consumer, communication), written in 2026 as "the leaders" of
each sector (`engine/allocator.py`). Every backtest from v1 to v9 used that list.

## 2. How the point-in-time universe was built

The goal: on each date, the strategy may only choose among the stocks that were in the S&P 500
*on that date*, including those that later left the index or stopped trading.

**Membership.** Two CSV files from the MIT-licensed
[fja05680/sp500](https://github.com/fja05680/sp500) dataset (see
`research/data_pit/SOURCES.md`):

- `sp500_hist.csv`: the full list of index members for 2,712 dates from 1996-01-02 to
  2026-06-02. `pit_universe.Membership().asof(d)` returns the most recent list on or before `d`.
- `sp500_start_end.csv`: 1,255 membership windows (ticker, start, end). A ticker can have
  several windows.

**Prices.** `research/pit_fetch.py` requests daily OHLCV from Databento for every ticker that
was a member at any point between 2018-05-01 and 2026-06-27: 682 tickers. Data was returned
for 681 of them (FDXF is missing), and 678 have at least 30 bars inside their membership
windows, which is the panel the backtests load (`PIT panel: 678 tickers` in the output of
`backtest_v10_pit.py`). The panel includes companies that later collapsed or were acquired,
such as SIVB and FRC (2023), TWTR (2022), ATVI (2023), XLNX (2022) and CELG (2019). Databento
history for these datasets starts in May 2018, which is what limits the audit window.

Databento is queried by `raw_symbol`, so a ticker returns data only while that ticker traded.
Renames are handled by the membership file itself, which lists the ticker in force on each
date (for example FB until 2022, META afterwards).

**Masking.** `pit_universe.load_ohlc()` keeps a ticker's prices only inside its membership
windows, plus 200 days before each inclusion so the 90-day momentum lookback has history.
This matters because tickers get reused: after a company is delisted, its ticker can be given
to a different company, whose prices would otherwise leak into the test.

**Decision timing.** On day *t* the engine ranks only the members as of day *t-1*, using
closes up to *t-1*, and trades at the open of day *t*. A stock that leaves the index drops out
of the ranking and is rotated out at the next rebalance. A held stock that stops trading is
sold at its last close if no price appears in the next five sessions.

**Sectors.** The sector cap (at most 4 of 5 holdings per sector) needs a sector for every
ticker. Current constituents use the GICS sector from Wikipedia's list of S&P 500 companies
(`sectors_wiki.json`), with semiconductors split from tech as in the live strategy. Delisted
tickers use a manual mapping in `pit_universe.py`.

## 3. The engine is also more faithful to the robot

`research/backtest_v10_pit.py` does not reuse the v5-v9 simulator. It replays the robot's
rules as the orchestrator executes them:

| Aspect | v5-v9 (close-only) | v10 |
|---|---|---|
| Trailing stop | Checked on daily closes | Intraday: high-water mark from highs, triggered by the low, filled at the open on a gap |
| Execution | Same close as the signal | Signal on yesterday's close, trade at today's open |
| Stops after a rebalance | Kept | Re-placed, so the high-water mark resets, as in the robot |
| Daily rotation | Only in v8/v9 | Rank hysteresis (top 12 band), sector cap, 5% monthly drift band |
| Costs | 10 bps per trade | 10 bps one-way on traded notional (25 bps stress test) |

Both v10 runs below use the same engine, the same dates and the same price-return data, and
change only the universe.

## 4. Why the result changes

Window 2018-10-01 to 2026-06-26, price return (no dividends) for the strategy and for the
benchmarks alike (`backtest_v10_pit.py`, sections A-C):

| Universe | CAGR | Max drawdown | Sharpe | Calmar |
|---|---:|---:|---:|---:|
| 36 leaders chosen in 2026 | 29.2% | -29.7% | 1.04 | 0.98 |
| S&P 500 point-in-time | 14.4% | -35.2% | 0.68 | 0.41 |
| QQQ buy and hold | 18.8% | -35.6% | 0.83 | 0.53 |
| SPY buy and hold | 12.6% | -33.2% | 0.71 | 0.38 |

Same rules, same engine, same dates: choosing the universe in 2026 adds 14.9 percentage
points of CAGR (the script prints `+14.9 pp`). In the honest universe the strategy trails
QQQ on return and Calmar, with a similar drawdown (-35.2% against -35.6%).

The earlier close-only backtests on the 36-name universe reported more:
`backtest_v5_multisector.py` gives 36.5% CAGR / -30.7% / Calmar 1.19 for the monthly top 5
with a 20% stop, and `backtest_v9_hysteresis.py` gives 37.4% / -31.4% / 1.19 for the exact
robot configuration (exit_rank 12). Those runs differ from v10 in both the mechanics
(section 3) and the start date, so the drop from about 37% to 29.2% is not split between the
two causes here. The drop from 29.2% to 14.4% is the universe alone.

Other results from the same script:

- **Out of sample.** From 2023-01 the PIT strategy returns 27.7% CAGR (-24.7% drawdown),
  against 32.6% (-24.0%) for QQQ and 20.6% (-19.8%) for SPY over the same stretch.
- **Costs.** At 25 bps one-way instead of 10, the PIT CAGR falls from 14.4% to 7.3%, with a
  -46.3% drawdown.
- **Hysteresis band.** Without it (exit_rank 5) the PIT result is -3.1% CAGR with about 193
  rebalances a year; exit_rank 10/12/15 give 10.4% / 14.4% / 14.0%.
- **Crisis windows.** COVID (2020-02-19 to 2020-04-30): -19.8% for the PIT strategy against
  -9.1% for QQQ. Bear 2022: -11.0% against -33.6% for QQQ.
- **Parameter grid** (`--full`, saved in `research/data_pit/v10_grid.csv`): 4 of the 36
  combinations of lookback x trailing stop x top-N beat QQQ on both CAGR and Calmar. All four
  sit in one corner (lookback 120, top 3; best 26.0% CAGR / -32.1%), while the other top-3
  cells range down to 3.9%. That is an isolated corner, not a plateau, and finding it after
  looking at the whole grid is exactly the kind of selection the audit is meant to avoid. The
  robot's own cell (lookback 90, 20% stop, top 5) is the 14.4% above.
- **Walk-forward** (`--full`, section E): the combination with the best 2018-22 Calmar
  (lookback 90, 15% stop, top 3; 7.1% CAGR in sample) returns 31.2% over 2023-26 with a
  -35.3% drawdown, against 32.6% / -24.0% for QQQ.

## 5. Limitations of the audit

- **Short window.** Eight years (2018-10 to 2026-06), limited by Databento's history. It
  contains one fast crash (2020) and one bear market (2022), and no long bear market like 2000-02
  or 2008.
- **Price return only.** Databento prices exclude dividends. Strategy and benchmarks are
  treated the same way, but absolute returns are understated by roughly the dividend yield.
- **Split adjustment is a heuristic, and it has errors.** Databento returns unadjusted prices.
  `pit_fetch.py` and `db_fetch.py` treat any overnight move beyond +/-45% that is close to a
  round ratio (2, 3, 4, 5, 6, 7, 8, 10, 20) as a split and rescale the past. The 94 adjustments
  in the PIT data include moves that match known single-day crashes rather than splits (for
  example SIVB on 2023-03-10, FRC on 2023-03-13, and APA, FANG, OXY and TRGP on 2020-03-09),
  and splits whose real ratio is not in the list (TSLA's 5:1 in 2020 was applied as 4:1;
  CMG's 50:1 in 2024 and ORLY's 15:1 in 2025 as 20:1). None of the affected names was held on
  the day of the bad adjustment. Within the following 130 days, the PIT portfolio held GL and
  ORLY and the 36-name portfolio held TSLA, so their momentum scores and some returns in those
  periods are distorted. The effect on the headline numbers is not quantified. Fixing it
  needs a corporate-actions source for splits.
- **Delistings.** A held stock that stops trading is sold at its last close. In the PIT run
  this happened 7 times (CA, APC, CELG, TIF, NLSN, ABMD, ATVI), all of them acquisitions,
  where the last close is a reasonable exit price. For a sudden failure the real recovery would
  be lower, so the rule can favor the strategy; no failed company was held when it stopped
  trading in this run.
- **Sectors.** Current GICS sectors are applied to the whole history, and delisted tickers are
  mapped by hand. This only affects the sector cap.
- **Membership data.** The membership file is a community-maintained reconstruction, not an
  official S&P product.
- **Benchmark choice.** QQQ is the bar because the strategy is growth-oriented. Picking QQQ is
  itself informed by hindsight: the Nasdaq-100 was among the best-performing indices of the
  period.

## 6. Reproducing the audit

```bash
pip install -r requirements.txt databento pyarrow
# DATABENTO_API_KEY in .env (downloads use Databento credit)
python research/db_fetch.py          # SPY/QQQ benchmarks + the 36-name universe (data_db/)
python research/pit_fetch.py         # every historical S&P 500 member (data_pit/*.parquet)
python research/backtest_v10_pit.py  # sections A-F; add --full for the grid and walk-forward
```

The download scripts have fixed end dates (2026-06-27), so a new download targets the same
window. The results above were produced from the cached data; the downloads were not re-run
when this document was written.
