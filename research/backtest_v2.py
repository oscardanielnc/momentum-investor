"""
investor — Backtest v2: COSTOS reales + TURNOVER + ROBUSTEZ (barrido de parámetros).
Oscar acepta ~−32% si rinde más → NO se aprieta el freno. Se valida si la canasta:
  (a) sobrevive a costos de ejecución (spread/slippage; Alpaca comisión $0 pero spread existe),
  (b) no rota tanto que rompa el "estable día a día",
  (c) es ROBUSTA (funciona en un rango de parámetros, no solo en uno = no overfit).

Holdings en VALOR (capturan drift entre rebalanceos) → turnover y costos exactos.
Costos: ETF 4 bps, acción individual 10 bps por cada $ rotado (conservador para semis líquidos).
Uso: python research/backtest_v2.py
"""
import sys, itertools
import numpy as np, pandas as pd
from db_fetch import load_panel, STOCKS
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

BASE = ["XLV","XLP","XLU","XLE","GLD","DBC","TLT","SHY","EEM","EFA","QQQ"]
CASH = "SHY"
COST_BPS = {**{s: 10 for s in STOCKS}, **{s: 4 for s in BASE+["SMH"]}}

def metrics(r):
    r = r.dropna(); eq=(1+r).cumprod(); n=len(r)
    return (eq.iloc[-1]**(252/n)-1, r.std()*np.sqrt(252),
            (r.mean()*252)/(r.std()*np.sqrt(252)) if r.std()>0 else 0,
            (eq/eq.cummax()-1).min())

def build(P, R, params, costs=True):
    base=[s for s in BASE if s in P]; stocks=[s for s in STOCKS if s in P]
    rebal=set(P.resample(params["rebal"]).last().index)
    LB, TOPN, TCAP = params["lb"], params["topn"], params["tcap"]

    def vol_parity(assets, asof, frac):
        v=R[assets].loc[:asof].tail(60).std(); v=v[v>0]
        if v.empty: return {}
        iv=1/v; return (iv/iv.sum()*frac).to_dict()
    def rmom(s, asof):
        h=P[s].loc[:asof]
        if len(h)<LB+5: return -9
        m=h.iloc[-1]/h.iloc[-LB]-1; vol=R[s].loc[:asof].tail(20).std()*np.sqrt(252)
        return (m*252/LB)/vol if vol>0 else -9
    def tilt(g, dd):
        t=float(np.clip(0.35+0.25*g, 0.10, TCAP))
        thr=0.10 if dd<=-0.25 else 0.35 if dd<=-0.18 else 0.65 if dd<=-0.10 else 1.0
        return t*thr
    def wfun(asof, dd):
        ranked=sorted(stocks, key=lambda s: rmom(s, asof), reverse=True)[:TOPN]
        g=np.mean([rmom(s, asof) for s in ranked]) if ranked else 0
        t=tilt(g, dd)
        w=vol_parity(base, asof, 1-t)
        for s in ranked: w[s]=w.get(s,0)+t/len(ranked)
        tot=sum(w.values())
        if tot<0.999: w[CASH]=w.get(CASH,0)+(1-tot)
        return w

    # Simulación con PESOS + drift + costo explícito como drag del retorno del día (correcto).
    days=R.index; w={}; E=1.0; peak=1.0; rets={}; turns=[]
    for i,day in enumerate(days):
        day_ret=0.0
        if i>0 and w:
            gr=sum(w[s]*(R[s].iloc[i]) for s in w if not np.isnan(R[s].iloc[i]))
            E*=(1+gr); day_ret=gr
            nw={s: w[s]*(1+(R[s].iloc[i] if not np.isnan(R[s].iloc[i]) else 0.0)) for s in w}
            tot=sum(nw.values()); w={s:v/tot for s,v in nw.items()} if tot>0 else w
        if day in rebal:
            tw=wfun(day, E/peak-1)
            turn=0.5*sum(abs(tw.get(s,0)-w.get(s,0)) for s in set(tw)|set(w))
            turns.append(turn)
            if costs:
                cost=sum(abs(tw.get(s,0)-w.get(s,0))*COST_BPS.get(s,8)/1e4 for s in set(tw)|set(w))
                E*=(1-cost); day_ret=(1+day_ret)*(1-cost)-1
            w=tw
        if i>0: rets[day]=day_ret
        peak=max(peak,E)
    return pd.Series(rets), (np.mean(turns)*_periods(params["rebal"]) if turns else 0)

def _periods(freq): return 52 if freq.startswith("W") else 12

def main():
    print("Cargando panel…"); P=load_panel(); R=P.pct_change()
    default=dict(lb=63, topn=3, tcap=0.60, rebal="ME")

    # 1) impacto de costos + turnover
    print("\n"+"="*70+"\n1) COSTOS y TURNOVER (DYN-CANASTA default 63/3/0.60, mensual)\n"+"="*70)
    for label, costs in [("SIN costos", False), ("CON costos", True)]:
        r, turn = build(P, R, default, costs=costs)
        c,v,sh,dd = metrics(r)
        print(f"{label:11} CAGR {c*100:5.1f}%  Sharpe {sh:.2f}  maxDD {dd*100:6.1f}%  turnover anual ~{turn*100:.0f}%")

    # 2) robustez: barrido lookback × topN (costos ON, mensual, tcap 0.60)
    print("\n"+"="*70+"\n2) ROBUSTEZ — CAGR / maxDD por (lookback, topN) [costos ON]\n"+"="*70)
    LBS=[42,63,90,120]; TOPNS=[2,3,4,5]
    print(f"{'lb/topN':>8}"+"".join(f"{n:>14}" for n in TOPNS))
    for lb in LBS:
        cells=[]
        for n in TOPNS:
            r,_=build(P,R,dict(lb=lb,topn=n,tcap=0.60,rebal="ME"),costs=True)
            c,v,sh,dd=metrics(r); cells.append(f"{c*100:5.1f}%/{dd*100:5.1f}%")
        print(f"{lb:>8}"+"".join(f"{x:>14}" for x in cells))
    print("\nLectura: si CAGR/maxDD son parecidos en toda la grilla → robusto. Si solo brilla 1 celda → overfit.")

    # 3) head-to-head de las configs prometedoras (costos ON)
    print("\n"+"="*70+"\n3) HEAD-TO-HEAD configs candidatas [costos ON]\n"+"="*70)
    print(f"{'config':22}{'CAGR':>7}{'Sharpe':>8}{'maxDD':>8}{'turn/año':>10}")
    cfgs=[("63/3 mensual",dict(lb=63,topn=3,tcap=0.60,rebal="ME")),
          ("90/3 mensual",dict(lb=90,topn=3,tcap=0.60,rebal="ME")),
          ("90/3 semanal", dict(lb=90,topn=3,tcap=0.60,rebal="W-FRI")),
          ("63/3 semanal", dict(lb=63,topn=3,tcap=0.60,rebal="W-FRI"))]
    for lbl,cf in cfgs:
        r,turn=build(P,R,cf,costs=True); c,v,sh,dd=metrics(r)
        print(f"{lbl:22}{c*100:6.1f}%{sh:>8.2f}{dd*100:>7.1f}%{turn*100:>9.0f}%")

if __name__=="__main__":
    main()
