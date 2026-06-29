"""
investor — CAPA DE EJECUCIÓN (ALPACA). Venue definitivo (decisión 2026-06-29).
Re-port del patrón de kepler/execution_spot a la Trading API de Alpaca.

Por qué Alpaca (vs Binance/eToro): comisión $0 en acciones/ETFs, API nativa de algotrading,
+7000 acciones/ETFs (cubre todos los buckets), órdenes FRACCIONALES por $ (ideal para $500 y
pesos objetivo), y el equity MTM lo da la cuenta directo (no hay que calcularlo).

Modos (env):
  INVESTOR_DRY_RUN=true   → solo loguea, no envía. DEFAULT seguro.
  INVESTOR_ALPACA_LIVE=false (default) → paper-api.alpaca.markets (cuenta de práctica).
  INVESTOR_ALPACA_LIVE=true            → api.alpaca.markets (REAL). Solo tras validar demo.

Claves: ALPACA_API_KEY / ALPACA_SECRET_KEY del entorno; si no, se leen de opportunity_alert/.env
(paper). Para REAL, Oscar pone SUS claves en el .env de investor.

Notas Alpaca:
  - Órdenes notional (por $) y fraccionales DEBEN ser type=market, tif=day. Para pesos objetivo
    eso es ideal (rebalanceo por valor). ETFs líquidos + $0 comisión → slippage mínimo.
  - Chandelier = type=trailing_stop NATIVO (trail_percent) → vive en el exchange.
  - flatten = DELETE /v2/positions (liquida todo) para el HALT del circuit breaker.
  - LONG-ONLY: nunca se vende más de lo que se tiene.
"""
from __future__ import annotations
import logging, os, time
import requests

log = logging.getLogger("investor.exec.alpaca")

from _env import load_env
load_env()   # carga investor/.env a os.environ (portable, sin rutas hardcodeadas)

def _envstr(name, default=""):
    return os.environ.get(name, default).split("#")[0].strip().strip('"').strip("'")

def _load_keys():
    return _envstr("ALPACA_API_KEY"), _envstr("ALPACA_SECRET_KEY")

DRY_RUN = _envstr("INVESTOR_DRY_RUN", "true").lower() != "false"
LIVE    = _envstr("INVESTOR_ALPACA_LIVE", "false").lower() == "true"
API_KEY, API_SECRET = _load_keys()
MIN_ORDER_USD = float(_envstr("INVESTOR_MIN_ORDER_USD", "1"))   # Alpaca permite notional chico
CAPITAL_FALLBACK = float(_envstr("INVESTOR_CAPITAL_FALLBACK", "500"))

_BASE_PAPER = "https://paper-api.alpaca.markets"
_BASE_LIVE  = "https://api.alpaca.markets"

def _base():
    return _BASE_LIVE if LIVE else _BASE_PAPER

def _hdr():
    return {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET}

def _req(method, path, **kw):
    try:
        r = requests.request(method, _base()+path, headers=_hdr(), timeout=15, **kw)
        if r.status_code >= 400:
            log.warning(f"[alpaca] {method} {path}: {r.status_code} {r.text[:150]}")
            return None
        return r.json() if r.text else {}
    except Exception as e:
        log.warning(f"[alpaca] {method} {path}: {e}")
        return None

# ─── Estado de cuenta ───────────────────────────────────────────────────────
def get_account(retries=3, backoff=1.0):
    if DRY_RUN:
        return {"equity": CAPITAL_FALLBACK, "cash": CAPITAL_FALLBACK, "status": "DRY_RUN"}
    for i in range(max(1, retries)):
        d = _req("GET", "/v2/account")
        if isinstance(d, dict) and "equity" in d:
            return d
        if i < retries-1:
            time.sleep(backoff*(i+1))
    return None

def get_equity(retries=3):
    """Equity MTM (portfolio value marcado a mercado). None si ilegible → el ciclo omite
    (mejor un hueco que un valor falso)."""
    if DRY_RUN:
        return CAPITAL_FALLBACK
    d = get_account(retries)
    return float(d["equity"]) if d and d.get("equity") is not None else None

def get_positions():
    """{symbol: {'qty':float,'mv':float,'avg':float}} de posiciones abiertas."""
    if DRY_RUN:
        return {}
    d = _req("GET", "/v2/positions")
    if not isinstance(d, list):
        return {}
    return {p["symbol"]: {"qty": float(p["qty"]), "mv": float(p["market_value"]),
                          "avg": float(p["avg_entry_price"])} for p in d}

def market_open():
    d = _req("GET", "/v2/clock")
    return bool(d.get("is_open")) if isinstance(d, dict) else False

# ─── Órdenes ────────────────────────────────────────────────────────────────
def _coid(tag, symbol):
    return f"inv-{tag}-{symbol}-{int(time.time())}"[:48]

def submit_notional(symbol, side, usd, coid=None):
    """Orden de mercado por $ (fraccional). Para rebalanceo por pesos. side: 'buy'|'sell'."""
    body = {"symbol": symbol, "notional": round(abs(usd), 2), "side": side,
            "type": "market", "time_in_force": "day",
            "client_order_id": coid or _coid("mk", symbol)}
    if DRY_RUN:
        log.info(f"[alpaca] DRY order {side} ${abs(usd):.2f} {symbol}")
        return {"dry_run": True, **body}
    return _req("POST", "/v2/orders", json=body)

def submit_qty(symbol, side, qty, coid=None):
    """Orden de mercado por cantidad (para vender exactamente lo que se tiene)."""
    body = {"symbol": symbol, "qty": round(abs(qty), 6), "side": side,
            "type": "market", "time_in_force": "day",
            "client_order_id": coid or _coid("qt", symbol)}
    if DRY_RUN:
        log.info(f"[alpaca] DRY order {side} {abs(qty)} {symbol}")
        return {"dry_run": True, **body}
    return _req("POST", "/v2/orders", json=body)

def place_trailing_stop(symbol, qty, trail_percent, coid=None):
    """Trailing stop NATIVO (SELL, GTC) = 'sale a tiempo' aunque el bot esté caído.
    Alpaca exige ACCIONES ENTERAS en stops → redondeo hacia abajo; si <1 acción, se omite."""
    q = int(abs(qty))
    if q < 1:
        return None
    body = {"symbol": symbol, "qty": q, "side": "sell",
            "type": "trailing_stop", "trail_percent": round(trail_percent, 2),
            "time_in_force": "gtc", "client_order_id": coid or _coid("ts", symbol)}
    if DRY_RUN:
        log.info(f"[alpaca] DRY trailing_stop {symbol} {q}sh {trail_percent}%")
        return {"dry_run": True, **body}
    return _req("POST", "/v2/orders", json=body)

def close_position(symbol):
    """Cierra la posición COMPLETA (exacto, sin redondeos que provoquen 'insufficient qty')."""
    if DRY_RUN:
        log.info(f"[alpaca] DRY close_position {symbol}"); return {"dry_run": True}
    return _req("DELETE", f"/v2/positions/{symbol}")

def cancel_all_orders():
    if DRY_RUN:
        log.info("[alpaca] DRY cancel_all_orders"); return {"dry_run": True}
    return _req("DELETE", "/v2/orders")

# ─── Rebalanceo ─────────────────────────────────────────────────────────────
def rebalance(target_weights, equity=None):
    """Lleva la cartera a los pesos objetivo. LONG-ONLY. VENDE PRIMERO (libera caja), luego compra
    → evita cash negativo. Cierres completos vía close_position (exacto). Huérfanas (no en target)→cerradas."""
    equity = equity or get_equity() or CAPITAL_FALLBACK
    cur = get_positions()  # {} en DRY_RUN
    target = {s: w for s, w in target_weights.items() if w > 1e-4}
    syms = set(target) | set(cur)
    cancel_all_orders()
    sells, buys = [], []
    for s in syms:
        delta = target.get(s, 0.0) * equity - cur.get(s, {}).get("mv", 0.0)
        if abs(delta) < MIN_ORDER_USD:
            continue
        (buys if delta > 0 else sells).append((s, delta))
    placed = 0
    # 1) VENTAS primero (libera caja antes de comprar)
    for s, delta in sells:
        if target.get(s, 0.0) * equity < MIN_ORDER_USD and s in cur:
            if close_position(s) is not None: placed += 1          # salida total exacta
        elif submit_notional(s, "sell", -delta) is not None:        # reducción parcial por $
            placed += 1
    if sells and not DRY_RUN:
        time.sleep(2)                                               # esperar que liquiden
    # 2) COMPRAS
    for s, delta in buys:
        if submit_notional(s, "buy", delta) is not None: placed += 1
    log.info(f"[alpaca] rebalanceo: {placed} órden(es) ({len(sells)} venta/{len(buys)} compra) · "
             f"equity ${equity:.0f} · {mode_str()}")
    return placed

def flatten():
    """Liquida TODAS las posiciones (HALT del circuit breaker / salida por evento)."""
    if DRY_RUN:
        log.info("[alpaca] DRY flatten (cerrar todo)"); return {"dry_run": True}
    cancel_all_orders()
    return _req("DELETE", "/v2/positions")

def mode_str():
    return "DRY_RUN" if DRY_RUN else ("LIVE-REAL" if LIVE else "PAPER")
