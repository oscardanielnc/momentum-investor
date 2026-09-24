"""
Smoke test for engine/execution_alpaca.py.
  DRY_RUN    : offline logic (mode, fallback, order construction).
  PAPER read : reads the paper account (MTM equity, positions, clock). Needs Alpaca paper keys.
  PAPER write: ONE real paper order ($3 of SMH, simulated money) to exercise the write path.
               Opt-in only: set INVESTOR_SMOKE_PAPER_WRITE=1.
The PAPER sections are skipped when ALPACA_API_KEY is not configured.
Usage: python tests/smoke_alpaca.py
"""
import importlib, os, sys, time
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

_p=_f=0
def chk(n,c,d=""):
    global _p,_f
    print(("PASS" if c else "FAIL")+f"  {n}"+(f"   · {d}" if d else ""))
    _p+= 1 if c else 0; _f+= 0 if c else 1

os.environ["INVESTOR_DRY_RUN"]="true"; os.environ["INVESTOR_ALPACA_LIVE"]="false"
ex=importlib.import_module("execution_alpaca"); importlib.reload(ex)
print("="*68); print(f"SMOKE execution_alpaca · mode={ex.mode_str()} · base={ex._base()}"); print("="*68)
print("\n[DRY_RUN]")
chk("mode DRY_RUN", ex.mode_str()=="DRY_RUN")
chk("base = paper", "paper" in ex._base())
chk("get_equity = fallback", ex.get_equity()==ex.CAPITAL_FALLBACK, f"${ex.CAPITAL_FALLBACK:.0f}")
o=ex.submit_notional("SMH","buy",10.0)
chk("DRY notional order well formed", o.get("dry_run") and o["notional"]==10.0 and o["type"]=="market")
n=ex.rebalance({"SMH":0.5,"GLD":0.3}, equity=1000)
chk("DRY rebalance runs", isinstance(n,int))

if not ex.API_KEY:
    print("\nSKIP  PAPER sections: ALPACA_API_KEY is not configured")
    print("\n"+"="*68); print(f"SUMMARY: {_p} PASS · {_f} FAIL"); print("="*68)
    sys.exit(1 if _f else 0)

os.environ["INVESTOR_DRY_RUN"]="false"; os.environ["INVESTOR_ALPACA_LIVE"]="false"
importlib.reload(ex)
print(f"\n[PAPER read-only · mode={ex.mode_str()}]")
acc=ex.get_account()
chk("paper account readable", acc is not None and "equity" in (acc or {}), f"equity=${acc.get('equity') if acc else '-'}")
eq=ex.get_equity()
chk("get_equity MTM > 0", eq and eq>0, f"${eq:,.0f}" if eq else "None")
pos=ex.get_positions()
chk("get_positions returns a dict", isinstance(pos,dict), f"{len(pos)} position(s)")
chk("market_open() returns a bool", isinstance(ex.market_open(),bool), f"open={ex.market_open()}")

print("\n[PAPER write · real paper order, $3 of SMH (simulated money)]")
if os.environ.get("INVESTOR_SMOKE_PAPER_WRITE") != "1":
    print("SKIP  set INVESTOR_SMOKE_PAPER_WRITE=1 to place the paper order")
elif ex.market_open():
    r=ex.submit_notional("SMH","buy",3.0)
    ok = isinstance(r,dict) and r.get("id") and r.get("status")
    chk("paper order accepted", ok, f"id={str(r.get('id'))[:8]}... status={r.get('status')}" if isinstance(r,dict) else str(r))
    if ok:
        time.sleep(2)
        oo=ex._req("GET", f"/v2/orders/{r['id']}")
        chk("order retrievable by id", isinstance(oo,dict) and oo.get("symbol")=="SMH",
            f"status={oo.get('status')} filled_qty={oo.get('filled_qty')}" if isinstance(oo,dict) else "-")
else:
    print("SKIP  market closed, live write path not tested")

print("\n"+"="*68); print(f"SUMMARY: {_p} PASS · {_f} FAIL"); print("="*68)
sys.exit(1 if _f else 0)
