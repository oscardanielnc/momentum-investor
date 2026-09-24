"""
Backtest v10: the decisive test. POINT-IN-TIME universe (historical S&P 500 members, no
survivorship bias) with mechanics faithful to the live robot:

  - decisions use data up to YESTERDAY's close, execution at TODAY's open (no look-ahead)
  - 20% INTRADAY trailing stop (high-water mark from highs, triggered by the low; on a gap
    through the stop the fill is the open)
  - every executed rebalance re-places the stops, so the high-water mark RESETS (as in the robot)
  - monthly = reset to top 5 (5% drift band) · daily = top-EXIT_RANK hysteresis · 4 per sector
  - a member that leaves the index or stops trading drops out of the ranking and is rotated
    (or liquidated at its last price if it disappears while held)
  - costs: one-way bps on traded notional (base 10, stress 25)

Prices are Databento price return (no dividends), for the strategy and for the SPY/QQQ
benchmarks alike.

Questions answered (one printed block each):
  A. Does the strategy survive in an honest universe? (the robot's exact config)
  B. How much of the "validated" result came from the 36-name universe chosen in 2026?
     (same engine, the 36 names)
  C. Passive SPY/QQQ benchmarks over the same window.
  D. Sensitivity to exit_rank / trailing / lookback / topN (robustness, not cherry-picking).
  E. Walk-forward: choose parameters on 2018-22 and evaluate 2023-26 in the honest universe.
  F. Cost stress, per-year returns, crisis windows, worst months (momentum crash).

Usage:  python research/backtest_v10_pit.py            # A-D + F
        python research/backtest_v10_pit.py --full     # + D2 sensitivity grid and E walk-forward (slow)
"""
import os, sys
import numpy as np, pandas as pd

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pit_universe as PU

# ── Robot parameters (engine/allocator.py + orchestrator.py); NOT re-tuned here ─────────────
LB, VOLSHORT   = 90, 20
TOPN           = 5
MAX_PER_SECTOR = 4
TRAIL          = 0.20
EXIT_RANK      = 12
REBAL_BAND     = 0.05
COST_BPS       = 10 / 1e4


# ── Metrics ───────────────────────────────────────────────────────────────────────────────────
def metrics(eq):
    """CAGR, max drawdown, Sharpe (rf=0) and Calmar from an equity curve (calendar-day CAGR)."""
    eq = eq.dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    r = eq.pct_change().dropna()
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0
    return {"CAGR": cagr, "maxDD": dd, "Sharpe": sharpe, "Calmar": cagr / abs(dd) if dd else np.nan}


def fmt(name, m, extra=""):
    return (f"{name:34s} CAGR {m['CAGR']*100:6.1f}% · maxDD {m['maxDD']*100:6.1f}% · "
            f"Sharpe {m['Sharpe']:4.2f} · Calmar {m['Calmar']:5.2f}{extra}")


# ── Signal (same as engine/allocator._riskadj_mom, vectorized) ───────────────────────────────
def score_panel(C, lb=LB):
    """Risk-adjusted momentum for every ticker and date; NaN until lb+5 valid closes exist."""
    ret = C.pct_change()
    mom = (C / C.shift(lb) - 1) * (252 / lb)
    vol = ret.rolling(VOLSHORT).std() * np.sqrt(252)
    score = mom / vol
    # require a REAL minimum history (LB+5 valid points), as the allocator does
    enough = C.notna().rolling(lb + 5).sum() >= (lb + 4)
    return score.where(enough & (vol > 0))


def pick_capped(order, sectors, topn, cap, keep=()):
    """Top-N respecting the sector cap; names in `keep` go in first (hysteresis)."""
    top, cnt = [], {}
    for s in keep:
        sec = sectors.get(s, s)
        top.append(s); cnt[sec] = cnt.get(sec, 0) + 1
    for s in order:
        if len(top) >= topn:
            break
        sec = sectors.get(s, s)
        if s in top or cnt.get(sec, 0) >= cap:
            continue
        top.append(s); cnt[sec] = cnt.get(sec, 0) + 1
    return top[:topn]


# ── Simulation engine (faithful to the orchestrator) ─────────────────────────────────────────
def run(O, H, L, C, membership=None, universe=None, topn=TOPN, trail=TRAIL, lb=LB,
        exit_rank=EXIT_RANK, cap=MAX_PER_SECTOR, cost=COST_BPS, start="2018-10-01",
        sectors=PU.SECTOR):
    """Simulate the robot from `start`.

    membership: a PU.Membership (point-in-time), or None together with a fixed `universe` list.
    Returns (equity_series, stats) where stats has stops, rebalances and turnover per year.
    """
    S = score_panel(C, lb)
    dates = C.index
    i0 = dates.searchsorted(pd.Timestamp(start))
    cash, shares, hwm = 1.0, {}, {}
    eq_hist, n_stops, n_rebs, turnover = [], 0, 0, 0.0
    Cv, Ov, Hv, Lv, Sv = C.values, O.values, H.values, L.values, S.values
    col = {s: j for j, s in enumerate(C.columns)}
    last_px = {}

    def px(mat, i, s, fb=None):
        v = mat[i, col[s]]
        return v if np.isfinite(v) else fb

    for i in range(i0, len(dates)):
        d = dates[i]
        # reference prices for the day (fallback: last known close)
        for s in list(shares):
            p = px(Cv, i, s)
            if p is not None:
                last_px[s] = p
        # ── decision (with data up to YESTERDAY's close, i-1) ──
        srow = Sv[i - 1]
        elig = membership.asof(dates[i - 1]) if membership else universe
        cand = [s for s in elig if s in col and np.isfinite(srow[col[s]])]
        order = sorted(cand, key=lambda s: srow[col[s]], reverse=True)
        rank = {s: k + 1 for k, s in enumerate(order)}
        held = list(shares)
        is_monthly = d.month != dates[i - 1].month
        if is_monthly:
            final = pick_capped(order, sectors, topn, cap)
            same = set(final) == set(held)
            if same and held:
                eq_now = cash + sum(shares[s] * (px(Ov, i, s, last_px.get(s)) or 0) for s in held)
                drift = max(abs(shares[s] * (px(Ov, i, s, last_px.get(s)) or 0) / eq_now - 1 / topn)
                            for s in held) if eq_now > 0 else 1.0
                do_trade = drift >= REBAL_BAND
            else:
                do_trade = True
        else:
            keep = [s for s in held if rank.get(s, 10**9) <= exit_rank]
            final = pick_capped(order, sectors, topn, cap, keep=keep)
            do_trade = set(final) != set(held) or (len(held) < topn and len(final) > len(held))
        if not final:
            do_trade = False

        if do_trade:
            # execute at today's OPEN; re-place stops (hwm = fill), as the robot does
            n_rebs += 1
            eq_now = cash + sum(shares[s] * (px(Ov, i, s, last_px.get(s)) or 0) for s in held)
            tgt_notional = {s: eq_now / len(final) for s in final}
            traded = 0.0
            new_shares = {}
            for s in set(held) | set(final):
                p = px(Ov, i, s, last_px.get(s))
                if p is None or p <= 0:            # dead with no price: the position is worth 0
                    continue
                cur_n = shares.get(s, 0.0) * p
                tgt_n = tgt_notional.get(s, 0.0)
                traded += abs(tgt_n - cur_n)
                if tgt_n > 0:
                    new_shares[s] = tgt_n / p
            cash = eq_now - sum(new_shares[s] * px(Ov, i, s, last_px.get(s)) for s in new_shares) \
                   - traded * cost
            turnover += traded / max(eq_now, 1e-9)
            shares = new_shares
            hwm = {s: px(Ov, i, s, last_px.get(s)) for s in shares}
        else:
            # ── intraday trailing stops (previous hwm; on a gap the fill is the open) ──
            for s in list(shares):
                o_, h_, l_ = px(Ov, i, s), px(Hv, i, s), px(Lv, i, s)
                if o_ is None:                      # no data today: delisted? liquidate at last price
                    c_last = last_px.get(s)
                    j = col[s]
                    alive = np.isfinite(Cv[i:min(i + 5, len(dates)), j]).any()
                    if not alive and c_last:
                        cash += shares[s] * c_last * (1 - cost)
                        turnover += shares[s] * c_last / max(cash, 1e-9)
                        del shares[s]; hwm.pop(s, None); n_stops += 1
                    continue
                stop_px = hwm.get(s, o_) * (1 - trail)
                if l_ is not None and l_ <= stop_px:
                    fill = o_ if o_ <= stop_px else stop_px
                    cash += shares[s] * fill * (1 - cost)
                    del shares[s]; hwm.pop(s, None); n_stops += 1
                elif h_ is not None:
                    hwm[s] = max(hwm.get(s, o_), h_)

        eq = cash + sum(shares[s] * (px(Cv, i, s, last_px.get(s)) or 0) for s in shares)
        eq_hist.append((d, eq))

    eq = pd.Series(dict(eq_hist)).sort_index()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    stats = {"stops/yr": n_stops / yrs, "rebs/yr": n_rebs / yrs, "turnover/yr": turnover / yrs}
    return eq, stats


# ── Report ────────────────────────────────────────────────────────────────────────────────────
def yearly(eq):
    """{year: return} from an equity curve (first to last point of each calendar year)."""
    y = eq.resample("YE").last() / eq.resample("YE").first() - 1
    return {ts.year: v for ts, v in y.items()}


def bench(sym, start, end):
    """Normalized buy-and-hold curve from the Databento price-return cache (data_db/)."""
    df = pd.read_parquet(os.path.join(HERE, "data_db", f"{sym}.parquet"))
    px_ = df["close"]
    px_ = px_[(px_.index >= start) & (px_.index <= end)]
    return px_ / px_.iloc[0]


def main(full=False):
    print("=" * 96)
    print("BACKTEST v10 · POINT-IN-TIME S&P 500 universe · mechanics faithful to the robot · no re-tuning")
    print("=" * 96)
    O, H, L, C = PU.load_ohlc()
    M = PU.Membership()
    START = "2018-10-01"

    print("\n── A. The robot's EXACT config in the honest (PIT) universe " + "─" * 35)
    eqA, stA = run(O, H, L, C, membership=M, start=START)
    mA = metrics(eqA)
    print(fmt("PIT top-5 (robot config)", mA,
              f" · stops/yr {stA['stops/yr']:.0f} · rebs/yr {stA['rebs/yr']:.0f}"))
    eqA_oos = eqA[eqA.index >= "2023-01-01"]; eqA_oos = eqA_oos / eqA_oos.iloc[0]
    print(fmt("PIT top-5 · OOS 2023-26", metrics(eqA_oos)))

    print("\n── B. SAME engine, the 36 '2026 leaders' universe (quantifies the bias) " + "─" * 23)
    U36 = [s for s in
           ("MU INTC NVDA AMD WDC STX MRVL TXN AVGO AMAT LRCX QCOM ADI MSFT ORCL CRM NOW ADBE "
            "XOM CVX COP SLB LLY UNH JNJ ABBV JPM GS V MA AMZN TSLA COST HD GOOGL NFLX").split()
           if s in C.columns]
    sec36 = {**{s: "semis" for s in "MU INTC NVDA AMD WDC STX MRVL TXN AVGO AMAT LRCX QCOM ADI".split()},
             **{s: "software" for s in "MSFT ORCL CRM NOW ADBE".split()},
             **{s: "energy" for s in "XOM CVX COP SLB".split()},
             **{s: "health" for s in "LLY UNH JNJ ABBV".split()},
             **{s: "financials" for s in "JPM GS V MA".split()},
             **{s: "consumer" for s in "AMZN TSLA COST HD".split()},
             **{s: "comm" for s in "GOOGL NFLX".split()}}
    eqB, stB = run(O, H, L, C, membership=None, universe=U36, start=START, sectors=sec36)
    mB = metrics(eqB)
    print(fmt("36 hand-picked (same engine)", mB,
              f" · stops/yr {stB['stops/yr']:.0f} · rebs/yr {stB['rebs/yr']:.0f}"))
    print(f"   -> premium of the universe chosen in 2026: {(mB['CAGR']-mA['CAGR'])*100:+.1f} pp of CAGR")

    print("\n── C. Passive benchmarks (same window, price return like everything else) " + "─" * 19)
    for s in ("SPY", "QQQ"):
        b = bench(s, eqA.index[0], eqA.index[-1])
        print(fmt(f"{s} buy&hold", metrics(b)))
    for s in ("SPY", "QQQ"):
        b = bench(s, "2023-01-01", eqA.index[-1])
        print(fmt(f"{s} buy&hold · OOS 2023-26", metrics(b)))

    print("\n── F1. Returns by year (PIT robot config vs SPY/QQQ) " + "─" * 42)
    yA = yearly(eqA); yS = yearly(bench("SPY", eqA.index[0], eqA.index[-1]))
    yQ = yearly(bench("QQQ", eqA.index[0], eqA.index[-1]))
    print("year  " + "".join(f"{y:>9}" for y in yA))
    print("PIT   " + "".join(f"{yA[y]*100:8.1f}%" for y in yA))
    print("SPY   " + "".join(f"{yS.get(y,float('nan'))*100:8.1f}%" for y in yA))
    print("QQQ   " + "".join(f"{yQ.get(y,float('nan'))*100:8.1f}%" for y in yA))

    print("\n── F2. Crisis windows (PIT robot config) " + "─" * 54)
    for name, a, b in (("Q4-2018", "2018-10-01", "2018-12-31"),
                       ("COVID Feb-Apr 2020", "2020-02-19", "2020-04-30"),
                       ("Bear 2022", "2022-01-01", "2022-12-31")):
        seg = eqA[(eqA.index >= a) & (eqA.index <= b)]
        if len(seg) > 5:
            tot = seg.iloc[-1] / seg.iloc[0] - 1
            ddm = (seg / seg.cummax() - 1).min()
            sS = bench("SPY", a, b); sQ = bench("QQQ", a, b)
            print(f"  {name:20s} PIT {tot*100:+6.1f}% (dd {ddm*100:.1f}%) · "
                  f"SPY {(sS.iloc[-1]-1)*100:+6.1f}% · QQQ {(sQ.iloc[-1]-1)*100:+6.1f}%")

    print("\n── F3. Worst 5 months (momentum crash check) " + "─" * 50)
    mo = eqA.resample("ME").last().pct_change().dropna().sort_values()
    for ts, v in mo.head(5).items():
        print(f"  {ts.strftime('%Y-%m')}: {v*100:+.1f}%")

    print("\n── F4. Cost stress (PIT robot config) " + "─" * 57)
    for bps in (10, 25):
        eqX, _ = run(O, H, L, C, membership=M, start=START, cost=bps / 1e4)
        print(fmt(f"costs {bps} bps one-way", metrics(eqX)))

    print("\n── D. exit_rank sensitivity (PIT) " + "─" * 61)
    for er in (5, 10, 12, 15):
        eqX, stX = run(O, H, L, C, membership=M, start=START, exit_rank=er)
        print(fmt(f"exit_rank={er}", metrics(eqX), f" · rebs/yr {stX['rebs/yr']:.0f}"))

    if full:
        print("\n── D2. Robustness grid lb x trail x topN (PIT, full) " + "─" * 41)
        rows = []
        for lb_ in (63, 90, 120):
            for tr in (0.15, 0.20, 0.25, 9.99):
                for tn in (3, 5, 8):
                    eqX, _ = run(O, H, L, C, membership=M, start=START, lb=lb_, trail=tr, topn=tn)
                    m = metrics(eqX)
                    rows.append({"lb": lb_, "trail": tr if tr < 9 else None, "topN": tn, **m})
                    print(fmt(f"lb={lb_} trail={tr if tr<9 else '-'} topN={tn}", m))
        pd.DataFrame(rows).to_csv(os.path.join(HERE, "data_pit", "v10_grid.csv"), index=False)

        print("\n── E. Honest walk-forward: choose on 2018-22, evaluate 2023-26 (PIT) " + "─" * 26)
        best, best_m = None, None
        for lb_ in (63, 90, 120):
            for tr in (0.15, 0.20, 0.25):
                for tn in (3, 5, 8):
                    eqX, _ = run(O, H, L, C.loc[:"2022-12-31"], membership=M, start=START,
                                 lb=lb_, trail=tr, topn=tn)
                    m = metrics(eqX)
                    if best_m is None or m["Calmar"] > best_m["Calmar"]:
                        best, best_m = (lb_, tr, tn), m
        print(f"  best IS 2018-22 (by Calmar): lb={best[0]} trail={best[1]} topN={best[2]} -> "
              + fmt("", best_m))
        O2, H2, L2, C2 = (X.loc["2022-06-01":] for X in (O, H, L, C))
        eqO, _ = run(O2, H2, L2, C2, membership=M, start="2023-01-02",
                     lb=best[0], trail=best[1], topn=best[2])
        print(fmt("  IS params applied OOS 2023-26", metrics(eqO)))
        eqR, _ = run(O2, H2, L2, C2, membership=M, start="2023-01-02")
        print(fmt("  robot config OOS 2023-26", metrics(eqR)))

    eqA.to_frame("equity").to_parquet(os.path.join(HERE, "data_pit", "v10_equity_pit.parquet"))
    print("\nPIT equity saved to data_pit/v10_equity_pit.parquet")


if __name__ == "__main__":
    main(full="--full" in sys.argv)
