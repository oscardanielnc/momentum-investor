"""
investor — ALLOCATOR (cerebro) · ESTRATEGIA AGRESIVA MULTI-SECTOR (validada walk-forward 2026-06-29).

CONFIG BLOQUEADA:
  - Universo: 36 acciones líderes de 7 sectores (semis, software, energía, salud, finanzas, consumo, comm).
  - TOP-5 por momentum ajustado-riesgo (lookback 90d), EQUIPONDERADO (20% c/u), SIEMPRE invertido.
  - SIN SHY / sin caja muerta. El "defensivo" = rotar al sector líder + trailing stop + Global66 (manual).
  - TRAILING STOP 20% por posición (lo coloca el orquestador como orden nativa en Alpaca → "sale a tiempo").
  - El robot opera sobre ALPACA = 100%. Global66 (~10-12%) es colchón fijo de Oscar, FUERA del robot.

Validación: top-5 generaliza OOS (Calmar 1.38), diversifica ~3 sectores, en Bear-2022 rotó a energía
(−18% vs −33% semis-solo), trailing cortó COVID a −22%. CAGR ~36% ciclo completo (OOS 42% del toro IA,
no sostenible). compute_target() es PURO. load_prices() trae datos de Alpaca.
"""
from __future__ import annotations
import os, sys, time
import numpy as np, pandas as pd

# ── Universo multi-sector (idéntico al backtest validado) ────────────────────
SEMIS    = ["MU","INTC","NVDA","AMD","WDC","STX","MRVL","TXN","AVGO","AMAT","LRCX","QCOM","ADI"]
SOFTWARE = ["MSFT","ORCL","CRM","NOW","ADBE"]
ENERGIA  = ["XOM","CVX","COP","SLB"]
SALUD    = ["LLY","UNH","JNJ","ABBV"]
FINANZAS = ["JPM","GS","V","MA"]
CONSUMO  = ["AMZN","TSLA","COST","HD"]
COMM     = ["GOOGL","NFLX"]
SECTOR = {**{s:"semis" for s in SEMIS}, **{s:"software" for s in SOFTWARE},
          **{s:"energia" for s in ENERGIA}, **{s:"salud" for s in SALUD},
          **{s:"finanzas" for s in FINANZAS}, **{s:"consumo" for s in CONSUMO},
          **{s:"comm" for s in COMM}}
UNIVERSE = SEMIS + SOFTWARE + ENERGIA + SALUD + FINANZAS + CONSUMO + COMM

# ── Parámetros BLOQUEADOS (validados walk-forward) ───────────────────────────
LB = 90              # lookback de momentum (días) — robusto 63-90
TOPN = 5             # nº de líderes en cartera (equiponderado)
MAX_PER_SECTOR = 4   # tope 80% por sector (máx 4 de 5) — óptimo validado: mata el 100%-concentrado
TRAIL_PCT = 20.0     # trailing stop por posición (%) — lo coloca el orquestador en Alpaca
VOLSHORT = 20        # ventana de vol para el ajuste por riesgo


def _riskadj_mom(P, R, sym, asof):
    """Momentum (LB días) anualizado dividido por la vol → ~Sharpe del activo."""
    h = P[sym].loc[:asof]
    if len(h) < LB + 5:
        return -9.0
    m = h.iloc[-1] / h.iloc[-LB] - 1
    vol = R[sym].loc[:asof].tail(VOLSHORT).std() * np.sqrt(252)
    return (m * 252 / LB) / vol if vol > 0 else -9.0


def compute_target(prices: pd.DataFrame, current: dict | None = None):
    """PURO. Selecciona el TOP-5 por momentum ajustado-riesgo y lo equipondera (20% c/u).
    Siempre invertido, sin caja. Devuelve (target_weights, meta). `current` no se usa para banda
    (el rebalanceo es mensual a top-5); el orquestador decide actuar si cambia la membresía."""
    P = prices.sort_index()
    R = P.pct_change()
    asof = P.index[-1]
    univ = [s for s in UNIVERSE if s in P.columns]
    scores = {s: _riskadj_mom(P, R, s, asof) for s in univ}
    ret3m = {s: float(P[s].iloc[-1] / P[s].iloc[-63] - 1) if len(P[s]) > 63 else 0.0 for s in univ}
    ranked = sorted(univ, key=lambda s: scores[s], reverse=True)
    # selección top-N con TOPE POR SECTOR (máx MAX_PER_SECTOR por sector → mata el 100%-concentrado)
    top, sec_count = [], {}
    for s in ranked:
        sec = SECTOR.get(s, "?")
        if sec_count.get(sec, 0) < MAX_PER_SECTOR:
            top.append(s); sec_count[sec] = sec_count.get(sec, 0) + 1
        if len(top) == TOPN:
            break
    w = {s: round(1.0 / len(top), 4) for s in top} if top else {}
    sectors = sorted(set(SECTOR.get(s, "?") for s in top))
    meta = {
        "asof": str(asof.date()), "leaders": top, "sectors": sectors, "n_sectors": len(sectors),
        "scores": {s: round(scores[s], 2) for s in univ},
        "ret3m": {s: round(ret3m[s], 3) for s in univ},
        "ranking": ranked, "next_best": next((s for s in ranked if s not in top), None),
        "trail_pct": TRAIL_PCT, "max_per_sector": MAX_PER_SECTOR,
    }
    return w, meta


def rationale(target: dict, meta: dict, prev: dict | None = None):
    """Justificación rica por redistribución (markdown + estructura) para el frontend/DB."""
    prev = prev or {}
    rows = []
    for s, w in sorted(target.items(), key=lambda x: -x[1]):
        sec = SECTOR.get(s, "?")
        why = (f"momentum #{meta['ranking'].index(s)+1} de {len(meta['ranking'])} · "
               f"score {meta['scores'].get(s)} · +{meta['ret3m'].get(s,0)*100:.0f}% 3m")
        chg = "NUEVO" if prev.get(s, 0.0) < 0.005 else "mantiene"
        rows.append({"symbol": s, "sector": sec, "weight": round(w, 4), "why": why, "change": chg})
    removed = [s for s in prev if s not in target and prev.get(s, 0) >= 0.01]

    md = [f"### 🔄 Redistribución {meta['asof']}",
          f"**Cartera:** top-{len(target)} momentum · **{meta['n_sectors']} sectores** "
          f"({', '.join(meta['sectors'])}) · trailing stop {meta['trail_pct']:.0f}% · sin caja",
          "", "| Activo | Sector | Peso | Por qué | Δ |", "|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| 🚀 **{r['symbol']}** | {r['sector']} | {r['weight']*100:.0f}% | {r['why']} | {r['change']} |")
    if meta.get("next_best"):
        nb = meta["next_best"]
        md.append(f"\n*Próximo en la fila: {nb} ({SECTOR.get(nb,'?')}, score {meta['scores'].get(nb)}) "
                  f"— entra si supera a un líder.*")
    if removed:
        md.append(f"\n*Salieron: {', '.join(removed)} (perdieron momentum / stop activado).*")
    return "\n".join(md), {"context": {k: meta[k] for k in ("asof","leaders","sectors","n_sectors","trail_pct")},
                           "positions": rows, "removed": removed}


def load_prices(lookback_days: int = 320):
    """Cierres diarios ajustados de Alpaca (split+div) para todo el universo."""
    import requests
    from _env import load_env
    load_env()
    def _env(name):
        return os.environ.get(name, "").split("#")[0].strip().strip('"').strip("'")
    HDR = {"APCA-API-KEY-ID": _env("ALPACA_API_KEY"), "APCA-API-SECRET-KEY": _env("ALPACA_SECRET_KEY")}
    start = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=lookback_days)).date().isoformat()
    px = {}
    for s in UNIVERSE:
        rows, tok = [], None
        while True:
            p = {"symbols": s, "timeframe": "1Day", "start": start, "limit": 10000,
                 "adjustment": "all", "feed": "iex"}
            if tok: p["page_token"] = tok
            r = requests.get("https://data.alpaca.markets/v2/stocks/bars", params=p, headers=HDR, timeout=30)
            if r.status_code != 200: break
            j = r.json(); rows.extend((j.get("bars") or {}).get(s, []))
            tok = j.get("next_page_token")
            if not tok: break
        if rows:
            ser = pd.Series({b["t"][:10]: b["c"] for b in rows}); ser.index = pd.to_datetime(ser.index)
            px[s] = ser.sort_index()
        time.sleep(0.02)
    return pd.DataFrame(px).sort_index().ffill()


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    print("Cargando precios (Alpaca, 36 acciones)…")
    P = load_prices()
    w, meta = compute_target(P)
    md, _ = rationale(w, meta)
    print("\n" + md)
