"""
investor — Descarga POINT-IN-TIME: OHLCV diario de TODOS los miembros históricos del S&P 500
(2018-05 → 2026-06), incluidos los que luego salieron/quebraron/fueron adquiridos.

Es la base del re-backtest honesto: el universo de cada fecha es el que un inversor habría
conocido ESE día (membresía S&P 500 point-in-time, fja05680/sp500), no los "líderes de 2026".

- Fuente precios: Databento ohlcv-1d (XNAS.ITCH → XNYS.PILLAR → ARCX.PILLAR), raw_symbol.
  Los cambios de ticker se resuelven solos: la membresía usa el ticker vigente en cada fecha
  (FB hasta 2022, META después) y Databento raw_symbol devuelve datos solo mientras ese
  ticker existió → el panel por-ticker empalma naturalmente.
- Se guarda OHLC completo (el backtest v10 simula el trailing stop con high/low INTRADÍA,
  como la orden nativa de Alpaca — el viejo backtest close-only no era fiel al robot).
- Ajuste de splits sobre el CLOSE (mismo heurístico auditado de db_fetch) aplicado a O/H/L/C;
  se guarda `factor` para poder reconstruir volumen/dólar-volumen crudos.
- Caché parquet por símbolo en research/data_pit/ (no re-descarga). Costo verificado: ~$3.8.

Uso:  python research/pit_fetch.py
"""
import os, sys
import databento as db
import numpy as np, pandas as pd

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
from _env import load_env
load_env()

HERE  = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data_pit")
os.makedirs(CACHE, exist_ok=True)
START, END = "2018-05-01", "2026-06-27"
DATASETS = ["XNAS.ITCH", "XNYS.PILLAR", "ARCX.PILLAR"]
BATCH = 682   # todos en una llamada por dataset (cost-check hecho: ~$3.8 total)


def needed_tickers():
    se = pd.read_csv(os.path.join(CACHE, "sp500_start_end.csv"))
    se["end_date"] = se["end_date"].fillna("2099-01-01")
    need = se[(se["end_date"] >= START) & (se["start_date"] <= END)]
    return sorted(need["ticker"].unique())


def _adjust_splits_ohlc(df, sym):
    """Detecta splits por salto overnight del close (>±45%, ratio redondo) y re-escala el
    PASADO de O/H/L/C. Devuelve (df_ajustado, n_ajustes). `factor` queda como columna."""
    ratios = np.array([2, 3, 4, 5, 6, 7, 8, 10, 20])
    c = df["close"]
    rel = c / c.shift(1)
    factor = pd.Series(1.0, index=df.index)
    n_adj = 0
    for i in range(len(c) - 1, 0, -1):
        r = rel.iloc[i]
        if pd.isna(r):
            continue
        if r < 0.6:
            n = ratios[np.argmin(np.abs(1 / ratios - r))]
            if abs(1 / n - r) < 0.06:
                factor.iloc[:i] /= n; n_adj += 1
        elif r > 1.7:
            n = ratios[np.argmin(np.abs(ratios - r))]
            if abs(n - r) < 0.1:
                factor.iloc[:i] *= n; n_adj += 1
    out = df.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = df[col] * factor
    out["factor"] = factor
    return out, n_adj


def fetch_all():
    tickers = needed_tickers()
    have = {f[:-8] for f in os.listdir(CACHE) if f.endswith(".parquet")}
    todo = [t for t in tickers if t not in have]
    print(f"tickers: {len(tickers)} · en caché: {len(tickers)-len(todo)} · a descargar: {len(todo)}")
    if not todo:
        return
    client = db.Historical(os.environ["DATABENTO_API_KEY"])
    pending = list(todo)
    for ds in DATASETS:
        if not pending:
            break
        print(f"→ {ds}: pidiendo {len(pending)} símbolos…")
        try:
            data = client.timeseries.get_range(dataset=ds, symbols=pending, schema="ohlcv-1d",
                                               start=START, end=END, stype_in="raw_symbol")
            dfx = data.to_df()
        except Exception as e:
            print(f"  ❌ {ds}: {str(e)[:140]}")
            continue
        if dfx is None or len(dfx) == 0:
            continue
        got = []
        for sym, g in dfx.groupby("symbol"):
            g = g[["open", "high", "low", "close", "volume"]].copy()
            g.index = pd.to_datetime(g.index).tz_localize(None).normalize()
            g = g[~g.index.duplicated(keep="last")].sort_index()
            if len(g) < 30:            # residuos (p.ej. cross-listings con días sueltos)
                continue
            g, n_adj = _adjust_splits_ohlc(g, sym)
            g.to_parquet(os.path.join(CACHE, f"{sym}.parquet"))
            got.append(sym)
            if n_adj:
                print(f"    · {sym}: {n_adj} split(s) ajustado(s)")
        pending = [t for t in pending if t not in set(got)]
        print(f"  ✅ {ds}: {len(got)} símbolos guardados · faltan {len(pending)}")
    if pending:
        print(f"⚠️ sin datos en ningún venue: {pending}")


if __name__ == "__main__":
    print("=" * 78)
    print(f"DESCARGA POINT-IN-TIME S&P500 · ohlcv-1d · {START}→{END}")
    print("=" * 78)
    fetch_all()
    n = len([f for f in os.listdir(CACHE) if f.endswith(".parquet")])
    print("-" * 78)
    print(f"parquets en caché: {n}")
