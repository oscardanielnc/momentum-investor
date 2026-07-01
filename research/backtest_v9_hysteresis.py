"""
investor — Backtest v9: BANDA DE HISTÉRESIS para el rebalanceo DIARIO.

Problema (v8): el robot vivo rebalancea a diario si cambia la membresía del top-5. Esa membresía
cambia el 56.7% de los días → ~143 rebal/año → churn que hunde la estrategia a 19% CAGR / −44% maxDD.

Fix: histéresis de RANGO. Un nombre se MANTIENE mientras siga dentro del top-`exit_rank` (banda),
y solo se reemplaza cuando cae claramente fuera. Entrada por top-5, salida por top-`exit_rank`.
exit_rank=5 = sin banda (robot HOY). exit_rank>5 = banda: menos churn.

Mensual = rebalanceo completo a top-5 equiponderado (reset). Entre medias = rotación diaria con banda.
Trailing stop 20% (a caja; se redepliega en la siguiente rotación diaria). Costos 10bps/trade.
Opcional: cuarentena de N días al nombre stopeado (no reentra durante N días).

Uso: python research/backtest_v9_hysteresis.py
"""
import sys
import numpy as np, pandas as pd
from db_fetch import load_panel
from backtest_v5_multisector import UNIV, SECTOR, CRASHES, COST, metrics, rmom
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def run_hyst(P, R, topn=5, exit_rank=8, trail=0.20, lb=90, quar_days=0):
    rebset = set(P.resample("ME").last().index); days = R.index
    w = {}; peaks = {}; E = 1.0; rets = {}; secs = []; quar = {}
    n_reb = 0; n_stops = 0
    universe = [s for s in UNIV if s in P]

    def price(s, i): return P[s].iloc[i]
    def rebalance_to(newset, i, dr):
        nonlocal w, peaks, n_reb
        tw = {s: 1.0 / len(newset) for s in newset}
        cost = sum(abs(tw.get(s, 0) - w.get(s, 0)) * COST for s in set(tw) | set(w))
        w = tw; peaks = {s: price(s, i) for s in newset}; n_reb += 1
        return (1 + dr) * (1 - cost) - 1

    for i, day in enumerate(days):
        dr = 0.0
        def _ret(s):
            if s == "CASH": return 0.0
            v = R[s].iloc[i]; return 0.0 if np.isnan(v) else v
        if i > 0 and w:
            gr = sum(w[s] * _ret(s) for s in w); E *= (1 + gr); dr = gr
            nw = {s: w[s] * (1 + _ret(s)) for s in w}
            tot = sum(nw.values()); w = {s: v / tot for s, v in nw.items()} if tot > 0 else w
            # trailing stops → a caja + cuarentena
            if trail:
                for s in list(w):
                    if s == "CASH": continue
                    peaks[s] = max(peaks.get(s, price(s, i)), price(s, i))
                    if price(s, i) <= peaks[s] * (1 - trail):
                        w["CASH"] = w.get("CASH", 0) + w.pop(s); quar[s] = i + quar_days; n_stops += 1

        # rebalanceo mensual completo (a top-5 exacto), excluyendo cuarentenados
        if day in rebset:
            ranked = sorted([s for s in universe if quar.get(s, -1) <= i],
                            key=lambda s: rmom(P, R, s, day, lb), reverse=True)[:topn]
            dr = rebalance_to(ranked, i, dr)
            secs.append(len(set(SECTOR[s] for s in ranked)))
        elif i > 0:
            # rotación diaria con BANDA: mantener held dentro de top-exit_rank; rellenar huecos
            ranked = sorted(universe, key=lambda s: rmom(P, R, s, day, lb), reverse=True)
            rank = {s: k + 1 for k, s in enumerate(ranked)}
            held = [s for s in w if s != "CASH" and w[s] > 0.01]
            keep = [s for s in held if rank.get(s, 999) <= exit_rank]
            has_cash = w.get("CASH", 0) > 1e-6
            need = topn - len(keep)
            if need > 0 or has_cash or len(keep) != len(held):
                elig = [s for s in ranked if s not in keep and quar.get(s, -1) <= i]
                adds = elig[:max(0, topn - len(keep))]
                newset = keep + adds
                if set(newset) != set(held) or has_cash:
                    dr = rebalance_to(newset, i, dr)
        if i > 0: rets[day] = dr

    return pd.Series(rets), (np.mean(secs) if secs else 0), n_reb, n_stops


def main():
    print("Cargando panel…"); P = load_panel(); R = P.pct_change()
    yrs = len(R) / 252
    print(f"Universo: {len([s for s in UNIV if s in P])}/{len(UNIV)} acciones · {yrs:.1f} años\n")

    print("="*90 + "\nBARRIDO DE BANDA (histéresis de rango) · diario · trailing 20% · top-5\n" + "="*90)
    print(f"{'config':32}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}{'Calmar':>8}{'sect':>6}{'reb/año':>9}{'stops':>7}")
    res = {}
    for er in (5, 6, 7, 8, 10, 12, 15):
        tag = f"exit_rank {er}" + (" (robot HOY)" if er == 5 else "")
        r, sec, nreb, ns = run_hyst(P, R, exit_rank=er)
        res[tag] = r
        c, sh, dd = metrics(r); cal = c/abs(dd) if dd < 0 else float('nan')
        print(f"{tag:32}{c*100:>7.1f}%{sh:>8.2f}{dd*100:>7.1f}%{cal:>8.2f}{sec:>6.1f}{nreb/yrs:>9.0f}{ns:>7}")

    # sobre el mejor rango razonable, ver si la cuarentena aporta algo extra
    print("\n" + "="*90 + "\nCUARENTENA sobre banda exit_rank=10\n" + "="*90)
    print(f"{'config':32}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}{'Calmar':>8}{'reb/año':>9}")
    for q in (0, 2, 5):
        r, sec, nreb, ns = run_hyst(P, R, exit_rank=10, quar_days=q)
        res[f"exit10 · cuar {q}d"] = r
        c, sh, dd = metrics(r); cal = c/abs(dd) if dd < 0 else float('nan')
        print(f"{'exit10 · cuar '+str(q)+'d':32}{c*100:>7.1f}%{sh:>8.2f}{dd*100:>7.1f}%{cal:>8.2f}{nreb/yrs:>9.0f}")

    print("\n" + "="*90 + "\nEN CADA CRASH (maxDD en la ventana)\n" + "="*90)
    print(f"{'config':32}" + "".join(f"{k:>15}" for k in CRASHES))
    for n, r in res.items():
        cells = []
        for k, (a, b) in CRASHES.items():
            seg = r.loc[a:b].dropna()
            dd = ((1+seg).cumprod()/(1+seg).cumprod().cummax()-1).min() if len(seg) > 1 else float('nan')
            cells.append(f"{dd*100:>14.1f}%")
        print(f"{n:32}" + "".join(cells))

    print("\nObjetivo: recuperar el perfil mensual (~36%/−30%/Calmar~1.2) bajando reb/año lo más posible.")

if __name__ == "__main__":
    main()
