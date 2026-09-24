"""
Smoke test for the allocator's pure compute_target(): offline, synthetic prices.
Checks: top-5 equal weight, weights sum to 1, selection by momentum, sector spread, metadata.
Usage: python tests/smoke_allocator.py
"""
import os, sys
import numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
import allocator as A

_p=_f=0
def chk(n,c,d=""):
    global _p,_f; print(("PASS" if c else "FAIL")+f"  {n}"+(f"   · {d}" if d else "")); _p+=1 if c else 0; _f+=0 if c else 1

# Synthetic 400-day panel with a strong drift on leaders from different sectors.
np.random.seed(11)
idx = pd.bdate_range("2024-01-01", periods=400)
P = pd.DataFrame(index=idx)
leaders = {"AMD":0.0016, "XOM":0.0015, "LLY":0.0015, "JPM":0.0014, "MSFT":0.0014}
for s in A.UNIVERSE:
    drift = leaders.get(s, 0.0002)
    P[s] = 100*np.exp(np.cumsum(np.random.normal(drift, 0.02, len(idx))))

w, meta = A.compute_target(P)
chk("weights sum to ~1", abs(sum(w.values())-1) < 1e-6, f"{sum(w.values()):.4f}")
chk("portfolio = top-5", len(w)==5, f"{len(w)} positions")
chk("equal weight 20% each", all(abs(v-0.2)<1e-6 for v in w.values()))
chk("leaders come from the universe", set(meta["leaders"])<=set(A.UNIVERSE), f"{meta['leaders']}")
chk("spreads across sectors", meta["n_sectors"]>=3, f"{meta['n_sectors']} sectors: {meta['sectors']}")
chk("trailing stop exposed = 20%", meta["trail_pct"]==20.0)
chk("has a 'next in line'", meta["next_best"] is not None, meta["next_best"])

# Most of the high-drift names should make the top 5 (the rest is noise).
chk("picks the highest momentum", len(set(leaders) & set(meta["leaders"]))>=3,
    f"{sorted(set(leaders)&set(meta['leaders']))} of the 5 with drift")

md, struct = A.rationale(w, meta)
chk("rationale renders a markdown table", "| Asset | Sector | Weight |" in md and struct["positions"])
chk("struct has 5 positions", len(struct["positions"])==5)

print("\n"+"="*56); print(f"SUMMARY: {_p} PASS · {_f} FAIL"); print("="*56)
sys.exit(1 if _f else 0)
