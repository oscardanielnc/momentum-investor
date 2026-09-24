"""
Databento daily bars (ohlcv-1d, May 2018 onward) with a parquet cache and automatic split
adjustment. Used by the survivorship-biased backtests v1-v9.

Databento history starts on 2018-05-01 for these datasets (verified); 2008 is not reachable.
Venues are tried in order per symbol until data is found: XNAS.ITCH (Nasdaq), ARCX.PILLAR
(NYSE Arca), XNYS.PILLAR (NYSE).

Split adjustment: Databento returns raw, unadjusted prices, so splits show up as artificial
jumps (NVDA 10:1 in June 2024, AVGO 10:1, LRCX 10:1). Any overnight move beyond +/-45% is
matched to the nearest round ratio and the past is rescaled. Every adjustment is printed so it
can be audited. Prices are NOT dividend-adjusted (price return only).

Usage: python research/db_fetch.py      (needs DATABENTO_API_KEY; cached symbols are free)
"""
import os, sys
import databento as db
import numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
from _env import load_env
load_env()
KEY = os.environ.get("DATABENTO_API_KEY", "")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_db")
os.makedirs(CACHE, exist_ok=True)
START, END = "2018-05-01", "2026-06-27"
DATASETS = ["XNAS.ITCH", "ARCX.PILLAR", "XNYS.PILLAR"]

ETFS = ["SMH","SOXX","XLK","QQQ","SPY","XLV","XLP","XLU","XLE","GLD","DBC","TLT","SHY","IEF","EEM","EFA"]
STOCKS = ["MU","INTC","NVDA","AMD","WDC","STX","MRVL","TXN","AVGO","AMAT","LRCX","QCOM","ADI"]
# Leaders from six more sectors, for the multi-sector rotation backtest.
# META is left out: its raw_symbol in Databento was FB until 2022.
STOCKS_MULTI = ["MSFT","ORCL","CRM","NOW","ADBE",          # software
                "XOM","CVX","COP","SLB",                    # energy
                "LLY","UNH","JNJ","ABBV",                   # health
                "JPM","GS","V","MA",                        # financials
                "AMZN","TSLA","COST","HD",                  # consumer
                "GOOGL","NFLX"]                             # communication
UNIVERSE = ETFS + STOCKS + STOCKS_MULTI

_client = None
def client():
    """Lazily created Databento client."""
    global _client
    if _client is None:
        _client = db.Historical(KEY)
    return _client

def _adjust_splits(close, sym):
    """Rescale the past for detected splits (overnight move beyond +/-45%)."""
    ratios = np.array([2,3,4,5,6,7,8,10,20])
    c = close.copy()
    rel = c / c.shift(1)
    factor = pd.Series(1.0, index=c.index)
    for i in range(len(c) - 1, 0, -1):
        r = rel.iloc[i]
        if pd.isna(r): continue
        # forward split: the price drops to ~1/N, so the past is divided by N
        if r < 0.6:
            n = ratios[np.argmin(np.abs(1/ratios - r))]
            if abs(1/n - r) < 0.06:
                factor.iloc[:i] /= n
                print(f"    · {sym}: split {n}:1 on {c.index[i].date()} (ratio {r:.3f}) adjusted")
        elif r > 1.7:  # reverse split (rare)
            n = ratios[np.argmin(np.abs(ratios - r))]
            if abs(n - r) < 0.1:
                factor.iloc[:i] *= n
                print(f"    · {sym}: reverse split 1:{n} on {c.index[i].date()} adjusted")
    return c * factor

def fetch(sym):
    """Adjusted close series for `sym` (from the cache when present), or None."""
    path = os.path.join(CACHE, f"{sym}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)["close"]
    for ds in DATASETS:
        try:
            data = client().timeseries.get_range(
                dataset=ds, symbols=[sym], schema="ohlcv-1d",
                start=START, end=END, stype_in="raw_symbol")
            dfx = data.to_df()
            if dfx is None or len(dfx) == 0:
                continue
            close = dfx["close"].copy()
            close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
            close = close[~close.index.duplicated(keep="last")].sort_index()
            close = _adjust_splits(close, sym)
            close.to_frame("close").to_parquet(path)
            print(f"  OK   {sym:5} {len(close):5} bars · {close.index[0].date()}->{close.index[-1].date()} · {ds}")
            return close
        except Exception as e:
            last = str(e)[:70]
            continue
    print(f"  FAIL {sym:5} no data (last error: {last if 'last' in dir() else '-'})")
    return None

def load_panel():
    """Date x symbol frame of adjusted closes for UNIVERSE (cached)."""
    px = {}
    for s in UNIVERSE:
        c = fetch(s)
        if c is not None and len(c) > 200:
            px[s] = c
    return pd.DataFrame(px).sort_index().ffill()

if __name__ == "__main__":
    print("=" * 78)
    print(f"Databento ohlcv-1d download · {len(UNIVERSE)} symbols · {START}->{END}")
    print("=" * 78)
    P = load_panel()
    print("-" * 78)
    print(f"Panel: {P.shape[1]} symbols · {P.index[0].date()}->{P.index[-1].date()} · {len(P)} days")
    print(f"Missing: {sorted(set(UNIVERSE) - set(P.columns))}")
