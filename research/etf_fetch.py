"""
investor — Descarga OHLC diario TOTAL-RETURN (adjustment=all: splits+dividendos) de un MENÚ
pre-especificado de ETFs vía Alpaca Data API (feed IEX, gratis). Base del backtest v11.

El menú se fija ANTES de mirar resultados (regla anti-hindsight): índices core US, los 11
sectores SPDR completos, países/regiones principales completos, defensivos, factor y
apalancados. NO se eligen "los que más rindieron".

Caché parquet en research/data_etf/. Uso: python research/etf_fetch.py
"""
import os, sys, time
import pandas as pd, requests

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
from _env import load_env
load_env()

HERE  = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data_etf")
os.makedirs(CACHE, exist_ok=True)
START = "2015-01-01"

MENU = {
    "core_us":    ["SPY", "QQQ", "DIA", "IWM", "MDY"],
    "sectores11": ["XLK", "XLY", "XLP", "XLE", "XLF", "XLV", "XLI", "XLB", "XLU", "XLRE", "XLC"],
    "industria":  ["SMH", "IGV", "XBI"],          # ⚠️ satélites con riesgo de hindsight (se flaggea)
    "paises":     ["EFA", "EEM", "MCHI", "FXI", "KWEB", "ASHR", "EWJ", "EWY", "EWT", "INDA", "EWZ", "ILF"],
    "defensivos": ["TLT", "IEF", "SHY", "GLD", "BIL"],
    "factor":     ["MTUM", "SPMO", "VUG", "IWY", "QUAL", "USMV"],
    "apalancado": ["QLD", "SSO", "TQQQ"],
}
ALL = sorted({s for v in MENU.values() for s in v})


def _env(name):
    return os.environ.get(name, "").split("#")[0].strip().strip('"').strip("'")


def fetch(sym):
    path = os.path.join(CACHE, f"{sym}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)
    hdr = {"APCA-API-KEY-ID": _env("ALPACA_API_KEY"), "APCA-API-SECRET-KEY": _env("ALPACA_SECRET_KEY")}
    rows, tok = [], None
    while True:
        p = {"symbols": sym, "timeframe": "1Day", "start": START, "limit": 10000,
             "adjustment": "all", "feed": "iex"}
        if tok:
            p["page_token"] = tok
        r = requests.get("https://data.alpaca.markets/v2/stocks/bars", params=p, headers=hdr, timeout=30)
        if r.status_code != 200:
            print(f"  ❌ {sym}: HTTP {r.status_code} {r.text[:80]}")
            return None
        j = r.json()
        rows.extend((j.get("bars") or {}).get(sym, []))
        tok = j.get("next_page_token")
        if not tok:
            break
        time.sleep(0.05)
    if not rows:
        print(f"  ❌ {sym}: sin barras")
        return None
    df = pd.DataFrame([{"date": b["t"][:10], "open": b["o"], "high": b["h"],
                        "low": b["l"], "close": b["c"], "volume": b["v"]} for b in rows])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df.to_parquet(path)
    print(f"  ✅ {sym:5} {len(df):5} barras · {df.index[0].date()}→{df.index[-1].date()}")
    return df


def load_panel(field="close"):
    px = {}
    for s in ALL:
        df = fetch(s)
        if df is not None and len(df) > 200:
            px[s] = df[field]
    return pd.DataFrame(px).sort_index()


if __name__ == "__main__":
    print(f"DESCARGA ETFs Alpaca (total return) · {len(ALL)} símbolos · desde {START}")
    P = load_panel()
    print(f"panel: {P.shape[1]} ETFs · {P.index[0].date()}→{P.index[-1].date()}")
    print("faltan:", sorted(set(ALL) - set(P.columns)) or "ninguno")
