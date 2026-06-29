"""
Smoke test de engine/db.py — usa una DB temporal (no toca la real). Valida schema, estado
(equity/pico/drawdown para el freno DD), idempotencia de órdenes, logs y colas de revisión.
Uso: python tests/smoke_db.py
"""
import os, sys, tempfile
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import db as DBM

_p=_f=0
def chk(n,c,d=""):
    global _p,_f; print(("✅ PASS" if c else "❌ FAIL")+f"  {n}"+(f"   · {d}" if d else "")); _p+=1 if c else 0; _f+=0 if c else 1

tmp = os.path.join(tempfile.gettempdir(), "investor_smoke.db")
for ext in ("", "-wal", "-shm"):
    try: os.remove(tmp+ext)
    except OSError: pass

d = DBM.DB(path=tmp, schema=DBM._SCHEMA)
print("="*60); print(f"SMOKE db · {tmp}"); print("="*60)

# schema cargó
tabs = {r["name"] for r in d.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
chk("schema creó tablas clave", {"equity_history","order_log","app_log","review_queue","heartbeat"} <= tabs,
    f"{len(tabs)} tablas")

# estado vacío
chk("estado inicial vacío", d.get_state()["equity"] is None)

# equity + pico + drawdown (freno DD)
s1 = d.record_equity(1000.0, cash=200)
chk("equity guarda pico=equity", s1["peak"]==1000.0 and abs(s1["drawdown"])<1e-9)
s2 = d.record_equity(1100.0)         # nuevo pico
chk("pico sube con nuevo máximo", s2["peak"]==1100.0)
s3 = d.record_equity(880.0)          # caída → drawdown -20%
chk("drawdown se calcula vs pico", abs(s3["drawdown"]-(880/1100-1))<1e-9, f"dd={s3['drawdown']*100:.1f}%")
chk("get_state lee último estado", abs(d.get_state()["drawdown"]-s3["drawdown"])<1e-9)

# idempotencia de órdenes
d.record_order("inv-mk-AMD-1", "AMD", "buy", "market", 1.5, "PAPER", rebalance_id="rb1")
d.record_order("inv-mk-AMD-1", "AMD", "buy", "market", 9.9, "PAPER")  # mismo id → ignora
n = d.conn.execute("SELECT COUNT(*) c FROM order_log WHERE client_order_id='inv-mk-AMD-1'").fetchone()["c"]
chk("orden idempotente (no duplica)", n==1)
d.update_order("inv-mk-AMD-1", "FILLED", filled_qty=1.5, avg_fill_price=200.0)
st = d.conn.execute("SELECT status,filled_qty FROM order_log WHERE client_order_id='inv-mk-AMD-1'").fetchone()
chk("update_order actualiza estado", st["status"]=="FILLED" and st["filled_qty"]==1.5)

# pesos objetivo
d.record_target("rb1", {"AMD":0.2,"SHY":0.3}, {"AMD":"líder momentum"})
tw = d.conn.execute("SELECT COUNT(*) c FROM target_weight WHERE rebalance_id='rb1'").fetchone()["c"]
chk("pesos objetivo guardados", tw==2)

# logs + review + heartbeat
d.log("INFO","test","hola", {"x":1}); d.log_error("test","fallo simulado", ValueError("boom"))
d.review("spread_alto","NVDA spread 50bps", symbol="NVDA", severity="warn")
d.heartbeat("heartbeat_15m","ok", equity=880.0)
chk("app_log escribe", d.conn.execute("SELECT COUNT(*) c FROM app_log").fetchone()["c"]>=1)
chk("error_log escribe", len(d.open_errors())>=1)
chk("review_queue escribe", len(d.pending_reviews())>=1)
chk("heartbeat escribe", d.conn.execute("SELECT COUNT(*) c FROM heartbeat").fetchone()["c"]>=1)

# config hot-reload
d.set_config("dry_run","true"); chk("config get/set", d.get_config("dry_run")=="true")

d.close()
print("\n"+"="*60); print(f"RESUMEN: {_p} PASS · {_f} FAIL"); print("="*60)
sys.exit(1 if _f else 0)
