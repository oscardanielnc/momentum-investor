"""
Smoke test for engine/db.py on a temporary database (the real one is never touched).
Checks: schema, equity/peak/drawdown state, order idempotency, logs and review queue.
Usage: python tests/smoke_db.py
"""
import os, sys, tempfile
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import db as DBM

_p=_f=0
def chk(n,c,d=""):
    global _p,_f; print(("PASS" if c else "FAIL")+f"  {n}"+(f"   · {d}" if d else "")); _p+=1 if c else 0; _f+=0 if c else 1

tmp = os.path.join(tempfile.gettempdir(), "investor_smoke.db")
for ext in ("", "-wal", "-shm"):
    try: os.remove(tmp+ext)
    except OSError: pass

d = DBM.DB(path=tmp, schema=DBM._SCHEMA)
print("="*60); print(f"SMOKE db · {tmp}"); print("="*60)

tabs = {r["name"] for r in d.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
chk("schema created the key tables", {"equity_history","order_log","app_log","review_queue","heartbeat"} <= tabs,
    f"{len(tabs)} tables")

chk("initial state is empty", d.get_state()["equity"] is None)

s1 = d.record_equity(1000.0, cash=200)
chk("first equity sets peak = equity", s1["peak"]==1000.0 and abs(s1["drawdown"])<1e-9)
s2 = d.record_equity(1100.0)
chk("peak rises with a new high", s2["peak"]==1100.0)
s3 = d.record_equity(880.0)          # -20% drawdown
chk("drawdown is measured from the peak", abs(s3["drawdown"]-(880/1100-1))<1e-9, f"dd={s3['drawdown']*100:.1f}%")
chk("get_state reads the latest state", abs(d.get_state()["drawdown"]-s3["drawdown"])<1e-9)

d.record_order("inv-mk-AMD-1", "AMD", "buy", "market", 1.5, "PAPER", rebalance_id="rb1")
d.record_order("inv-mk-AMD-1", "AMD", "buy", "market", 9.9, "PAPER")  # same id: ignored
n = d.conn.execute("SELECT COUNT(*) c FROM order_log WHERE client_order_id='inv-mk-AMD-1'").fetchone()["c"]
chk("orders are idempotent (no duplicate)", n==1)
d.update_order("inv-mk-AMD-1", "FILLED", filled_qty=1.5, avg_fill_price=200.0)
st = d.conn.execute("SELECT status,filled_qty FROM order_log WHERE client_order_id='inv-mk-AMD-1'").fetchone()
chk("update_order updates status", st["status"]=="FILLED" and st["filled_qty"]==1.5)

d.record_target("rb1", {"AMD":0.2,"SHY":0.3}, {"AMD":"momentum leader"})
tw = d.conn.execute("SELECT COUNT(*) c FROM target_weight WHERE rebalance_id='rb1'").fetchone()["c"]
chk("target weights stored", tw==2)

d.log("INFO","test","hello", {"x":1}); d.log_error("test","simulated failure", ValueError("boom"))
d.review("wide_spread","NVDA spread 50bps", symbol="NVDA", severity="warn")
d.heartbeat("heartbeat","ok", equity=880.0)
chk("app_log writes", d.conn.execute("SELECT COUNT(*) c FROM app_log").fetchone()["c"]>=1)
chk("error_log writes", len(d.open_errors())>=1)
chk("review_queue writes", len(d.pending_reviews())>=1)
chk("heartbeat writes", d.conn.execute("SELECT COUNT(*) c FROM heartbeat").fetchone()["c"]>=1)

d.set_config("dry_run","true"); chk("config get/set", d.get_config("dry_run")=="true")

d.close()
print("\n"+"="*60); print(f"SUMMARY: {_p} PASS · {_f} FAIL"); print("="*60)
sys.exit(1 if _f else 0)
