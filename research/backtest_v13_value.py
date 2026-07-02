"""
investor — BACKTEST v13: la idea de Oscar de "calidad castigada" (value contrarian), testeada
honestamente ANTES de ponerle un dólar.

REGLAS PRE-REGISTRADAS (escritas antes de mirar resultados — 02-jul-2026):
  Universo: S&P 500 point-in-time (los 681 con precios de v10).
  SÓLIDA  = EPS TTM > 0 · EPS TTM hace 3 años > 0 · crecimiento EPS 3y ≥ 0 (con lag de
            publicación de 90 días tras el cierre del trimestre — sin lookahead).
  CASTIGADA = cierre ≤ 65% de su máximo de 252 días (−35% desde el pico).
  Cartera: mensual, equiponderada entre TODAS las que califican (sin cherry-pick);
           si ninguna califica → caja al 0%. Señal al cierre → ejecuta al open siguiente. 10 bps.
  Variantes de atribución: solo-calidad (sin caída) · solo-castigada (sin calidad).

Fundamentales: SEC EDGAR xbrl/frames (EarningsPerShareDiluted trimestral, 2014Q1→2026Q1),
CIK→ticker con company_tickers.json. ⚠️ Cobertura imperfecta (deslistados sin mapeo actual,
fiscales no calendario) — se reporta; el sesgo resultante FAVORECE a la estrategia (solo puede
comprar empresas que siguen vivas), así que un resultado negativo es robusto.

⚠️ Pre-registro honesto: 2018-2026 es un período pésimo para value (régimen growth/IA). Un mal
resultado no mata el estilo para siempre; un buen resultado sería señal fuerte.

Uso:  python research/backtest_v13_value.py
"""
import json, os, sys, time
import numpy as np, pandas as pd
import requests

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pit_universe as PU

FUND = os.path.join(HERE, "data_fund")
os.makedirs(FUND, exist_ok=True)
UA = {"User-Agent": "oscar@pairus.ai investor-research (analisis personal)"}
COST = 10 / 1e4
DIP = 0.65            # cierre <= 65% del máximo 252d
LAG_DAYS = 90         # el EPS del trimestre se conoce ~90 días después del cierre del trimestre


def _get_json(url, cache_name):
    path = os.path.join(FUND, cache_name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    r = requests.get(url, headers=UA, timeout=30)
    if r.status_code != 200:
        return None
    j = r.json()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(j, f)
    time.sleep(0.15)
    return j


def load_eps_ttm():
    """DataFrame trimestral: fecha_disponible × ticker → EPS TTM (suma 4 trimestres, lag 90d)."""
    cmap = _get_json("https://www.sec.gov/files/company_tickers.json", "company_tickers.json")
    cik2tick = {}
    for v in cmap.values():
        cik2tick.setdefault(int(v["cik_str"]), v["ticker"])
    rows = []
    for y in range(2014, 2027):
        for q in (1, 2, 3, 4):
            if (y, q) > (2026, 1):
                break
            j = _get_json(f"https://data.sec.gov/api/xbrl/frames/us-gaap/EarningsPerShareDiluted/"
                          f"USD-per-shares/CY{y}Q{q}.json", f"eps_CY{y}Q{q}.json")
            if not j:
                continue
            for d in j.get("data", []):
                t = cik2tick.get(int(d["cik"]))
                if t:
                    rows.append((t, d["end"], float(d["val"])))
    df = pd.DataFrame(rows, columns=["ticker", "end", "eps"])
    df["end"] = pd.to_datetime(df["end"])
    df = df.drop_duplicates(["ticker", "end"]).sort_values("end")
    panel = df.pivot(index="end", columns="ticker", values="eps")
    # TTM POR EMPRESA sobre su propia serie de trimestres (el panel es disperso: cada empresa
    # cierra trimestre en fechas distintas — un rolling directo sobre el pivot rompe con los NaN)
    ttm_cols, ttm3_cols = {}, {}
    for t in panel.columns:
        s = panel[t].dropna()
        if len(s) < 4:
            continue
        ts = s.rolling(4).sum()
        ttm_cols[t] = ts
        ttm3_cols[t] = ts.shift(12)                           # 12 trimestres = 3 años atrás
    ttm = pd.DataFrame(ttm_cols)
    ttm3 = pd.DataFrame(ttm3_cols)
    ttm.index = ttm.index + pd.Timedelta(days=LAG_DAYS)       # disponible 90d después
    ttm3.index = ttm3.index + pd.Timedelta(days=LAG_DAYS)
    return ttm, ttm3


def build_masks(C, ttm, ttm3):
    """(quality_bool, dip_bool) diarios, alineados al panel de precios C (sin lookahead)."""
    def daily(df):
        d = df.reindex(columns=C.columns)
        return d.reindex(d.index.union(C.index)).ffill().reindex(C.index)
    ttm_daily, ttm3_daily = daily(ttm), daily(ttm3)
    quality = (ttm_daily > 0) & (ttm3_daily > 0) & (ttm_daily >= ttm3_daily)
    dip = C <= C.rolling(252).max() * DIP
    return quality.fillna(False), dip.fillna(False)


def run_screen(O, C, M, quality, dip, use_q=True, use_d=True, start="2018-10-01"):
    dates = C.index
    i0 = dates.searchsorted(pd.Timestamp(start))
    month_end = set(pd.Series(dates).groupby([dates.year, dates.month]).apply(lambda s: s.iloc[-1]))
    Cv, Ov = C.values, O.values
    col = {s: j for j, s in enumerate(C.columns)}
    held, eq, out, pending = [], 1.0, [], None
    n_hist = []
    for i in range(i0, len(dates)):
        d = dates[i]
        if pending is not None:
            new = pending; pending = None
            turn = (len(set(new) ^ set(held)) / max(len(new) + len(held), 1))
            ret = 0.0
            for s in new:
                o_, c_ = Ov[i, col[s]], Cv[i, col[s]]
                if np.isfinite(o_) and np.isfinite(c_) and o_ > 0:
                    ret += (c_ / o_ - 1) / len(new)
            eq *= (1 - COST * turn) * (1 + ret) if new else 1.0
            held = new
        elif held:
            ret, n = 0.0, 0
            for s in held:
                c0, c1 = Cv[i - 1, col[s]], Cv[i, col[s]]
                if np.isfinite(c0) and np.isfinite(c1) and c0 > 0:
                    ret += c1 / c0 - 1; n += 1
            eq *= 1 + (ret / len(held) if held else 0)
        if d in month_end and i + 1 < len(dates):
            members = M.asof(dates[i])
            ok = []
            qrow, drow = quality.iloc[i], dip.iloc[i]
            for s in members:
                if s not in col or not np.isfinite(Cv[i, col[s]]):
                    continue
                if use_q and not qrow.get(s, False):
                    continue
                if use_d and not drow.get(s, False):
                    continue
                ok.append(s)
            n_hist.append(len(ok))
            if set(ok) != set(held):
                pending = ok
        out.append((d, eq))
    eq = pd.Series(dict(out)).sort_index()
    return eq, (np.mean(n_hist) if n_hist else 0, np.max(n_hist) if n_hist else 0)


def metrics(eq):
    eq = eq.dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    r = eq.pct_change().dropna()
    sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    return cagr, dd, sh, (cagr / abs(dd) if dd else np.nan)


def main():
    print("=" * 96)
    print("v13 — 'CALIDAD CASTIGADA' (idea de Oscar) · fundamentales EDGAR point-in-time · 10bps")
    print("=" * 96)
    O, H, L, C = PU.load_ohlc(verbose=True)
    M = PU.Membership()
    print("bajando/cargando EPS trimestral EDGAR (frames 2014Q1-2026Q1)…")
    ttm, ttm3 = load_eps_ttm()
    cov = len([t for t in C.columns if t in ttm.columns])
    print(f"cobertura de fundamentales: {cov}/{C.shape[1]} tickers del universo PIT")
    quality, dip = build_masks(C, ttm, ttm3)

    variants = [("CALIDAD + CASTIGADA (la idea)", True, True),
                ("solo CALIDAD (sin exigir caída)", True, False),
                ("solo CASTIGADA (sin calidad)", False, True)]
    res = {}
    for name, uq, ud in variants:
        eq, (n_avg, n_max) = run_screen(O, C, M, quality, dip, use_q=uq, use_d=ud)
        c, d, s, cal = metrics(eq)
        res[name] = eq
        print(f"{name:34s} CAGR {c*100:6.1f}% · maxDD {d*100:6.1f}% · Sharpe {s:4.2f} · "
              f"Calmar {cal:5.2f} · nombres prom/máx {n_avg:.0f}/{n_max}")

    print("\nbenchmarks (mismos datos price-return Databento, misma ventana):")
    for s in ("SPY", "QQQ"):
        px = pd.read_parquet(os.path.join(HERE, "data_db", f"{s}.parquet"))["close"]
        px = px[(px.index >= "2018-10-01")]
        c, d, sh, cal = metrics(px / px.iloc[0])
        print(f"{s+' B&H':34s} CAGR {c*100:6.1f}% · maxDD {d*100:6.1f}% · Sharpe {sh:4.2f} · Calmar {cal:5.2f}")

    print("\nretornos por año de la idea vs SPY:")
    eq = res["CALIDAD + CASTIGADA (la idea)"]
    ya = eq.resample("YE").last() / eq.resample("YE").first() - 1
    spy = pd.read_parquet(os.path.join(HERE, "data_db", "SPY.parquet"))["close"]
    spy = spy[spy.index >= "2018-10-01"]; ys = spy.resample("YE").last() / spy.resample("YE").first() - 1
    print("año  " + "".join(f"{t.year:>8}" for t in ya.index))
    print("idea " + "".join(f"{v*100:7.1f}%" for v in ya))
    print("SPY  " + "".join(f"{ys.get(t, np.nan)*100:7.1f}%" for t in ya.index))


if __name__ == "__main__":
    main()
