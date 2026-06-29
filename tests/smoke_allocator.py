"""
Smoke test del allocator AGRESIVO multi-sector (función PURA compute_target) — sin red, sintético.
Valida: top-5 equiponderado, suma=1, selección por momentum, diversificación de sectores, meta.
Uso: python tests/smoke_allocator.py
"""
import os, sys
import numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
import allocator as A

_p=_f=0
def chk(n,c,d=""):
    global _p,_f; print(("✅ PASS" if c else "❌ FAIL")+f"  {n}"+(f"   · {d}" if d else "")); _p+=1 if c else 0; _f+=0 if c else 1

# panel sintético: 400 días. Le damos drift fuerte a líderes de SECTORES distintos.
np.random.seed(11)
idx = pd.bdate_range("2024-01-01", periods=400)
P = pd.DataFrame(index=idx)
leaders = {"AMD":0.0016, "XOM":0.0015, "LLY":0.0015, "JPM":0.0014, "MSFT":0.0014}  # 5 sectores
for s in A.UNIVERSE:
    drift = leaders.get(s, 0.0002)
    P[s] = 100*np.exp(np.cumsum(np.random.normal(drift, 0.02, len(idx))))

w, meta = A.compute_target(P)
chk("pesos suman ~1", abs(sum(w.values())-1) < 1e-6, f"{sum(w.values()):.4f}")
chk("cartera = top-5", len(w)==5, f"{len(w)} posiciones")
chk("equiponderado 20% c/u", all(abs(v-0.2)<1e-6 for v in w.values()))
chk("líderes del universo", set(meta["leaders"])<=set(A.UNIVERSE), f"{meta['leaders']}")
chk("diversifica varios sectores", meta["n_sectors"]>=3, f"{meta['n_sectors']} sectores: {meta['sectors']}")
chk("trailing stop expuesto = 20%", meta["trail_pct"]==20.0)
chk("hay 'próximo en la fila'", meta["next_best"] is not None, meta["next_best"])

# selección correcta: la mayoría de los de mayor drift deben estar en el top-5 (resto = ruido)
chk("elige a los de mayor momentum", len(set(leaders) & set(meta["leaders"]))>=3,
    f"{sorted(set(leaders)&set(meta['leaders']))} de los 5 con drift")

# rationale genera markdown
md, struct = A.rationale(w, meta)
chk("rationale produce tabla markdown", "| Activo | Sector | Peso |" in md and struct["positions"])
chk("struct tiene 5 posiciones", len(struct["positions"])==5)

print("\n"+"="*56); print(f"RESUMEN: {_p} PASS · {_f} FAIL"); print("="*56)
sys.exit(1 if _f else 0)
