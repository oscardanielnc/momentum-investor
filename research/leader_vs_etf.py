"""
investor — ¿Conviene la ACCIÓN LÍDER individual o el ETF del sector?
Test del criterio de Oscar: "el que más crece PERO con caídas controladas".
Métrica clave = Calmar (CAGR / |maxDD|) → crecimiento por unidad de dolor.
También: retorno total, vol, maxDD (la "caída controlada"), y score de momentum actual.

Uso: python research/leader_vs_etf.py
"""
import os, sys, time, datetime as dt
import requests, numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
def _load_env(p):
    o = {}
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); o[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return o
_e = _load_env(ENV)
HDR = {"APCA-API-KEY-ID": _e["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": _e["ALPACA_SECRET_KEY"]}
DATA = "https://data.alpaca.markets/v2/stocks/bars"

# memoria/almacenamiento (lo que menciona Oscar) + líderes semis + ETFs de referencia
TICKERS = ["MU", "WDC", "STX", "SNDK", "NVDA", "AVGO", "MRVL", "SMH", "SOXX", "XLK"]
START = "2020-07-27"; END = dt.date.today().isoformat()

def fetch(sym):
    rows, tok = [], None
    while True:
        p = {"symbols": sym, "timeframe": "1Day", "start": START, "end": END,
             "limit": 10000, "adjustment": "all", "feed": "iex"}
        if tok: p["page_token"] = tok
        r = requests.get(DATA, params=p, headers=HDR, timeout=30)
        if r.status_code != 200: return None
        j = r.json(); rows.extend((j.get("bars") or {}).get(sym, []))
        tok = j.get("next_page_token")
        if not tok: break
    if not rows: return None
    s = pd.Series({b["t"][:10]: b["c"] for b in rows}); s.index = pd.to_datetime(s.index)
    return s.sort_index()

def stats(p):
    r = p.pct_change().dropna()
    n = len(r)
    cagr = (p.iloc[-1] / p.iloc[0]) ** (252 / n) - 1
    vol = r.std() * np.sqrt(252)
    dd = (p / p.cummax() - 1).min()
    calmar = cagr / abs(dd) if dd < 0 else float("nan")
    # momentum actual ajustado por riesgo
    def ret(k): return p.iloc[-1] / p.iloc[-k] - 1 if len(p) > k else np.nan
    mom = 0.5*ret(63) + 0.3*ret(126) + 0.2*ret(252)
    vol20 = r.tail(20).std() * np.sqrt(252)
    score = mom / vol20 if vol20 > 0 else np.nan
    return cagr, vol, dd, calmar, score, n

def main():
    px = {}
    for t in TICKERS:
        b = fetch(t)
        if b is not None and len(b) > 60: px[t] = b
        time.sleep(0.05)
    print("=" * 86)
    print("LÍDER INDIVIDUAL vs ETF · ventana 2020-07 → hoy · ¿más crecimiento con caída controlada?")
    print("=" * 86)
    print(f"{'ticker':7} {'CAGR':>8} {'vol':>7} {'maxDD':>8} {'Calmar':>7} {'score_hoy':>10} {'desde':>11}")
    print("-" * 86)
    rows = []
    for t, p in px.items():
        c, v, dd, cal, sc, n = stats(p)
        rows.append((t, c, v, dd, cal, sc, p.index[0].date()))
    # ordenar por Calmar (criterio de Oscar: crecer con caída controlada)
    rows.sort(key=lambda x: (-(x[4] if not np.isnan(x[4]) else -9)))
    for t, c, v, dd, cal, sc, d0 in rows:
        flag = "  ← ETF" if t in ("SMH", "SOXX", "XLK") else ""
        print(f"{t:7} {c*100:>7.1f}% {v*100:>6.1f}% {dd*100:>7.1f}% {cal:>7.2f} {sc:>10.2f} {str(d0):>11}{flag}")
    print("\nCalmar alto = más crecimiento por unidad de caída (el criterio de Oscar).")
    print("OJO: ventana corta y SOLO sobrevivientes (sesgo de supervivencia). maxDD = la 'caída' real sufrida.")

if __name__ == "__main__":
    main()
