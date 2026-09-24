"""
Backtest v5: multi-sector momentum rotation.

Idea: the defensive move is to be in the right sector, not in T-bills. Stay fully invested in
the momentum leaders across sectors; when one sector falls, capital rotates to an uncorrelated
one that is rising. Trailing stops provide the exit.

Strategies (2018+, with costs, 10 bps per stock trade):
  BASE-semis  : reference = top-3 semis + SHY cash (the v4 model).
  MULTI top-N : top-N momentum out of 36 stocks / 7 sectors, equal weight, always invested.
  MULTI+TS    : same with an X% trailing stop (the position goes to cash when it falls X% from
                its peak; redeployed at the next monthly rebalance). Bounds the drawdown.

Reports CAGR/Sharpe/maxDD, drawdown per crash, average number of sectors held, and a sizing
table: what fraction of total wealth can sit in the strategy (the rest in a stable external
cash reserve) so that the total drawdown stays under -20/-25/-30%.

This is the backtest that produced the 36% CAGR headline. The universe is the 36 names chosen
in 2026, which is survivorship-biased; see backtest_v10_pit.py for the point-in-time audit.
Usage: python research/backtest_v5_multisector.py
"""
import sys
import numpy as np, pandas as pd
from db_fetch import load_panel, STOCKS, STOCKS_MULTI
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

UNIV = STOCKS + STOCKS_MULTI
SECTOR = {**{s:"semis" for s in STOCKS},
          **{s:"software" for s in ["MSFT","ORCL","CRM","NOW","ADBE"]},
          **{s:"energy" for s in ["XOM","CVX","COP","SLB"]},
          **{s:"health" for s in ["LLY","UNH","JNJ","ABBV"]},
          **{s:"financials" for s in ["JPM","GS","V","MA"]},
          **{s:"consumer" for s in ["AMZN","TSLA","COST","HD"]},
          **{s:"comm" for s in ["GOOGL","NFLX"]}}
CRASHES = {"2018-Q4":("2018-09-20","2018-12-26"),"COVID-20":("2020-02-19","2020-03-23"),"Bear-22":("2022-01-03","2022-10-12")}
COST = 10/1e4

def metrics(r):
    """(CAGR, Sharpe, maxDD) from daily returns; NaNs when fewer than 30 observations."""
    r=r.dropna();
    if len(r)<30: return (np.nan,np.nan,np.nan)
    eq=(1+r).cumprod(); n=len(r)
    return (eq.iloc[-1]**(252/n)-1, (r.mean()*252)/(r.std()*np.sqrt(252)) if r.std()>0 else 0, (eq/eq.cummax()-1).min())

def rmom(P,R,s,asof,lb=90):
    """Risk-adjusted momentum: annualized lb-day return over 20-day annualized volatility."""
    h=P[s].loc[:asof]
    if len(h)<lb+5: return -9
    m=h.iloc[-1]/h.iloc[-lb]-1; vol=R[s].loc[:asof].tail(20).std()*np.sqrt(252)
    return (m*252/lb)/vol if vol>0 else -9

def run_multi(P,R,topn=5,trail=None,lb=90):
    """Monthly top-N multi-sector momentum, equal weight, always invested.

    trail: None or a fraction (e.g. 0.20) for a close-to-close trailing stop.
    Returns (daily returns, average number of sectors held).
    """
    rebset=set(P.resample("ME").last().index); days=R.index
    w={}; peaks={}; E=1.0; peak=1.0; rets={}; secs=[]
    universe=[s for s in UNIV if s in P]
    for i,day in enumerate(days):
        dr=0.0
        def _ret(s,i):  # cash from a stopped position earns 0
            if s=="CASH": return 0.0
            v=R[s].iloc[i]; return 0.0 if np.isnan(v) else v
        if i>0 and w:
            gr=sum(w[s]*_ret(s,i) for s in w); E*=(1+gr); dr=gr
            nw={s:w[s]*(1+_ret(s,i)) for s in w}
            tot=sum(nw.values()); w={s:v/tot for s,v in nw.items()} if tot>0 else w
            if trail:
                for s in list(w):
                    if s=="CASH": continue
                    peaks[s]=max(peaks.get(s,P[s].iloc[i]), P[s].iloc[i])
                    if P[s].iloc[i] <= peaks[s]*(1-trail):
                        w["CASH"]=w.get("CASH",0)+w.pop(s)
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
    """Reference: top-3 semis + SHY cash with inverse-volatility weights (the v4 model)."""
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
    print("Loading panel..."); P=load_panel(); R=P.pct_change()
    have=[s for s in UNIV if s in P]
    print(f"Multi-sector universe available: {len(have)}/{len(UNIV)} stocks, "
          f"{len(set(SECTOR[s] for s in have))} sectors")
    res={}
    res["BASE-semis (v4 model)"]=(run_base(P,R), None)
    for topn in (3,5):
        res[f"MULTI top-{topn}"]=run_multi(P,R,topn=topn,trail=None)
    res["MULTI top-5 +TS15%"]=run_multi(P,R,topn=5,trail=0.15)
    res["MULTI top-5 +TS20%"]=run_multi(P,R,topn=5,trail=0.20)

    print("\n"+"="*78+"\nGLOBAL 2018+ (with costs)\n"+"="*78)
    print(f"{'strategy':24}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}{'Calmar':>8}{'sectors':>9}")
    pool_dd={}
    for n,(r,sec) in res.items():
        c,sh,dd=metrics(r); cal=c/abs(dd) if dd<0 else float('nan'); pool_dd[n]=dd
        sx=f"{sec:.1f}" if sec else "-"
        print(f"{n:24}{c*100:>7.1f}%{sh:>8.2f}{dd*100:>7.1f}%{cal:>8.2f}{sx:>9}")

    print("\n"+"="*78+"\nIN EACH CRASH (max drawdown inside the window)\n"+"="*78)
    print(f"{'strategy':24}"+"".join(f"{k:>14}" for k in CRASHES))
    for n,(r,_) in res.items():
        cells=[]
        for k,(a,b) in CRASHES.items():
            seg=r.loc[a:b].dropna()
            dd=((1+seg).cumprod()/(1+seg).cumprod().cummax()-1).min() if len(seg)>1 else float('nan')
            cells.append(f"{dd*100:>13.1f}%")
        print(f"{n:24}"+"".join(cells))

    print("\n"+"="*78+"\nSIZING: % in the strategy so that TOTAL wealth stays under each cap\n"+"="*78)
    print("(stable reserve -> total maxDD ~ %strategy x strategy maxDD. %reserve = 100 - %strategy)")
    print(f"{'strategy':24}{'maxDD':>11}{'cap -20%':>12}{'cap -25%':>12}{'cap -30%':>12}")
    for n,dd in pool_dd.items():
        if dd>=0 or np.isnan(dd): continue
        row=f"{n:24}{dd*100:>10.1f}%"
        for cap in (0.20,0.25,0.30):
            f_alpaca=min(1.0, cap/abs(dd)); row+=f"{f_alpaca*100:>6.0f}%S/{(1-f_alpaca)*100:>3.0f}%R"
        print(row)
    print("\nE.g. 70%S/30%R = 70% in the strategy + 30% in a stable reserve keeps the total within the cap.")

if __name__=="__main__":
    main()
