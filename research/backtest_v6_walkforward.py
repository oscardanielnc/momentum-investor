"""
Backtest v6: in-sample / out-of-sample check of the multi-sector config (top-5 + 20% trailing).

Parameters (topN, trailing stop) are chosen on 2018-2022 and evaluated on 2023-2026. If the
in-sample choice also does well out of sample, it was not luck. Adds a lookback robustness
check and the reserve sizing based on the out-of-sample max drawdown.

Uses the survivorship-biased 36-stock universe (see backtest_v10_pit.py).
Usage: python research/backtest_v6_walkforward.py
"""
import sys
import numpy as np
from db_fetch import load_panel
from backtest_v5_multisector import run_multi, metrics
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

IS_END = "2022-12-31"; OOS_START = "2023-01-01"

def split(r):
    return metrics(r.loc[:IS_END]), metrics(r.loc[OOS_START:])

def main():
    print("Loading panel..."); P=load_panel(); R=P.pct_change()

    print("\n"+"="*82)
    print("WALK-FORWARD · choose (topN, trailing) on 2018-2022, evaluate on 2023-2026")
    print("="*82)
    print(f"{'config':18}{'IS Sharpe':>11}{'OOS CAGR':>10}{'OOS Sharpe':>11}{'OOS maxDD':>11}{'OOS Calmar':>11}")
    grid=[(n,t) for n in (3,5,8) for t in (None,0.15,0.20,0.25)]
    rows={}
    for n,t in grid:
        r,_=run_multi(P,R,topn=n,trail=t,lb=90)
        (_,is_sh,_),(o_c,o_sh,o_dd)=split(r)
        cal=o_c/abs(o_dd) if o_dd<0 else float('nan')
        rows[(n,t)]=dict(is_sh=is_sh,oc=o_c,osh=o_sh,odd=o_dd,cal=cal)
        lbl=f"top{n} TS{int(t*100) if t else 0}"
        print(f"{lbl:18}{is_sh:>11.2f}{o_c*100:>9.1f}%{o_sh:>11.2f}{o_dd*100:>10.1f}%{cal:>11.2f}")

    best=max(rows, key=lambda k: rows[k]["is_sh"])
    print(f"\nBest IN-SAMPLE by Sharpe = top{best[0]} TS{int(best[1]*100) if best[1] else 0}")
    b=rows[best]
    print(f"  -> OOS: CAGR {b['oc']*100:.1f}% · Sharpe {b['osh']:.2f} · maxDD {b['odd']*100:.1f}% · Calmar {b['cal']:.2f}")
    oos_rank=sorted(rows, key=lambda k:-(rows[k]['cal'] if not np.isnan(rows[k]['cal']) else -9))
    pos=oos_rank.index(best)+1
    print(f"  -> that config ranked #{pos} of {len(rows)} by OOS Calmar (1 = best). Near the top means it generalizes.")

    print("\n"+"="*82)
    print("ROBUSTNESS by LOOKBACK (top-5, TS20%): does it depend on one exact lookback?")
    print("="*82)
    print(f"{'lookback':>9}{'CAGR':>9}{'Sharpe':>9}{'maxDD':>9}")
    for lb in (63,90,120):
        r,_=run_multi(P,R,topn=5,trail=0.20,lb=lb); c,sh,dd=metrics(r)
        print(f"{lb:>9}{c*100:>8.1f}%{sh:>9.2f}{dd*100:>8.1f}%")

    print("\n"+"="*82)
    print("RESERVE SIZING on the OUT-OF-SAMPLE max drawdown of top-5 TS20%")
    print("="*82)
    r,_=run_multi(P,R,topn=5,trail=0.20,lb=90)
    _,(o_c,o_sh,o_dd)=split(r)
    _,_,full_dd=metrics(r)
    print(f"top-5 TS20%: full 2018+ maxDD {full_dd*100:.1f}% · OOS 2023+ maxDD {o_dd*100:.1f}%")
    worst=min(full_dd,o_dd)
    for cap in (0.20,0.25,0.27,0.30):
        fa=min(1.0,cap/abs(worst))
        print(f"  total cap {cap*100:.0f}% -> {fa*100:.0f}% strategy / {(1-fa)*100:.0f}% reserve")
    print(f"\n(uses the worst max drawdown seen, {worst*100:.1f}%, to be conservative)")

if __name__=="__main__":
    main()
