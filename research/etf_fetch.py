"""
Download daily OHLC with adjustment=all (splits + dividends) for a pre-specified ETF menu from
the Alpaca Data API (IEX feed, free).

The menu is fixed BEFORE looking at results (anti-hindsight rule): core US indices, all 11
SPDR sectors, the main countries/regions, defensives, factors and leveraged ETFs. It does NOT
pick "the ones that did best".

Note: backtest_v11_etf.py ended up loading its data from yfinance (research/data_etf_long/,
history back to 1998), and no backtest reads this script's cache (research/data_etf/).

Usage: python research/etf_fetch.py      (needs Alpaca keys)
"""
import os, sys, time
import pandas as pd, requests

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
from _env import load_env
load_env()

HERE  = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data_etf")
os.makedirs(CACHE, exist_ok=True)
START = "2015-01-01"

MENU = {
    "core_us":    ["SPY", "QQQ", "DIA", "IWM", "MDY"],
    "sectors11":  ["XLK", "XLY", "XLP", "XLE", "XLF", "XLV", "XLI", "XLB", "XLU", "XLRE", "XLC"],
    "industry":   ["SMH", "IGV", "XBI"],          # satellites with hindsight risk (flagged)
    "countries":  ["EFA", "EEM", "MCHI", "FXI", "KWEB", "ASHR", "EWJ", "EWY", "EWT", "INDA", "EWZ", "ILF"],
    "defensive":  ["TLT", "IEF", "SHY", "GLD", "BIL"],
    "factor":     ["MTUM", "SPMO", "VUG", "IWY", "QUAL", "USMV"],
    "leveraged":  ["QLD", "SSO", "TQQQ"],
}
ALL = sorted({s for v in MENU.values() for s in v})


def _env(name):
    return os.environ.get(name, "").split("#")[0].strip().strip('"').strip("'")


def fetch(sym):
    """Daily OHLCV frame for `sym` (from the cache when present), or None."""
    path = os.path.join(CACHE, f"{sym}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)
    hdr = {"APCA-API-KEY-ID": _env("ALPACA_API_KEY"), "APCA-API-SECRET-KEY": _env("ALPACA_SECRET_KEY")}
    rows, tok = [], None
    while True:
        p = {"symbols": sym, "timeframe": "1Day", "start": START, "limit": 10000,
             "adjustment": "all", "feed": "iex"}
        if tok:
            p["page_token"] = tok
        r = requests.get("https://data.alpaca.markets/v2/stocks/bars", params=p, headers=hdr, timeout=30)
        if r.status_code != 200:
            print(f"  FAIL {sym}: HTTP {r.status_code} {r.text[:80]}")
            return None
        j = r.json()
        rows.extend((j.get("bars") or {}).get(sym, []))
        tok = j.get("next_page_token")
        if not tok:
            break
        time.sleep(0.05)
    if not rows:
        print(f"  FAIL {sym}: no bars")
        return None
    df = pd.DataFrame([{"date": b["t"][:10], "open": b["o"], "high": b["h"],
                        "low": b["l"], "close": b["c"], "volume": b["v"]} for b in rows])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df.to_parquet(path)
    print(f"  OK   {sym:5} {len(df):5} bars · {df.index[0].date()}->{df.index[-1].date()}")
    return df


def load_panel(field="close"):
    px = {}
    for s in ALL:
        df = fetch(s)
        if df is not None and len(df) > 200:
            px[s] = df[field]
    return pd.DataFrame(px).sort_index()


if __name__ == "__main__":
    print(f"Alpaca ETF download (total return) · {len(ALL)} symbols · since {START}")
    P = load_panel()
    print(f"panel: {P.shape[1]} ETFs · {P.index[0].date()}->{P.index[-1].date()}")
    print("missing:", sorted(set(ALL) - set(P.columns)) or "none")
