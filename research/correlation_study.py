"""
investor — Estudio de correlación + backtest de asignación DINÁMICA.
Objetivo (pedido de Oscar): probar con DATOS REALES (no opiniones) que los buckets no caen
juntos, y derivar los pesos. Regla de oro kepler: nada sin backtest.

Diseño validado:
  - Universo por DRIVER (5 buckets), histórico diario de Alpaca (feed IEX, 2016+).
  - Correlación semanal de retornos vs el bucket de crecimiento (Semis/IA).
  - Backtest mensual de 3 estrategias + benchmarks:
      STATIC  : base 65% (vol-parity de diversificadores) + tilt 35% (líder de crecimiento).
      DYNAMIC : tilt ESCALA con el momentum ajustado-por-riesgo del líder (0.10–0.60) + vol-parity
                en la base + FRENO por drawdown escalonado (techo −30%). ← lo que pide Oscar.
      Benchmarks: 100% QQQ, 60/40 (SPY/TLT).
  - Métricas: CAGR, vol anual, Sharpe, maxDD.

Uso: python research/correlation_study.py
"""
import os, sys, time, datetime as dt
import requests
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── Keys desde opportunity_alert/.env (reuso, no se duplican) ──────────────────
ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
def _load_env(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return out
_env = _load_env(ENV)
KEY, SEC = _env["ALPACA_API_KEY"], _env["ALPACA_SECRET_KEY"]

DATA = "https://data.alpaca.markets/v2/stocks/bars"
HDR = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SEC}

# ── Universo por bucket (driver) ──────────────────────────────────────────────
BUCKETS = {
    "1_Crecimiento": ["SMH", "XLK", "QQQ"],          # Semis/IA/tech — risk-on (tilt)
    "2_Real/Inflac": ["XLE", "GLD", "DBC"],          # energía/oro/materias
    "3_Duracion":    ["TLT", "SHY"],                 # bonos largos / t-bills (cash proxy)
    "4_Defensivo":   ["XLV", "XLP", "XLU"],          # salud/consumo/utilities
    "5_Geografia":   ["EFA", "EEM", "EWJ", "INDA", "EWT", "EWY"],  # internacional/Asia
}
BENCH = ["SPY"]
ALL = sorted({s for v in BUCKETS.values() for s in v} | set(BENCH))
GROWTH = BUCKETS["1_Crecimiento"]
CASH = "SHY"   # refugio donde se aparca lo des-arriesgado

START = "2016-01-01"   # IEX arranca ~2016
END = dt.date.today().isoformat()


def fetch_bars(symbol):
    """Cierres diarios ajustados de Alpaca (IEX), con paginación."""
    rows, token = [], None
    while True:
        p = {"symbols": symbol, "timeframe": "1Day", "start": START, "end": END,
             "limit": 10000, "adjustment": "all", "feed": "iex"}
        if token:
            p["page_token"] = token
        r = requests.get(DATA, params=p, headers=HDR, timeout=30)
        if r.status_code != 200:
            print(f"  ⚠️ {symbol}: HTTP {r.status_code} {r.text[:120]}")
            return None
        j = r.json()
        bars = (j.get("bars") or {}).get(symbol, [])
        rows.extend(bars)
        token = j.get("next_page_token")
        if not token:
            break
    if not rows:
        return None
    s = pd.Series({b["t"][:10]: b["c"] for b in rows})
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def metrics(daily_ret):
    """CAGR, vol anual, Sharpe (rf=0), maxDD a partir de retornos diarios."""
    eq = (1 + daily_ret).cumprod()
    n = len(daily_ret)
    cagr = eq.iloc[-1] ** (252 / n) - 1
    vol = daily_ret.std() * np.sqrt(252)
    sharpe = (daily_ret.mean() * 252) / vol if vol > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return cagr, vol, sharpe, dd


def main():
    print("=" * 74)
    print(f"DESCARGA Alpaca · {len(ALL)} activos · {START}→{END[:10]}")
    print("=" * 74)
    px = {}
    for s in ALL:
        b = fetch_bars(s)
        if b is not None and len(b) > 50:
            px[s] = b
            print(f"  ✅ {s:5} {len(b):5} barras · {b.index[0].date()}→{b.index[-1].date()}")
        else:
            print(f"  ❌ {s:5} sin datos suficientes")
        time.sleep(0.1)
    P = pd.DataFrame(px).sort_index().ffill()
    R = P.pct_change().dropna(how="all")

    # ── 1. CORRELACIÓN (retornos semanales) vs crecimiento ────────────────────
    print("\n" + "=" * 74)
    print("CORRELACIÓN (retornos SEMANALES) — vs bucket Crecimiento (proxy SMH)")
    print("=" * 74)
    Rw = P.resample("W-FRI").last().pct_change().dropna(how="all")
    if "SMH" in Rw:
        corr = Rw.corr()["SMH"].sort_values()
        print(f"{'activo':6} {'corr_SMH':>9}   bucket")
        sym2b = {s: b for b, v in BUCKETS.items() for s in v}
        for s, c in corr.items():
            tag = "🟢 diversifica" if c < 0.4 else ("🟡 parcial" if c < 0.7 else "🔴 misma apuesta")
            print(f"{s:6} {c:>9.2f}   {sym2b.get(s,'bench'):14} {tag}")

    # ── 2. BACKTEST ───────────────────────────────────────────────────────────
    print("\n" + "=" * 74)
    print("BACKTEST mensual 2016+ · STATIC vs DYNAMIC vs benchmarks")
    print("=" * 74)
    rebal = P.resample("ME").last().index   # fin de mes
    daily = R.index

    base_assets = [s for b, v in BUCKETS.items() if b != "1_Crecimiento" for s in v if s in P]
    growth_assets = [s for s in GROWTH if s in P]

    def vol_parity(assets, asof, frac):
        """Pesos ∝ 1/vol (20d) entre `assets`, normalizados a `frac`. Mapa symbol→peso."""
        win = R[assets].loc[:asof].tail(60)
        v = win.std()
        v = v[v > 0]
        if v.empty:
            return {}
        iv = 1.0 / v
        w = iv / iv.sum() * frac
        return w.to_dict()

    def growth_leader(asof):
        """Líder de crecimiento por momentum 90d y su momentum ajustado-por-riesgo."""
        hist = P[growth_assets].loc[:asof]
        if len(hist) < 95:
            return None, 0.0
        mom = hist.iloc[-1] / hist.iloc[-63] - 1          # ~90 días bursátiles (3m)
        leader = mom.idxmax()
        r_ann = mom[leader] * (252 / 63)
        vol = R[leader].loc[:asof].tail(20).std() * np.sqrt(252)
        g = r_ann / vol if vol > 0 else 0.0               # ~Sharpe del líder
        return leader, g

    def weights_static(asof):
        w = vol_parity(base_assets, asof, 0.65)
        leader, _ = growth_leader(asof)
        if leader:
            w[leader] = w.get(leader, 0) + 0.35
        return w

    def weights_dynamic(asof, cur_dd):
        leader, g = growth_leader(asof)
        # tilt escala con el momentum ajustado-por-riesgo del líder (0.10–0.60)
        tilt = float(np.clip(0.35 + 0.25 * g, 0.10, 0.60))
        # FRENO por drawdown escalonado (techo −30%)
        if   cur_dd <= -0.25: thr = 0.10
        elif cur_dd <= -0.18: thr = 0.35
        elif cur_dd <= -0.10: thr = 0.65
        else:                 thr = 1.00
        tilt *= thr
        base_frac = 1.0 - tilt
        w = vol_parity(base_assets, asof, base_frac)
        if leader:
            w[leader] = w.get(leader, 0) + tilt
        # lo no asignado (por activos sin vol) → cash
        assigned = sum(w.values())
        if assigned < 0.999 and CASH in P:
            w[CASH] = w.get(CASH, 0) + (1 - assigned)
        return w

    def run(weight_fn, dynamic=False):
        eq = 1.0
        peak = 1.0
        rets = []
        w = {}
        idx_rebal = set(rebal)
        for i, day in enumerate(daily):
            if i > 0:
                r = sum(w.get(s, 0) * R[s].iloc[i] for s in w if not np.isnan(R[s].iloc[i]))
                eq *= (1 + r)
                rets.append((day, r))
                peak = max(peak, eq)
            if day in idx_rebal:
                cur_dd = eq / peak - 1
                w = weight_fn(day, cur_dd) if dynamic else weight_fn(day)
        return pd.Series(dict(rets))

    strat = {
        "STATIC 65/35": run(weights_static, dynamic=False),
        "DYNAMIC":      run(weights_dynamic, dynamic=True),
    }
    # benchmarks
    if "QQQ" in R:
        strat["BENCH QQQ"] = R["QQQ"]
    if "SPY" in R and "TLT" in R:
        strat["BENCH 60/40"] = 0.6 * R["SPY"] + 0.4 * R["TLT"]

    print(f"\n{'estrategia':16} {'CAGR':>7} {'vol':>7} {'Sharpe':>7} {'maxDD':>8} {'Calmar':>7}")
    print("-" * 60)
    for name, r in strat.items():
        r = r.dropna()
        c, v, sh, dd = metrics(r)
        calmar = c / abs(dd) if dd < 0 else float("nan")
        print(f"{name:16} {c*100:>6.1f}% {v*100:>6.1f}% {sh:>7.2f} {dd*100:>7.1f}% {calmar:>7.2f}")
    print("\nCalmar = CAGR/|maxDD| (retorno por unidad de dolor). Mayor = mejor.")


if __name__ == "__main__":
    main()
