"""
investor — A vs B: ¿caja en el vol-parity (A, validado) o diversificadores reales de pie (B)?
Pregunta de Oscar: que un crash sorpresa no afecte tanto. Mido cuánto retorno cuesta B.

A (actual/validado): base = vol-parity sobre TODOS los diversificadores incl. SHY (cash) →
   en momentum fuerte la base se va casi toda a caja (SHY), diversificadores reales ~1-2%.
B (variante):        base = vol-parity sobre diversificadores REALES (SHY EXCLUIDO) → siempre
   hay oro/bonos/energía/defensivos de pie; la caja solo aparece por el freno DD.

Config bloqueada: lookback 90, top-3, mensual, banda 5%, costos ON. Global + por crash.
Uso: python research/backtest_v4_baseAB.py
"""
import sys
import numpy as np, pandas as pd
from db_fetch import load_panel, STOCKS
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

BASE=["XLV","XLP","XLU","XLE","GLD","DBC","TLT","SHY","EEM","EFA","QQQ"]; CASH="SHY"
DIVS=[s for s in BASE if s!=CASH]
COST_BPS={**{s:10 for s in STOCKS}, **{s:4 for s in BASE+["SMH"]}}
CRASHES={"2018-Q4":("2018-09-20","2018-12-26"),"COVID-20":("2020-02-19","2020-03-23"),"Bear-22":("2022-01-03","2022-10-12")}

def metrics(r):
    r=r.dropna(); eq=(1+r).cumprod(); n=len(r)
    return (eq.iloc[-1]**(252/n)-1, (r.mean()*252)/(r.std()*np.sqrt(252)) if r.std()>0 else 0, (eq/eq.cummax()-1).min())

def build(P,R,mode="A",lb=90,topn=3,band=0.05):
    base = BASE if mode=="A" else DIVS    # B excluye la caja del vol-parity
    stocks=[s for s in STOCKS if s in P]; rebset=set(P.resample("ME").last().index)
    def vp(assets,asof,frac):
        assets=[a for a in assets if a in R.columns]
        v=R[assets].loc[:asof].tail(60).std(); v=v[v>0]
        return {} if v.empty else ((1/v)/(1/v).sum()*frac).to_dict()
    def rmom(s,asof):
        h=P[s].loc[:asof]
        if len(h)<lb+5: return -9
        m=h.iloc[-1]/h.iloc[-lb]-1; vol=R[s].loc[:asof].tail(20).std()*np.sqrt(252)
        return (m*252/lb)/vol if vol>0 else -9
    def wfun(asof,dd):
        rk=sorted(stocks,key=lambda s:rmom(s,asof),reverse=True)[:topn]
        g=np.mean([rmom(s,asof) for s in rk]) if rk else 0
        t=float(np.clip(0.35+0.25*g,0.10,0.60))
        t*= 0.10 if dd<=-0.25 else 0.35 if dd<=-0.18 else 0.65 if dd<=-0.10 else 1.0
        w=vp(base,asof,1-t)
        for s in rk: w[s]=w.get(s,0)+t/len(rk)
        tot=sum(w.values())
        if tot<0.999: w[CASH]=w.get(CASH,0)+(1-tot)   # remanente (freno) → caja
        return w
    days=R.index; w={}; E=1.0; peak=1.0; rets={}
    for i,day in enumerate(days):
        dr=0.0
        if i>0 and w:
            gr=sum(w[s]*R[s].iloc[i] for s in w if not np.isnan(R[s].iloc[i])); E*=(1+gr); dr=gr
            nw={s:w[s]*(1+(R[s].iloc[i] if not np.isnan(R[s].iloc[i]) else 0)) for s in w}
            tot=sum(nw.values()); w={s:v/tot for s,v in nw.items()} if tot>0 else w
        if day in rebset:
            tw=wfun(day,E/peak-1)
            if band>0:
                kept={s:(w.get(s,0) if abs(tw.get(s,0)-w.get(s,0))<band else tw.get(s,0)) for s in set(tw)|set(w)}
                tot=sum(kept.values()); tw={s:v/tot for s,v in kept.items()} if tot>0 else tw
            cost=sum(abs(tw.get(s,0)-w.get(s,0))*COST_BPS.get(s,8)/1e4 for s in set(tw)|set(w))
            E*=(1-cost); dr=(1+dr)*(1-cost)-1; w=tw
        if i>0: rets[day]=dr
        peak=max(peak,E)
    return pd.Series(rets)

def main():
    print("Cargando panel…"); P=load_panel(); R=P.pct_change()
    print("\n"+"="*60+"\nGLOBAL 2018+\n"+"="*60)
    print(f"{'modo':28}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}")
    series={}
    for mode,lbl in [("A","A: caja en vol-parity (validado)"),("B","B: diversificadores de pie")]:
        r=build(P,R,mode=mode); series[mode]=r; c,sh,dd=metrics(r)
        print(f"{lbl:28}{c*100:>7.1f}%{sh:>8.2f}{dd*100:>7.1f}%")
    print("\n"+"="*60+"\nEN CADA CRASH (maxDD dentro de la ventana)\n"+"="*60)
    print(f"{'modo':8}"+"".join(f"{k:>16}" for k in CRASHES))
    for mode in ("A","B"):
        cells=[]
        for k,(a,b) in CRASHES.items():
            seg=series[mode].loc[a:b].dropna()
            dd=((1+seg).cumprod()/(1+seg).cumprod().cummax()-1).min() if len(seg)>1 else float('nan')
            cells.append(f"{dd*100:>15.1f}%")
        print(f"{mode:8}"+"".join(cells))
    print("\nObjetivo de B: maxDD MENOR en los crashes. Costo de B: menor CAGR. Ver el trade-off.")

if __name__=="__main__":
    main()
