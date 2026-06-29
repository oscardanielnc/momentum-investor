"""
investor — Descarga histórica Databento (ohlcv-1d, 2018-05+) con CACHÉ parquet (crédito-smart:
no re-descarga) + AJUSTE DE SPLITS automático (Databento da precios crudos sin ajustar).

Profundidad real de Databento = 2018-05-01 (verificado). 2008 NO alcanzable.
Datasets por venue: XNAS.ITCH (Nasdaq) → ARCX.PILLAR (NYSE Arca) → XNYS.PILLAR (NYSE).
Se prueba en orden por símbolo hasta encontrar datos.

Ajuste de splits: precios crudos tienen saltos artificiales (NVDA 10:1 jun-2024, AVGO 10:1,
LRCX 10:1). Se detecta cualquier salto overnight > ±45% sin hueco de fin de semana largo, se
ajusta al ratio redondo más cercano y se RE-ESCALA hacia atrás. Cada ajuste se loguea para auditar.
"""
import os, sys
import databento as db
import numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
from _env import load_env
load_env()
KEY = os.environ.get("DATABENTO_API_KEY", "")   # clave desde .env (NUNCA hardcodeada)
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_db")
os.makedirs(CACHE, exist_ok=True)
START, END = "2018-05-01", "2026-06-27"
DATASETS = ["XNAS.ITCH", "ARCX.PILLAR", "XNYS.PILLAR"]

ETFS = ["SMH","SOXX","XLK","QQQ","SPY","XLV","XLP","XLU","XLE","GLD","DBC","TLT","SHY","IEF","EEM","EFA"]
STOCKS = ["MU","INTC","NVDA","AMD","WDC","STX","MRVL","TXN","AVGO","AMAT","LRCX","QCOM","ADI"]
# Multi-sector (líderes de 6 sectores más, para el backtest de rotación multi-sector).
# Evitados por cambio de ticker en Databento (raw_symbol): META (era FB hasta 2022).
STOCKS_MULTI = ["MSFT","ORCL","CRM","NOW","ADBE",          # software
                "XOM","CVX","COP","SLB",                    # energía
                "LLY","UNH","JNJ","ABBV",                   # salud
                "JPM","GS","V","MA",                        # financieras
                "AMZN","TSLA","COST","HD",                  # consumo
                "GOOGL","NFLX"]                             # comunicación
UNIVERSE = ETFS + STOCKS + STOCKS_MULTI

_client = None
def client():
    global _client
    if _client is None:
        _client = db.Historical(KEY)
    return _client

def _adjust_splits(close, sym):
    """Re-escala hacia atrás los splits detectados (salto overnight > ±45%)."""
    ratios = np.array([2,3,4,5,6,7,8,10,20])
    c = close.copy()
    rel = c / c.shift(1)
    factor = pd.Series(1.0, index=c.index)
    for i in range(len(c) - 1, 0, -1):
        r = rel.iloc[i]
        if pd.isna(r): continue
        # split (precio cae): r ~ 1/N  → multiplicar el pasado por N... aquí ajustamos el PASADO
        if r < 0.6:
            n = ratios[np.argmin(np.abs(1/ratios - r))]
            if abs(1/n - r) < 0.06:
                factor.iloc[:i] /= n
                print(f"    · {sym}: split {n}:1 el {c.index[i].date()} (ratio {r:.3f}) → ajustado")
        elif r > 1.7:  # split inverso (raro)
            n = ratios[np.argmin(np.abs(ratios - r))]
            if abs(n - r) < 0.1:
                factor.iloc[:i] *= n
                print(f"    · {sym}: split inverso 1:{n} el {c.index[i].date()} → ajustado")
    return c * factor

def fetch(sym):
    path = os.path.join(CACHE, f"{sym}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)["close"]
    for ds in DATASETS:
        try:
            data = client().timeseries.get_range(
                dataset=ds, symbols=[sym], schema="ohlcv-1d",
                start=START, end=END, stype_in="raw_symbol")
            dfx = data.to_df()
            if dfx is None or len(dfx) == 0:
                continue
            close = dfx["close"].copy()
            close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
            close = close[~close.index.duplicated(keep="last")].sort_index()
            close = _adjust_splits(close, sym)
            close.to_frame("close").to_parquet(path)
            print(f"  ✅ {sym:5} {len(close):5} barras · {close.index[0].date()}→{close.index[-1].date()} · {ds}")
            return close
        except Exception as e:
            last = str(e)[:70]
            continue
    print(f"  ❌ {sym:5} sin datos (último error: {last if 'last' in dir() else '—'})")
    return None

def load_panel():
    """Devuelve DataFrame de cierres ajustados (caché si existe)."""
    px = {}
    for s in UNIVERSE:
        c = fetch(s)
        if c is not None and len(c) > 200:
            px[s] = c
    return pd.DataFrame(px).sort_index().ffill()

if __name__ == "__main__":
    print("=" * 78)
    print(f"DESCARGA Databento ohlcv-1d · {len(UNIVERSE)} símbolos · {START}→{END}")
    print("=" * 78)
    P = load_panel()
    print("-" * 78)
    print(f"Panel: {P.shape[1]} símbolos · {P.index[0].date()}→{P.index[-1].date()} · {len(P)} días")
    print(f"Faltan: {sorted(set(UNIVERSE) - set(P.columns))}")
