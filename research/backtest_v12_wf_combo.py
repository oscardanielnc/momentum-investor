"""
Backtest v12: ANCHORED walk-forward of the "filtered QQQ core + filtered QLD satellite" combo.

Question: is SMA200 + 1% band + 80/20 a lucky cell or a robust plateau?

Design (against data mining):
  1. GRID: sma_n in {150,175,200,225,250} x band in {0, 0.5%, 1%, 2%} (20 cells).
     The satellite weight (lambda) is NOT optimized: it is the owner's risk dial, and its
     lambda -> drawdown map is reported separately.
  2. ANCHORED WALK-FORWARD (4 folds): train on 1999 -> cutoff, pick the cell by Calmar (signal
     on QQQ alone, so 1999-2006 can be used even though QLD did not exist yet), then trade the
     80/20 combo on the following segment WITHOUT touching it. Cutoffs: 2007 -> trade 2008-11 ·
     2011 -> 2012-15 · 2015 -> 2016-19 · 2019 -> 2020-26. The STITCHED out-of-sample equity is
     the result of an investor who never saw the future.
  3. SENSITIVITY MAP: all 20 cells of the full combo over 2006-2026. If nearly all of them
     beat QQQ buy-and-hold on Calmar, the plateau is real and the exact cell does not matter.
  4. lambda -> (CAGR, maxDD) map, so the satellite weight can be chosen with the -30% cap in view.

Data: yfinance total return (from v11). Costs 10 bps one-way. Signal at the close -> next open.
Usage:  python research/backtest_v12_wf_combo.py
"""
import os, sys
import numpy as np, pandas as pd

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backtest_v11_etf as V

GRID = [(n, b) for n in (150, 175, 200, 225, 250) for b in (0.0, 0.005, 0.01, 0.02)]
LAM = 0.20


def overlay_n(O, C, sym, a, b, cash, sma_n, band, signal_sym=None):
    """Like V.overlay(rule='sma200') with a configurable SMA length and a WARM-UP.

    The SMA is pre-computed with data BEFORE the window; otherwise the first sma_n days have
    no signal and default to long, which would invalidate a fold that starts in 2008.
    Returns (equity curve normalized to 1 at `a`, number of switches inside the window).
    """
    sig_px = C[signal_sym or sym].dropna()
    px_c = C[sym].dropna()
    idx = px_c.index.intersection(sig_px.index)
    a_ext = pd.Timestamp(a) - pd.Timedelta(days=int(sma_n * 2) + 40)
    idx = idx[(idx >= a_ext) & (idx <= b)]
    px_c, px_o = C[sym].reindex(idx), O[sym].reindex(idx)
    sig = sig_px.reindex(idx)
    sma = sig.rolling(sma_n).mean()
    on = pd.Series(np.where(sig > sma * (1 + band), 1.0,
                   np.where(sig < sma * (1 - band), 0.0, np.nan)), index=idx)
    on = on.ffill().fillna(1.0).astype(bool)
    ret_c = px_c.pct_change().fillna(0)
    ret_co = (px_o / px_c.shift(1) - 1).fillna(0)
    ret_oc = (px_c / px_o - 1).fillna(0)
    cash_r = cash.reindex(idx).fillna(0)
    p1, p2 = on.shift(1).fillna(True), on.shift(2).fillna(True)
    in_win = idx >= pd.Timestamp(a)
    mult, flips = np.ones(len(idx)), 0
    for i in range(len(idx)):
        if p1.iloc[i] == p2.iloc[i]:
            mult[i] = (1 + ret_c.iloc[i]) if p1.iloc[i] else (1 + cash_r.iloc[i])
        elif p1.iloc[i]:
            mult[i] = (1 + ret_oc.iloc[i]) * (1 - V.COST)
            flips += in_win[i]
        else:
            mult[i] = (1 + ret_co.iloc[i]) * (1 - V.COST) * (1 + cash_r.iloc[i] * 0.5)
            flips += in_win[i]
    eq = pd.Series(np.cumprod(mult), index=idx)[in_win]
    return eq / eq.iloc[0], int(flips)


def combo(O, C, cash, a, b, sma_n, band, lam=LAM):
    """(1-lam) filtered QQQ + lam filtered QLD, daily-rebalanced mix of the two overlays.
    Returns (equity curve, number of core switches). Falls back to the core alone before QLD existed."""
    core, f1 = overlay_n(O, C, "QQQ", a, b, cash, sma_n, band)
    sat, _ = overlay_n(O, C, "QLD", a, b, cash, sma_n, band, signal_sym="QQQ")
    idx = core.index.intersection(sat.index)
    if len(idx) < 60:                      # QLD did not exist yet: core only
        return core, f1
    r = ((1 - lam) * core.reindex(idx).pct_change().fillna(0)
         + lam * sat.reindex(idx).pct_change().fillna(0))
    return (1 + r).cumprod(), f1


def main():
    O, C = V.load()
    cash = V.cash_ret(C)
    print("=" * 100)
    print(f"v12 · ANCHORED WALK-FORWARD of the {int((1-LAM)*100)}/{int(LAM*100)} QQQf/QLDf combo · "
          "SMA x band grid · total return · 10bps")
    print("=" * 100)

    FOLDS = [("1999-03-10", "2007-12-31", "2008-01-01", "2011-12-31"),
             ("1999-03-10", "2011-12-31", "2012-01-01", "2015-12-31"),
             ("1999-03-10", "2015-12-31", "2016-01-01", "2019-12-31"),
             ("1999-03-10", "2019-12-31", "2020-01-01", "2026-07-01")]

    print("\n── 1. Anchored walk-forward (pick by Calmar of the CORE in training; trade the combo) ──")
    stitched = []
    for tr_a, tr_b, te_a, te_b in FOLDS:
        best, bm = None, None
        for n, bd in GRID:
            eq, _ = overlay_n(O, C, "QQQ", tr_a, tr_b, cash, n, bd)
            m = V.metrics(eq)
            if bm is None or (m["Calmar"] or -9) > (bm["Calmar"] or -9):
                best, bm = (n, bd), m
        eq_te, flips = combo(O, C, cash, te_a, te_b, *best)
        m_te = V.metrics(eq_te)
        qqq_te = V.metrics(V.bh(C, "QQQ", te_a, te_b))
        stitched.append(eq_te.pct_change().fillna(0))
        print(f"  train->{tr_b[:4]}: picks SMA{best[0]} band {best[1]*100:.1f}% (IS Calmar {bm['Calmar']:.2f}) "
              f"-> trades {te_a[:4]}-{te_b[:4]}: "
              f"CAGR {m_te['CAGR']*100:5.1f}% · DD {m_te['maxDD']*100:6.1f}% · Calmar {m_te['Calmar']:.2f} "
              f"(QQQ: {qqq_te['CAGR']*100:.1f}% / {qqq_te['maxDD']*100:.1f}%) · switches {flips}")
    r_all = pd.concat(stitched)
    eq_wf = (1 + r_all).cumprod()
    m_wf = V.metrics(eq_wf)
    qqq_full = V.metrics(V.bh(C, "QQQ", "2008-01-01", "2026-07-01"))
    fix, _ = combo(O, C, cash, "2008-01-01", "2026-07-01", 200, 0.01)
    print("  " + "─" * 92)
    print(V.fmt("  STITCHED OOS 2008-2026 (never saw the future)", m_wf, qqq_full))
    print(V.fmt("  fixed cell SMA200/1% same period", V.metrics(fix), qqq_full))
    print(V.fmt("  QQQ B&H same period", qqq_full))

    print("\n── 2. Sensitivity map: the 20 cells of the combo, 2006-07 -> 2026 (GFC included) ──")
    qqq06 = V.metrics(V.bh(C, "QQQ", "2006-07-01", "2026-07-01"))
    print(f"  QQQ B&H: CAGR {qqq06['CAGR']*100:.1f}% · DD {qqq06['maxDD']*100:.1f}% · Calmar {qqq06['Calmar']:.2f}")
    print("  SMA\\band      0%          0.5%         1%           2%")
    win_cal, win_dd = 0, 0
    for n in (150, 175, 200, 225, 250):
        cells = []
        for bd in (0.0, 0.005, 0.01, 0.02):
            eq, _ = combo(O, C, cash, "2006-07-01", "2026-07-01", n, bd)
            m = V.metrics(eq)
            win_cal += (m["Calmar"] or -9) > qqq06["Calmar"]
            win_dd += (m["maxDD"] or -1) > qqq06["maxDD"]
            cells.append(f"{m['CAGR']*100:4.1f}%/{m['maxDD']*100:3.0f}%/{m['Calmar']:.2f}")
        print(f"  {n:3d}       " + "  ".join(cells))
    print(f"  -> cells beating QQQ's Calmar: {win_cal}/20 · with a smaller drawdown than QQQ: {win_dd}/20")

    print("\n── 3. Risk dial lambda (fixed SMA200/1%): choose the mix with the -30% cap in view ──")
    print("     lambda = % in the filtered QLD satellite · rest = filtered QQQ · window 2006-07 -> 2026 (GFC)")
    for lam in (0.0, 0.10, 0.20, 0.30, 0.40, 0.50):
        eq, _ = combo(O, C, cash, "2006-07-01", "2026-07-01", 200, 0.01, lam=lam)
        m = V.metrics(eq)
        eq18, _ = combo(O, C, cash, "2018-10-01", "2026-07-01", 200, 0.01, lam=lam)
        m18 = V.metrics(eq18)
        flag = " <- -30% cap" if -0.32 < m["maxDD"] <= -0.28 else ""
        print(f"  lambda={lam*100:3.0f}%:  2006+ CAGR {m['CAGR']*100:5.1f}% · DD {m['maxDD']*100:6.1f}% · "
              f"Calmar {m['Calmar']:.2f}   ·   2018+ CAGR {m18['CAGR']*100:5.1f}% · DD {m18['maxDD']*100:6.1f}%{flag}")


if __name__ == "__main__":
    main()
