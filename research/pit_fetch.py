"""
Point-in-time download: daily OHLCV for EVERY historical S&P 500 member (2018-05 to 2026-06),
including companies that were later removed, went bankrupt or were acquired.

This is the base of the honest re-test: the universe on each date is the one an investor
could have known on that date (point-in-time S&P 500 membership from fja05680/sp500), not
"the leaders of 2026".

- Prices: Databento ohlcv-1d (XNAS.ITCH -> XNYS.PILLAR -> ARCX.PILLAR), raw_symbol.
  Ticker changes resolve themselves: membership uses the ticker in force on each date (FB
  until 2022, META afterwards) and Databento raw_symbol only returns data while that ticker
  existed, so the per-ticker panel stitches naturally.
- Full OHLC is stored: backtest v10 simulates the trailing stop with the INTRADAY high/low,
  like Alpaca's native order (the older close-only backtests were not faithful to the robot).
- Split adjustment on the close (same heuristic as db_fetch.py) is applied to O/H/L/C; the
  `factor` column is kept so raw volume and dollar volume can be reconstructed.
- Parquet cache per symbol in research/data_pit/ (never downloaded twice). The original
  download cost about $3.8 of Databento credit.

Usage:  python research/pit_fetch.py      (needs DATABENTO_API_KEY)
"""
import os, sys
import databento as db
import numpy as np, pandas as pd

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
from _env import load_env
load_env()

HERE  = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data_pit")
os.makedirs(CACHE, exist_ok=True)
START, END = "2018-05-01", "2026-06-27"
DATASETS = ["XNAS.ITCH", "XNYS.PILLAR", "ARCX.PILLAR"]


def needed_tickers():
    """Every ticker that was an index member at some point inside [START, END]."""
    se = pd.read_csv(os.path.join(CACHE, "sp500_start_end.csv"))
    se["end_date"] = se["end_date"].fillna("2099-01-01")
    need = se[(se["end_date"] >= START) & (se["start_date"] <= END)]
    return sorted(need["ticker"].unique())


def _adjust_splits_ohlc(df, sym):
    """Detect splits from overnight close jumps (beyond +/-45%, round ratio) and rescale the
    PAST of O/H/L/C. Returns (adjusted_df, n_adjustments); `factor` is added as a column."""
    ratios = np.array([2, 3, 4, 5, 6, 7, 8, 10, 20])
    c = df["close"]
    rel = c / c.shift(1)
    factor = pd.Series(1.0, index=df.index)
    n_adj = 0
    for i in range(len(c) - 1, 0, -1):
        r = rel.iloc[i]
        if pd.isna(r):
            continue
        if r < 0.6:
            n = ratios[np.argmin(np.abs(1 / ratios - r))]
            if abs(1 / n - r) < 0.06:
                factor.iloc[:i] /= n; n_adj += 1
        elif r > 1.7:
            n = ratios[np.argmin(np.abs(ratios - r))]
            if abs(n - r) < 0.1:
                factor.iloc[:i] *= n; n_adj += 1
    out = df.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = df[col] * factor
    out["factor"] = factor
    return out, n_adj


def fetch_all():
    """Download every missing ticker, trying each venue in turn, and cache it as parquet."""
    tickers = needed_tickers()
    have = {f[:-8] for f in os.listdir(CACHE) if f.endswith(".parquet")}
    todo = [t for t in tickers if t not in have]
    print(f"tickers: {len(tickers)} · cached: {len(tickers)-len(todo)} · to download: {len(todo)}")
    if not todo:
        return
    client = db.Historical(os.environ["DATABENTO_API_KEY"])
    pending = list(todo)
    for ds in DATASETS:
        if not pending:
            break
        print(f"-> {ds}: requesting {len(pending)} symbols...")
        try:
            data = client.timeseries.get_range(dataset=ds, symbols=pending, schema="ohlcv-1d",
                                               start=START, end=END, stype_in="raw_symbol")
            dfx = data.to_df()
        except Exception as e:
            print(f"  FAIL {ds}: {str(e)[:140]}")
            continue
        if dfx is None or len(dfx) == 0:
            continue
        got = []
        for sym, g in dfx.groupby("symbol"):
            g = g[["open", "high", "low", "close", "volume"]].copy()
            g.index = pd.to_datetime(g.index).tz_localize(None).normalize()
            g = g[~g.index.duplicated(keep="last")].sort_index()
            if len(g) < 30:            # leftovers (e.g. cross-listings with a few stray days)
                continue
            g, n_adj = _adjust_splits_ohlc(g, sym)
            g.to_parquet(os.path.join(CACHE, f"{sym}.parquet"))
            got.append(sym)
            if n_adj:
                print(f"    · {sym}: {n_adj} split(s) adjusted")
        pending = [t for t in pending if t not in set(got)]
        print(f"  OK {ds}: {len(got)} symbols saved · {len(pending)} still missing")
    if pending:
        print(f"WARNING no data on any venue: {pending}")


if __name__ == "__main__":
    print("=" * 78)
    print(f"POINT-IN-TIME S&P 500 download · ohlcv-1d · {START}->{END}")
    print("=" * 78)
    fetch_all()
    n = len([f for f in os.listdir(CACHE) if f.endswith(".parquet")])
    print("-" * 78)
    print(f"cached parquet files: {n}")
