"""
Smoke test de engine/orchestrator.py — DRY_RUN, DB temporal, precios sintéticos (sin red).
Valida un ciclo completo: heartbeat, equity/drawdown, rebalanceo mensual→diario→solo-heartbeat,
y el circuit breaker (flatten + halt + review) al cruzar el umbral de drawdown.
Uso: python tests/smoke_orchestrator.py
"""
import os, sys, tempfile
from datetime import datetime, timezone
import numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
os.environ["INVESTOR_DRY_RUN"] = "true"      # nada real, antes de importar
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import orchestrator as O
from db import DB, _SCHEMA

_p=_f=0
def chk(n,c,d=""):
    global _p,_f; print(("✅ PASS" if c else "❌ FAIL")+f"  {n}"+(f"   · {d}" if d else "")); _p+=1 if c else 0; _f+=0 if c else 1

# precios sintéticos → monkeypatch del loader (sin red). Universo = 36 acciones multi-sector.
np.random.seed(3)
idx = pd.bdate_range("2024-01-01", periods=400)
P = pd.DataFrame(index=idx)
for s in O.allocator.UNIVERSE:
    drift = 0.0015 if s in ("AMD","STX","MU","XOM","LLY") else 0.0002
    P[s] = 100*np.exp(np.cumsum(np.random.normal(drift, 0.02, len(idx))))
O.allocator.load_prices = lambda lookback_days=320: P

tmp = os.path.join(tempfile.gettempdir(), "investor_orch.db")
for ext in ("","-wal","-shm"):
    try: os.remove(tmp+ext)
    except OSError: pass
d = DB(path=tmp, schema=_SCHEMA)
NOW = datetime(2026,6,29,14,0,tzinfo=timezone.utc)
print("="*62); print(f"SMOKE orchestrator · modo={O.ex.mode_str()}"); print("="*62)

# Ciclo 1 → mensual
r1 = O.run_cycle(d, now=NOW)
chk("ciclo 1 = rebalanceo mensual", str(r1).startswith("mensual"), r1)
chk("heartbeat registrado", d.conn.execute("SELECT COUNT(*) c FROM heartbeat").fetchone()["c"]>=1)
st = d.get_state()
chk("equity MTM registrado (fallback $500)", st["equity"]==500.0, f"${st['equity']}")
chk("pesos objetivo persistidos", d.conn.execute("SELECT COUNT(*) c FROM target_weight").fetchone()["c"]>0)
chk("explicación IA registrada", d.conn.execute("SELECT COUNT(*) c FROM ai_explanation").fetchone()["c"]>=1)
chk("config last_monthly seteada", d.get_config("last_monthly")=="2026-06")

# Ciclo 2 (mismo día) → diario
r2 = O.run_cycle(d, now=NOW)
chk("ciclo 2 = diario", str(r2).startswith("diario"), r2)
chk("config last_daily seteada", d.get_config("last_daily")=="2026-06-29")

# Ciclo 3 (mismo día) → solo heartbeat
r3 = O.run_cycle(d, now=NOW)
chk("ciclo 3 = solo heartbeat (sin re-operar)", r3=="heartbeat_only", r3)

# Circuit breaker: forzar drawdown profundo
d.record_equity(500.0)            # pico 500
d.record_equity(360.0)            # dd -28% → bajo CB_HALT 25%
halted = O.check_circuit_breaker(d, d.get_state())
chk("circuit breaker activa (flatten+halt)", halted is True)
chk("config halted = true", d.get_config("halted")=="true")
chk("review_queue registra el CB", any(x["kind"]=="cb_activado" for x in d.pending_reviews()))
# recuperación
d.record_equity(440.0)            # dd -12% → sobre CB_RESUME 15%
halted2 = O.check_circuit_breaker(d, d.get_state())
chk("circuit breaker reanuda al recuperar", halted2 is False and d.get_config("halted")=="false")

d.close()
print("\n"+"="*62); print(f"RESUMEN: {_p} PASS · {_f} FAIL"); print("="*62)
sys.exit(1 if _f else 0)
