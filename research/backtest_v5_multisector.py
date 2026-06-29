"""
investor — Backtest v5: ROTACIÓN MULTI-SECTOR (la idea de Oscar).
"El defensivo es estar en el sector correcto, no en SHY." Siempre invertido en los líderes de
momentum a través de SECTORES; si un sector cae, su capital rota al sector no-correlacionado que
sube. + Trailing stops para "salir a tiempo". + Cuánto Global66 fijo se necesita por agresividad.

Estrategias (2018+, costos ON, 10bps/acción):
  BASE-semis   : referencia = top-3 semis + caja SHY (modelo validado actual).
  MULTI top-5  : top-5 momentum de 36 acciones / 7 sectores, equiponderado, SIEMPRE invertido.
  MULTI+TS     : igual pero con trailing stop X% (sale a caja si una posición cae desde su pico;
                 redepliega al próximo rebalanceo). Acota el drawdown.

Reporta CAGR/Sharpe/maxDD + por crash + diversificación (nº sectores promedio en cartera) +
SIZING GLOBAL66: para cada maxDD del pool, qué % en Global66 mantiene el patrimonio TOTAL ≤ −20/−25/−30%.
Uso: python research/backtest_v5_multisector.py
"""
import sys
import numpy as np, pandas as pd
from db_fetch import load_panel, STOCKS, STOCKS_MULTI
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

UNIV = STOCKS + STOCKS_MULTI
SECTOR = {**{s:"semis" for s in STOCKS},
          **{s:"software" for s in ["MSFT","ORCL","CRM","NOW","ADBE"]},
          **{s:"energia" for s in ["XOM","CVX","COP","SLB"]},
          **{s:"salud" for s in ["LLY","UNH","JNJ","ABBV"]},
          **{s:"finanzas" for s in ["JPM","GS","V","MA"]},
          **{s:"consumo" for s in ["AMZN","TSLA","COST","HD"]},
          **{s:"comm" for s in ["GOOGL","NFLX"]}}
CRASHES = {"2018-Q4":("2018-09-20","2018-12-26"),"COVID-20":("2020-02-19","2020-03-23"),"Bear-22":("2022-01-03","2022-10-12")}
COST = 10/1e4

def metrics(r):
    r=r.dropna();
    if len(r)<30: return (np.nan,np.nan,np.nan)
    eq=(1+r).cumprod(); n=len(r)
    return (eq.iloc[-1]**(252/n)-1, (r.mean()*252)/(r.std()*np.sqrt(252)) if r.std()>0 else 0, (eq/eq.cummax()-1).min())

def rmom(P,R,s,asof,lb=90):
    h=P[s].loc[:asof]
    if len(h)<lb+5: return -9
    m=h.iloc[-1]/h.iloc[-lb]-1; vol=R[s].loc[:asof].tail(20).std()*np.sqrt(252)
    return (m*252/lb)/vol if vol>0 else -9

def run_multi(P,R,topn=5,trail=None,lb=90):
    """Top-N momentum multi-sector, equiponderado, siempre invertido. trail=None o 0.xx (stop %)."""
    rebset=set(P.resample("ME").last().index); days=R.index
    w={}; peaks={}; eqp_w=None; E=1.0; peak=1.0; rets={}; secs=[]
    universe=[s for s in UNIV if s in P]
    for i,day in enumerate(days):
        dr=0.0
        def _ret(s,i):  # CASH (caja del trailing stop) rinde 0
            if s=="CASH": return 0.0
            v=R[s].iloc[i]; return 0.0 if np.isnan(v) else v
        if i>0 and w:
            gr=sum(w[s]*_ret(s,i) for s in w); E*=(1+gr); dr=gr
            nw={s:w[s]*(1+_ret(s,i)) for s in w}
            tot=sum(nw.values()); w={s:v/tot for s,v in nw.items()} if tot>0 else w
            # trailing stop: actualizar picos y salir a caja si cae desde el pico
            if trail:
                for s in list(w):
                    if s=="CASH": continue
                    peaks[s]=max(peaks.get(s,P[s].iloc[i]), P[s].iloc[i])
                    if P[s].iloc[i] <= peaks[s]*(1-trail):
                        w["CASH"]=w.get("CASH",0)+w.pop(s)   # a caja
        if day in rebset:
            ranked=sorted(universe,key=lambda s:rmom(P,R,s,day,lb),reverse=True)[:topn]
            tw={s:1.0/len(ranked) for s in ranked}
            cost=sum(abs(tw.get(s,0)-w.get(s,0))*COST for s in set(tw)|set(w))
            E*=(1-cost); dr=(1+dr)*(1-cost)-1
            w=tw; peaks={s:P[s].loc[:day].iloc[-1] for s in ranked}
            secs.append(len(set(SECTOR[s] for s in ranked)))
        if i>0: rets[day]=dr
        peak=max(peak,E)
    return pd.Series(rets), (np.mean(secs) if secs else 0)

def run_base(P,R):
    """Referencia: top-3 semis + caja SHY vol-parity (modelo validado)."""
    base=["XLV","XLP","XLU","XLE","GLD","DBC","TLT","SHY","EEM","EFA","QQQ"]; CASH="SHY"
    base=[b for b in base if b in P]; semis=[s for s in STOCKS if s in P]
    rebset=set(P.resample("ME").last().index); days=R.index
    w={}; E=1.0; peak=1.0; rets={}
    def vp(a,asof,frac):
        v=R[a].loc[:asof].tail(60).std(); v=v[v>0]
        return {} if v.empty else ((1/v)/(1/v).sum()*frac).to_dict()
    for i,day in enumerate(days):
        dr=0.0
        if i>0 and w:
            gr=sum(w[s]*R[s].iloc[i] for s in w if not np.isnan(R[s].iloc[i])); E*=(1+gr); dr=gr
            nw={s:w[s]*(1+(R[s].iloc[i] if not np.isnan(R[s].iloc[i]) else 0)) for s in w}
            tot=sum(nw.values()); w={s:v/tot for s,v in nw.items()} if tot>0 else w
        if day in rebset:
            rk=sorted(semis,key=lambda s:rmom(P,R,s,day),reverse=True)[:3]
            g=np.mean([rmom(P,R,s,day) for s in rk]); tilt=float(np.clip(0.35+0.25*g,0.10,0.60))
            tw=vp(base,day,1-tilt)
            for s in rk: tw[s]=tw.get(s,0)+tilt/3
            t=sum(tw.values());
            if t<0.999: tw[CASH]=tw.get(CASH,0)+(1-t)
            cost=sum(abs(tw.get(s,0)-w.get(s,0))*COST for s in set(tw)|set(w)); E*=(1-cost); dr=(1+dr)*(1-cost)-1
            w=tw
        if i>0: rets[day]=dr
        peak=max(peak,E)
    return pd.Series(rets)

def main():
    print("Cargando panel…"); P=load_panel(); R=P.pct_change()
    have=[s for s in UNIV if s in P]
    print(f"Universo multi-sector disponible: {len(have)}/{len(UNIV)} acciones, "
          f"{len(set(SECTOR[s] for s in have))} sectores")
    res={}
    res["BASE-semis (validado)"]=(run_base(P,R), None)
    for topn in (3,5):
        res[f"MULTI top-{topn}"]=run_multi(P,R,topn=topn,trail=None)
    res["MULTI top-5 +TS15%"]=run_multi(P,R,topn=5,trail=0.15)
    res["MULTI top-5 +TS20%"]=run_multi(P,R,topn=5,trail=0.20)

    print("\n"+"="*78+"\nGLOBAL 2018+ (costos ON)\n"+"="*78)
    print(f"{'estrategia':24}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}{'Calmar':>8}{'sectores':>9}")
    pool_dd={}
    for n,(r,sec) in res.items():
        c,sh,dd=metrics(r); cal=c/abs(dd) if dd<0 else float('nan'); pool_dd[n]=dd
        sx=f"{sec:.1f}" if sec else "—"
        print(f"{n:24}{c*100:>7.1f}%{sh:>8.2f}{dd*100:>7.1f}%{cal:>8.2f}{sx:>9}")

    print("\n"+"="*78+"\nEN CADA CRASH (maxDD en la ventana)\n"+"="*78)
    print(f"{'estrategia':24}"+"".join(f"{k:>14}" for k in CRASHES))
    for n,(r,_) in res.items():
        cells=[]
        for k,(a,b) in CRASHES.items():
            seg=r.loc[a:b].dropna()
            dd=((1+seg).cumprod()/(1+seg).cumprod().cummax()-1).min() if len(seg)>1 else float('nan')
            cells.append(f"{dd*100:>13.1f}%")
        print(f"{n:24}"+"".join(cells))

    print("\n"+"="*78+"\nSIZING GLOBAL66: % en Alpaca para que el patrimonio TOTAL no pase cada techo\n"+"="*78)
    print("(Global66 estable → maxDD_total ≈ %Alpaca × maxDD_pool. %Global66 = 100 − %Alpaca)")
    print(f"{'estrategia':24}{'maxDD pool':>11}{'tope -20%':>12}{'tope -25%':>12}{'tope -30%':>12}")
    for n,dd in pool_dd.items():
        if dd>=0 or np.isnan(dd): continue
        row=f"{n:24}{dd*100:>10.1f}%"
        for cap in (0.20,0.25,0.30):
            f_alpaca=min(1.0, cap/abs(dd)); row+=f"{f_alpaca*100:>6.0f}%A/{(1-f_alpaca)*100:>3.0f}%G"
        print(row)
    print("\nEj: 70%A/30%G = 70% en Alpaca (robot agresivo) + 30% fijo en Global66 → total respeta el techo.")

if __name__=="__main__":
    main()
