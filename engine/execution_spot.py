"""
investor — CAPA DE EJECUCIÓN (SPOT). Port fiel de kepler/execution.py (Futures) a Binance SPOT.

Rebalancea la cartera hacia los pesos objetivo del allocator usando órdenes maker
(LIMIT_MAKER = post-only) + gestión de no-fills (persigue el precio sin cruzar).

POR QUÉ SPOT (no Futures): sin apalancamiento → SIN liquidación → el tope −30% se gestiona
reduciendo exposición, no lo rompe una liquidación. Sin funding → sin sangría cada 8h.

DIFERENCIAS CLAVE vs kepler (Futures), documentadas en línea con [PORT]:
  - Base: api.binance.com (real) / testnet.binance.vision (demo spot).
  - Equity = MTM calculado a mano (Σ holdings×precio + USDT). Spot NO da totalMarginBalance.
  - "Posiciones" = saldos de la cuenta (no hay /positionRisk).
  - Maker = type=LIMIT_MAKER (spot no acepta timeInForce=GTX).
  - LONG-ONLY: nunca se vende más de lo que se tiene (spot no permite cortos).
  - Sin set_leverage, sin funding/income, sin β-neutralize.
  - NUEVO: place_stop_loss (chandelier en el exchange) + newClientOrderId (idempotencia).

Modos (env):
  INVESTOR_DRY_RUN=true  → solo loguea (no envía). DEFAULT seguro.
  INVESTOR_USE_DEMO=true → testnet.binance.vision (cuenta demo spot).
  ambos false            → api.binance.com (REAL). SOLO tras validar 1 semana en demo sin errores.
"""
from __future__ import annotations
import hashlib, hmac, logging, os, sys, time
from urllib.parse import urlencode
import requests

log = logging.getLogger("investor.execution")

# ─── Config de ejecución (por variables de entorno) ───────────────────────────
def _envstr(name, default=""):
    # robusto a comentarios inline y comillas (systemd no los separa)
    return os.environ.get(name, default).split("#")[0].strip().strip('"').strip("'")

DRY_RUN  = _envstr("INVESTOR_DRY_RUN", "true").lower() != "false"
USE_DEMO = _envstr("INVESTOR_USE_DEMO", "true").lower() != "false"
API_KEY    = _envstr("BINANCE_API_KEY")
API_SECRET = _envstr("BINANCE_API_SECRET")

MIN_ORDER_USD          = float(_envstr("INVESTOR_MIN_ORDER_USD", "5"))   # ignora deltas-polvo
MIN_NOTIONAL_FALLBACK  = float(_envstr("INVESTOR_MIN_NOTIONAL", "5"))    # si exchangeInfo no lo trae
MAX_POSITION_EQUITY    = float(_envstr("INVESTOR_MAX_POS_EQUITY", "0"))  # 0 = sin tope por posición
QUOTE = "USDT"   # moneda de cotización del universo
CAPITAL_FALLBACK = float(_envstr("INVESTOR_CAPITAL_FALLBACK", "500"))    # arranque $500

# [PORT] Futures: fapi / demo-fapi.  Spot: api / testnet.binance.vision
_BASE_REAL = "https://api.binance.com"
_BASE_DEMO = "https://testnet.binance.vision"


def _base():
    return _BASE_DEMO if USE_DEMO else _BASE_REAL


def _sign(p):
    p["timestamp"] = int(time.time() * 1000)
    p["signature"] = hmac.new(API_SECRET.encode(), urlencode(p).encode(), hashlib.sha256).hexdigest()
    return p


def _hdr():
    return {"X-MBX-APIKEY": API_KEY}


def _get(path, params):
    try:
        r = requests.get(_base() + path, params=_sign(params), headers=_hdr(), timeout=10)
        r.raise_for_status(); return r.json()
    except Exception as e:
        log.warning(f"[exec] GET {path}: {e}"); return None


def _post(path, params):
    if DRY_RUN:
        log.info(f"[exec] DRY_RUN POST {path} {params}"); return {"dry_run": True}
    try:
        r = requests.post(_base() + path, params=_sign(params), headers=_hdr(), timeout=10)
        r.raise_for_status(); return r.json()
    except Exception as e:
        log.warning(f"[exec] POST {path}: {e}"); return None


def _delete(path, params):
    if DRY_RUN:
        log.info(f"[exec] DRY_RUN DELETE {path}"); return {"dry_run": True}
    try:
        r = requests.delete(_base() + path, params=_sign(params), headers=_hdr(), timeout=10)
        r.raise_for_status(); return r.json()
    except Exception as e:
        log.warning(f"[exec] DELETE {path}: {e}"); return None


# ─── Precios (público) ─────────────────────────────────────────────────────────

def all_book_tickers():
    """Mapa {symbol: mid} de TODO el libro (1 llamada). Para valorar el equity MTM."""
    try:
        d = requests.get(_base() + "/api/v3/ticker/bookTicker", timeout=15).json()
        out = {}
        for e in d if isinstance(d, list) else []:
            try:
                out[e["symbol"]] = (float(e["bidPrice"]) + float(e["askPrice"])) / 2
            except (KeyError, ValueError, TypeError):
                continue
        return out
    except Exception as e:
        log.warning(f"[exec] bookTicker masivo: {e}"); return {}


def book_mid(symbol):
    d = _get("/api/v3/ticker/bookTicker", {"symbol": symbol})
    if isinstance(d, dict) and "bidPrice" in d:
        return (float(d["bidPrice"]) + float(d["askPrice"])) / 2
    return None


# ─── Estado de cuenta (SPOT) ────────────────────────────────────────────────────

def _account(retries=3, backoff_s=1.0):
    """/api/v3/account con reintento. None si ilegible tras los reintentos.
    [PORT] el parpadeo intermitente de la demo (kepler 2026-06-06) también ocurre en spot."""
    if DRY_RUN:
        return None
    for i in range(max(1, retries)):
        d = _get("/api/v3/account", {"recvWindow": 5000})
        if isinstance(d, dict) and "balances" in d:
            return d
        if i < retries - 1:
            time.sleep(backoff_s * (i + 1))   # backoff lineal: 1s, 2s, …
    return None


def get_balances(retries=3):
    """{asset: qty} con free+locked > 0. Base para posiciones y equity."""
    d = _account(retries)
    if not d:
        return {}
    out = {}
    for b in d.get("balances", []):
        q = float(b.get("free", 0)) + float(b.get("locked", 0))
        if q > 0:
            out[b["asset"]] = q
    return out


def get_balance(retries=3, prices=None):
    """Equity REAL = MTM = USDT + Σ (qty_activo × precio_activoUSDT). NUNCA solo el cash.

    [PORT] Futures lo daba en totalMarginBalance; en spot NO existe → se calcula sumando el
    valor a mercado de cada saldo. Sin esto el maxDD intradía se subestima y el tope −30% miente.

    Devuelve None si la cuenta es ilegible (mejor un hueco que una curva falsa → el ciclo omite)."""
    if DRY_RUN:
        return CAPITAL_FALLBACK
    bals = get_balances(retries)
    if not bals:
        return None
    prices = prices or all_book_tickers()
    equity = bals.get(QUOTE, 0.0)   # USDT a la par
    for asset, qty in bals.items():
        if asset == QUOTE:
            continue
        px = prices.get(f"{asset}{QUOTE}")
        if px:
            equity += qty * px
        else:
            log.debug(f"[exec] sin precio para {asset}{QUOTE}; excluido del MTM")
    return equity


def get_positions(prices=None):
    """{symbol(par USDT): qty} de los activos que tenemos (excluye el propio USDT).
    [PORT] sustituye a /fapi/v2/positionRisk: en spot la 'posición' es el saldo del activo base."""
    if DRY_RUN:
        return {}
    bals = get_balances()
    return {f"{a}{QUOTE}": q for a, q in bals.items() if a != QUOTE}


def get_my_trades(symbol, start_ms):
    """Fills REALES para medir slippage. Read-only, blindado ([] si falla/DRY_RUN).
    [PORT] /fapi/v1/userTrades → /api/v3/myTrades."""
    if DRY_RUN:
        return []
    d = _get("/api/v3/myTrades", {"symbol": symbol, "startTime": int(start_ms), "limit": 200})
    return d if isinstance(d, list) else []


# ─── Filtros de precisión por símbolo (exchangeInfo) ──────────────────────────
_FILTERS: dict = {}

def load_filters():
    """[PORT] /fapi/v1/exchangeInfo → /api/v3/exchangeInfo. Idéntico parseo de filtros."""
    global _FILTERS
    if _FILTERS:
        return _FILTERS
    try:
        d = requests.get(_base() + "/api/v3/exchangeInfo", timeout=15).json()
    except Exception as e:
        log.warning(f"[exec] exchangeInfo: {e}"); return {}
    for s in d.get("symbols", []):
        qp = pp = 0; minq = 0.0; minnot = MIN_NOTIONAL_FALLBACK
        for f in s["filters"]:
            if f["filterType"] == "LOT_SIZE":
                step = f["stepSize"]; qp = max(0, len(step.rstrip("0").split(".")[1]) if "." in step.rstrip("0") else 0); minq = float(f["minQty"])
            if f["filterType"] == "PRICE_FILTER":
                tick = f["tickSize"]; pp = max(0, len(tick.rstrip("0").split(".")[1]) if "." in tick.rstrip("0") else 0)
            if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                minnot = float(f.get("notional", f.get("minNotional", MIN_NOTIONAL_FALLBACK)))
        _FILTERS[s["symbol"]] = dict(qp=qp, pp=pp, minq=minq, minnot=minnot)
    return _FILTERS


# ─── Órdenes ──────────────────────────────────────────────────────────────────

def _coid(tag, symbol):
    """client_order_id determinista → IDEMPOTENCIA (enlaza con order_log.client_order_id).
    Reintentar con el MISMO id no duplica la orden (Binance lo rechaza si ya existe)."""
    base = f"inv-{tag}-{symbol}-{int(time.time())}"
    return base[:36]   # límite Binance


def place_limit_maker(symbol, side, qty, price, qp, pp, coid=None):
    """[PORT] type=LIMIT_MAKER (post-only de spot) en vez de LIMIT+timeInForce=GTX (futures)."""
    p = {
        "symbol": symbol, "side": side, "type": "LIMIT_MAKER",
        "quantity": f"{qty:.{qp}f}", "price": f"{price:.{pp}f}",
        "newClientOrderId": coid or _coid("mk", symbol),
    }
    return _post("/api/v3/order", p)


def place_stop_loss(symbol, qty, stop_price, limit_price, qp, pp, coid=None):
    """NUEVO (no existía en kepler): chandelier stop VIVE EN EL EXCHANGE como STOP_LOSS_LIMIT.
    Protege la posición aunque el robot esté caído. Solo SELL (long-only)."""
    p = {
        "symbol": symbol, "side": "SELL", "type": "STOP_LOSS_LIMIT", "timeInForce": "GTC",
        "quantity": f"{qty:.{qp}f}", "stopPrice": f"{stop_price:.{pp}f}", "price": f"{limit_price:.{pp}f}",
        "newClientOrderId": coid or _coid("sl", symbol),
    }
    return _post("/api/v3/order", p)


def cancel_all(symbol):
    """[PORT] /fapi/v1/allOpenOrders → /api/v3/openOrders."""
    return _delete("/api/v3/openOrders", {"symbol": symbol})


# ─── Rebalanceo ───────────────────────────────────────────────────────────────

MAKER_RETRIES = int(_envstr("INVESTOR_MAKER_RETRIES", "3"))
MAKER_WAIT_S  = int(_envstr("INVESTOR_MAKER_WAIT_S", "20"))


def _place_deltas(target_weights, equity, filt, prices, attempt):
    """Calcula deltas vs holdings actuales y coloca límites maker, persiguiendo el precio en
    cada reintento (menos pasivo) sin cruzar. LONG-ONLY: nunca vende más de lo que se tiene.
    Devuelve nº de órdenes colocadas."""
    current = get_positions(prices)
    placed = 0
    offset = max(0.0002 - attempt * 0.00007, 0.00002)   # menos pasivo en cada reintento
    for sym, w in target_weights.items():
        if w < 0:        # [PORT] long-only: el allocator no debe mandar pesos negativos; guard
            w = 0.0
        target_notional = w * equity if abs(w) >= 1e-4 else 0.0
        price = prices.get(sym) or book_mid(sym)
        if not price or sym not in filt:
            continue
        held = current.get(sym, 0.0)
        delta = target_notional / price - held
        if abs(delta) * price < MIN_ORDER_USD:
            continue
        f = filt[sym]
        side = "BUY" if delta > 0 else "SELL"
        if side == "SELL":
            delta = -min(abs(delta), held)   # nunca vender más de lo que se tiene
            if abs(delta) * price < MIN_ORDER_USD:
                continue
        px = price * (1 - offset) if side == "BUY" else price * (1 + offset)
        if place_limit_maker(sym, side, abs(delta), px, f["qp"], f["pp"]) is not None:
            placed += 1
    return placed


def _capital_aware_drop(target_weights, equity, filt):
    """[PORT] LOW-BARRIER de kepler SIN la parte β (long-only). A poco capital ($500), las patas
    cuyo notional (w·equity) < min-notional de su símbolo NO se pueden colocar: se SUELTAN y se
    RENORMALIZA el resto al mismo gross (redespliega el capital liberado). El libro se adapta al
    capital sin órdenes rechazadas y preservando el gross (→ respeta la exposición objetivo)."""
    import pandas as pd
    nz = target_weights[target_weights.abs() > 1e-6]
    g0 = nz.abs().sum()
    keep = {s: w for s, w in nz.items()
            if abs(w) * equity >= filt.get(s, {}).get("minnot", MIN_NOTIONAL_FALLBACK)}
    out = target_weights * 0.0
    if keep:
        kt = pd.Series(keep)
        g1 = kt.abs().sum()
        if g1 > 0:
            kt = kt * (g0 / g1)
        if MAX_POSITION_EQUITY:
            kt = kt.clip(-MAX_POSITION_EQUITY, MAX_POSITION_EQUITY)
        out.loc[kt.index] = kt
    return out, len(nz) - len(keep)


def rebalance(target_weights, equity=None, prices=None):
    """Rebalancea hacia el objetivo con órdenes maker + gestión de no-fills: cancela stale →
    coloca → espera → re-coloca lo no llenado persiguiendo el precio, hasta MAKER_RETRIES.
    Lo que no llene queda para el próximo ciclo (drift lento, aceptable).
    [PORT] sin set_leverage ni β; LONG-ONLY."""
    prices = prices or all_book_tickers()
    equity = equity or get_balance(prices=prices) or CAPITAL_FALLBACK
    filt = load_filters()   # público — también en DRY_RUN para previsualizar el libro real
    if getattr_low_barrier():
        target_weights, dropped = _capital_aware_drop(target_weights, equity, filt)
        if dropped:
            n_op = int((target_weights.abs() > 1e-6).sum())
            log.info(f"[exec] low-barrier: {dropped} pata(s) < min-notional a ${equity:.0f} soltada(s) "
                     f"→ libro adaptativo, operando {n_op} patas (gross preservado)")
    if DRY_RUN:
        for sym, w in target_weights.items():
            if abs(w) > 1e-3:
                log.info(f"[exec] DRY target {sym}: w={w:+.3f} notional={w*equity:+.0f}USD")
        return [("dry_run", int((target_weights.abs() > 1e-6).sum()))]
    current = get_positions(prices)
    # HUÉRFANAS: incluir con peso 0 los holdings que ya NO están en el target → se venden a USDT
    # (activo retirado del universo o cuya señal cayó), en vez de dejarlos drifteando sin gestión.
    orphans = [s for s in current if s not in target_weights.index]
    if orphans:
        import pandas as pd
        target_weights = pd.concat([target_weights, pd.Series(0.0, index=orphans)])
        log.info(f"[exec] vendiendo {len(orphans)} holding(s) huérfano(s) (fuera del target): {orphans}")
    syms = set(target_weights.index) | set(current.keys())
    for sym in syms:                      # 1. limpiar órdenes stale del ciclo anterior
        cancel_all(sym)
    summary = []
    for attempt in range(MAKER_RETRIES):  # 2. colocar + perseguir lo no llenado
        n = _place_deltas(target_weights, equity, filt, prices, attempt)
        summary.append(n)
        if n == 0:
            break
        if attempt < MAKER_RETRIES - 1:
            time.sleep(MAKER_WAIT_S)
            for sym in syms:
                cancel_all(sym)
            prices = all_book_tickers()   # refrescar precios para perseguir
    remaining = summary[-1] if summary else 0
    log.info(f"[exec] rebalanceo: intentos={summary} · sin llenar al final={remaining} (queda p/próximo ciclo)")
    return [("rebalance", summary, remaining)]


def flatten(equity=None):
    """Vende TODO a USDT (rebalancea a target 0). Para el HALT del circuit breaker (salida intradía
    ante eventos). Idempotente: si ya está plano, no coloca órdenes."""
    if DRY_RUN:
        return []
    import pandas as pd
    current = get_positions()
    if not current:
        return []
    target = pd.Series(0.0, index=list(current.keys()))
    return rebalance(target, equity)


def getattr_low_barrier():
    """Modo low-barrier activable por env (default ON a poco capital, como el arranque $500)."""
    return _envstr("INVESTOR_LOW_BARRIER", "true").lower() != "false"


def mode_str():
    return "DRY_RUN" if DRY_RUN else ("DEMO" if USE_DEMO else "REAL")
