"""
investor — Backtest v3: BANDAS de tolerancia + WALK-FORWARD out-of-sample.
Cierra los 2 últimos huecos honestos antes de codear el allocator:
  A) Bandas: ¿cuánto baja el turnover sin matar el retorno? (estable día a día + menos costo)
  B) Walk-forward: elegir lookback en 2018-2022 (in-sample) y validar en 2023-2026 (OOS).
     Si el lookback elegido IS también rinde OOS → la elección generaliza (no fue suerte/overfit).

Uso: python research/backtest_v3.py
"""
import sys
import numpy as np, pandas as pd
from db_fetch import load_panel, STOCKS
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

BASE=["XLV","XLP","XLU","XLE","GLD","DBC","TLT","SHY","EEM","EFA","QQQ"]; CASH="SHY"
COST_BPS={**{s:10 for s in STOCKS}, **{s:4 for s in BASE+["SMH"]}}

def metrics(r):
    r=r.dropna()
    if len(r)<30: return (np.nan,)*4
    eq=(1+r).cumprod(); n=len(r)
    return (eq.iloc[-1]**(252/n)-1, r.std()*np.sqrt(252),
            (r.mean()*252)/(r.std()*np.sqrt(252)) if r.std()>0 else 0, (eq/eq.cummax()-1).min())

def build(P,R,lb=90,topn=3,tcap=0.60,rebal="ME",band=0.0,costs=True):
    base=[s for s in BASE if s in P]; stocks=[s for s in STOCKS if s in P]
    rebset=set(P.resample(rebal).last().index)
    def vp(assets,asof,frac):
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
        t=float(np.clip(0.35+0.25*g,0.10,tcap))
        thr=0.10 if dd<=-0.25 else 0.35 if dd<=-0.18 else 0.65 if dd<=-0.10 else 1.0
        t*=thr
        w=vp(base,asof,1-t)
        for s in rk: w[s]=w.get(s,0)+t/len(rk)
        tot=sum(w.values())
        if tot<0.999: w[CASH]=w.get(CASH,0)+(1-tot)
        return w
    days=R.index; w={}; E=1.0; peak=1.0; rets={}; turns=[]
    for i,day in enumerate(days):
        dr=0.0
        if i>0 and w:
            gr=sum(w[s]*R[s].iloc[i] for s in w if not np.isnan(R[s].iloc[i])); E*=(1+gr); dr=gr
            nw={s:w[s]*(1+(R[s].iloc[i] if not np.isnan(R[s].iloc[i]) else 0)) for s in w}
            tot=sum(nw.values()); w={s:v/tot for s,v in nw.items()} if tot>0 else w
        if day in rebset:
            tw=wfun(day,E/peak-1)
            if band>0:  # banda: no tocar posiciones cuyo cambio < band; renormalizar
                kept={s:(w.get(s,0) if abs(tw.get(s,0)-w.get(s,0))<band else tw.get(s,0)) for s in set(tw)|set(w)}
                tot=sum(kept.values()); tw={s:v/tot for s,v in kept.items()} if tot>0 else tw
            turn=0.5*sum(abs(tw.get(s,0)-w.get(s,0)) for s in set(tw)|set(w)); turns.append(turn)
            if costs:
                cost=sum(abs(tw.get(s,0)-w.get(s,0))*COST_BPS.get(s,8)/1e4 for s in set(tw)|set(w))
                E*=(1-cost); dr=(1+dr)*(1-cost)-1
            w=tw
        if i>0: rets[day]=dr
        peak=max(peak,E)
    return pd.Series(rets), (np.mean(turns)*(52 if rebal.startswith("W") else 12) if turns else 0)

def main():
    print("Cargando panel…"); P=load_panel(); R=P.pct_change()

    print("\n"+"="*68+"\nA) BANDAS DE TOLERANCIA (90/3 mensual, costos ON)\n"+"="*68)
    print(f"{'banda':>7}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}{'turn/año':>10}")
    for band in [0.0,0.03,0.05,0.08]:
        r,turn=build(P,R,lb=90,topn=3,band=band,costs=True); c,v,sh,dd=metrics(r)
        print(f"{band*100:>6.0f}%{c*100:>7.1f}%{sh:>8.2f}{dd*100:>7.1f}%{turn*100:>9.0f}%")

    print("\n"+"="*68+"\nB) WALK-FORWARD: elegir lookback en 2018-2022, validar 2023-2026\n"+"="*68)
    print(f"{'lookback':>9}{'IS Sharpe':>11}{'OOS CAGR':>10}{'OOS Shrp':>10}{'OOS maxDD':>11}")
    res={}
    for lb in [42,63,90,120]:
        r,_=build(P,R,lb=lb,topn=3,band=0.05,costs=True)
        is_m=metrics(r.loc[:"2022-12-31"]); oos=metrics(r.loc["2023-01-01":])
        res[lb]=(is_m[2], oos)
        print(f"{lb:>9}{is_m[2]:>11.2f}{oos[0]*100:>9.1f}%{oos[2]:>10.2f}{oos[3]*100:>10.1f}%")
    best=max(res, key=lambda k:res[k][0])
    print(f"\nMejor lookback IN-SAMPLE (2018-22) por Sharpe = {best}d")
    print(f"→ su desempeño OUT-OF-SAMPLE (2023-26): CAGR {res[best][1][0]*100:.1f}% · "
          f"Sharpe {res[best][1][2]:.2f} · maxDD {res[best][1][3]*100:.1f}%")
    print("Si el lookback elegido IS está entre los mejores OOS → la elección generaliza (no fue suerte).")

if __name__=="__main__":
    main()
