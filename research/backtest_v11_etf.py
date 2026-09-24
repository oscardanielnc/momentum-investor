"""
Backtest v11: is there anything that beats QQQ buy and hold?

Every family tested is a RULE over a menu fixed in advance; nothing is picked because of its
past performance (that was the mistake behind v1-v9).

  0. Descriptive: buy and hold of the whole menu (China/Asia included), to see the landscape,
     with the caveat that picking the winner from this table would be hindsight.
  1. Regime overlays on QQQ: daily SMA200 (with an anti-whipsaw band), SMA200 evaluated
     monthly (Faber), 12-month absolute momentum vs cash. Windows 1999+ (dot-com), 2007+
     (GFC), 2018-10+ (comparable with v10).
  2. Leveraged ETFs: QLD/TQQQ buy and hold vs with the SMA200 filter (signal on QQQ, the
     leveraged ETF is traded).
  3. SECTOR rotation: all 11 SPDR sectors, monthly top-K by momentum (12-1 / 6m / the robot's
     riskadj90), with and without an absolute gate (to cash), with rank hysteresis.
  4. COUNTRY/global rotation: a full menu of regions + SPY/QQQ, monthly top-K.
  5. Dual momentum (aggressive GEM): QQQ vs EFA vs cash, 12 months.
  6. Temporal validation: the best rule of each family is chosen ONLY with data up to 2015
     and evaluated on 2016-2026 (walk-forward of rules, not of parameters).
  7. Core + satellite combos sized against a -30% drawdown cap.

Data: yfinance auto_adjust=True, i.e. TOTAL RETURN (dividends included, unlike v10).
Execution: signal at the close, traded at the next day's open. Costs 10 bps one-way.
Cash: BIL (T-bills), SHY before BIL existed, 0% before 2002.

Usage:  python research/backtest_v11_etf.py
"""
import os, sys
import numpy as np, pandas as pd

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
HERE  = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data_etf_long")
os.makedirs(CACHE, exist_ok=True)

MENU_ALL = ("QQQ SPY DIA IWM MDY XLK XLY XLP XLE XLF XLV XLI XLB XLU XLRE XLC SMH IGV XBI "
            "EFA EEM EWJ EWY EWT EWZ FXI ILF MCHI KWEB ASHR INDA TLT IEF SHY GLD BIL "
            "MTUM SPMO VUG IWY QUAL USMV QLD SSO TQQQ").split()
SECTORS   = "XLK XLY XLP XLE XLF XLV XLI XLB XLU XLRE XLC".split()
COUNTRIES = "SPY QQQ EFA EEM EWJ EWY EWT EWZ FXI ILF MCHI INDA KWEB ASHR".split()
COST = 10 / 1e4


# ── Data ──────────────────────────────────────────────────────────────────────────────────────
def load():
    """(Open, Close) total-return panels for MENU_ALL since 1998-11, cached in data_etf_long/."""
    po, pc = os.path.join(CACHE, "open.parquet"), os.path.join(CACHE, "close.parquet")
    if os.path.exists(pc):
        return pd.read_parquet(po), pd.read_parquet(pc)
    import yfinance as yf
    df = yf.download(" ".join(MENU_ALL), start="1998-11-01", auto_adjust=True, progress=False)
    O, C = df["Open"], df["Close"]
    O.to_parquet(po); C.to_parquet(pc)
    return O, C


def cash_ret(C):
    """Daily return of 'cash': BIL, else SHY, else 0."""
    r = pd.Series(0.0, index=C.index)
    for proxy in ("SHY", "BIL"):        # BIL overrides SHY where it exists
        if proxy in C:
            rp = C[proxy].pct_change()
            r[rp.notna()] = rp[rp.notna()]
    return r.fillna(0.0)


def metrics(eq):
    """CAGR, max drawdown, Sharpe (rf=0) and Calmar from an equity curve (calendar-day CAGR)."""
    eq = eq.dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    if yrs <= 0 or eq.iloc[0] <= 0:
        return {"CAGR": np.nan, "maxDD": np.nan, "Sharpe": np.nan, "Calmar": np.nan}
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    r = eq.pct_change().dropna()
    sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0
    return {"CAGR": cagr, "maxDD": dd, "Sharpe": sh, "Calmar": cagr / abs(dd) if dd else np.nan}


def fmt(name, m, qqq=None):
    s = (f"{name:42s} CAGR {m['CAGR']*100:6.1f}% · maxDD {m['maxDD']*100:6.1f}% · "
         f"Sharpe {m['Sharpe']:5.2f} · Calmar {m['Calmar']:5.2f}")
    if qqq:
        s += f" · vsQQQ {(m['CAGR']-qqq['CAGR'])*100:+5.1f}pp"
    return s


def bh(C, sym, a, b):
    """Normalized buy-and-hold curve of `sym` between dates a and b."""
    px = C[sym].dropna()
    px = px[(px.index >= a) & (px.index <= b)]
    return px / px.iloc[0]


# ── 1. Single-asset overlays ─────────────────────────────────────────────────────────────────
def overlay(O, C, sym, rule, a, b, cash, signal_sym=None, band=0.0, monthly=False):
    """Hold `sym` when the rule is ON, cash when OFF. Returns the equity curve.

    rule: 'sma200' | 'absmom12'. Signal on the close of t, executed at the open of t+1.
    signal_sym: asset that generates the signal (e.g. QQQ) when trading another (e.g. QLD).
    band: hysteresis around the SMA (0.01 = must cross 1% above/below to switch).
    monthly: only re-evaluate on the last trading day of each month.
    """
    sig_px = C[signal_sym or sym].dropna()
    px_c = C[sym].dropna()
    idx = px_c.index.intersection(sig_px.index)
    idx = idx[(idx >= a) & (idx <= b)]
    px_c, px_o = C[sym].reindex(idx), O[sym].reindex(idx)
    sig_px = sig_px.reindex(idx)
    if rule == "sma200":
        sma = sig_px.rolling(200).mean()
        raw_on = sig_px > sma * (1 + band)
        raw_off = sig_px < sma * (1 - band)
        on = pd.Series(np.where(raw_on, 1.0, np.where(raw_off, 0.0, np.nan)), index=idx)
        on = on.ffill().fillna(1.0).astype(bool)
    else:  # absmom12: 12-month return of the signal asset > 12-month return of cash
        r12 = sig_px / sig_px.shift(252) - 1
        cash_idx = (1 + cash.reindex(idx).fillna(0)).cumprod()
        c12 = cash_idx / cash_idx.shift(252) - 1
        on = (r12 > c12).fillna(True)
    if monthly:                          # decide only on the last day of the month
        me = on.groupby([idx.year, idx.month]).tail(1).index
        on = on.astype(float).where(on.index.isin(me)).ffill().fillna(1.0).astype(bool)
    ret_c = px_c.pct_change().fillna(0)
    ret_co = (px_o / px_c.shift(1) - 1).fillna(0)   # close t-1 -> open t
    ret_oc = (px_c / px_o - 1).fillna(0)            # open t -> close t
    cash_r = cash.reindex(idx).fillna(0)
    pos_prev = on.shift(1).fillna(True)
    pos_prev2 = on.shift(2).fillna(True)
    out = []
    eq = 1.0
    for i in range(len(idx)):
        p_now, p_prev = pos_prev.iloc[i], pos_prev2.iloc[i]
        if p_now == p_prev:              # no change: the whole day in the current state
            eq *= (1 + ret_c.iloc[i]) if p_now else (1 + cash_r.iloc[i])
        elif p_now and not p_prev:       # enters at today's open
            eq *= (1 + ret_oc.iloc[i]); eq *= (1 - COST)
        else:                            # exits at today's open
            eq *= (1 + ret_co.iloc[i]); eq *= (1 - COST)
            eq *= (1 + cash_r.iloc[i] * 0.5)
        out.append(eq)
    return pd.Series(out, index=idx)


# ── 3/4. Monthly top-K rotation over a menu ──────────────────────────────────────────────────
def rotation(O, C, menu, a, b, k=2, score="12-1", gate=False, hyst=0, cash=None):
    """Monthly: rank the menu at the last close of the month; execute at the next open.

    score: '12-1' | '6m' | 'riskadj90'. hyst: keep a held ETF while it stays in the top k+hyst.
    gate: a slot whose 12-month return is below cash goes to cash. Returns the equity curve.
    """
    cols = [s for s in menu if s in C.columns]
    Cm = C[cols]
    idx = Cm.dropna(how="all").index
    idx = idx[(idx >= a) & (idx <= b)]
    Cm, Om = Cm.reindex(idx), O[cols].reindex(idx)
    if score == "12-1":
        S = Cm.shift(21) / Cm.shift(252) - 1
    elif score == "6m":
        S = Cm / Cm.shift(126) - 1
    else:  # riskadj90 (the robot's signal)
        S = ((Cm / Cm.shift(90) - 1) * (252 / 90)) / (Cm.pct_change().rolling(20).std() * np.sqrt(252))
    cash_r = (cash if cash is not None else pd.Series(0.0, index=idx)).reindex(idx).fillna(0)
    cash_idx = (1 + cash_r).cumprod()
    r12_all = Cm / Cm.shift(252) - 1
    c12 = cash_idx / cash_idx.shift(252) - 1
    month_end = pd.Series(idx, index=idx).groupby([idx.year, idx.month]).tail(1)
    me_set = set(month_end)
    held, eq, out = [], 1.0, []
    pending = None
    for i, d in enumerate(idx):
        if pending is not None and i > 0:
            # execute at today's open: open->close return for the new holdings, cost by turnover
            new = pending; pending = None
            turn = len(set(new) ^ set(held)) / max(k, 1)
            day_r = 0.0
            for s in new:
                oc = Cm[s].iloc[i] / Om[s].iloc[i] - 1 if np.isfinite(Om[s].iloc[i]) else 0.0
                day_r += oc / max(len(new), 1)
            n_cash = k - len(new)
            day_r = day_r * (len(new) / k) + cash_r.iloc[i] * (n_cash / k)
            # the old holdings were sold at the open: close t-1 -> open t
            carry = 0.0
            for s in held:
                co = Om[s].iloc[i] / Cm[s].iloc[i - 1] - 1 if np.isfinite(Om[s].iloc[i]) else 0.0
                carry += co / max(len(held), 1)
            if held:
                eq *= (1 + carry * (len(held) / k) + cash_r.iloc[i] * ((k - len(held)) / k))
            eq *= (1 - COST * turn)
            eq *= (1 + day_r)
            held = new
        else:
            if held:
                day = sum(Cm[s].pct_change().iloc[i] if np.isfinite(Cm[s].pct_change().iloc[i]) else 0.0
                          for s in held) / max(len(held), 1)
                eq *= (1 + day * (len(held) / k) + cash_r.iloc[i] * ((k - len(held)) / k))
            else:
                eq *= (1 + cash_r.iloc[i])
        if d in me_set and i + 1 < len(idx):
            srow = S.iloc[i]
            elig = [s for s in cols if np.isfinite(srow[s])]
            order = sorted(elig, key=lambda s: srow[s], reverse=True)
            rank = {s: j + 1 for j, s in enumerate(order)}
            keep = [s for s in held if rank.get(s, 999) <= k + hyst]
            new = list(keep)
            for s in order:
                if len(new) >= k:
                    break
                if s not in new:
                    new.append(s)
            if gate:
                new = [s for s in new
                       if np.isfinite(r12_all[s].iloc[i]) and r12_all[s].iloc[i] > c12.iloc[i]]
            if set(new) != set(held):
                pending = new
        out.append(eq)
    return pd.Series(out, index=idx)


# ── Report ────────────────────────────────────────────────────────────────────────────────────
def main():
    O, C = load()
    cash = cash_ret(C)
    W = [("1999-2026 (dot-com+GFC)", "1999-03-10", "2026-07-01"),
         ("2007-2026 (GFC)",         "2007-06-01", "2026-07-01"),
         ("2018-10 -> 2026 (=v10)",  "2018-10-01", "2026-07-01")]

    print("=" * 100)
    print("v11 · WHAT BEATS QQQ BUY & HOLD? · total return · signal at close -> next open · 10bps")
    print("=" * 100)

    print("\n── 0. Buy & hold LANDSCAPE of the menu (2018-10 -> 2026; picking the winner here = hindsight) ──")
    rows = []
    for s in MENU_ALL:
        if s in C.columns and C[s].loc["2018-10-01":"2026-07-01"].notna().sum() > 1800:
            rows.append((s, metrics(bh(C, s, "2018-10-01", "2026-07-01"))))
    rows.sort(key=lambda x: -(x[1]["CAGR"] or -9))
    for s, m in rows:
        print(fmt(f"  {s} B&H", m))

    for wname, a, b in W:
        qqq = metrics(bh(C, "QQQ", a, b))
        print(f"\n── 1. OVERLAYS on QQQ · window {wname} " + "─" * 40)
        print(fmt("QQQ buy&hold", qqq))
        for name, kw in [
            ("QQQ + daily SMA200", dict(rule="sma200")),
            ("QQQ + daily SMA200 1% band", dict(rule="sma200", band=0.01)),
            ("QQQ + SMA200 monthly eval (Faber)", dict(rule="sma200", monthly=True)),
            ("QQQ + 12m absolute momentum (monthly)", dict(rule="absmom12", monthly=True)),
        ]:
            eq = overlay(O, C, "QQQ", a=a, b=b, cash=cash, **kw)
            print(fmt(name, metrics(eq), qqq))

    print("\n── 2. LEVERAGED (the structural route to 'more than QQQ') " + "─" * 38)
    for sym, a0 in (("QLD", "2006-07-01"), ("TQQQ", "2010-03-01")):
        for wname, a, b in W:
            a = max(a, a0)
            qqq = metrics(bh(C, "QQQ", a, b))
            print(f"  · {sym} from {a[:7]} ({wname}):")
            print(fmt(f"    {sym} B&H", metrics(bh(C, sym, a, b)), qqq))
            eq = overlay(O, C, sym, rule="sma200", a=a, b=b, cash=cash, signal_sym="QQQ", band=0.01)
            print(fmt(f"    {sym} + SMA200(QQQ) 1% band", metrics(eq), qqq))

    print("\n── 3. SECTOR ROTATION (11 SPDR, monthly) " + "─" * 55)
    for wname, a, b in W:
        qqq = metrics(bh(C, "QQQ", a, b))
        spy = metrics(bh(C, "SPY", a, b))
        print(f"  window {wname}  (SPY {spy['CAGR']*100:.1f}% / QQQ {qqq['CAGR']*100:.1f}%)")
        for name, kw in [
            ("top-1 12-1", dict(k=1, score="12-1")),
            ("top-2 12-1", dict(k=2, score="12-1")),
            ("top-3 12-1", dict(k=3, score="12-1")),
            ("top-2 12-1 + cash gate", dict(k=2, score="12-1", gate=True)),
            ("top-2 12-1 + hysteresis 2", dict(k=2, score="12-1", hyst=2)),
            ("top-2 riskadj90 (robot signal)", dict(k=2, score="riskadj90")),
            ("top-2 6m", dict(k=2, score="6m")),
        ]:
            eq = rotation(O, C, SECTORS, a, b, cash=cash, **kw)
            print(fmt(f"    sectors {name}", metrics(eq), qqq))

    print("\n── 4. COUNTRY/GLOBAL ROTATION (China/Asia included, monthly) " + "─" * 36)
    for wname, a, b in [W[1], W[2]]:
        qqq = metrics(bh(C, "QQQ", a, b))
        print(f"  window {wname}")
        for name, kw in [
            ("top-1 12-1", dict(k=1, score="12-1")),
            ("top-2 12-1", dict(k=2, score="12-1")),
            ("top-2 12-1 + cash gate", dict(k=2, score="12-1", gate=True)),
            ("top-2 riskadj90", dict(k=2, score="riskadj90")),
        ]:
            eq = rotation(O, C, COUNTRIES, a, b, cash=cash, **kw)
            print(fmt(f"    countries {name}", metrics(eq), qqq))

    print("\n── 5. DUAL MOMENTUM (aggressive GEM: QQQ/EFA/cash, 12m, monthly) " + "─" * 32)
    for wname, a, b in W:
        qqq = metrics(bh(C, "QQQ", a, b))
        eq = rotation(O, C, ["QQQ", "EFA"], a, b, k=1, score="12-1", gate=True, cash=cash)
        print(fmt(f"  GEM {wname}", metrics(eq), qqq))

    print("\n── 6. TEMPORAL VALIDATION: chosen with data <= 2015, evaluated 2016-2026 " + "─" * 25)
    fams = {
        "overlay": [("SMA200 daily b1%", lambda a, b: overlay(O, C, "QQQ", "sma200", a, b, cash, band=0.01)),
                    ("SMA200 monthly", lambda a, b: overlay(O, C, "QQQ", "sma200", a, b, cash, monthly=True)),
                    ("absmom12", lambda a, b: overlay(O, C, "QQQ", "absmom12", a, b, cash, monthly=True))],
        "sectors": [(f"top-{k} {sc}" + ("+gate" if g else ""),
                     (lambda k=k, sc=sc, g=g: lambda a, b: rotation(O, C, SECTORS, a, b, k=k, score=sc, gate=g, cash=cash))())
                    for k in (1, 2, 3) for sc in ("12-1", "6m") for g in (False, True)],
        "countries": [(f"top-{k} 12-1" + ("+gate" if g else ""),
                       (lambda k=k, g=g: lambda a, b: rotation(O, C, COUNTRIES, a, b, k=k, score="12-1", gate=g, cash=cash))())
                      for k in (1, 2) for g in (False, True)],
    }
    for fam, variants in fams.items():
        best, bm = None, None
        for name, f in variants:
            m = metrics(f("1999-03-10", "2015-12-31"))
            if bm is None or (m["Calmar"] or -9) > (bm["Calmar"] or -9):
                best, bm = (name, f), m
        m_oos = metrics(best[1]("2016-01-01", "2026-07-01"))
        qqq_oos = metrics(bh(C, "QQQ", "2016-01-01", "2026-07-01"))
        print(f"  {fam:9s} best <=2015: {best[0]:22s} (IS Calmar {bm['Calmar']:.2f}) -> OOS 2016-26: "
              + fmt("", m_oos, qqq_oos).strip())
    print(fmt("  QQQ B&H 2016-26 (the bar)", metrics(bh(C, "QQQ", "2016-01-01", "2026-07-01"))))

    print("\n── 7. COMBO sized to a -30% cap: filtered QQQ core + filtered leveraged satellite ──")
    print("   (TQQQ starts in 2010: its rows do NOT include 2008; a 3x fund in 2008 would have lost 90%+.")
    print("    For a serious -30% cap the honest satellite is QLD (2x), which did live through the GFC.)")

    def combo(lam, lev, a, b):
        core = overlay(O, C, "QQQ", "sma200", a, b, cash, band=0.01)
        sat = overlay(O, C, lev, "sma200", a, b, cash, signal_sym="QQQ", band=0.01)
        idx = core.index.intersection(sat.index)
        r = ((1 - lam) * core.reindex(idx).pct_change().fillna(0)
             + lam * sat.reindex(idx).pct_change().fillna(0))
        return (1 + r).cumprod()

    for wname, a, b, levs in [("2006-07 -> 2026 (GFC included)", "2006-07-01", "2026-07-01", ["QLD"]),
                              ("2010-03 -> 2026 (no GFC)", "2010-03-01", "2026-07-01", ["QLD", "TQQQ"]),
                              ("2018-10 -> 2026 (=v10)", "2018-10-01", "2026-07-01", ["QLD", "TQQQ"])]:
        qqq = metrics(bh(C, "QQQ", a, b))
        print(f"  window {wname} · " + fmt("QQQ B&H", qqq).strip())
        print("  " + fmt("core QQQ+SMA200 b1% only", metrics(combo(0.0, "QLD", a, b)), qqq))
        for lev in levs:
            for lam in (0.2, 0.3, 0.5):
                print("  " + fmt(f"{(1-lam)*100:.0f}% QQQf + {lam*100:.0f}% {lev}f",
                                 metrics(combo(lam, lev, a, b)), qqq))


if __name__ == "__main__":
    main()
