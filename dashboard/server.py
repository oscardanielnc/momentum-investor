"""
investor — Backend del dashboard "Mi Patrimonio" (FastAPI).
Sirve la DB (estado/justificaciones/logs) + Alpaca (cuenta/posiciones) como JSON,
y el frontend pastel. TODAS las fechas se devuelven en HORA DE LIMA (UTC−5).

Run:  python dashboard/server.py     → http://127.0.0.1:8000
"""
import os, sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "index.html"))


if __name__ == "__main__":
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80))
        lan = s.getsockname()[0]; s.close()
    except Exception:
        lan = "<IP-de-tu-PC>"
    print("=" * 56)
    print("  Dashboard 'Mi Patrimonio'  ·  Ctrl+C para parar")
    print(f"  En esta PC:     http://127.0.0.1:8000")
    print(f"  En tu celular:  http://{lan}:8000   (mismo WiFi)")
    print("=" * 56)
    # 0.0.0.0 = accesible desde la red LOCAL (tu celular en el mismo WiFi). NO expuesto a internet.
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
