"""
Smoke test de engine/execution_alpaca.py.
  DRY_RUN  : lógica sin red (modo, fallback, construcción de órdenes).
  PAPER RO : lectura real de la cuenta paper (equity MTM, posiciones, reloj).
  PAPER WR : UNA orden real en paper ($3 SMH, dinero ficticio) → prueba el camino de escritura.
Uso: python tests/smoke_alpaca.py
"""
import importlib, os, sys, time
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

_p=_f=0
def chk(n,c,d=""):
    global _p,_f
    print(("✅ PASS" if c else "❌ FAIL")+f"  {n}"+(f"   · {d}" if d else ""));
    _p+= 1 if c else 0; _f+= 0 if c else 1

# ── DRY_RUN ──
os.environ["INVESTOR_DRY_RUN"]="true"; os.environ["INVESTOR_ALPACA_LIVE"]="false"
ex=importlib.import_module("execution_alpaca"); importlib.reload(ex)
print("="*68); print(f"SMOKE execution_alpaca · modo={ex.mode_str()} · base={ex._base()}"); print("="*68)
print("\n[DRY_RUN]")
chk("modo DRY_RUN", ex.mode_str()=="DRY_RUN")
chk("base = paper", "paper" in ex._base())
chk("get_equity = fallback", ex.get_equity()==ex.CAPITAL_FALLBACK, f"${ex.CAPITAL_FALLBACK:.0f}")
o=ex.submit_notional("SMH","buy",10.0)
chk("orden notional DRY bien formada", o.get("dry_run") and o["notional"]==10.0 and o["type"]=="market")
n=ex.rebalance({"SMH":0.5,"GLD":0.3}, equity=1000)
chk("rebalance DRY corre", isinstance(n,int))

# ── PAPER read-only ──
os.environ["INVESTOR_DRY_RUN"]="false"; os.environ["INVESTOR_ALPACA_LIVE"]="false"
importlib.reload(ex)
print(f"\n[PAPER read-only · modo={ex.mode_str()}]")
acc=ex.get_account()
chk("cuenta paper legible", acc is not None and "equity" in (acc or {}), f"equity=${acc.get('equity') if acc else '—'}")
eq=ex.get_equity()
chk("get_equity MTM > 0", eq and eq>0, f"${eq:,.0f}" if eq else "None")
pos=ex.get_positions()
chk("get_positions devuelve dict", isinstance(pos,dict), f"{len(pos)} posición(es)")
chk("market_open() devuelve bool", isinstance(ex.market_open(),bool), f"abierto={ex.market_open()}")

# ── PAPER write (orden real en paper) ──
print(f"\n[PAPER write · orden real $3 SMH (dinero ficticio)]")
if ex.market_open():
    r=ex.submit_notional("SMH","buy",3.0)
    ok = isinstance(r,dict) and r.get("id") and r.get("status")
    chk("orden paper aceptada", ok, f"id={str(r.get('id'))[:8]}… status={r.get('status')}" if isinstance(r,dict) else str(r))
    if ok:
        time.sleep(2)
        oo=ex._req("GET", f"/v2/orders/{r['id']}")
        chk("orden consultable por id", isinstance(oo,dict) and oo.get("symbol")=="SMH",
            f"status={oo.get('status')} filled_qty={oo.get('filled_qty')}" if isinstance(oo,dict) else "—")
else:
    print("⏭️  SKIP  mercado cerrado → no se prueba escritura en vivo")

print("\n"+"="*68); print(f"RESUMEN: {_p} PASS · {_f} FAIL"); print("="*68)
sys.exit(1 if _f else 0)
