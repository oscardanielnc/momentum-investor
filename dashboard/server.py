"""
investor — Backend del dashboard "Mi Patrimonio" (FastAPI).
Sirve la DB (estado/justificaciones/logs) + Alpaca (cuenta/posiciones) como JSON,
y el frontend pastel. TODAS las fechas se devuelven en HORA DE LIMA (UTC−5).

Run:  python dashboard/server.py     → http://127.0.0.1:8000
"""
import os, sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PORT = int(os.environ.get("INVESTOR_DASHBOARD_PORT", "8080"))   # 8080 por defecto (VM)

# El dashboard lee la cuenta PAPER por defecto (Oscar cambia a real con env).
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
app = FastAPI(title="investor · Mi Patrimonio")


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
    return DB()  # WAL → lectura concurrente segura mientras el orquestador escribe


@app.get("/api/summary")
def summary():
    d = _db()
    acc = ex.get_account() or {}
    equity = float(acc.get("equity", 0) or 0)
    last_eq = float(acc.get("last_equity", equity) or equity)
    st = d.get_state()
    peak = max(st.get("peak") or equity, equity)   # pico vivo (la DB puede estar atrás) → dd ≤ 0
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
    """Posiciones vivas de Alpaca con P&L y sector (raw para reuso interno)."""
    data = ex._req("GET", "/v2/positions")
    if not isinstance(data, list):
        return []
    out = []
    for p in data:
        try:
            mv = float(p["market_value"])
            if abs(mv) < 10:           # ignora polvo (restos de pruebas, dust de fraccionales)
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
    return {"ts": lima(r["ts"]) if r else None, "markdown": r["summary"] if r else "Sin redistribuciones aún."}


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


# ── Estrategia MANUAL de Oscar: 80% QQQ + 20% QLD con filtro SMA200 ± banda 1% ────────────────
# Validada en research/backtest_v11_etf.py y v12 (walk-forward): señal al CIERRE de QQQ →
# se ejecuta al OPEN del día siguiente. RIESGO ON = 80/20 · RIESGO OFF = 100% caja.
SMA_N, BAND = 200, 0.01
W_QQQ, W_QLD = 0.80, 0.20
DRIFT_LO, DRIFT_HI = 0.15, 0.25          # rebalancear solo si QLD pesa <15% o >25% de lo invertido
_strat_cache = {"t": 0.0, "data": None}


def _daily_closes(sym, days=720):
    """Cierres diarios ajustados (split+div) de Alpaca. [(fecha_iso, close), ...]"""
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


@app.get("/api/estrategia")
def estrategia():
    import time as _t
    if _strat_cache["data"] and _t.time() - _strat_cache["t"] < 900:
        return _strat_cache["data"]
    qqq = _daily_closes("QQQ")
    if len(qqq) < SMA_N + 5:
        return JSONResponse({"error": "datos insuficientes de QQQ"}, status_code=503)
    # si el mercado está ABIERTO, la última barra diaria es parcial → la señal usa el cierre previo
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
        "estado": "ON" if state else "OFF",
        "objetivo": ({"QQQ": W_QQQ, "QLD": W_QLD} if state else {"caja": 1.0}),
        "senal_fecha": dates[-1],
        "qqq_close": round(closes[-1], 2),
        "sma": round(sma[-1], 2),
        "dist_pct": round((closes[-1] / sma[-1] - 1) * 100, 2),
        "nivel_off": round(sma[-1] * (1 - BAND), 2),
        "nivel_on": round(sma[-1] * (1 + BAND), 2),
        "flip_pendiente": flip_today,           # cruzó en el último cierre → ejecutar al próximo open
        "ultimo_cambio": last_flip,
        "px": {"QQQ": round(closes[-1], 2), "QLD": round(qld[-1][1], 2) if qld else None},
        "drift": {"lo": DRIFT_LO, "hi": DRIFT_HI},
        "params": {"sma_n": SMA_N, "band_pct": BAND * 100, "w_qqq": W_QQQ, "w_qld": W_QLD},
        "chart": {"dates": dates[i0:], "close": [round(c, 2) for c in closes[i0:]],
                  "sma": [round(s, 2) if s else None for s in sma[i0:]],
                  "on": [round(s * (1 + BAND), 2) if s else None for s in sma[i0:]],
                  "off": [round(s * (1 - BAND), 2) if s else None for s in sma[i0:]]},
        "actualizado": lima(datetime.now(timezone.utc).isoformat()),
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
        lan = "<IP-de-tu-PC>"
    print("=" * 56)
    print("  Dashboard 'Mi Patrimonio'  ·  Ctrl+C para parar")
    print(f"  Local:    http://127.0.0.1:{PORT}")
    print(f"  Red/VM:   http://{lan}:{PORT}")
    print("=" * 56)
    # 0.0.0.0 = accesible desde la red local / la IP pública de la VM. Protege el puerto con firewall.
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
