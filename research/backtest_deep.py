"""
investor — Backtest PROFUNDO 2018+ (Databento) a través de 3 crashes reales.
Compara: ¿el tilt debe ser el ETF (SMH) o una CANASTA top-N de semis individuales?
+ valida que el freno escalonado respeta el techo −30%.

Estrategias:
  DYN-ETF    : tilt = SMH (ETF), tilt dinámico (escala con momentum/vol) + freno DD + vol-parity base.
  DYN-CANASTA: tilt = top-3 semis por momentum ajustado-riesgo (rota), mismo sizing dinámico.
  STATIC 65/35: base vol-parity 65% + SMH 35% fijo.
  Benchmarks : QQQ, SPY, 60/40.

Reporta métricas globales + retorno/maxDD en cada crash (2018-Q4, COVID-2020, bear-2022).
Uso: python research/backtest_deep.py
"""
import sys
import numpy as np, pandas as pd
from db_fetch import load_panel, STOCKS
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

BASE = ["XLV","XLP","XLU","XLE","GLD","DBC","TLT","SHY","EEM","EFA","QQQ"]  # diversificadores + núcleo
CASH = "SHY"
CRASHES = {
    "2018-Q4":  ("2018-09-20", "2018-12-26"),
    "COVID-20": ("2020-02-19", "2020-03-23"),
    "Bear-22":  ("2022-01-03", "2022-10-12"),
}

def metrics(r):
    r = r.dropna(); eq = (1+r).cumprod(); n = len(r)
    cagr = eq.iloc[-1]**(252/n) - 1
    vol = r.std()*np.sqrt(252)
    sh = (r.mean()*252)/vol if vol>0 else 0
    dd = (eq/eq.cummax()-1).min()
    return cagr, vol, sh, dd

def main():
    print("Cargando panel (caché Databento)…")
    P = load_panel()
    R = P.pct_change()
    base = [s for s in BASE if s in P]
    stocks = [s for s in STOCKS if s in P]
    print(f"Base: {base}\nSemis canasta: {stocks}\nSMH presente: {'SMH' in P}")
    rebal = P.resample("ME").last().index
    idx_rebal = set(rebal)

    def vol_parity(assets, asof, frac):
        win = R[assets].loc[:asof].tail(60); v = win.std(); v = v[v>0]
        if v.empty: return {}
        iv = 1/v; return (iv/iv.sum()*frac).to_dict()

    def riskadj_mom(sym, asof):
        h = P[sym].loc[:asof]
        if len(h) < 70: return -9
        mom = h.iloc[-1]/h.iloc[-63]-1
        vol = R[sym].loc[:asof].tail(20).std()*np.sqrt(252)
        return (mom*252/63)/vol if vol>0 else -9

    def tilt_frac(g, dd):
        t = float(np.clip(0.35 + 0.25*g, 0.10, 0.60))
        thr = 0.10 if dd<=-0.25 else 0.35 if dd<=-0.18 else 0.65 if dd<=-0.10 else 1.0
        return t*thr

    def w_static(asof, dd):
        w = vol_parity(base, asof, 0.65)
        if "SMH" in P: w["SMH"] = w.get("SMH",0)+0.35
        return w

    def w_dyn_etf(asof, dd):
        g = riskadj_mom("SMH", asof) if "SMH" in P else 0
        t = tilt_frac(g, dd)
        w = vol_parity(base, asof, 1-t)
        if "SMH" in P: w["SMH"] = w.get("SMH",0)+t
        s = sum(w.values())
        if s < 0.999: w[CASH] = w.get(CASH,0)+(1-s)
        return w

    def w_dyn_basket(asof, dd):
        ranked = sorted(stocks, key=lambda s: riskadj_mom(s, asof), reverse=True)
        top = ranked[:3]
        g = np.mean([riskadj_mom(s, asof) for s in top]) if top else 0
        t = tilt_frac(g, dd)
        w = vol_parity(base, asof, 1-t)
        for s in top: w[s] = w.get(s,0)+t/len(top)
        s2 = sum(w.values())
        if s2 < 0.999: w[CASH] = w.get(CASH,0)+(1-s2)
        return w

    def run(fn):
        eq, peak, w = 1.0, 1.0, {}
        rets = {}
        days = R.index
        for i, day in enumerate(days):
            if i>0:
                r = sum(w.get(s,0)*R[s].iloc[i] for s in w if not np.isnan(R[s].iloc[i]))
                eq *= (1+r); peak = max(peak, eq); rets[day] = r
            if day in idx_rebal:
                w = fn(day, eq/peak-1)
        return pd.Series(rets)

    strat = {
        "STATIC 65/35": run(w_static),
        "DYN-ETF (SMH)": run(w_dyn_etf),
        "DYN-CANASTA":   run(w_dyn_basket),
    }
    if "QQQ" in R: strat["BENCH QQQ"] = R["QQQ"]
    if "SPY" in R and "TLT" in R: strat["BENCH 60/40"] = 0.6*R["SPY"]+0.4*R["TLT"]

    print("\n" + "="*72)
    print("GLOBAL 2018+")
    print("="*72)
    print(f"{'estrategia':16}{'CAGR':>8}{'vol':>7}{'Sharpe':>8}{'maxDD':>8}{'Calmar':>8}")
    print("-"*72)
    for n, r in strat.items():
        c,v,sh,dd = metrics(r); cal = c/abs(dd) if dd<0 else float('nan')
        print(f"{n:16}{c*100:>7.1f}%{v*100:>6.1f}%{sh:>8.2f}{dd*100:>7.1f}%{cal:>8.2f}")

    print("\n" + "="*72)
    print("EN CADA CRASH (retorno acumulado · maxDD dentro de la ventana)")
    print("="*72)
    print(f"{'estrategia':16}" + "".join(f"{k:>20}" for k in CRASHES))
    for n, r in strat.items():
        cells = []
        for k,(a,b) in CRASHES.items():
            seg = r.loc[a:b].dropna()
            if len(seg)<2: cells.append(f"{'s/d':>20}"); continue
            cum = (1+seg).prod()-1
            dd = ((1+seg).cumprod()/(1+seg).cumprod().cummax()-1).min()
            cells.append(f"{cum*100:>9.1f}% dd{dd*100:>6.1f}%")
        print(f"{n:16}" + "".join(cells))
    print("\nObjetivo: que las DYN tengan maxDD mucho menor que QQQ en cada crash, sin matar el CAGR global.")

if __name__ == "__main__":
    main()
