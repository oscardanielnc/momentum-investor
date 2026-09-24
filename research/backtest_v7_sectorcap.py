"""
Backtest v7: per-sector cap. What does forced diversification cost?

Top-5 momentum with at most N positions per sector (2=40%, 3=60%, 4=80%, 5=no cap).
Config: lookback 90, 20% trailing stop, with costs, 2018+. Measures return/risk plus actual
concentration (average weight of the dominant sector, share of rebalances where one sector
holds 80% or more).

Uses the survivorship-biased 36-stock universe (see backtest_v10_pit.py).
Usage: python research/backtest_v7_sectorcap.py
"""
import sys
import numpy as np, pandas as pd
from db_fetch import load_panel
from backtest_v5_multisector import SECTOR, UNIV, metrics, rmom
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

COST = 10/1e4
CRASHES = {"2018-Q4":("2018-09-20","2018-12-26"),"COVID-20":("2020-02-19","2020-03-23"),"Bear-22":("2022-01-03","2022-10-12")}

def select_capped(P, R, day, topn, cap, lb):
    """Top-N by momentum with at most `cap` names per sector."""
    ranked = sorted([s for s in UNIV if s in P], key=lambda s: rmom(P,R,s,day,lb), reverse=True)
    chosen, sc = [], {}
    for s in ranked:
        sec = SECTOR[s]
        if sc.get(sec, 0) < cap:
            chosen.append(s); sc[sec] = sc.get(sec, 0) + 1
        if len(chosen) == topn:
            break
    return chosen

def run(P, R, topn=5, trail=0.20, lb=90, cap=5):
    """Returns (daily returns, avg sectors, avg dominant-sector weight, share of rebalances >= 80% one sector)."""
    rebset=set(P.resample("ME").last().index); days=R.index
    w={}; peaks={}; E=1.0; peak=1.0; rets={}; secs=[]; maxsec=[]
    for i,day in enumerate(days):
        dr=0.0
        def _ret(s,i):
            if s=="CASH": return 0.0
            v=R[s].iloc[i]; return 0.0 if np.isnan(v) else v
        if i>0 and w:
            gr=sum(w[s]*_ret(s,i) for s in w); E*=(1+gr); dr=gr
            nw={s:w[s]*(1+_ret(s,i)) for s in w}; tot=sum(nw.values()); w={s:v/tot for s,v in nw.items()} if tot>0 else w
            if trail:
                for s in list(w):
                    if s=="CASH": continue
                    peaks[s]=max(peaks.get(s,P[s].iloc[i]), P[s].iloc[i])
                    if P[s].iloc[i] <= peaks[s]*(1-trail): w["CASH"]=w.get("CASH",0)+w.pop(s)
        if day in rebset:
            ch=select_capped(P,R,day,topn,cap,lb)
            tw={s:1.0/len(ch) for s in ch}
            cost=sum(abs(tw.get(s,0)-w.get(s,0))*COST for s in set(tw)|set(w)); E*=(1-cost); dr=(1+dr)*(1-cost)-1
            w=tw; peaks={s:P[s].loc[:day].iloc[-1] for s in ch}
            sccount={}
            for s in ch: sccount[SECTOR[s]]=sccount.get(SECTOR[s],0)+1
            secs.append(len(sccount)); maxsec.append(max(sccount.values())/len(ch))
        if i>0: rets[day]=dr
        peak=max(peak,E)
    return pd.Series(rets), np.mean(secs), np.mean(maxsec), np.mean([1 for m in maxsec if m>=0.8])/max(len(maxsec),1) if maxsec else 0

def main():
    print("Loading panel..."); P=load_panel(); R=P.pct_change()
    print("\n"+"="*84)
    print("SECTOR CAP (top-5, lb90, 20% trailing, with costs) · 2018+")
    print("="*84)
    print(f"{'cap/sector':14}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}{'Calmar':>8}{'sectors':>9}{'maxWt':>9}{'%>=80%1sec':>11}")
    out={}
    for cap,lbl in [(2,"max2 (40%)"),(3,"max3 (60%)"),(4,"max4 (80%)"),(5,"no cap (100%)")]:
        r,sec,mx,conc=run(P,R,cap=cap); out[cap]=r; c,sh,dd=metrics(r); cal=c/abs(dd) if dd<0 else float('nan')
        print(f"{lbl:14}{c*100:>7.1f}%{sh:>8.2f}{dd*100:>7.1f}%{cal:>8.2f}{sec:>9.1f}{mx*100:>8.0f}%{conc*100:>10.0f}%")
    print("\n"+"="*84+"\nIN EACH CRASH (max drawdown inside the window)\n"+"="*84)
    print(f"{'cap/sector':14}"+"".join(f"{k:>14}" for k in CRASHES))
    for cap,lbl in [(2,"max2 (40%)"),(3,"max3 (60%)"),(4,"max4 (80%)"),(5,"no cap")]:
        cells=[]
        for k,(a,b) in CRASHES.items():
            seg=out[cap].loc[a:b].dropna()
            dd=((1+seg).cumprod()/(1+seg).cumprod().cummax()-1).min() if len(seg)>1 else float('nan')
            cells.append(f"{dd*100:>13.1f}%")
        print(f"{lbl:14}"+"".join(cells))
    print("\nmaxWt = average weight of the dominant sector · %>=80% = share of rebalances with one sector >= 80%")

if __name__=="__main__":
    main()
