"""
Smoke test for engine/orchestrator.py: DRY_RUN, temporary database, synthetic prices, offline.
Checks a full cycle (heartbeat, equity/drawdown, monthly -> daily -> heartbeat-only), the
circuit breaker (flatten, halt, review entry, automatic resume) and stop reconciliation.
Usage: python tests/smoke_orchestrator.py
"""
import os, sys, tempfile
from datetime import datetime, timezone
import numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
os.environ["INVESTOR_DRY_RUN"] = "true"      # must be set before importing the engine
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import orchestrator as O
from db import DB, _SCHEMA

_p=_f=0
def chk(n,c,d=""):
    global _p,_f; print(("PASS" if c else "FAIL")+f"  {n}"+(f"   · {d}" if d else "")); _p+=1 if c else 0; _f+=0 if c else 1

# Synthetic prices replace the network loader.
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
print("="*62); print(f"SMOKE orchestrator · mode={O.ex.mode_str()}"); print("="*62)

r1 = O.run_cycle(d, now=NOW)
chk("cycle 1 = monthly rebalance", str(r1).startswith("monthly"), r1)
chk("heartbeat recorded", d.conn.execute("SELECT COUNT(*) c FROM heartbeat").fetchone()["c"]>=1)
st = d.get_state()
chk("MTM equity recorded (fallback $500)", st["equity"]==500.0, f"${st['equity']}")
chk("target weights persisted", d.conn.execute("SELECT COUNT(*) c FROM target_weight").fetchone()["c"]>0)
chk("rationale recorded", d.conn.execute("SELECT COUNT(*) c FROM ai_explanation").fetchone()["c"]>=1)
chk("config last_monthly set", d.get_config("last_monthly")=="2026-06")

r2 = O.run_cycle(d, now=NOW)
chk("cycle 2 = daily", str(r2).startswith("daily"), r2)
chk("config last_daily set", d.get_config("last_daily")=="2026-06-29")

r3 = O.run_cycle(d, now=NOW)
chk("cycle 3 = heartbeat only (no re-trade)", r3=="heartbeat_only", r3)

# Circuit breaker: force a deep drawdown.
d.record_equity(500.0)            # peak 500
d.record_equity(360.0)            # dd -28%, beyond CB_HALT 25%
halted = O.check_circuit_breaker(d, d.get_state())
chk("circuit breaker trips (flatten + halt)", halted is True)
chk("config halted = true", d.get_config("halted")=="true")
chk("review_queue records the halt", any(x["kind"]=="cb_halt" for x in d.pending_reviews()))
d.record_equity(440.0)            # dd -12%, above CB_RESUME 15%
halted2 = O.check_circuit_breaker(d, d.get_state())
chk("circuit breaker resumes on recovery", halted2 is False and d.get_config("halted")=="false")
chk("resume re-anchors the peak (peak_since)", (d.get_config("peak_since") or "") != "")
chk("resume forces re-entry (last_daily reset)", d.get_config("last_daily")=="")

# Automatic resume: while halted, real equity is flat cash, so recovery must be measured on the
# hypothetical equity of the sold basket (cb_ref + latest prices).
import json as _json
d.set_config("halted","true")
d.set_config("cb_ref", _json.dumps({"basket":{"AMD":100.0,"STX":50.0},"equity":360.0,"peak":500.0,"ts":"t"}))
deep = {"equity":360.0, "peak":500.0, "drawdown":-0.28}
_orig_lp = O.ex.latest_prices
O.ex.latest_prices = lambda syms: {"AMD":100.0,"STX":50.0}    # no recovery: stays halted
chk("CB stays halted while the basket has not recovered", O.check_circuit_breaker(d, deep) is True)
O.ex.latest_prices = lambda syms: {"AMD":125.0,"STX":62.5}    # +25%: hypothetical 450 = -10% from peak
h4 = O.check_circuit_breaker(d, deep)
chk("CB resumes only once the basket recovers (automatic)", h4 is False and d.get_config("halted")=="false")
O.ex.latest_prices = _orig_lp

# Stop reconciliation: only the missing trailing stop is replaced.
_orig = (O.ex.DRY_RUN, O.ex.get_positions, O.ex.open_trailing_stops, O.ex.place_trailing_stop)
O.ex.DRY_RUN = False
O.ex.get_positions = lambda: {"INTC":{"qty":157.3,"mv":1.0,"avg":1.0},"AMD":{"qty":37.9,"mv":1.0,"avg":1.0}}
O.ex.open_trailing_stops = lambda: {"AMD": 37}
_placed = []
O.ex.place_trailing_stop = lambda s,q,t,coid=None: (_placed.append((s,q)) or {"ok":1})
n_fix = O.reconcile_stops(d)
chk("reconciliation replaces only the missing stop", n_fix==1 and _placed==[("INTC",157)], str(_placed))
chk("repair leaves a review_queue entry", any(x["kind"]=="stop_repaired" for x in d.pending_reviews()))
O.ex.DRY_RUN, O.ex.get_positions, O.ex.open_trailing_stops, O.ex.place_trailing_stop = _orig

d.close()
print("\n"+"="*62); print(f"SUMMARY: {_p} PASS · {_f} FAIL"); print("="*62)
sys.exit(1 if _f else 0)
