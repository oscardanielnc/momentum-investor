"""
investor — Backtest v8: CUARENTENA + REINVERSIÓN tras un trailing stop (la idea de Oscar).

Pregunta: cuando un trailing stop corta una posición, ¿qué hacemos con esa caja?
  (a) esperar al próximo rebalanceo mensual (baseline validado v5),
  (b) esperar al siguiente día y reinvertir en OTRO sector,
  (c) reinvertir el MISMO día en el mejor momentum de OTRO sector (el sector cortado cae),
  (d) reinvertir el mismo día en el mejor momentum de CUALQUIER sector.
Y en paralelo: ¿cuántos días de CUARENTENA al nombre cortado antes de poder recomprarlo (0/2/5)?

Overlay sobre el core validado: top-5 momentum multi-sector, equiponderado, rebalanceo mensual,
trailing stop 20%. Costos 10bps por trade (incluye stops y reinversiones intra-mes).

Ancla de validación: reinvest=cash_month + quar=0 debe reproducir el "MULTI top-5 +TS20%" de v5
(CAGR ~36.5%, maxDD ~-30.7%). Si coincide, el harness es correcto.

Uso: python research/backtest_v8_quarantine.py
"""
import sys
import numpy as np, pandas as pd
from db_fetch import load_panel
from backtest_v5_multisector import UNIV, SECTOR, CRASHES, COST, metrics, rmom
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def run_q(P, R, topn=5, trail=0.20, lb=90, reinvest="cash_month", quar_days=0, daily_reb=False):
    """Core top-N + trailing stop, con overlay de cuarentena/reinversión.
      reinvest: 'cash_month' | 'next_day' | 'now_diff' | 'now_any'
      quar_days: días que un nombre cortado queda bloqueado para reentrar (rebalanceo y reemplazo).
      daily_reb: si True, rebalancea también a diario cuando CAMBIA la membresía del top-N
                 (como el robot vivo: recompra al día siguiente salvo cuarentena). El mensual siempre corre.
    """
    rebset = set(P.resample("ME").last().index); days = R.index
    w = {}; peaks = {}; E = 1.0; rets = {}; secs = []
    quar = {}                 # symbol -> índice de día hasta el cual está bloqueado (reentra si i >= quar[s])
    pending = []              # [(weight, cut_sector)] a reinvertir al día siguiente (modo next_day)
    universe = [s for s in UNIV if s in P]
    n_stops = 0

    def price(s, i): return P[s].iloc[i]

    def best_repl(i, day, cut_sector, diff_sector):
        cands = [s for s in universe if s not in w and quar.get(s, -1) <= i
                 and not np.isnan(price(s, i))
                 and (not diff_sector or SECTOR[s] != cut_sector)]
        if not cands: return None
        return max(cands, key=lambda s: rmom(P, R, s, day, lb))

    for i, day in enumerate(days):
        dr = 0.0; day_cost = 0.0
        def _ret(s):
            if s == "CASH": return 0.0
            v = R[s].iloc[i]; return 0.0 if np.isnan(v) else v
        if i > 0 and w:
            gr = sum(w[s] * _ret(s) for s in w); E *= (1 + gr); dr = gr
            nw = {s: w[s] * (1 + _ret(s)) for s in w}
            tot = sum(nw.values()); w = {s: v / tot for s, v in nw.items()} if tot > 0 else w

            # (A) reinvertir pendientes del día anterior (modo next_day)
            if reinvest == "next_day" and pending and w.get("CASH", 0) > 1e-9:
                still = []
                for wt, cutsec in pending:
                    repl = best_repl(i, day, cutsec, diff_sector=True)
                    take = min(wt, w.get("CASH", 0)) if repl is not None else 0.0
                    if take > 0:
                        w["CASH"] -= take; w[repl] = w.get(repl, 0) + take
                        peaks[repl] = price(repl, i); day_cost += take * COST
                        if wt - take > 1e-9: still.append((wt - take, cutsec))
                    elif repl is None:
                        still.append((wt, cutsec))
                if w.get("CASH", 0) <= 1e-9: w.pop("CASH", None)
                pending = still

            # (B) trailing stops → cuarentena + reinversión según modo
            if trail:
                for s in list(w):
                    if s == "CASH": continue
                    peaks[s] = max(peaks.get(s, price(s, i)), price(s, i))
                    if price(s, i) <= peaks[s] * (1 - trail):
                        wt = w.pop(s); cutsec = SECTOR[s]; n_stops += 1
                        quar[s] = i + quar_days                 # bloquea reentrada quar_days
                        day_cost += wt * COST                   # coste de la venta (todos los modos)
                        if reinvest in ("now_diff", "now_any"):
                            repl = best_repl(i, day, cutsec, diff_sector=(reinvest == "now_diff"))
                            if repl is not None:
                                w[repl] = w.get(repl, 0) + wt; peaks[repl] = price(repl, i)
                                day_cost += wt * COST           # coste de la compra
                            else:
                                w["CASH"] = w.get("CASH", 0) + wt
                        elif reinvest == "next_day":
                            w["CASH"] = w.get("CASH", 0) + wt; pending.append((wt, cutsec))
                        else:                                    # cash_month
                            w["CASH"] = w.get("CASH", 0) + wt

            if day_cost:
                E *= (1 - day_cost); dr = (1 + dr) * (1 - day_cost) - 1

        # gatillo diario opcional: rebalancea si CAMBIA la membresía del top-N (como el robot vivo)
        do_reb = day in rebset
        if daily_reb and not do_reb and i > 0:
            top_now = set(sorted([s for s in universe if quar.get(s, -1) <= i],
                                 key=lambda s: rmom(P, R, s, day, lb), reverse=True)[:topn])
            held = {s for s, wv in w.items() if s != "CASH" and wv > 0.01}
            if top_now != held: do_reb = True

        # rebalanceo: top-N excluyendo cuarentenados; redepliega caja
        if do_reb:
            ranked = sorted([s for s in universe if quar.get(s, -1) <= i],
                            key=lambda s: rmom(P, R, s, day, lb), reverse=True)[:topn]
            tw = {s: 1.0 / len(ranked) for s in ranked}
            cost = sum(abs(tw.get(s, 0) - w.get(s, 0)) * COST for s in set(tw) | set(w))
            E *= (1 - cost); dr = (1 + dr) * (1 - cost) - 1
            w = tw; peaks = {s: price(s, i) for s in ranked}; pending = []
            secs.append(len(set(SECTOR[s] for s in ranked)))
        if i > 0: rets[day] = dr

    return pd.Series(rets), (np.mean(secs) if secs else 0), n_stops


REGIME_LABEL = {False: "mensual", True: "DIARIO (robot vivo)"}


def main():
    print("Cargando panel…"); P = load_panel(); R = P.pct_change()
    have = [s for s in UNIV if s in P]
    print(f"Universo: {len(have)}/{len(UNIV)} acciones, {len(set(SECTOR[s] for s in have))} sectores\n")

    CONFIGS = [
        ("baseline: cash→mensual (v5)",      dict(reinvest="cash_month", quar_days=0)),
        ("cash→mensual · cuar 2d",           dict(reinvest="cash_month", quar_days=2)),
        ("next_day otro sector · cuar 0d",   dict(reinvest="next_day",   quar_days=0)),
        ("next_day otro sector · cuar 2d",   dict(reinvest="next_day",   quar_days=2)),
        ("YA otro sector · cuar 0d",         dict(reinvest="now_diff",   quar_days=0)),
        ("YA otro sector · cuar 2d",         dict(reinvest="now_diff",   quar_days=2)),
        ("YA otro sector · cuar 5d",         dict(reinvest="now_diff",   quar_days=5)),
        ("YA cualquier sector · cuar 2d",    dict(reinvest="now_any",    quar_days=2)),
    ]
    # Régimen DIARIO = como opera el robot vivo (recompra al día siguiente salvo cuarentena).
    # Aquí sí se puede medir si la cuarentena frena el whipsaw de recomprar al nombre stopeado.
    CONFIGS += [
        ("[DIARIO] cash · cuar 0d (robot HOY)", dict(reinvest="cash_month", quar_days=0, daily_reb=True)),
        ("[DIARIO] cash · cuar 2d",             dict(reinvest="cash_month", quar_days=2, daily_reb=True)),
        ("[DIARIO] cash · cuar 5d",             dict(reinvest="cash_month", quar_days=5, daily_reb=True)),
        ("[DIARIO] YA otro sector · cuar 2d",   dict(reinvest="now_diff",   quar_days=2, daily_reb=True)),
    ]
    res = {}
    for name, kw in CONFIGS:
        res[name] = run_q(P, R, topn=5, trail=0.20, **kw)

    print("="*84 + "\nGLOBAL 2018+ (costos ON, trailing 20%, top-5)\n" + "="*84)
    print(f"{'config':34}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}{'Calmar':>8}{'sect':>6}{'stops':>7}")
    pool = {}
    for n, (r, sec, ns) in res.items():
        c, sh, dd = metrics(r); cal = c/abs(dd) if dd < 0 else float('nan'); pool[n] = (c, sh, dd, cal)
        print(f"{n:34}{c*100:>7.1f}%{sh:>8.2f}{dd*100:>7.1f}%{cal:>8.2f}{sec:>6.1f}{ns:>7}")

    print("\n" + "="*84 + "\nEN CADA CRASH (maxDD en la ventana)\n" + "="*84)
    print(f"{'config':34}" + "".join(f"{k:>15}" for k in CRASHES))
    for n, (r, _, _) in res.items():
        cells = []
        for k, (a, b) in CRASHES.items():
            seg = r.loc[a:b].dropna()
            dd = ((1+seg).cumprod()/(1+seg).cumprod().cummax()-1).min() if len(seg) > 1 else float('nan')
            cells.append(f"{dd*100:>14.1f}%")
        print(f"{n:34}" + "".join(cells))

    print("\nLectura: comparar contra baseline. Sube Calmar/Sharpe sin empeorar maxDD → la idea vale.")

if __name__ == "__main__":
    main()
