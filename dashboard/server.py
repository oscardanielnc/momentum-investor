"""
Dashboard backend (FastAPI).

Serves the robot's state from the database (equity, rationale, logs) and from Alpaca (account,
positions) as JSON, plus the single-page frontend. All timestamps are returned in Lima time
(UTC-5).

Also computes the manual QQQ/QLD SMA200 signal (/api/strategy), the one strategy that survived
the research (see research/backtest_v11_etf.py and research/backtest_v12_wf_combo.py).

Run:  python dashboard/server.py     -> http://127.0.0.1:8080
"""
import os, sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PORT = int(os.environ.get("INVESTOR_DASHBOARD_PORT", "8080"))

# The dashboard reads the PAPER account unless the environment says otherwise.
os.environ.setdefault("INVESTOR_DRY_RUN", "false")
os.environ.setdefault("INVESTOR_ALPACA_LIVE", "false")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
from db import DB
import execution_alpaca as ex
from allocator import SECTOR, MAX_PER_SECTOR, TRAIL_PCT, TOPN

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

LIMA = ZoneInfo("America/Lima")
HERE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="investor dashboard")


def lima(iso, fmt="%d %b %Y · %H:%M"):
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(LIMA).strftime(fmt)
    except Exception:
        return iso


def _db():
    return DB()  # WAL: safe concurrent reads while the orchestrator writes


@app.get("/api/summary")
def summary():
    d = _db()
    acc = ex.get_account() or {}
    equity = float(acc.get("equity", 0) or 0)
    last_eq = float(acc.get("last_equity", equity) or equity)
    st = d.get_state()
    peak = max(st.get("peak") or equity, equity)   # the DB may lag the live account; keeps dd <= 0
    dd = (equity / peak - 1) if peak else 0.0
    pos = _positions_raw()
    sectors = sorted({SECTOR.get(p["symbol"], "?") for p in pos})
    hb = d.conn.execute("SELECT ts,status FROM heartbeat ORDER BY ts DESC LIMIT 1").fetchone()
    halted = d.get_config("halted", "false") == "true"
    d.close()
    return {
        "mode": ex.mode_str(),
        "robot_status": "halted" if halted else "active",
        "last_heartbeat": lima(hb["ts"]) if hb else None,
        "equity": round(equity, 2), "cash": round(float(acc.get("cash", 0) or 0), 2),
        "peak": round(peak, 2), "drawdown": round(dd, 4), "dd_cap": -0.30,
        "day_change_pct": round((equity / last_eq - 1) * 100, 2) if last_eq else 0.0,
        "day_change_usd": round(equity - last_eq, 2),
        "n_sectors": len(sectors), "sectors": sectors,
        "config": {"topn": TOPN, "trail_pct": TRAIL_PCT, "max_per_sector": MAX_PER_SECTOR},
    }


@app.get("/api/equity")
def equity_series():
    d = _db()
    rows = d.conn.execute("SELECT ts,equity_mtm,peak,drawdown FROM equity_history ORDER BY ts ASC").fetchall()
    d.close()
    return [{"ts": lima(r["ts"], "%d/%m %H:%M"), "equity": round(r["equity_mtm"], 2),
             "peak": round(r["peak"], 2), "dd": round(r["drawdown"], 4)} for r in rows]


def _positions_raw():
    """Live Alpaca positions with P&L and last price."""
    data = ex._req("GET", "/v2/positions")
    if not isinstance(data, list):
        return []
    out = []
    for p in data:
        try:
            mv = float(p["market_value"])
            if abs(mv) < 10:           # ignore dust (test orders, fractional leftovers)
                continue
            out.append({"symbol": p["symbol"], "mv": mv,
                        "pnl_pct": round(float(p["unrealized_plpc"]) * 100, 2),
                        "qty": float(p["qty"]), "price": float(p.get("current_price") or 0)})
        except (KeyError, ValueError, TypeError):
            continue
    return out


@app.get("/api/positions")
def positions():
    pos = _positions_raw()
    total = sum(p["mv"] for p in pos) or 1
    for p in pos:
        p["sector"] = SECTOR.get(p["symbol"], "?")
        p["weight"] = round(p["mv"] / total * 100, 1)
        p["trail_pct"] = TRAIL_PCT
    pos.sort(key=lambda x: -x["mv"])
    return pos


@app.get("/api/rationale")
def rationale():
    d = _db()
    r = d.conn.execute("SELECT ts,summary FROM ai_explanation ORDER BY ts DESC LIMIT 1").fetchone()
    d.close()
    return {"ts": lima(r["ts"]) if r else None, "markdown": r["summary"] if r else "No rebalances yet."}


@app.get("/api/history")
def history(limit: int = 12):
    d = _db()
    rows = d.conn.execute("SELECT ts,summary FROM ai_explanation ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    d.close()
    out = []
    for r in rows:
        first = (r["summary"] or "").splitlines()[0].replace("#", "").replace("*", "").strip()
        out.append({"ts": lima(r["ts"]), "title": first[:90]})
    return out


@app.get("/api/health")
def health():
    d = _db()
    hb = d.conn.execute("SELECT ts,cycle_type,status,skip_reason FROM heartbeat ORDER BY ts DESC LIMIT 1").fetchone()
    errors = d.open_errors()
    reviews = d.pending_reviews()
    d.close()
    return {
        "heartbeat": {"ts": lima(hb["ts"]), "status": hb["status"], "cycle": hb["cycle_type"]} if hb else None,
        "open_errors": len(errors),
        "reviews": [{"ts": lima(r["ts"], "%d/%m %H:%M"), "kind": r["kind"], "detail": r["detail"],
                     "severity": r["severity"]} for r in reviews[:8]],
    }


# ── Manual QQQ/QLD strategy: 80% QQQ + 20% QLD while QQQ is above its SMA200 (1% band) ─────────
# Validated in research/backtest_v11_etf.py and backtest_v12_wf_combo.py. The signal uses the
# QQQ close and is executed at the next day's open. Risk on = 80/20, risk off = 100% cash.
SMA_N, BAND = 200, 0.01
W_QQQ, W_QLD = 0.80, 0.20
DRIFT_LO, DRIFT_HI = 0.15, 0.25          # rebalance only if QLD is <15% or >25% of the invested amount
_strat_cache = {"t": 0.0, "data": None}


def _daily_closes(sym, days=720):
    """Split- and dividend-adjusted daily closes from Alpaca: [(iso_date, close), ...]."""
    import requests
    from datetime import timedelta
    start = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    rows, tok = [], None
    while True:
        p = {"symbols": sym, "timeframe": "1Day", "start": start, "limit": 10000,
             "adjustment": "all", "feed": "iex"}
        if tok:
            p["page_token"] = tok
        r = requests.get("https://data.alpaca.markets/v2/stocks/bars", params=p,
                         headers=ex._hdr(), timeout=20)
        if r.status_code != 200:
            return []
        j = r.json()
        rows.extend((j.get("bars") or {}).get(sym, []))
        tok = j.get("next_page_token")
        if not tok:
            break
    return [(b["t"][:10], float(b["c"])) for b in rows]


@app.get("/api/strategy")
def strategy():
    import time as _t
    if _strat_cache["data"] and _t.time() - _strat_cache["t"] < 900:
        return _strat_cache["data"]
    qqq = _daily_closes("QQQ")
    if len(qqq) < SMA_N + 5:
        return JSONResponse({"error": "not enough QQQ data"}, status_code=503)
    # While the market is open the last daily bar is partial, so the signal uses the prior close.
    if ex.market_open():
        qqq = qqq[:-1]
    dates = [d for d, _ in qqq]
    closes = [c for _, c in qqq]
    sma, run = [None] * len(closes), 0.0
    for i, c in enumerate(closes):
        run += c
        if i >= SMA_N:
            run -= closes[i - SMA_N]
        if i >= SMA_N - 1:
            sma[i] = run / SMA_N
    state, states, last_flip = True, [], None
    for i, c in enumerate(closes):
        if sma[i]:
            if c > sma[i] * (1 + BAND):
                new = True
            elif c < sma[i] * (1 - BAND):
                new = False
            else:
                new = state
            if states and new != state:
                last_flip = dates[i]
            state = new
        states.append(state)
    qld = _daily_closes("QLD", days=15)
    flip_today = len(states) >= 2 and states[-1] != states[-2]
    i0 = max(0, len(dates) - 130)
    data = {
        "state": "ON" if state else "OFF",
        "target": ({"QQQ": W_QQQ, "QLD": W_QLD} if state else {"cash": 1.0}),
        "signal_date": dates[-1],
        "qqq_close": round(closes[-1], 2),
        "sma": round(sma[-1], 2),
        "dist_pct": round((closes[-1] / sma[-1] - 1) * 100, 2),
        "level_off": round(sma[-1] * (1 - BAND), 2),
        "level_on": round(sma[-1] * (1 + BAND), 2),
        "flip_pending": flip_today,           # crossed on the last close: act at the next open
        "last_flip": last_flip,
        "px": {"QQQ": round(closes[-1], 2), "QLD": round(qld[-1][1], 2) if qld else None},
        "drift": {"lo": DRIFT_LO, "hi": DRIFT_HI},
        "params": {"sma_n": SMA_N, "band_pct": BAND * 100, "w_qqq": W_QQQ, "w_qld": W_QLD},
        "chart": {"dates": dates[i0:], "close": [round(c, 2) for c in closes[i0:]],
                  "sma": [round(s, 2) if s else None for s in sma[i0:]],
                  "on": [round(s * (1 + BAND), 2) if s else None for s in sma[i0:]],
                  "off": [round(s * (1 - BAND), 2) if s else None for s in sma[i0:]]},
        "updated": lima(datetime.now(timezone.utc).isoformat()),
    }
    _strat_cache.update(t=_t.time(), data=data)
    return data


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "index.html"), headers={"Cache-Control": "no-store"})


if __name__ == "__main__":
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80))
        lan = s.getsockname()[0]; s.close()
    except Exception:
        lan = "<your-ip>"
    print("=" * 56)
    print("  investor dashboard  ·  Ctrl+C to stop")
    print(f"  Local:    http://127.0.0.1:{PORT}")
    print(f"  LAN/VM:   http://{lan}:{PORT}")
    print("=" * 56)
    # 0.0.0.0 makes the dashboard reachable from the LAN or the VM's public IP. It has no
    # authentication: restrict the port with a firewall.
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
