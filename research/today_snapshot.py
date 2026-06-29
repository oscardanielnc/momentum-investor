"""
investor — Foto de HOY: momentum + vol actuales para construir el portafolio del día
de forma data-driven (no a ojo). Aplica el principio de Oscar: dentro de un clúster
correlacionado, gana el de mayor crecimiento AJUSTADO POR RIESGO.

Para cada candidato imprime: retorno 3m/6m/12m, vol anual (20d), y score = momentum
ajustado por riesgo. Ordena por score → de ahí salen los elegidos por bucket.

Uso: python research/today_snapshot.py
"""
import os, sys, time, datetime as dt
import requests, numpy as np, pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
def _load_env(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); out[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return out
_env = _load_env(ENV)
HDR = {"APCA-API-KEY-ID": _env["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": _env["ALPACA_SECRET_KEY"]}
DATA = "https://data.alpaca.markets/v2/stocks/bars"

# candidatos por bucket (incluye individuales para comparar dentro del clúster)
CAND = {
    "1_Crecim/Semis": ["SMH", "XLK", "QQQ", "NVDA", "AVGO"],
    "2_Real/Inflac":  ["XLE", "GLD", "DBC"],
    "3_Duracion":     ["TLT", "SHY"],
    "4_Defensivo":    ["XLV", "XLP", "XLU"],
    "5_Geografia":    ["INDA", "EWT", "EEM"],
}
BUCK = {s: b for b, v in CAND.items() for s in v}
ALL = [s for v in CAND.values() for s in v]
START = "2020-07-27"; END = dt.date.today().isoformat()

def fetch(sym):
    rows, token = [], None
    while True:
        p = {"symbols": sym, "timeframe": "1Day", "start": START, "end": END,
             "limit": 10000, "adjustment": "all", "feed": "iex"}
        if token: p["page_token"] = token
        r = requests.get(DATA, params=p, headers=HDR, timeout=30)
        if r.status_code != 200: return None
        j = r.json(); rows.extend((j.get("bars") or {}).get(sym, []))
        token = j.get("next_page_token")
        if not token: break
    if not rows: return None
    s = pd.Series({b["t"][:10]: b["c"] for b in rows}); s.index = pd.to_datetime(s.index)
    return s.sort_index()

def main():
    px = {}
    for s in ALL:
        b = fetch(s)
        if b is not None and len(b) > 260: px[s] = b
        time.sleep(0.05)
    P = pd.DataFrame(px).sort_index().ffill()
    R = P.pct_change()
    last = P.iloc[-1]
    def ret(n): return (P.iloc[-1] / P.iloc[-n] - 1) * 100
    r3, r6, r12 = ret(63), ret(126), ret(252)
    vol = R.tail(20).std() * np.sqrt(252) * 100
    # score = momentum combinado (más peso al reciente) ajustado por vol
    mom = 0.5 * r3 + 0.3 * r6 + 0.2 * r12
    score = mom / vol

    print("=" * 82)
    print(f"FOTO DE HOY · datos hasta {P.index[-1].date()} · score = momentum / vol (mayor=mejor)")
    print("=" * 82)
    print(f"{'activo':6} {'bucket':16} {'3m%':>7} {'6m%':>7} {'12m%':>7} {'vol%':>6} {'SCORE':>7}")
    print("-" * 82)
    order = score.sort_values(ascending=False).index
    for s in order:
        print(f"{s:6} {BUCK[s]:16} {r3[s]:>7.1f} {r6[s]:>7.1f} {r12[s]:>7.1f} {vol[s]:>6.1f} {score[s]:>7.2f}")

    print("\n— GANADOR por bucket (mayor score, riesgo no disparado) —")
    for b, syms in CAND.items():
        syms = [s for s in syms if s in score.index]
        if not syms: continue
        win = max(syms, key=lambda s: score[s])
        print(f"  {b:16} → {win:5} (score {score[win]:.2f}, vol {vol[win]:.0f}%)")

if __name__ == "__main__":
    main()
