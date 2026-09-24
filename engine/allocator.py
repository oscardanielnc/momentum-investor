"""
Allocator: the signal of the multi-sector momentum robot.

Configuration (chosen by the survivorship-biased research in research/backtest_v5..v9; see
research/backtest_v10_pit.py for the point-in-time audit that later refuted it):
  - Universe: 36 hand-picked large caps across 7 sectors.
  - Hold the top 5 by risk-adjusted momentum (90-day lookback), equal weight, always invested.
  - At most MAX_PER_SECTOR names per sector.
  - A 20% trailing stop per position, placed by the orchestrator as a native Alpaca order.

compute_target() is pure. load_prices() fetches daily bars from Alpaca.
"""
from __future__ import annotations
import os, sys, time
import numpy as np, pandas as pd

# Same universe as the research backtests.
SEMIS    = ["MU","INTC","NVDA","AMD","WDC","STX","MRVL","TXN","AVGO","AMAT","LRCX","QCOM","ADI"]
SOFTWARE = ["MSFT","ORCL","CRM","NOW","ADBE"]
ENERGY   = ["XOM","CVX","COP","SLB"]
HEALTH   = ["LLY","UNH","JNJ","ABBV"]
FINANCE  = ["JPM","GS","V","MA"]
CONSUMER = ["AMZN","TSLA","COST","HD"]
COMM     = ["GOOGL","NFLX"]
SECTOR = {**{s:"semis" for s in SEMIS}, **{s:"software" for s in SOFTWARE},
          **{s:"energy" for s in ENERGY}, **{s:"health" for s in HEALTH},
          **{s:"financials" for s in FINANCE}, **{s:"consumer" for s in CONSUMER},
          **{s:"comm" for s in COMM}}
UNIVERSE = SEMIS + SOFTWARE + ENERGY + HEALTH + FINANCE + CONSUMER + COMM

LB = 90              # momentum lookback (days); results were similar for 63-90
TOPN = 5             # number of holdings, equal weight
MAX_PER_SECTOR = 4   # at most 4 of 5 (80%) in one sector; avoids a fully concentrated book
TRAIL_PCT = 20.0     # per-position trailing stop (%)
VOLSHORT = 20        # volatility window for the risk adjustment


def _riskadj_mom(P, R, sym, asof):
    """Annualized LB-day momentum divided by annualized volatility (a Sharpe-like score)."""
    h = P[sym].loc[:asof]
    if len(h) < LB + 5:
        return -9.0
    m = h.iloc[-1] / h.iloc[-LB] - 1
    vol = R[sym].loc[:asof].tail(VOLSHORT).std() * np.sqrt(252)
    return (m * 252 / LB) / vol if vol > 0 else -9.0


def compute_target(prices: pd.DataFrame):
    """Select the top TOPN symbols by risk-adjusted momentum, subject to the sector cap.

    Pure function: `prices` is a date x symbol frame of adjusted closes. Returns
    (target_weights, meta), where target_weights is equal weight across the selection and meta
    carries the full ranking and scores used by rationale() and the orchestrator's hysteresis.
    """
    P = prices.sort_index()
    R = P.pct_change()
    asof = P.index[-1]
    univ = [s for s in UNIVERSE if s in P.columns]
    scores = {s: _riskadj_mom(P, R, s, asof) for s in univ}
    ret3m = {s: float(P[s].iloc[-1] / P[s].iloc[-63] - 1) if len(P[s]) > 63 else 0.0 for s in univ}
    ranked = sorted(univ, key=lambda s: scores[s], reverse=True)
    top, sec_count = [], {}
    for s in ranked:
        sec = SECTOR.get(s, "?")
        if sec_count.get(sec, 0) < MAX_PER_SECTOR:
            top.append(s); sec_count[sec] = sec_count.get(sec, 0) + 1
        if len(top) == TOPN:
            break
    w = {s: round(1.0 / len(top), 4) for s in top} if top else {}
    sectors = sorted(set(SECTOR.get(s, "?") for s in top))
    meta = {
        "asof": str(asof.date()), "leaders": top, "sectors": sectors, "n_sectors": len(sectors),
        "scores": {s: round(scores[s], 2) for s in univ},
        "ret3m": {s: round(ret3m[s], 3) for s in univ},
        "ranking": ranked, "next_best": next((s for s in ranked if s not in top), None),
        "trail_pct": TRAIL_PCT, "max_per_sector": MAX_PER_SECTOR,
    }
    return w, meta


def rationale(target: dict, meta: dict, prev: dict | None = None):
    """Explain a rebalance: returns (markdown, struct) for the dashboard and the database."""
    prev = prev or {}
    rows = []
    for s, w in sorted(target.items(), key=lambda x: -x[1]):
        sec = SECTOR.get(s, "?")
        why = (f"momentum #{meta['ranking'].index(s)+1} of {len(meta['ranking'])} · "
               f"score {meta['scores'].get(s)} · +{meta['ret3m'].get(s,0)*100:.0f}% 3m")
        chg = "NEW" if prev.get(s, 0.0) < 0.005 else "kept"
        rows.append({"symbol": s, "sector": sec, "weight": round(w, 4), "why": why, "change": chg})
    removed = [s for s in prev if s not in target and prev.get(s, 0) >= 0.01]

    md = [f"### Rebalance {meta['asof']}",
          f"**Portfolio:** top-{len(target)} momentum · **{meta['n_sectors']} sectors** "
          f"({', '.join(meta['sectors'])}) · trailing stop {meta['trail_pct']:.0f}% · fully invested",
          "", "| Asset | Sector | Weight | Why | Change |", "|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| **{r['symbol']}** | {r['sector']} | {r['weight']*100:.0f}% | {r['why']} | {r['change']} |")
    if meta.get("next_best"):
        nb = meta["next_best"]
        md.append(f"\n*Next in line: {nb} ({SECTOR.get(nb,'?')}, score {meta['scores'].get(nb)}) "
                  f"— enters if it overtakes a holding.*")
    if removed:
        md.append(f"\n*Removed: {', '.join(removed)} (lost momentum or stopped out).*")
    return "\n".join(md), {"context": {k: meta[k] for k in ("asof","leaders","sectors","n_sectors","trail_pct")},
                           "positions": rows, "removed": removed}


def load_prices(lookback_days: int = 320):
    """Split- and dividend-adjusted daily closes from Alpaca for the whole universe."""
    import requests
    from _env import load_env
    load_env()
    def _env(name):
        return os.environ.get(name, "").split("#")[0].strip().strip('"').strip("'")
    HDR = {"APCA-API-KEY-ID": _env("ALPACA_API_KEY"), "APCA-API-SECRET-KEY": _env("ALPACA_SECRET_KEY")}
    start = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=lookback_days)).date().isoformat()
    px = {}
    for s in UNIVERSE:
        rows, tok = [], None
        while True:
            p = {"symbols": s, "timeframe": "1Day", "start": start, "limit": 10000,
                 "adjustment": "all", "feed": "iex"}
            if tok: p["page_token"] = tok
            r = requests.get("https://data.alpaca.markets/v2/stocks/bars", params=p, headers=HDR, timeout=30)
            if r.status_code != 200: break
            j = r.json(); rows.extend((j.get("bars") or {}).get(s, []))
            tok = j.get("next_page_token")
            if not tok: break
        if rows:
            ser = pd.Series({b["t"][:10]: b["c"] for b in rows}); ser.index = pd.to_datetime(ser.index)
            px[s] = ser.sort_index()
        time.sleep(0.02)
    return pd.DataFrame(px).sort_index().ffill()


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    print(f"Loading prices from Alpaca ({len(UNIVERSE)} stocks)...")
    P = load_prices()
    w, meta = compute_target(P)
    md, _ = rationale(w, meta)
    print("\n" + md)
