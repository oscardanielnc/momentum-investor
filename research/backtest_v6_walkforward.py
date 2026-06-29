"""
investor — Walk-forward de la config AGRESIVA multi-sector (top-5 + TS20%).
¿Generaliza o es overfit? Elijo parámetros en IN-SAMPLE (2018-2022) y valido en
OUT-OF-SAMPLE (2023-2026). Si el (topN, trail) elegido IS también rinde OOS → no fue suerte.
+ robustez por lookback. + sizing Global66 sobre el maxDD OOS (lo que de verdad enfrentaríamos).
Uso: python research/backtest_v6_walkforward.py
"""
import sys
import numpy as np, pandas as pd
from db_fetch import load_panel
from backtest_v5_multisector import run_multi, metrics
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

IS_END = "2022-12-31"; OOS_START = "2023-01-01"

def split(r):
    return metrics(r.loc[:IS_END]), metrics(r.loc[OOS_START:])

def main():
    print("Cargando panel…"); P=load_panel(); R=P.pct_change()

    print("\n"+"="*82)
    print("WALK-FORWARD · elegir (topN, trailing) en 2018-2022, validar en 2023-2026")
    print("="*82)
    print(f"{'config':18}{'IS Sharpe':>11}{'OOS CAGR':>10}{'OOS Sharpe':>11}{'OOS maxDD':>11}{'OOS Calmar':>11}")
    grid=[(n,t) for n in (3,5,8) for t in (None,0.15,0.20,0.25)]
    rows={}
    for n,t in grid:
        r,_=run_multi(P,R,topn=n,trail=t,lb=90)
        (is_c,is_sh,is_dd),(o_c,o_sh,o_dd)=split(r)
        cal=o_c/abs(o_dd) if o_dd<0 else float('nan')
        rows[(n,t)]=dict(is_sh=is_sh,oc=o_c,osh=o_sh,odd=o_dd,cal=cal)
        lbl=f"top{n} TS{int(t*100) if t else 0}"
        print(f"{lbl:18}{is_sh:>11.2f}{o_c*100:>9.1f}%{o_sh:>11.2f}{o_dd*100:>10.1f}%{cal:>11.2f}")

    best=max(rows, key=lambda k: rows[k]["is_sh"])
    print(f"\nMejor IN-SAMPLE por Sharpe = top{best[0]} TS{int(best[1]*100) if best[1] else 0}")
    b=rows[best]
    print(f"  → OOS: CAGR {b['oc']*100:.1f}% · Sharpe {b['osh']:.2f} · maxDD {b['odd']*100:.1f}% · Calmar {b['cal']:.2f}")
    # ranking OOS por Calmar
    oos_rank=sorted(rows, key=lambda k:-(rows[k]['cal'] if not np.isnan(rows[k]['cal']) else -9))
    pos=oos_rank.index(best)+1
    print(f"  → ese config quedó #{pos} de {len(rows)} en Calmar OOS (1=mejor). Si está arriba → generaliza.")

    print("\n"+"="*82)
    print("ROBUSTEZ por LOOKBACK (top-5, TS20%) — ¿depende de un lookback exacto?")
    print("="*82)
    print(f"{'lookback':>9}{'CAGR':>9}{'Sharpe':>9}{'maxDD':>9}")
    for lb in (63,90,120):
        r,_=run_multi(P,R,topn=5,trail=0.20,lb=lb); c,sh,dd=metrics(r)
        print(f"{lb:>9}{c*100:>8.1f}%{sh:>9.2f}{dd*100:>8.1f}%")

    print("\n"+"="*82)
    print("SIZING GLOBAL66 sobre el maxDD OUT-OF-SAMPLE de top-5 TS20% (realista)")
    print("="*82)
    r,_=run_multi(P,R,topn=5,trail=0.20,lb=90)
    _,(o_c,o_sh,o_dd)=split(r)
    full_c,full_sh,full_dd=metrics(r)
    print(f"top-5 TS20%: full 2018+ maxDD {full_dd*100:.1f}% · OOS 2023+ maxDD {o_dd*100:.1f}%")
    worst=min(full_dd,o_dd)
    for cap in (0.20,0.25,0.27,0.30):
        fa=min(1.0,cap/abs(worst))
        print(f"  tope total {cap*100:.0f}% → {fa*100:.0f}% Alpaca / {(1-fa)*100:.0f}% Global66")
    print(f"\n(usando el peor maxDD visto {worst*100:.1f}% para ser conservadores)")

if __name__=="__main__":
    main()
