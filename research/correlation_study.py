"""
Early exploration: correlation study and a DYNAMIC allocation backtest.

Goal: check with real data (not opinions) that the buckets do not fall together, and derive
the weights. Nothing goes live without a backtest.

Design:
  - Universe grouped by driver (5 buckets), daily history from Alpaca (IEX feed, 2016+).
  - Weekly return correlation against the growth bucket (semis/AI, proxy SMH).
  - Monthly backtest of two strategies plus benchmarks:
      STATIC  : base 65% (inverse-volatility diversifiers) + 35% tilt to the growth leader.
      DYNAMIC : the tilt SCALES with the leader's risk-adjusted momentum (0.10-0.60), the base
                is inverse-volatility, and a stepped drawdown BRAKE targets a -30% cap.
      Benchmarks: 100% QQQ, 60/40 (SPY/TLT).
  - Metrics: CAGR, annual vol, Sharpe, maxDD.

Usage: python research/correlation_study.py      (reads Alpaca keys from .env)
"""
import os, sys, time, datetime as dt
import requests
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── Keys from the project .env ────────────────────────────────────────────────
ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
def _load_env(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return out
_env = _load_env(ENV)
KEY, SEC = _env["ALPACA_API_KEY"], _env["ALPACA_SECRET_KEY"]

DATA = "https://data.alpaca.markets/v2/stocks/bars"
HDR = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SEC}

# ── Universe by bucket (driver) ───────────────────────────────────────────────
BUCKETS = {
    "1_Growth":      ["SMH", "XLK", "QQQ"],          # semis/AI/tech, risk-on (the tilt)
    "2_Real/Infl":   ["XLE", "GLD", "DBC"],          # energy/gold/commodities
    "3_Duration":    ["TLT", "SHY"],                 # long bonds / T-bills (cash proxy)
    "4_Defensive":   ["XLV", "XLP", "XLU"],          # health/staples/utilities
    "5_Geography":   ["EFA", "EEM", "EWJ", "INDA", "EWT", "EWY"],  # international/Asia
}
BENCH = ["SPY"]
ALL = sorted({s for v in BUCKETS.values() for s in v} | set(BENCH))
GROWTH = BUCKETS["1_Growth"]
CASH = "SHY"   # where de-risked capital is parked

START = "2016-01-01"   # IEX history starts around 2016
END = dt.date.today().isoformat()


def fetch_bars(symbol):
    """Adjusted daily closes from Alpaca (IEX), with pagination."""
    rows, token = [], None
    while True:
        p = {"symbols": symbol, "timeframe": "1Day", "start": START, "end": END,
             "limit": 10000, "adjustment": "all", "feed": "iex"}
        if token:
            p["page_token"] = token
        r = requests.get(DATA, params=p, headers=HDR, timeout=30)
        if r.status_code != 200:
            print(f"  WARN {symbol}: HTTP {r.status_code} {r.text[:120]}")
            return None
        j = r.json()
        bars = (j.get("bars") or {}).get(symbol, [])
        rows.extend(bars)
        token = j.get("next_page_token")
        if not token:
            break
    if not rows:
        return None
    s = pd.Series({b["t"][:10]: b["c"] for b in rows})
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def metrics(daily_ret):
    """CAGR, annual vol, Sharpe (rf=0), maxDD from daily returns."""
    eq = (1 + daily_ret).cumprod()
    n = len(daily_ret)
    cagr = eq.iloc[-1] ** (252 / n) - 1
    vol = daily_ret.std() * np.sqrt(252)
    sharpe = (daily_ret.mean() * 252) / vol if vol > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return cagr, vol, sharpe, dd


def main():
    print("=" * 74)
    print(f"Alpaca download · {len(ALL)} assets · {START}->{END[:10]}")
    print("=" * 74)
    px = {}
    for s in ALL:
        b = fetch_bars(s)
        if b is not None and len(b) > 50:
            px[s] = b
            print(f"  OK   {s:5} {len(b):5} bars · {b.index[0].date()}->{b.index[-1].date()}")
        else:
            print(f"  FAIL {s:5} not enough data")
        time.sleep(0.1)
    P = pd.DataFrame(px).sort_index().ffill()
    R = P.pct_change().dropna(how="all")

    # ── 1. Correlation (weekly returns) vs growth ──────────────────────────────
    print("\n" + "=" * 74)
    print("CORRELATION (WEEKLY returns) vs the Growth bucket (proxy SMH)")
    print("=" * 74)
    Rw = P.resample("W-FRI").last().pct_change().dropna(how="all")
    if "SMH" in Rw:
        corr = Rw.corr()["SMH"].sort_values()
        print(f"{'asset':6} {'corr_SMH':>9}   bucket")
        sym2b = {s: b for b, v in BUCKETS.items() for s in v}
        for s, c in corr.items():
            tag = "diversifies" if c < 0.4 else ("partial" if c < 0.7 else "same bet")
            print(f"{s:6} {c:>9.2f}   {sym2b.get(s,'bench'):14} {tag}")

    # ── 2. Backtest ────────────────────────────────────────────────────────────
    print("\n" + "=" * 74)
    print("Monthly BACKTEST 2016+ · STATIC vs DYNAMIC vs benchmarks")
    print("=" * 74)
    rebal = P.resample("ME").last().index   # month end
    daily = R.index

    base_assets = [s for b, v in BUCKETS.items() if b != "1_Growth" for s in v if s in P]
    growth_assets = [s for s in GROWTH if s in P]

    def vol_parity(assets, asof, frac):
        """Weights proportional to 1/vol (60-day window) across `assets`, scaled to `frac`."""
        win = R[assets].loc[:asof].tail(60)
        v = win.std()
        v = v[v > 0]
        if v.empty:
            return {}
        iv = 1.0 / v
        w = iv / iv.sum() * frac
        return w.to_dict()

    def growth_leader(asof):
        """Growth leader by 63-day momentum and its risk-adjusted momentum."""
        hist = P[growth_assets].loc[:asof]
        if len(hist) < 95:
            return None, 0.0
        mom = hist.iloc[-1] / hist.iloc[-63] - 1
        leader = mom.idxmax()
        r_ann = mom[leader] * (252 / 63)
        vol = R[leader].loc[:asof].tail(20).std() * np.sqrt(252)
        g = r_ann / vol if vol > 0 else 0.0               # Sharpe-like score of the leader
        return leader, g

    def weights_static(asof):
        w = vol_parity(base_assets, asof, 0.65)
        leader, _ = growth_leader(asof)
        if leader:
            w[leader] = w.get(leader, 0) + 0.35
        return w

    def weights_dynamic(asof, cur_dd):
        leader, g = growth_leader(asof)
        # tilt scales with the leader's risk-adjusted momentum (0.10-0.60)
        tilt = float(np.clip(0.35 + 0.25 * g, 0.10, 0.60))
        # stepped drawdown brake (-30% cap)
        if   cur_dd <= -0.25: thr = 0.10
        elif cur_dd <= -0.18: thr = 0.35
        elif cur_dd <= -0.10: thr = 0.65
        else:                 thr = 1.00
        tilt *= thr
        base_frac = 1.0 - tilt
        w = vol_parity(base_assets, asof, base_frac)
        if leader:
            w[leader] = w.get(leader, 0) + tilt
        # anything unassigned (assets without vol) goes to cash
        assigned = sum(w.values())
        if assigned < 0.999 and CASH in P:
            w[CASH] = w.get(CASH, 0) + (1 - assigned)
        return w

    def run(weight_fn, dynamic=False):
        eq = 1.0
        peak = 1.0
        rets = []
        w = {}
        idx_rebal = set(rebal)
        for i, day in enumerate(daily):
            if i > 0:
                r = sum(w.get(s, 0) * R[s].iloc[i] for s in w if not np.isnan(R[s].iloc[i]))
                eq *= (1 + r)
                rets.append((day, r))
                peak = max(peak, eq)
            if day in idx_rebal:
                cur_dd = eq / peak - 1
                w = weight_fn(day, cur_dd) if dynamic else weight_fn(day)
        return pd.Series(dict(rets))

    strat = {
        "STATIC 65/35": run(weights_static, dynamic=False),
        "DYNAMIC":      run(weights_dynamic, dynamic=True),
    }
    if "QQQ" in R:
        strat["BENCH QQQ"] = R["QQQ"]
    if "SPY" in R and "TLT" in R:
        strat["BENCH 60/40"] = 0.6 * R["SPY"] + 0.4 * R["TLT"]

    print(f"\n{'strategy':16} {'CAGR':>7} {'vol':>7} {'Sharpe':>7} {'maxDD':>8} {'Calmar':>7}")
    print("-" * 60)
    for name, r in strat.items():
        r = r.dropna()
        c, v, sh, dd = metrics(r)
        calmar = c / abs(dd) if dd < 0 else float("nan")
        print(f"{name:16} {c*100:>6.1f}% {v*100:>6.1f}% {sh:>7.2f} {dd*100:>7.1f}% {calmar:>7.2f}")
    print("\nCalmar = CAGR/|maxDD| (return per unit of pain). Higher is better.")


if __name__ == "__main__":
    main()
